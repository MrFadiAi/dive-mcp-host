"""The agent's per-turn step ceiling (langgraph ``recursion_limit``).

Deep TIA Portal investigations exceed the old hardcoded 102-step ceiling, so
the agent stops mid-task with ``"Sorry, need more steps to process this
request."`` and forces the user to type ``continue`` (observed in production:
one chat ran a single 102-step turn for ~14 minutes, then halted). These tests
pin the env-tunable resolver that replaced the hardcoded literals.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.host.agents.agent_factory import (
    DEFAULT_RECURSION_LIMIT,
    resolve_recursion_limit,
)


def test_default_is_raised_above_old_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIVE_AGENT_RECURSION_LIMIT", raising=False)
    assert resolve_recursion_limit() == DEFAULT_RECURSION_LIMIT
    # raised from the old hardcoded 102 that cut deep investigations short
    assert DEFAULT_RECURSION_LIMIT > 102


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVE_AGENT_RECURSION_LIMIT", "300")
    assert resolve_recursion_limit() == 300


def test_invalid_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIVE_AGENT_RECURSION_LIMIT", "not-a-number")
    assert resolve_recursion_limit() == DEFAULT_RECURSION_LIMIT


def test_too_small_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny value would brick the agent — fall back to the default instead."""
    monkeypatch.setenv("DIVE_AGENT_RECURSION_LIMIT", "1")
    assert resolve_recursion_limit() == DEFAULT_RECURSION_LIMIT
