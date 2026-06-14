"""Tests for the PLC extraction follow-up query (the TIA feature).

``extract_plc_blocks`` caches a rich ``PlcExtraction`` and tells the AI to
"reference this cache_key for follow-up queries" — but until now there was no
tool to read the cache. These test the pure formatter that backs
``query_plc_blocks`` (block source / interface / calls / callers / tag usage /
call tree / summary), so follow-ups no longer require re-parsing the XML.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.extraction.models import (
    BlockInterface,
    BlockResult,
    InterfaceMember,
    Network,
    PlcExtraction,
    PlcSummary,
)
from dive_mcp_host.extraction.query import format_plc_query


def _sample_extraction() -> PlcExtraction:
    return PlcExtraction(
        source_path="/proj/blocks",
        summary=PlcSummary(
            total_blocks=2,
            fc_count=1,
            ob_count=1,
            stl_count=2,
            total_calls=1,
            unique_tag_refs=2,
            total_tag_refs=3,
            plc_tags_loaded=2,
        ),
        blocks=[
            BlockResult(
                block_name="OB1",
                block_number=1,
                block_type="OB",
                programming_language="STL",
                interface=BlockInterface(
                    inputs=[InterfaceMember(name="Start", data_type="Bool")]
                ),
                networks=[Network(language="STL", code="      CALL FC100\n")],
                calls=["FC100"],
                tag_references=["Motor_Start"],
            ),
            BlockResult(
                block_name="FC100",
                block_number=100,
                block_type="FC",
                programming_language="STL",
                code="      A #Start\n      = #Run\n",
                tag_references=["Motor_Start", "Motor_Run"],
            ),
        ],
        call_tree={"OB1": ["FC100"]},
        called_by={"FC100": ["OB1"]},
        tag_xref={
            # Real shape produced by plc_parser.build_tag_xref +
            # parse_plc_directory (resolved_tags): metadata + used_in block list
            "Motor_Start": {
                "plc_tag_address": "%I0.0",
                "data_type": "Bool",
                "used_in": ["OB1", "FC100"],
            },
            "Motor_Run": {
                "plc_tag_address": "%Q0.0",
                "data_type": "Bool",
                "used_in": ["FC100"],
            },
        },
        plc_tags={
            "Motor_Start": {"data_type": "Bool"},
            "Motor_Run": {"data_type": "Bool"},
        },
    )


def test_summary_detail() -> None:
    out = format_plc_query(_sample_extraction(), "summary")
    assert "Blocks: 2" in out
    assert "FC" in out and "STL" in out


def test_call_tree_detail() -> None:
    out = format_plc_query(_sample_extraction(), "call_tree")
    assert "OB1" in out and "FC100" in out
    assert "->" in out


def test_block_detail_includes_code_and_interface() -> None:
    out = format_plc_query(_sample_extraction(), "block", "FC100")
    assert "FC100" in out
    assert "A #Start" in out  # reconstructed code surfaced
    assert "= #Run" in out


def test_block_detail_reconstructs_code_from_networks_when_code_empty() -> None:
    # OB1 has no top-level `code` but a network with code -> must surface it.
    out = format_plc_query(_sample_extraction(), "block", "OB1")
    assert "CALL FC100" in out
    assert "IN:" in out  # interface section rendered
    assert "Start" in out


def test_block_detail_case_insensitive_lookup() -> None:
    out = format_plc_query(_sample_extraction(), "block", "fc100")
    assert "FC100" in out


def test_block_detail_unknown_block_lists_available() -> None:
    out = format_plc_query(_sample_extraction(), "block", "FB999")
    assert "not found" in out.lower()
    assert "OB1" in out and "FC100" in out  # suggestions


def test_calls_detail() -> None:
    out = format_plc_query(_sample_extraction(), "calls", "OB1")
    assert "FC100" in out


def test_callers_detail() -> None:
    out = format_plc_query(_sample_extraction(), "callers", "FC100")
    assert "OB1" in out


def test_callers_detail_for_top_level_block() -> None:
    out = format_plc_query(_sample_extraction(), "callers", "OB1")
    # OB1 is called by nobody (entry point)
    assert "nobody" in out.lower() or "top-level" in out.lower() or "none" in out.lower()


def test_tag_detail_lists_usage() -> None:
    out = format_plc_query(_sample_extraction(), "tag", "Motor_Start")
    assert "OB1" in out and "FC100" in out


def test_tag_detail_case_insensitive() -> None:
    out = format_plc_query(_sample_extraction(), "tag", "motor_run")
    assert "FC100" in out


def test_tag_detail_unknown_lists_available() -> None:
    out = format_plc_query(_sample_extraction(), "tag", "Nonexistent")
    assert "not found" in out.lower()


def test_tag_detail_renders_real_parser_shape() -> None:
    """Regression: the PLC parser produces tag_xref[tag] = {plc_tag_address,
    data_type, used_in: [blocks]}, NOT {block: usage}. The tag detail must
    surface the used_in block list + address + data type — not dump the
    metadata keys as blocks with a Python list repr."""
    out = format_plc_query(_sample_extraction(), "tag", "Motor_Start")
    assert "OB1" in out and "FC100" in out  # used_in blocks surfaced
    assert "%I0.0" in out  # address surfaced
    assert "Bool" in out  # data type surfaced
    # must NOT dump the list as a python repr under a metadata key
    assert "['OB1'" not in out
    assert "used_in:" not in out.lower()


def test_missing_name_for_name_required_detail() -> None:
    out = format_plc_query(_sample_extraction(), "block", None)
    assert "Error" in out
    assert "name" in out.lower()


def test_invalid_detail_returns_error() -> None:
    out = format_plc_query(_sample_extraction(), "bogus")
    assert "Error" in out
    assert "block" in out  # lists valid options


def test_never_raises_on_empty_extraction() -> None:
    """An extraction with no blocks/trees must produce a clean message, not raise."""
    empty = PlcExtraction(summary=PlcSummary())
    assert "Blocks: 0" in format_plc_query(empty, "summary")
    assert "No call tree" in format_plc_query(empty, "call_tree")
    out = format_plc_query(empty, "block", "X")
    assert "not found" in out.lower()


# --- dead_code: unused FB/FC detection (TIA feature) ---


def _dead_code_extraction() -> PlcExtraction:
    return PlcExtraction(
        summary=PlcSummary(),
        blocks=[
            BlockResult(block_name="OB1", block_type="OB", block_number=1),
            BlockResult(block_name="FC100", block_type="FC", block_number=100),
            BlockResult(
                block_name="FC200", block_type="FC", block_number=200
            ),  # never called -> dead
            BlockResult(block_name="DB10", block_type="DB", block_number=10),
        ],
        called_by={"FC100": ["OB1"]},
        call_tree={"OB1": ["FC100"]},
    )


def test_dead_code_lists_unused_fb_fc_only() -> None:
    """Dead-code detection lists FB/FC blocks with zero callers, excluding OB
    entry points and DB/IDB data blocks."""
    out = format_plc_query(_dead_code_extraction(), "dead_code")
    assert "FC200" in out  # uncalled FC -> dead
    assert "FC100" not in out  # FC100 is called by OB1
    assert "OB1" not in out  # OB = entry point, excluded
    assert "DB10" not in out  # DB = data block, excluded


def test_dead_code_reports_none_when_all_used() -> None:
    extraction = PlcExtraction(
        summary=PlcSummary(),
        blocks=[
            BlockResult(block_name="OB1", block_type="OB", block_number=1),
            BlockResult(block_name="FC100", block_type="FC", block_number=100),
        ],
        called_by={"FC100": ["OB1"]},
        call_tree={"OB1": ["FC100"]},
    )
    out = format_plc_query(extraction, "dead_code")
    assert "No unused" in out


# --- search: free-text grep over cached PLC code (TIA feature) ---


def _search_extraction() -> PlcExtraction:
    return PlcExtraction(
        summary=PlcSummary(),
        blocks=[
            BlockResult(
                block_name="FC100",
                block_type="FC",
                block_number=100,
                code="      A #Start\n      = #Run\n      L #Speed\n",
            ),
            BlockResult(
                block_name="FC200",
                block_type="FC",
                block_number=200,
                code="      L #Setpoint\n      T #Speed\n",
            ),
        ],
    )


def test_search_finds_blocks_containing_pattern() -> None:
    """Free-text grep over reconstructed code: find which blocks contain a
    pattern + the matching lines. Distinct from 'tag' (structured tag_xref)."""
    out = format_plc_query(_search_extraction(), "search", "= #Run")
    assert "FC100" in out
    assert "= #Run" in out  # matching line surfaced
    assert "FC200" not in out


def test_search_is_case_insensitive() -> None:
    out = format_plc_query(_search_extraction(), "search", "speed")
    assert "FC100" in out
    assert "FC200" in out


def test_search_no_match_message() -> None:
    out = format_plc_query(_search_extraction(), "search", "ZZZNOMATCHZZZ")
    assert "No blocks match" in out


def test_search_requires_a_term() -> None:
    out = format_plc_query(_search_extraction(), "search", None)
    assert "Error" in out


# --- End-to-end: the query_plc_blocks tool reading the real cache ---


@pytest.mark.asyncio
async def test_tool_reads_cached_extraction(tmp_path: object) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.query_plc import query_plc_blocks

    key = cache_result(str(tmp_path) + "/blocks", _sample_extraction(), prefix="plc")
    out = await query_plc_blocks.ainvoke(
        {"cache_key": key, "detail": "summary"}, {}
    )
    assert "Blocks: 2" in out


@pytest.mark.asyncio
async def test_tool_block_detail_from_cache(tmp_path: object) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.query_plc import query_plc_blocks

    key = cache_result(str(tmp_path) + "/blocks", _sample_extraction(), prefix="plc")
    out = await query_plc_blocks.ainvoke(
        {"cache_key": key, "detail": "block", "name": "FC100"}, {}
    )
    assert "FC100" in out and "A #Start" in out


@pytest.mark.asyncio
async def test_tool_missing_cache_key_is_a_clean_error() -> None:
    from dive_mcp_host.internal_tools.tools.query_plc import query_plc_blocks

    out = await query_plc_blocks.ainvoke(
        {"cache_key": "plc:does-not-exist", "detail": "summary"}, {}
    )
    assert "Error" in out
    assert "No cached" in out


@pytest.mark.asyncio
async def test_tool_rejects_hmi_extraction_in_cache(tmp_path: object) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.extraction.models import HmiExtraction
    from dive_mcp_host.internal_tools.tools.query_plc import query_plc_blocks

    key = cache_result(str(tmp_path) + "/hmi", HmiExtraction(), prefix="hmi")
    out = await query_plc_blocks.ainvoke(
        {"cache_key": key, "detail": "summary"}, {}
    )
    assert "Error" in out
    assert "HMI" in out


def test_query_plc_blocks_is_registered() -> None:
    """The follow-up tool must reach the agent via the local-tools registry."""
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "query_plc_blocks" in {t.name for t in get_local_tools()}

