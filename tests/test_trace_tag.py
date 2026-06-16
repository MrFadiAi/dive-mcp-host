"""Tests for the combined PLC↔HMI tag trace (the TIA feature).

Connects the HMI UI to PLC logic: given a tag, report where it's used in the
PLC (tag_xref: blocks/address/type) AND the HMI (plc_tag_index: screens/
connection). Pure formatter + thin tool; tested with fabricated extractions
using the real parser shapes.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction
from dive_mcp_host.extraction.trace import format_tag_trace


def _plc() -> PlcExtraction:
    return PlcExtraction(
        tag_xref={
            "Motor_Start": {
                "plc_tag_address": "%I0.0",
                "data_type": "Bool",
                "used_in": ["OB1", "FC100"],
            }
        }
    )


def _hmi() -> HmiExtraction:
    return HmiExtraction(
        plc_tag_index={
            "Motor_Start": {
                "plc_tag": '"Motor_Start"',
                "data_type": "Bool",
                "connection": "PLC_1",
                "used_in_screens": ["Main", "Operate"],
            }
        }
    )


def test_trace_tag_reports_both_plc_and_hmi_usage() -> None:
    out = format_tag_trace(_plc(), _hmi(), "Motor_Start")
    # PLC side
    assert "PLC usage" in out
    assert "OB1" in out and "FC100" in out
    assert "%I0.0" in out
    # HMI side
    assert "HMI usage" in out
    assert "Main" in out and "Operate" in out
    assert "PLC_1" in out


def test_trace_tag_case_insensitive() -> None:
    out = format_tag_trace(_plc(), _hmi(), "motor_start")
    assert "OB1" in out
    assert "Main" in out


def test_trace_tag_missing_on_hmi_side() -> None:
    out = format_tag_trace(_plc(), _hmi(), "Motor_Start")
    # present everywhere here; test the not-found path with a tag absent on HMI
    out2 = format_tag_trace(_plc(), HmiExtraction(), "Motor_Start")
    assert "PLC usage" in out2 and "OB1" in out2  # PLC side still shown
    assert "not found" in out2.lower()  # HMI side reports not found


def test_trace_tag_missing_on_both_sides() -> None:
    out = format_tag_trace(PlcExtraction(), HmiExtraction(), "Ghost")
    assert "not found" in out.lower()


# --- tool e2e ---


@pytest.mark.asyncio
async def test_trace_tag_tool_reads_both_caches(tmp_path) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.trace_tag import trace_tag

    plc_key = cache_result(str(tmp_path) + "/plc", _plc(), prefix="plc")
    hmi_key = cache_result(str(tmp_path) + "/hmi", _hmi(), prefix="hmi")
    out = await trace_tag.ainvoke(
        {"plc_cache_key": plc_key, "hmi_cache_key": hmi_key, "tag": "Motor_Start"}, {}
    )
    assert "OB1" in out and "Main" in out


@pytest.mark.asyncio
async def test_trace_tag_tool_missing_plc_key_is_clean_error(tmp_path) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.trace_tag import trace_tag

    hmi_key = cache_result(str(tmp_path) + "/hmi", _hmi(), prefix="hmi")
    out = await trace_tag.ainvoke(
        {"plc_cache_key": "plc:nope", "hmi_cache_key": hmi_key, "tag": "Motor_Start"},
        {},
    )
    assert "Error" in out


def test_trace_tag_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "trace_tag" in {t.name for t in get_local_tools()}
