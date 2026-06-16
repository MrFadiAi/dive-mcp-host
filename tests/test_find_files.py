"""Tests for the find_files tool + its pure _find_files helper.

Find files by NAME glob pattern without a shell — complements ``search_files``
(content grep) and ``file_tree`` (structure). ``_find_files`` is pure
filesystem I/O, unit-tested directly; ``find_files`` is a thin async wrapper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.find_files import _find_files, find_files


def test_find_files_recursive_glob(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "c.txt").write_text("x", encoding="utf-8")
    (tmp_path / "ignore.md").write_text("x", encoding="utf-8")

    found = _find_files(str(tmp_path), "**/*.txt")
    assert sorted(found) == ["a.txt", "sub/b.txt", "sub/deep/c.txt"]


def test_find_files_top_level_glob(tmp_path: Path) -> None:
    """A bare ``*.ext`` pattern (no ``**``) matches only the top level."""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("x", encoding="utf-8")

    found = _find_files(str(tmp_path), "*.txt")
    assert found == ["a.txt"]


def test_find_files_skips_junk_dirs(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "cfg.txt").write_text("x", encoding="utf-8")

    found = _find_files(str(tmp_path), "**/*.txt")
    assert found == ["real.txt"]


def test_find_files_respects_max_results(tmp_path: Path) -> None:
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    found = _find_files(str(tmp_path), "*.txt", max_results=5)
    assert len(found) == 5


def test_find_files_no_match_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert _find_files(str(tmp_path), "**/*.json") == []


def test_find_files_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _find_files(str(tmp_path / "nope"), "**/*.txt")


@pytest.mark.asyncio
async def test_find_files_tool_renders_matches(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    result = await find_files.ainvoke(
        {"path": str(tmp_path), "pattern": "*.json"}, {}
    )
    assert "a.json" in result


@pytest.mark.asyncio
async def test_find_files_tool_no_match_message(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = await find_files.ainvoke(
        {"path": str(tmp_path), "pattern": "*.json"}, {}
    )
    assert "No files" in result


@pytest.mark.asyncio
async def test_find_files_tool_missing_dir_is_clean_error(tmp_path: Path) -> None:
    result = await find_files.ainvoke(
        {"path": str(tmp_path / "nope"), "pattern": "*.txt"}, {}
    )
    assert "Error" in result


def test_find_files_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "find_files" in {t.name for t in get_local_tools()}
