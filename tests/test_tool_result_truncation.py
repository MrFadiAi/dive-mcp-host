"""Per-tool-result truncation cap (``MAX_TOOL_RESULT_CHARS``).

A production chat had a 165,018-char tag-table export hard-capped at the old
15,000, so the AI lost most of the data the user asked for. The cap is now
env-tunable (``DIVE_MAX_TOOL_RESULT_CHARS``) with a raised default, and the
conversation auto-compactor backstops context overflow. These tests pin the
resolver and the actual ``truncate_tool_results`` behavior.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import ToolMessage

from dive_mcp_host.host.agents.chat_agent import (
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    resolve_max_tool_result_chars,
    truncate_tool_results,
)


def test_default_cap_raised_above_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIVE_MAX_TOOL_RESULT_CHARS", raising=False)
    assert resolve_max_tool_result_chars() == DEFAULT_MAX_TOOL_RESULT_CHARS
    assert DEFAULT_MAX_TOOL_RESULT_CHARS > 15_000  # raised from the old 15K


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVE_MAX_TOOL_RESULT_CHARS", "60000")
    assert resolve_max_tool_result_chars() == 60_000


def test_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVE_MAX_TOOL_RESULT_CHARS", "nope")
    assert resolve_max_tool_result_chars() == DEFAULT_MAX_TOOL_RESULT_CHARS


def test_truncate_uses_default_for_long_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DIVE_MAX_TOOL_RESULT_CHARS", raising=False)
    long = ToolMessage(content="x" * 50_000, tool_call_id="1", name="t")
    out = truncate_tool_results.invoke([long])
    assert isinstance(out[0], ToolMessage)
    assert "[TRUNCATED" in out[0].content
    assert out[0].content.startswith("x" * DEFAULT_MAX_TOOL_RESULT_CHARS)


def test_truncate_leaves_short_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIVE_MAX_TOOL_RESULT_CHARS", raising=False)
    short = ToolMessage(content="x" * 100, tool_call_id="1", name="t")
    out = truncate_tool_results.invoke([short])
    assert out[0].content == "x" * 100  # unchanged


def test_truncate_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVE_MAX_TOOL_RESULT_CHARS", "5000")
    msg = ToolMessage(content="y" * 10_000, tool_call_id="1", name="t")
    out = truncate_tool_results.invoke([msg])
    assert "[TRUNCATED" in out[0].content
    assert out[0].content.startswith("y" * 5_000)  # body capped at override
