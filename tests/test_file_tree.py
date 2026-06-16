"""Tests for the file_tree tool + its pure _file_tree helper.

A depth-limited recursive directory tree (indented) without a shell — a project-
structure overview. Complements list_dir (flat one-level). _file_tree is pure
filesystem I/O, unit-tested directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.file_tree import _file_tree, file_tree


def test_file_tree_renders_nested_indented_structure(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "c.txt").write_text("x", encoding="utf-8")

    lines = _file_tree(str(tmp_path), max_depth=3)
    # directories listed first with a trailing slash, at no indent
    assert any(line == "sub/" for line in lines)
    assert any(line == "a.txt" for line in lines)
    # nested entries are indented (2 spaces per depth)
    assert any(line.startswith("  ") and "b.txt" in line for line in lines)
    assert any(line.startswith("  ") and line.rstrip().endswith("deep/") for line in lines)
    assert any(line.startswith("    ") and "c.txt" in line for line in lines)


def test_file_tree_respects_max_depth(tmp_path: Path) -> None:
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "d2").mkdir()
    (tmp_path / "d1" / "d2" / "d3").mkdir()
    (tmp_path / "d1" / "d2" / "d3" / "leaf.txt").write_text("x", encoding="utf-8")

    lines = _file_tree(str(tmp_path), max_depth=1)
    text = "\n".join(lines)
    assert "d1/" in text
    assert "d2" not in text  # beyond max_depth -> not descended


def test_file_tree_skips_junk_dirs_and_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden").write_text("x", encoding="utf-8")
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")

    lines = _file_tree(str(tmp_path))
    text = "\n".join(lines)
    assert "real.txt" in text
    assert "node_modules" not in text
    assert ".hidden" not in text


def test_file_tree_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert _file_tree(str(tmp_path)) == []


@pytest.mark.asyncio
async def test_file_tree_tool_renders(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("x", encoding="utf-8")
    result = await file_tree.ainvoke({"path": str(tmp_path)}, {})
    assert "sub/" in result
    assert "a.txt" in result


@pytest.mark.asyncio
async def test_file_tree_tool_missing_dir_is_clean_error(tmp_path: Path) -> None:
    result = await file_tree.ainvoke({"path": str(tmp_path / "nope")}, {})
    assert "Error" in result


def test_file_tree_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "file_tree" in {t.name for t in get_local_tools()}
