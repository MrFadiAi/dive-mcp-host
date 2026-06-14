"""Tests for the list_dir tool + its pure _list_dir helper.

A structured directory listing (name/type/size) without a shell — so the agent
can inspect a directory without bash and without tripping the write-command
detector. _list_dir is pure filesystem I/O, unit-tested directly; the tool is
a thin async wrapper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.internal_tools.tools.list_dir import _list_dir, list_dir


def test_list_dir_returns_dirs_first_then_files_with_sizes(tmp_path: Path) -> None:
    (tmp_path / "z_dir").mkdir()
    (tmp_path / "a_file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "b_dir").mkdir()

    entries = _list_dir(str(tmp_path))
    names = [e["name"] for e in entries]
    # directories first (sorted), then files (sorted)
    assert names == ["b_dir", "z_dir", "a_file.txt"]

    file_entry = next(e for e in entries if e["name"] == "a_file.txt")
    assert file_entry["type"] == "file"
    assert file_entry["size"] == 5

    dir_entry = next(e for e in entries if e["name"] == "b_dir")
    assert dir_entry["type"] == "dir"
    assert dir_entry["size"] is None


def test_list_dir_missing_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        _list_dir(str(tmp_path / "does-not-exist"))


def test_list_dir_not_a_directory_raises(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        _list_dir(str(f))


def test_list_dir_empty_returns_empty_list(tmp_path: Path) -> None:
    assert _list_dir(str(tmp_path)) == []


def test_list_dir_expands_user_home(tmp_path: Path, monkeypatch) -> None:
    # Path.expanduser() on Windows honours USERPROFILE (and HOME elsewhere);
    # set both so the test is platform-independent.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "in_home.txt").write_text("x", encoding="utf-8")
    entries = _list_dir("~")
    assert any(e["name"] == "in_home.txt" for e in entries)


@pytest.mark.asyncio
async def test_list_dir_tool_renders_entries(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")

    result = await list_dir.ainvoke({"path": str(tmp_path)}, {})
    assert "readme.txt" in result
    assert "sub" in result
    assert "2 bytes" in result  # file size rendered


@pytest.mark.asyncio
async def test_list_dir_tool_missing_dir_is_clean_error(tmp_path: Path) -> None:
    result = await list_dir.ainvoke({"path": str(tmp_path / "nope")}, {})
    assert "Error" in result


def test_list_dir_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "list_dir" in {t.name for t in get_local_tools()}
