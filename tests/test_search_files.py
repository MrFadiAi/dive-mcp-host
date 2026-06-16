"""Tests for the search_files tool + its pure _search_files helper.

A structured grep across a directory (file:line:match) without a shell — so the
agent can find code/text without bash and without tripping the write-command
detector. _search_files is pure filesystem I/O, unit-tested directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.search_files import _search_files, search_files


def test_search_files_finds_matches_with_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world peace\n", encoding="utf-8")

    matches = _search_files(str(tmp_path), "world")
    files = [m["file"] for m in matches]
    assert "a.txt" in files
    assert any("sub" in f for f in files)
    a = next(m for m in matches if m["file"] == "a.txt")
    assert a["line"] == 1


def test_search_files_no_match_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")
    assert _search_files(str(tmp_path), "zzz") == []


def test_search_files_respects_max_matches(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("target\n", encoding="utf-8")
    matches = _search_files(str(tmp_path), "target", max_matches=3)
    assert len(matches) == 3


def test_search_files_skips_junk_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.txt").write_text("target\n", encoding="utf-8")
    (tmp_path / "real.txt").write_text("target\n", encoding="utf-8")

    matches = _search_files(str(tmp_path), "target")
    files = [m["file"] for m in matches]
    assert "real.txt" in files
    assert not any("node_modules" in f for f in files)


def test_search_files_invalid_regex_raises(tmp_path: Path) -> None:
    with pytest.raises(re.error):
        _search_files(str(tmp_path), "(unclosed")


@pytest.mark.asyncio
async def test_search_files_tool_renders_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world\n", encoding="utf-8")
    result = await search_files.ainvoke({"path": str(tmp_path), "pattern": "world"}, {})
    assert "a.txt" in result
    assert "world" in result


@pytest.mark.asyncio
async def test_search_files_tool_invalid_regex_is_clean_error(tmp_path: Path) -> None:
    result = await search_files.ainvoke(
        {"path": str(tmp_path), "pattern": "(unclosed"}, {}
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_search_files_tool_no_match_message(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("nothing\n", encoding="utf-8")
    result = await search_files.ainvoke({"path": str(tmp_path), "pattern": "zzz"}, {})
    assert "No matches" in result


def test_search_files_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "search_files" in {t.name for t in get_local_tools()}


# --- context lines (grep -B/-A): surrounding lines around each match ---


def test_search_files_context_before_after(tmp_path: Path) -> None:
    """`before`/`after` attach surrounding (lineno, text) pairs to each match so
    the agent can read a hit in context (grep -B/-A)."""
    (tmp_path / "a.txt").write_text(
        "line1\nline2\nTARGET\nline4\nline5\n", encoding="utf-8"
    )
    matches = _search_files(str(tmp_path), "TARGET", before=1, after=1)
    assert len(matches) == 1
    m = matches[0]
    assert m["line"] == 3
    assert m["text"] == "TARGET"
    assert m["before"] == [(2, "line2")]
    assert m["after"] == [(4, "line4")]


def test_search_files_default_context_is_empty(tmp_path: Path) -> None:
    """Default before=0/after=0 yields no context — backward compatible."""
    (tmp_path / "a.txt").write_text("a\nTARGET\nb\n", encoding="utf-8")
    m = _search_files(str(tmp_path), "TARGET")[0]
    assert m["before"] == []
    assert m["after"] == []


def test_search_files_context_clamps_at_file_start(tmp_path: Path) -> None:
    """`before` larger than the available preceding lines returns only what
    exists (no negative line numbers)."""
    (tmp_path / "a.txt").write_text("TARGET\nx\n", encoding="utf-8")
    m = _search_files(str(tmp_path), "TARGET", before=5)[0]
    assert m["before"] == []  # match is line 1 — nothing before it


def test_search_files_context_clamps_at_file_end(tmp_path: Path) -> None:
    """`after` larger than the remaining lines returns only what exists."""
    (tmp_path / "a.txt").write_text("x\nTARGET\n", encoding="utf-8")
    m = _search_files(str(tmp_path), "TARGET", after=5)[0]
    assert m["after"] == []  # match is the last line — nothing after it


def test_search_files_context_multi_line(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text(
        "a\nb\nc\nTARGET\nd\ne\nf\n", encoding="utf-8"
    )
    m = _search_files(str(tmp_path), "TARGET", before=2, after=2)[0]
    assert m["before"] == [(2, "b"), (3, "c")]
    assert m["after"] == [(5, "d"), (6, "e")]


@pytest.mark.asyncio
async def test_search_files_tool_renders_context(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("p\nTARGET\nq\n", encoding="utf-8")
    result = await search_files.ainvoke(
        {"path": str(tmp_path), "pattern": "TARGET", "before": 1, "after": 1}, {}
    )
    # the match header line + the two context lines (indented, with line nums)
    assert "a.txt:2: TARGET" in result
    assert "1 | p" in result  # before context
    assert "3 | q" in result  # after context


@pytest.mark.asyncio
async def test_search_files_tool_default_has_no_context(tmp_path: Path) -> None:
    """Default (no before/after) keeps the original single-line output."""
    (tmp_path / "a.txt").write_text("p\nTARGET\nq\n", encoding="utf-8")
    result = await search_files.ainvoke(
        {"path": str(tmp_path), "pattern": "TARGET"}, {}
    )
    assert "a.txt:2: TARGET" in result
    assert "| p" not in result  # no context markers
