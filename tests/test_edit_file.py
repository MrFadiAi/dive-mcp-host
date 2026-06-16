"""Tests for the edit_file tool + its pure _edit_text helper.

A surgical find-and-replace in a file (vs write_file's full overwrite). The
pure helper is the tested core; the tool reads → edits → writes (elicitation is
skipped when no elicitation_manager is in the config, so tests pass {}).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.edit_file import _edit_text, edit_file


def test_edit_text_single_match() -> None:
    new, count = _edit_text("hello world", "world", "there")
    assert new == "hello there"
    assert count == 1


def test_edit_text_no_match_raises() -> None:
    with pytest.raises(ValueError):
        _edit_text("hello", "zzz", "x")


def test_edit_text_empty_old_raises() -> None:
    with pytest.raises(ValueError):
        _edit_text("hello", "", "x")


def test_edit_text_multiple_without_replace_all_raises() -> None:
    with pytest.raises(ValueError):
        _edit_text("a a a", "a", "b")


def test_edit_text_replace_all() -> None:
    new, count = _edit_text("a a a", "a", "b", replace_all=True)
    assert new == "b b b"
    assert count == 3


def test_edit_text_new_contains_old_does_not_loop() -> None:
    # str.replace is non-recursive; the count comes from the original.
    new, count = _edit_text("x", "x", "xx")
    assert new == "xx"
    assert count == 1


@pytest.mark.asyncio
async def test_edit_file_tool_replaces_one_occurrence(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("foo bar baz", encoding="utf-8")
    result = await edit_file.ainvoke(
        {"path": str(f), "old_text": "bar", "new_text": "qux"}, {}
    )
    assert "uccess" in result
    assert f.read_text(encoding="utf-8") == "foo qux baz"


@pytest.mark.asyncio
async def test_edit_file_tool_not_found_is_clean_error(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hello", encoding="utf-8")
    result = await edit_file.ainvoke(
        {"path": str(f), "old_text": "zzz", "new_text": "x"}, {}
    )
    assert "Error" in result
    assert f.read_text(encoding="utf-8") == "hello"  # unchanged


@pytest.mark.asyncio
async def test_edit_file_tool_missing_file(tmp_path: Path) -> None:
    result = await edit_file.ainvoke(
        {"path": str(tmp_path / "nope"), "old_text": "a", "new_text": "b"}, {}
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_edit_file_tool_replace_all(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("a a a", encoding="utf-8")
    result = await edit_file.ainvoke(
        {"path": str(f), "old_text": "a", "new_text": "b", "replace_all": True}, {}
    )
    assert "uccess" in result
    assert f.read_text(encoding="utf-8") == "b b b"


@pytest.mark.asyncio
async def test_edit_file_tool_ambiguous_without_replace_all(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("a a a", encoding="utf-8")
    result = await edit_file.ainvoke(
        {"path": str(f), "old_text": "a", "new_text": "b"}, {}
    )
    assert "Error" in result
    assert f.read_text(encoding="utf-8") == "a a a"  # unchanged


def test_edit_file_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "edit_file" in {t.name for t in get_local_tools()}
