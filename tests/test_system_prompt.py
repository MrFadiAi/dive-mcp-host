"""Regression: never advertise TIA Portal tools that don't work on the worker.

A production chat burned 23+ tool calls on ``"Unsupported worker method"`` for
``list_plcs``/``list_blocks``/``find_tags``/``search_code``/``tag_xref``/
``tag_usage`` (and ``read_cross_references`` failed with "Compile failed"), so
the AI could not search the project, read the wrong PLC's blocks, guessed an
answer, and got it wrong. These tests pin BOTH the guide-mode prompt and the
main system prompt to the search path that actually works
(extract_plc_blocks -> query_plc_blocks(search/tag)).
"""

from __future__ import annotations

from dive_mcp_host.httpd.conf.system_prompt import (
    guide_mode_instructions,
    system_prompt,
)

# TIA Portal worker methods that DO NOT WORK (observed "Unsupported worker
# method" or "Compile failed" in production). Neither prompt may advertise them.
# NOTE: `list_plcs` and `list_blocks` were REMOVED from this list after cycles 1-2
# (2026-06-16) confirmed they now WORK on the live (updated) worker — `list_plcs`
# is the canonical PLC-name source (its `deviceName`), and the prompt deliberately
# references it. Re-add a name here ONLY if a future live chat shows it erroring.
BROKEN_TOOLS = [
    "find_tags",
    "search_code",
    "tag_xref",
    "tag_usage",
    "read_cross_references",
]

# The WORKING code-search path (Python host tools, cycles 21-31) that the prompts
# must steer the AI toward instead.
WORKING_SEARCH_TOOLS = ["extract_plc_blocks", "query_plc_blocks"]


def test_guide_mode_does_not_advertise_broken_tools() -> None:
    out = guide_mode_instructions()
    for broken in BROKEN_TOOLS:
        assert broken not in out, (
            f"guide-mode advertises broken TIA tool '{broken}'"
        )


def test_guide_mode_advertises_working_search_path() -> None:
    out = guide_mode_instructions()
    for tool in WORKING_SEARCH_TOOLS:
        assert tool in out, f"guide-mode missing working search tool '{tool}'"


def test_guide_mode_forbids_inventing_tool_names() -> None:
    out = guide_mode_instructions().lower()
    assert (
        "tool list" in out
        or "do not invent" in out
        or "never invent" in out
        or "do not guess" in out
    )


def test_guide_mode_forbids_fabricating_answers() -> None:
    """If search fails, the AI must say so — not guess (the root cause of the
    wrong first answer in production)."""
    out = guide_mode_instructions().lower()
    assert "fabricate" in out or "cannot find" in out or "say so" in out


def test_main_system_prompt_does_not_advertise_broken_tools() -> None:
    """The main (non-guide) system prompt must also avoid the broken names."""
    out = system_prompt("")
    for broken in BROKEN_TOOLS:
        assert broken not in out, (
            f"main system prompt advertises broken TIA tool '{broken}'"
        )


def test_main_system_prompt_advertises_working_search_path() -> None:
    out = system_prompt("")
    for tool in WORKING_SEARCH_TOOLS:
        assert tool in out, f"main system prompt missing working search tool '{tool}'"


def test_main_system_prompt_warns_user_plc_name_unreliable() -> None:
    """The AI retried a wrong PLC name 6x because it trusted the user's spelling.
    The prompt must say the user's PLC name is not authoritative."""
    out = system_prompt("").lower()
    assert (
        "not authoritative" in out or "typo" in out or "partial" in out
    ), "system prompt must warn that the user's PLC name may be wrong"


def test_main_system_prompt_names_come_from_list_plcs_devicename() -> None:
    """Cycle-2 evidence: ``scan_open_projects`` returns the software/``plcName``
    (e.g. "PLC DIG TWIN", "PLUKROBOT"), but ``list_tag_tables`` /
    ``export_tag_table_xml`` REJECT it ("No PLC software named …") and require the
    ``list_plcs`` ``deviceName`` (e.g. "PLF-01A-PLC_2", "S7-1500/ET200MP station_1").
    The AI wasted 6 failing calls per tag query using the wrong name form. The
    prompt must steer it to ``list_plcs`` ``deviceName`` for block/tag tools."""
    out = system_prompt("").lower()
    assert "list_plcs" in out, "system prompt must name list_plcs as the PLC-name source"
    assert "devicename" in out, (
        "system prompt must tell the AI to use the list_plcs deviceName for block/tag tools"
    )


def test_main_system_prompt_requires_tiaversion_on_worker_calls() -> None:
    """A user chat called ``list_plcs({})`` with NO tiaVersion and got
    "Unsupported worker method 'list_plcs'" (version-routing sent it to a worker
    that doesn't expose it); the same call WITH ``tiaVersion`` works (loop cycle 2).
    The prompt must tell the AI to pass ``tiaVersion`` on worker tool calls."""
    out = system_prompt("").lower()
    assert "tiaversion" in out, (
        "system prompt must tell the AI to pass tiaVersion on worker tool calls"
    )


def test_main_system_prompt_forbids_open_project_when_one_open() -> None:
    """A user chat repeatedly called ``open_project`` while a project was already
    open, failing every time with "Another project is already open" (TIA Portal
    allows only one project). The prompt must say not to open a project when one
    is already open."""
    out = system_prompt("").lower()
    assert (
        "another project" in out or "already open" in out or "only one project" in out
    ), "system prompt must warn against open_project when a project is already open"
