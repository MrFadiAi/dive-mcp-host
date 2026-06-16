"""Tests for the list_extraction_cache tool + its helpers.

Lets the AI see what's cached (PLC/HMI, age) before querying/re-extracting.
``cache_snapshot`` (in extraction/__init__.py) and ``_format_cache_snapshot``
(the pure formatter) are unit-tested directly; the tool is a thin wrapper.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.extraction import cache_result, cache_snapshot
from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction
from dive_mcp_host.internal_tools.tools.list_extraction_cache import (
    _format_cache_snapshot,
)


def test_cache_snapshot_reports_key_type_and_age(tmp_path) -> None:
    key_p = cache_result(str(tmp_path / "plc_dir"), PlcExtraction(), prefix="plc")
    key_h = cache_result(str(tmp_path / "hmi_dir"), HmiExtraction(), prefix="hmi")

    snap = {e["key"]: e for e in cache_snapshot()}
    assert key_p in snap and key_h in snap
    assert "Plc" in snap[key_p]["type"]
    assert "Hmi" in snap[key_h]["type"]
    assert snap[key_p]["age_seconds"] >= 0
    assert snap[key_h]["age_seconds"] >= 0


def test_cache_snapshot_entries_have_expected_fields(tmp_path) -> None:
    cache_result(str(tmp_path / "x"), PlcExtraction(), prefix="plc")
    snap = cache_snapshot()
    assert snap  # at least the entry just cached
    assert {"key", "type", "age_seconds"} <= set(snap[0].keys())


def test_format_snapshot_empty_message() -> None:
    out = _format_cache_snapshot([])
    assert "No cached" in out


def test_format_snapshot_renders_entries_with_labels() -> None:
    snapshot = [
        {"key": "plc:abc123", "type": "PlcExtraction", "age_seconds": 65},
        {"key": "hmi:def456", "type": "HmiExtraction", "age_seconds": 5},
    ]
    out = _format_cache_snapshot(snapshot)
    assert "plc:abc123" in out and "hmi:def456" in out
    assert "PLC" in out and "HMI" in out
    assert "1m5s" in out  # 65s -> 1m5s
    assert "0m5s" in out  # 5s -> 0m5s


@pytest.mark.asyncio
async def test_list_extraction_cache_tool_renders_cached(tmp_path) -> None:
    from dive_mcp_host.internal_tools.tools.list_extraction_cache import (
        list_extraction_cache,
    )

    cache_result(str(tmp_path / "plc_dir"), PlcExtraction(), prefix="plc")
    out = await list_extraction_cache.ainvoke({}, {})
    assert "Cached extractions" in out
    assert "PLC" in out


def test_list_extraction_cache_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "list_extraction_cache" in {t.name for t in get_local_tools()}
