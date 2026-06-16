"""Tests for the PLC↔HMI tag coverage gap (the TIA feature).

Commissioning gap analysis: which PLC tags have NO HMI binding (not surfaced to
the operator) and which HMI tags reference PLC tags not present in the PLC
extraction (possible stale references). Pure formatter lives next to the tag
trace (extraction/trace.py); thin tool wraps it.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction
from dive_mcp_host.extraction.trace import format_coverage_gap


def _plc(*tags: str) -> PlcExtraction:
    return PlcExtraction(tag_xref={t: {"used_in": []} for t in tags})


def _hmi(*tags: str) -> HmiExtraction:
    return HmiExtraction(plc_tag_index={t: {"used_in_screens": []} for t in tags})


def test_coverage_gap_classifies_bound_plc_only_hmi_only() -> None:
    plc = _plc("A", "B", "C")  # PLC has A,B,C
    hmi = _hmi("B", "C", "D")  # HMI binds B,C (and references D)

    out = format_coverage_gap(plc, hmi)
    # bound (in both)
    assert "Bound (in both): 2" in out
    # PLC-only = A (no HMI binding -> not surfaced to operator)
    assert "PLC-only" in out and "1" in out
    assert "A" in out
    # HMI-only = D (no PLC usage -> stale reference)
    assert "HMI-only" in out
    assert "D" in out


def test_coverage_gap_all_bound() -> None:
    out = format_coverage_gap(_plc("X", "Y"), _hmi("X", "Y"))
    assert "Bound (in both): 2" in out
    assert "PLC-only" in out and "0" in out
    assert "HMI-only" in out and "0" in out


def test_coverage_gap_empty_extractions() -> None:
    out = format_coverage_gap(PlcExtraction(), HmiExtraction())
    assert "Bound (in both): 0" in out


# --- tool e2e ---


@pytest.mark.asyncio
async def test_coverage_gap_tool_reads_both_caches(tmp_path) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.coverage_gap import coverage_gap

    plc_key = cache_result(str(tmp_path) + "/plc", _plc("A", "B"), prefix="plc")
    hmi_key = cache_result(str(tmp_path) + "/hmi", _hmi("B", "C"), prefix="hmi")
    out = await coverage_gap.ainvoke(
        {"plc_cache_key": plc_key, "hmi_cache_key": hmi_key}, {}
    )
    assert "A" in out  # PLC-only
    assert "C" in out  # HMI-only
    assert "B" in out  # bound


@pytest.mark.asyncio
async def test_coverage_gap_tool_missing_cache_key_is_clean_error(tmp_path) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.coverage_gap import coverage_gap

    hmi_key = cache_result(str(tmp_path) + "/hmi", _hmi("B"), prefix="hmi")
    out = await coverage_gap.ainvoke(
        {"plc_cache_key": "plc:nope", "hmi_cache_key": hmi_key}, {}
    )
    assert "Error" in out


def test_coverage_gap_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "coverage_gap" in {t.name for t in get_local_tools()}
