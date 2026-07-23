import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from dive_mcp_host.host.agents.chat_agent import (
    ChatAgentFactory,
    coalesce_system_messages,
    complete_tool_calls,
)
from dive_mcp_host.models.fake import FakeMessageToolModel


@pytest.fixture
def agent() -> ChatAgentFactory:
    """Return a chat agent."""
    model = FakeMessageToolModel()
    return ChatAgentFactory(
        model=model,
        tools=[],
    )


@pytest.mark.asyncio
async def test_chat_agent(agent: ChatAgentFactory):
    """Test the chat agent."""
    graph = agent.create_agent(
        prompt="you are a helpful assistant",
    )

    initial_state = agent.create_initial_state(query="Hello, world!")
    config = agent.create_config(user_id="default", thread_id="default")

    end_state = await graph.ainvoke(initial_state, config)
    assert len(end_state["messages"]) == 2

    messages = [
        HumanMessage(content="Hello, world!"),
        AIMessage(content="I am a fake model."),
    ] * 100

    messages.append(HumanMessage(content="last human message"))

    initial_state = agent.create_initial_state(query=messages)
    config = agent.create_config(
        user_id="default",
        thread_id="default",
        max_input_tokens=100,
        oversize_policy="window",
    )

    end_state = await graph.ainvoke(initial_state, config)
    assert len(end_state["messages"]) < 100

    assert end_state["messages"][-2].content == "last human message"
    assert end_state["messages"][-1].content == "I am a fake model."


@pytest.mark.asyncio
async def test_summarize_compaction_fires_without_max_input_tokens():
    """Regression: compaction NEVER fired in production (real chats climbed to
    306k tokens with zero drops) for two reasons — (1) the summarize path was
    gated on max_input_tokens, which the chat launcher never passes, and (2) it
    compacted `ordered` (tool_call_order's tool-call-pairing DELTA), which is
    empty for any well-formed conversation. Both are fixed here: summarize needs
    only oversize_policy + context_window, and compacts the full state messages.
    """
    model = FakeMessageToolModel(responses=[AIMessage(content="compaction summary")])
    agent = ChatAgentFactory(model=model, tools=[])

    # A well-formed conversation (no orphaned tool calls) large enough to exceed
    # a tiny context window's 80% compaction threshold.
    messages = [HumanMessage(content="hello world " * 80), AIMessage(content="ack " * 80)] * 20
    messages.append(HumanMessage(content="last human message"))
    state = {"messages": messages}
    config = {"configurable": {"oversize_policy": "summarize", "context_window": 200}}

    result_state = await agent._before_agent(state, config)
    # Compaction fired → a SystemMessage summary was prepended.
    assert any(m.type == "system" for m in result_state["messages"]), (
        "summarize compaction must fire without max_input_tokens"
    )


def test_complete_tool_calls():
    """Test the complete_tool_calls function."""
    messages = [
        HumanMessage(content="Hello, world!"),
        AIMessage(
            content="I am a fake model.",
            tool_calls=[
                {"id": "tool-1", "name": "foo", "args": {}},
                {"id": "tool-2", "name": "bar", "args": {}},
            ],
        ),
        ToolMessage(content="result", tool_call_id="tool-1"),
        HumanMessage(content="last human message"),
    ]
    fixed_messages = complete_tool_calls(messages)
    assert len(fixed_messages) == 5
    tool_message_ids = {
        message.tool_call_id
        for message in fixed_messages
        if isinstance(message, ToolMessage)
    }
    assert tool_message_ids == {"tool-1", "tool-2"}


def test_coalesce_system_messages_merges_non_consecutive_system_to_single_front_message():
    """Regression: auto-compaction stores its summary SystemMessage at the END of
    state (langgraph's add_messages appends new-id messages while keeping
    existing-id ones in place), so at model-call time the list looks like
    [System(prompt), ...humans/ais..., System(summary), Human]. Anthropic rejects
    that as "Received multiple non-consecutive system messages". The pipeline
    must merge every SystemMessage into a single one at the front.
    """
    messages = [
        SystemMessage(content="You are TIA Agent."),
        HumanMessage(content="hi", id="1"),
        AIMessage(content="hello", id="2"),
        SystemMessage(
            content="<conversation_summary>earlier turns</conversation_summary>",
            name="compaction_summary",
            id="S1",
        ),
        HumanMessage(content="continue", id="3"),
    ]

    result = coalesce_system_messages.invoke(messages)

    # Exactly one SystemMessage, at the front (two were merged into one).
    assert len(result) == len(messages) - 1
    assert isinstance(result[0], SystemMessage)
    assert not any(isinstance(m, SystemMessage) for m in result[1:])
    # Both system contents merged (prompt text first, then the summary).
    assert "You are TIA Agent." in result[0].content
    assert "<conversation_summary>" in result[0].content
    # Non-system messages keep their original relative order.
    assert [m.content for m in result if not isinstance(m, SystemMessage)] == [
        "hi",
        "hello",
        "continue",
    ]


def test_coalesce_system_messages_leaves_single_or_no_system_untouched():
    """A single (or zero) SystemMessage is already provider-valid; don't reshape."""
    with_system = [SystemMessage(content="sys"), HumanMessage(content="hi", id="1")]
    result = coalesce_system_messages.invoke(with_system)
    assert [type(m) for m in result] == [SystemMessage, HumanMessage]
    assert result[0].content == "sys"

    no_system = [HumanMessage(content="hi", id="1"), AIMessage(content="yo", id="2")]
    assert coalesce_system_messages.invoke(no_system) == no_system


def test_coalesce_system_messages_prevents_anthropic_non_consecutive_error():
    """The exact production error must no longer be raised once coalesced.

    Reproduces the failing shape (prompt + stray compaction summary) and asserts
    Anthropic's message formatter accepts it instead of raising
    "Received multiple non-consecutive system messages".
    """
    from langchain_anthropic.chat_models import _format_messages

    messages = [
        SystemMessage(content="SYSTEM_PROMPT"),
        HumanMessage(content="hi", id="1"),
        AIMessage(content="yo", id="2"),
        SystemMessage(content="<summary>", name="compaction_summary", id="S1"),
        HumanMessage(content="continue", id="3"),
    ]

    coalesced = coalesce_system_messages.invoke(messages)

    # Must not raise.
    system, formatted = _format_messages(coalesced)
    assert system is not None  # merged system content survived at the top level
    assert len(formatted) == 3  # the three non-system messages
