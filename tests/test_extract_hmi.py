"""Tests for extract_hmi's HMITags.xlsx path resolution.

Bug: the ``hmi_tags_path`` field documents "If not specified, will look for
DATA_HMI/HMITags.xlsx relative to the project" — but the tool passed ``None``
straight through, so the documented auto-discovery never happened and tag
resolution was silently skipped. ``_resolve_hmi_tags_path`` implements the
documented lookup; it is pure (no openpyxl) so it unit-tests cleanly.
"""

from __future__ import annotations

from pathlib import Path

from dive_mcp_host.internal_tools.tools.extract_hmi import _resolve_hmi_tags_path


def test_explicit_path_returned_as_is(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.xlsx"
    explicit.write_text("x")
    assert _resolve_hmi_tags_path(str(tmp_path), str(explicit)) == str(explicit)


def test_explicit_path_wins_even_if_missing(tmp_path: Path) -> None:
    # An explicit path is honoured as-is; load_hmi_tags handles a missing file.
    missing = tmp_path / "nope.xlsx"
    assert _resolve_hmi_tags_path(str(tmp_path), str(missing)) == str(missing)


def test_auto_finds_data_hmi_location(tmp_path: Path) -> None:
    (tmp_path / "DATA_HMI").mkdir()
    xlsx = tmp_path / "DATA_HMI" / "HMITags.xlsx"
    xlsx.write_text("x")
    assert _resolve_hmi_tags_path(str(tmp_path), None) == str(xlsx)


def test_auto_finds_root_location(tmp_path: Path) -> None:
    xlsx = tmp_path / "HMITags.xlsx"
    xlsx.write_text("x")
    assert _resolve_hmi_tags_path(str(tmp_path), None) == str(xlsx)


def test_auto_returns_none_when_absent(tmp_path: Path) -> None:
    assert _resolve_hmi_tags_path(str(tmp_path), None) is None


def test_data_hmi_location_preferred_over_root(tmp_path: Path) -> None:
    (tmp_path / "DATA_HMI").mkdir()
    preferred = tmp_path / "DATA_HMI" / "HMITags.xlsx"
    preferred.write_text("x")
    (tmp_path / "HMITags.xlsx").write_text("x")  # root copy too
    assert _resolve_hmi_tags_path(str(tmp_path), None) == str(preferred)
