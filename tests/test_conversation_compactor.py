"""Tests for the auto-compaction logic (conversation_compactor.py)."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from dive_mcp_host.host.agents.conversation_compactor import (
    RECENT_WINDOW_RATIO,
    compact_conversation,
    find_safe_cut_point,
    get_context_window,
)


class _FakeModel:
    """Minimal async stand-in for a chat model returning a fixed summary."""

    def __init__(self, summary: str = "SUMMARY") -> None:
        self._summary = summary

    async def ainvoke(self, messages, **kwargs):  # noqa: ARG002
        """Return a canned AIMessage (duck-typed chat model)."""
        return AIMessage(content=self._summary)


def _run(coro):
    """Drive an async compaction call from a sync test."""
    return asyncio.run(coro)


# ----------------------------------------------------- get_context_window


def test_get_context_window_known_model():
    """Known model names resolve to their documented window."""
    assert get_context_window("gpt-4o") == 128_000


def test_get_context_window_configured_override_wins():
    """An explicit configured_window overrides the model's default."""
    assert get_context_window("gpt-4o", configured_window=50_000) == 50_000


def test_get_context_window_marker_suffix():
    """The '[1m]' marker denotes a 1,000,000-token window (e.g. glm-5.2[1m])."""
    assert get_context_window("glm-5.2[1m]") == 1_000_000
    assert get_context_window("model[2k]") == 2_000


def test_get_context_window_unknown_defaults_safely():
    """Unknown models fall back to a safe 128k window."""
    assert get_context_window("some-unknown-model") == 128_000


# ----------------------------------------------------- find_safe_cut_point


def test_find_safe_cut_point_finds_human_boundary():
    """Cutting mid-turn walks back to the nearest HumanMessage boundary."""
    msgs = [
        HumanMessage("a"),
        AIMessage("b"),
        HumanMessage("c"),
        AIMessage("d"),
        ToolMessage(content="r", tool_call_id="1"),
    ]
    assert find_safe_cut_point(msgs, 4) == 2
    assert find_safe_cut_point(msgs, 3) == 2


def test_find_safe_cut_point_falls_back_to_target_when_no_human():
    """With no HumanMessage, the target index is returned unchanged."""
    msgs = [AIMessage("a"), ToolMessage(content="b", tool_call_id="1"), AIMessage("c")]
    assert find_safe_cut_point(msgs, 2) == 2


def test_find_safe_cut_point_never_orphans_a_toolmessage():
    """When the target lands on a ToolMessage with no Human boundary below it
    (a long agentic tool-call chain), the cut must NOT return the ToolMessage
    index — otherwise ``recent_messages`` would start with an orphaned tool
    result whose preceding AI tool-call landed in ``old``, causing an API error.
    """
    msgs = [
        HumanMessage("hi"),  # 0
        AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),  # 1
        ToolMessage(content="r1", tool_call_id="1"),  # 2
        AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": "2"}]),  # 3
        ToolMessage(content="r2", tool_call_id="2"),  # 4
    ]
    cut = find_safe_cut_point(msgs, 4)  # target is a ToolMessage; no Human in 1..4
    assert cut <= 4
    assert not isinstance(msgs[cut], ToolMessage), (
        f"cut at {cut} would orphan a ToolMessage at the start of recent"
    )


def test_find_safe_cut_point_clamps_oversized_index():
    """target_index == len(messages) must not raise IndexError."""
    msgs = [HumanMessage("a"), AIMessage("b"), HumanMessage("c")]
    cut = find_safe_cut_point(msgs, len(msgs))
    assert 0 <= cut < len(msgs)


def test_find_safe_cut_point_empty_list_safe():
    """An empty message list returns 0 without error."""
    assert find_safe_cut_point([], 0) == 0


# ----------------------------------------------------- compact_conversation


def test_no_compaction_when_under_budget():
    """Under the budget, the message list is returned unchanged."""
    msgs = [HumanMessage("hi"), AIMessage("hello")]
    result = _run(compact_conversation(msgs, _FakeModel(), context_window=1_000_000))
    assert result.compacted is False
    assert result.messages == msgs


def test_compaction_summarizes_old_and_keeps_recent():
    """Over budget: old messages are summarized, the most recent is retained."""
    msgs = [HumanMessage(f"message number {n}") for n in range(500)]
    msgs.append(AIMessage("recent reply"))
    result = _run(
        compact_conversation(msgs, _FakeModel("THE SUMMARY"), context_window=2_000),
    )
    assert result.compacted is True
    assert result.summary == "THE SUMMARY"
    assert result.messages[-1].content == "recent reply"
    assert result.messages[0].name == "compaction_summary"
    assert result.tokens_after < result.tokens_before


def test_compaction_oversized_last_message_does_not_crash():
    """The most recent message is kept even if it alone exceeds the recent budget."""
    context_window = 2_000
    recent_budget = int(context_window * RECENT_WINDOW_RATIO)
    big = "X" * (recent_budget * 50)
    msgs = [
        HumanMessage("earlier turn one"),
        AIMessage("earlier reply"),
        HumanMessage(big),
    ]
    result = _run(
        compact_conversation(msgs, _FakeModel("OK"), context_window=context_window),
    )
    assert result.compacted is True
    assert any(getattr(m, "content", "") == big for m in result.messages)
