"""Tests for the compactor's _build_summarize_input — the text fed to the
summarization LLM. Pins that the FULL tool history is retained (the TIA agent
makes many tool calls per turn; capping at 5/turn lost later calls)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from dive_mcp_host.host.agents.conversation_compactor import _build_summarize_input


def _ai_with_tools(names: list[str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": {}, "id": str(i)} for i, n in enumerate(names)],
    )


def test_summary_includes_all_distinct_tool_names():
    """Six distinct tool calls must all appear (the old cap kept only the first 5)."""
    msgs = [
        HumanMessage("do the task"),
        _ai_with_tools(["t1", "t2", "t3", "t4", "t5", "t6"]),
    ]
    out = _build_summarize_input(msgs)
    for name in ["t1", "t2", "t3", "t4", "t5", "t6"]:
        assert name in out, f"tool {name} missing from summary"


def test_summary_dedupes_repeated_tool_names():
    """Repeated tool names are listed once (compact, no noise)."""
    msgs = [_ai_with_tools(["browse", "search", "browse", "search", "browse"])]
    out = _build_summarize_input(msgs)
    assert "browse" in out and "search" in out
    # The joined names appear once each: "browse, search".
    assert "browse, search" in out


def test_summary_includes_human_text_and_tool_result():
    msgs = [
        HumanMessage("please configure PROFINET"),
        ToolMessage(content="PROFINET configured successfully", tool_call_id="0"),
    ]
    out = _build_summarize_input(msgs)
    assert "PROFINET" in out
    assert "configure" in out
    assert "configured successfully" in out


def test_summary_starts_with_summarize_prompt():
    msgs = [HumanMessage("hi")]
    out = _build_summarize_input(msgs)
    assert out.startswith("Summarize")
