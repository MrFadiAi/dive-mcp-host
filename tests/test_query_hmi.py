"""Tests for the HMI extraction follow-up query (the TIA feature).

Mirrors ``query_plc_blocks`` for HMI: ``extract_hmi_screens`` caches an
``HmiExtraction`` but there was no tool to query it, so every follow-up about
screens / navigation / tag bindings / JS events re-parsed the project. These
test the pure formatter backing ``query_hmi_screens``.
"""

from __future__ import annotations

import pytest

from dive_mcp_host.extraction.hmi_query import format_hmi_query
from dive_mcp_host.extraction.models import (
    ElementEvent,
    HmiExtraction,
    HmiSummary,
    ScreenElement,
    ScreenResult,
    TagBinding,
)


def _sample_extraction() -> HmiExtraction:
    return HmiExtraction(
        source_project="proj",
        summary=HmiSummary(
            total_screens=2,
            total_elements=3,
            total_elements_with_events=1,
            total_tag_bindings=2,
            total_js_functions=1,
            total_unique_plc_tags=2,
            total_navigation_links=2,
        ),
        screens=[
            ScreenResult(
                screen_name="Main",
                file="screen_1.rdf",
                element_count=2,
                elements=[
                    ScreenElement(
                        name="Button_Start",
                        type="button",
                        io_role="control_button (writes to PLC)",
                        tag_bindings=[
                            TagBinding(
                                property="process_value",
                                hmi_tag="Start_HMI",
                                plc_tag="Motor_Start",
                                plc_name="Motor_Start",
                                data_type="Bool",
                                connection="PLC_1",
                            )
                        ],
                        events=[
                            ElementEvent(
                                function="Button_Start_OnClick",
                                event_type="OnClick",
                                plc_tags=["Motor_Start"],
                                navigates_to=["Operate"],
                                code='SetTagValue("Motor_Start", 1)',
                            )
                        ],
                    ),
                    ScreenElement(
                        name="IOField_Speed",
                        type="io_field",
                        io_role="output (reads from PLC)",
                        tag_bindings=[
                            TagBinding(
                                property="process_value",
                                hmi_tag="Speed_HMI",
                                plc_tag="Motor_Speed",
                                plc_name="Motor_Speed",
                                data_type="Int",
                                connection="PLC_1",
                            )
                        ],
                    ),
                ],
                screen_navigations=["Operate"],
                plc_tags_referenced=["Motor_Start"],
            ),
            ScreenResult(
                screen_name="Operate",
                file="screen_2.rdf",
                element_count=1,
                elements=[
                    ScreenElement(
                        name="Button_Back",
                        type="button",
                        io_role="navigation_button",
                        events=[
                            ElementEvent(
                                function="Button_Back_OnClick",
                                event_type="OnClick",
                                navigates_to=["Main"],
                            )
                        ],
                    )
                ],
                screen_navigations=["Main"],
            ),
        ],
        navigation_map={"Main": ["Operate"], "Operate": ["Main"]},
        plc_tag_index={
            "Motor_Start": {
                "plc_tag": '"Motor_Start"',
                "data_type": "Bool",
                "connection": "PLC_1",
                "used_in_screens": ["Main"],
            },
            "Motor_Speed": {
                "plc_tag": '"Motor_Speed"',
                "data_type": "Int",
                "connection": "PLC_1",
                "used_in_screens": ["Main"],
            },
        },
        hmi_device={"device_type": "TP700", "instance_id": "1"},
    )


def test_summary_detail() -> None:
    out = format_hmi_query(_sample_extraction(), "summary")
    assert "Screens: 2" in out
    assert "elements" in out.lower()


def test_navigation_detail() -> None:
    out = format_hmi_query(_sample_extraction(), "navigation")
    assert "Main" in out and "Operate" in out
    assert "->" in out


def test_screens_list_detail() -> None:
    out = format_hmi_query(_sample_extraction(), "screens")
    assert "Main" in out and "Operate" in out


def test_screen_detail_includes_elements_and_bindings() -> None:
    out = format_hmi_query(_sample_extraction(), "screen", "Main")
    assert "Button_Start" in out
    assert "Motor_Start" in out  # tag binding surfaced
    assert "Operate" in out  # navigation


def test_screen_detail_case_insensitive() -> None:
    out = format_hmi_query(_sample_extraction(), "screen", "operate")
    assert "Operate" in out
    assert "Button_Back" in out


def test_screen_detail_unknown_lists_available() -> None:
    out = format_hmi_query(_sample_extraction(), "screen", "Nope")
    assert "not found" in out.lower()
    assert "Main" in out and "Operate" in out


def test_tag_detail_renders_real_parser_shape() -> None:
    """hmi_parser builds plc_tag_index[tag] = {plc_tag, data_type, connection,
    used_in_screens: [...]} — render that cleanly, not a Python list repr."""
    out = format_hmi_query(_sample_extraction(), "tag", "Motor_Start")
    assert "Main" in out  # used_in_screens surfaced
    assert "PLC_1" in out  # connection surfaced
    assert "Bool" in out  # data type surfaced
    assert "['Main'" not in out  # no python list repr


def test_tag_detail_case_insensitive() -> None:
    out = format_hmi_query(_sample_extraction(), "tag", "motor_speed")
    assert "Main" in out


def test_tag_detail_unknown_lists_available() -> None:
    out = format_hmi_query(_sample_extraction(), "tag", "Ghost")
    assert "not found" in out.lower()


def test_missing_name_for_name_required_detail() -> None:
    out = format_hmi_query(_sample_extraction(), "screen", None)
    assert "Error" in out


def test_invalid_detail_returns_error() -> None:
    out = format_hmi_query(_sample_extraction(), "bogus")
    assert "Error" in out


