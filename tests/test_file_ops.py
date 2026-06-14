"""Robustness tests for read_file / write_file.

A tool must never raise out to the agent — bad inputs (unknown codec, binary
file read as text) must come back as an error *string*. Before the fix both
`LookupError` (bad encoding) and `UnicodeDecodeError` (binary as text, a
`ValueError`) escaped the `except OSError` handlers and crashed the tool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.file_ops import _slice_lines, read_file, write_file


@pytest.mark.asyncio
async def test_read_file_bad_encoding_returns_clean_error(tmp_path: Path) -> None:
    """An unknown codec raises LookupError (not OSError) — must be surfaced."""
    f = tmp_path / "note.txt"
    f.write_text("hello world", encoding="utf-8")

    result = await read_file.ainvoke(
        {"path": str(f), "encoding": "not-a-real-codec"}, {}
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_read_file_binary_returns_clean_error(tmp_path: Path) -> None:
    """Reading a binary file as text raises UnicodeDecodeError (a ValueError,
    not an OSError). Must return a clean error, not crash the tool."""
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00\xfa\xfb this is not utf-8")

    result = await read_file.ainvoke({"path": str(f)}, {})
    assert "Error" in result


@pytest.mark.asyncio
async def test_read_file_valid_still_works(tmp_path: Path) -> None:
    """Regression guard: normal reads are unaffected by the broader handler."""
    f = tmp_path / "ok.txt"
    f.write_text("plain content", encoding="utf-8")

    result = await read_file.ainvoke({"path": str(f)}, {})
    assert result == "plain content"


@pytest.mark.asyncio
async def test_write_file_bad_encoding_returns_clean_error(tmp_path: Path) -> None:
    """Writing with a bad codec raises LookupError — must be a clean error.

    No elicitation_manager in the config, so confirmation is skipped and the
    write is attempted (raising at the codec step)."""
    target = tmp_path / "out" / "x.txt"
    result = await write_file.ainvoke(
        {"path": str(target), "content": "x", "encoding": "not-a-real-codec"}, {}
    )
    assert "Error" in result


# --- read_file line-range support (_slice_lines) ---


_FIVE_LINES = "line1\nline2\nline3\nline4\nline5\n"


def test_slice_lines_no_range_returns_all() -> None:
    assert _slice_lines(_FIVE_LINES, None, None) == _FIVE_LINES


def test_slice_lines_start_only() -> None:
    assert _slice_lines(_FIVE_LINES, 3, None) == "line3\nline4\nline5\n"


def test_slice_lines_end_only() -> None:
    assert _slice_lines(_FIVE_LINES, None, 2) == "line1\nline2\n"


def test_slice_lines_both_inclusive() -> None:
    # 1-based, end inclusive
    assert _slice_lines(_FIVE_LINES, 2, 4) == "line2\nline3\nline4\n"


def test_slice_lines_start_beyond_total_is_empty() -> None:
    assert _slice_lines(_FIVE_LINES, 10, None) == ""


def test_slice_lines_start_after_end_is_empty() -> None:
    assert _slice_lines(_FIVE_LINES, 4, 2) == ""


def test_slice_lines_end_beyond_total_clamps() -> None:
    assert _slice_lines(_FIVE_LINES, 4, 100) == "line4\nline5\n"


def test_slice_lines_zero_or_negative_start_treated_as_one() -> None:
    assert _slice_lines(_FIVE_LINES, 0, None) == _FIVE_LINES
    assert _slice_lines(_FIVE_LINES, -2, 1) == "line1\n"


@pytest.mark.asyncio
async def test_read_file_returns_requested_line_range(tmp_path: Path) -> None:
    """read_file honours start_line/end_line so the agent can read a slice of a
    large file without loading all of it."""
    f = tmp_path / "big.txt"
    f.write_text(_FIVE_LINES, encoding="utf-8")

    result = await read_file.ainvoke(
        {"path": str(f), "start_line": 2, "end_line": 4}, {}
    )
    assert result == "line2\nline3\nline4\n"


@pytest.mark.asyncio
async def test_read_file_without_range_unchanged(tmp_path: Path) -> None:
    """Regression guard: omitting the range reads the whole file as before."""
    f = tmp_path / "small.txt"
    f.write_text(_FIVE_LINES, encoding="utf-8")
    result = await read_file.ainvoke({"path": str(f)}, {})
    assert result == _FIVE_LINES


# --- write_file append mode ---


@pytest.mark.asyncio
async def test_write_file_append_creates_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    result = await write_file.ainvoke(
        {"path": str(target), "content": "line1\n", "append": True}, {}
    )
    assert "uccess" in result  # success message
    assert target.read_text(encoding="utf-8") == "line1\n"


@pytest.mark.asyncio
async def test_write_file_append_preserves_existing(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    target.write_text("line1\n", encoding="utf-8")
    await write_file.ainvoke(
        {"path": str(target), "content": "line2\n", "append": True}, {}
    )
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


@pytest.mark.asyncio
async def test_write_file_overwrite_is_the_default(tmp_path: Path) -> None:
    """Regression guard: without append, write replaces the file as before."""
    target = tmp_path / "log.txt"
    target.write_text("old\n", encoding="utf-8")
    await write_file.ainvoke({"path": str(target), "content": "new\n"}, {})
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_write_file_append_respects_dry_run(tmp_path: Path) -> None:
    from dive_mcp_host.host.agents.agent_factory import ConfigurableKey

    target = tmp_path / "log.txt"
    result = await write_file.ainvoke(
        {"path": str(target), "content": "x", "append": True},
        {"configurable": {ConfigurableKey.DRY_RUN: True}},
    )
    assert "DRY RUN" in result
    assert not target.exists()
