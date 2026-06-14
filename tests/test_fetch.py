"""Tests for fetch's response truncation helper.

Bug: the `application/json` branch returned ``response.text`` UNTRUNCATED
while the ``text/``/``application/xml`` branch capped at 50 000 chars — so a
large JSON fetch could overflow the agent's context. Extracted a shared
``_truncate_response`` helper used by both branches.
"""

from __future__ import annotations

from dive_mcp_host.internal_tools.tools.fetch import _truncate_response


def test_truncate_response_under_limit_unchanged() -> None:
    assert _truncate_response("short body") == "short body"


def test_truncate_response_over_limit_is_capped_with_marker() -> None:
    big = "x" * 60_000
    out = _truncate_response(big)
    assert len(out) < len(big)
    assert out.startswith("x" * 50_000)
    assert "truncated" in out


def test_truncate_response_custom_limit() -> None:
    out = _truncate_response("abcdefghij", limit=4)
    assert out == "abcd\n... (truncated)"


def test_truncate_response_at_exact_limit_unchanged() -> None:
    exact = "y" * 50_000
    assert _truncate_response(exact) == exact