def test_never_raises_on_empty_extraction() -> None:
    empty = HmiExtraction(summary=HmiSummary())
    assert "Screens: 0" in format_hmi_query(empty, "summary")
    assert "No navigation" in format_hmi_query(empty, "navigation")
    assert "No screens" in format_hmi_query(empty, "screens")
    assert "not found" in format_hmi_query(empty, "screen", "X").lower()


# --- orphans: screens with no inbound navigation (TIA feature) ---


def test_orphans_lists_screens_with_no_inbound_links() -> None:
    """Orphan detection: screens that no other screen navigates to (zero inbound
    links) — potentially unreachable / dead-end entry points. Parallel to PLC
    dead_code."""
    extraction = HmiExtraction(
        summary=HmiSummary(total_screens=3),
        screens=[
            ScreenResult(screen_name="Main", file="s1.rdf", element_count=1),
            ScreenResult(screen_name="Operate", file="s2.rdf", element_count=1),
            ScreenResult(screen_name="Ghost", file="s3.rdf", element_count=1),
        ],
        # Main -> Operate; nobody navigates to Main (entry) or Ghost (orphan)
        navigation_map={"Main": ["Operate"], "Operate": ["Main"]},
    )
    out = format_hmi_query(extraction, "orphans")
    assert "Ghost" in out  # never a navigation target -> orphan
    assert "Operate" not in out  # Main navigates to it


def test_orphans_none_when_all_reachable() -> None:
    extraction = HmiExtraction(
        summary=HmiSummary(total_screens=2),
        screens=[
            ScreenResult(screen_name="A", file="a.rdf", element_count=1),
            ScreenResult(screen_name="B", file="b.rdf", element_count=1),
        ],
        navigation_map={"A": ["B"], "B": ["A"]},
    )
    out = format_hmi_query(extraction, "orphans")
    assert "No orphan" in out


# --- End-to-end: the query_hmi_screens tool reading the real cache ---


@pytest.mark.asyncio
async def test_tool_reads_cached_extraction(tmp_path: object) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.internal_tools.tools.query_hmi import query_hmi_screens

    key = cache_result(str(tmp_path) + "/hmi", _sample_extraction(), prefix="hmi")
    out = await query_hmi_screens.ainvoke({"cache_key": key, "detail": "summary"}, {})
    assert "Screens: 2" in out


@pytest.mark.asyncio
async def test_tool_missing_cache_key_is_a_clean_error() -> None:
    from dive_mcp_host.internal_tools.tools.query_hmi import query_hmi_screens

    out = await query_hmi_screens.ainvoke(
        {"cache_key": "hmi:does-not-exist", "detail": "summary"}, {}
    )
    assert "Error" in out
    assert "No cached" in out


@pytest.mark.asyncio
async def test_tool_rejects_plc_extraction_in_cache(tmp_path: object) -> None:
    from dive_mcp_host.extraction import cache_result
    from dive_mcp_host.extraction.models import PlcExtraction, PlcSummary
    from dive_mcp_host.internal_tools.tools.query_hmi import query_hmi_screens

    key = cache_result(
        str(tmp_path) + "/plc", PlcExtraction(summary=PlcSummary()), prefix="plc"
    )
    out = await query_hmi_screens.ainvoke({"cache_key": key, "detail": "summary"}, {})
    assert "Error" in out
    assert "PLC" in out


def test_query_hmi_screens_is_registered() -> None:
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    assert "query_hmi_screens" in {t.name for t in get_local_tools()}


# --- mermaid: navigation graph visualization (TIA feature) ---


def test_mermaid_renders_full_navigation_graph() -> None:
    """Render the whole navigation_map (screen -> targets) as a Mermaid
    flowchart — every source screen and every reachable target is a node."""
    out = format_hmi_query(_sample_extraction(), "mermaid")
    assert "```mermaid" in out
    assert "graph TD" in out
    assert "Main" in out and "Operate" in out
    assert "-->" in out  # navigation edges present


def test_mermaid_focused_subtree_from_root() -> None:
    """With name=root, render only the navigation sub-tree reachable from that
    screen (BFS) — upstream screens are excluded."""
    out = format_hmi_query(
        HmiExtraction(
            summary=HmiSummary(total_screens=4),
            screens=[
                ScreenResult(screen_name="Home", file="h.rdf", element_count=1),
                ScreenResult(screen_name="Setup", file="s.rdf", element_count=1),
                ScreenResult(screen_name="Tune", file="t.rdf", element_count=1),
                ScreenResult(screen_name="Diag", file="d.rdf", element_count=1),
            ],
            # Home -> Setup -> Tune; Diag is a sibling of Home (not reachable from Setup)
            navigation_map={
                "Home": ["Setup", "Diag"],
                "Setup": ["Tune"],
                "Tune": [],
                "Diag": [],
            },
        ),
        "mermaid",
        "Setup",
    )
    assert "Setup" in out and "Tune" in out
    assert "Home" not in out  # upstream of the root — excluded
    assert "Diag" not in out  # sibling branch — not reachable from Setup


def test_mermaid_empty_navigation_is_valid() -> None:
    """Screens with no navigation links still render a valid (edge-less) block."""
    extraction = HmiExtraction(
        summary=HmiSummary(total_screens=1),
        screens=[ScreenResult(screen_name="Only", file="o.rdf", element_count=1)],
    )
    out = format_hmi_query(extraction, "mermaid")
    assert "```mermaid" in out
    assert "Only" in out


def test_mermaid_unknown_root_lists_available() -> None:
    out = format_hmi_query(_sample_extraction(), "mermaid", "Nope")
    assert "not found" in out.lower()
    assert "Main" in out and "Operate" in out  # suggestions

