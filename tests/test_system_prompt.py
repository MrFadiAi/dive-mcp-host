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
    "tag_xref",
    "read_cross_references",
]
# NOTE (2026-09-07): `find_tags` and `search_code` were REMOVED from this list —
# live chat 2026-09-07 confirmed both WORK on the current worker (search_code
# returned 113 matches scoped / find_tags returned tag rows). The prompts now
# deliberately reference them for plcName-scoped searches.
# NOTE (2026-09-08): `tag_usage` was REMOVED — live-verified working in two
# chats (2026-09-07 PUTBAND + 2026-09-08 HOLD TO RUN, both returned reference
# rows with IsError=False). `tag_xref` stays banned (not yet live-verified).

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


def test_guide_mode_bans_unearned_completeness_claims() -> None:
    """Regression (chat 34e9a170, 2026-07-23): asked "what happens when
    KETTING_START_PULS = 1?", the agent wrote "the complete picture" after
    reading 3 of 47 matches for one of the signals. The skill must forbid
    completeness claims unless EVERY match was read, and make the agent state
    how many matches it actually checked."""
    out = guide_mode_instructions().lower()
    assert "complete picture" in out, (
        "skill must name the banned phrase 'complete picture'"
    )
    assert "every match" in out or "of 47" in out, (
        "skill must tell the agent to state how many matches it read vs exist"
    )


def test_main_system_prompt_bans_unearned_completeness_claims() -> None:
    """The no-overclaim rule must hold in the always-on prompt too — guide mode
    may be OFF during a normal analysis chat, so the rule can't live only in
    the guide-mode skill."""
    out = system_prompt("").lower()
    assert "complete picture" in out, (
        "main prompt must ban 'the complete picture'"
    )
    assert "fabricat" in out, "main prompt must keep the no-fabrication rule"


def test_prompts_require_verbatim_code_no_invented_placeholders() -> None:
    """Regression (chat 2026-09-07, FC PUTBAND): the extractor had dropped STL
    jump labels, and the agent "repaired" the gaps by echoing the code with
    invented '(skip)'/'(end)' placeholders plus inline commentary — the user
    couldn't tell real code from invention. BOTH prompts must force a verbatim
    code echo and an explicit "extraction incomplete" stop instead."""
    for out in (system_prompt("").lower(), guide_mode_instructions().lower()):
        assert "verbatim" in out, "prompts must demand verbatim code echo"
        assert "(skip)" in out, (
            "prompts must name the banned placeholder '(skip)' concretely"
        )


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


def test_main_system_prompt_tells_user_to_compile_blocked_blocks() -> None:
    """The user found that when a block is reported know-how-protected / blocked /
    reads empty (e.g. just '// Network 1'), COMPILING that block (or the project) in
    TIA Portal makes it extractable -- the AI then reads it on retry. The prompt must
    teach the AI this workflow: hit a protected/blocked/empty block -> tell the user
    to compile, then retry (not give up, guess, or strip protection)."""
    out = system_prompt("").lower()
    assert "compile" in out, (
        "prompt must tell the AI to suggest compiling blocked/protected blocks"
    )
    assert "know-how" in out or "blocked" in out or "protected" in out
    assert "retry" in out or "re-run" in out, (
        "prompt must say to retry after compiling"
    )


def test_main_system_prompt_prefers_query_for_block_reads() -> None:
    """A production vision-system debug chat re-read the SAME FC 6 times via the
    raw ``get_block_content`` (full VAR sections + S7_MLC noise on every call),
    bloating input to ~306k tokens and forcing compaction that dropped the exact
    lines under debug — which fed 5 silent 'root cause' revisions across two
    sessions. The prompt must steer block-source reads to the cheap path:
    ``extract_plc_blocks`` once, then ``query_plc_blocks(detail='block')``."""
    out = system_prompt("").lower()
    assert "detail='block'" in out, (
        "prompt must steer block-source reads to query_plc_blocks(detail='block')"
    )
    assert "floods context" in out or "compaction" in out, (
        "prompt must warn that repeated raw get_block_content floods context"
    )


def test_guide_mode_treats_root_cause_as_hypothesis() -> None:
    """The same chat declared a fix '✅ Perfect / bulletproof / I see EXACTLY'
    from static code, then reversed the root cause 5 times as each fix failed —
    performative confidence that erodes trust. Guide mode must frame a root
    cause as a hypothesis until the user's test confirms it, and ban the
    'verified / perfect / bulletproof' victory language before that."""
    out = guide_mode_instructions().lower()
    assert "hypothesis" in out, (
        "guide mode must frame a root cause as a hypothesis pending the user's test"
    )
    assert "bulletproof" in out or "perfect" in out or "verified" in out, (
        "guide mode must call out performative 'verified/perfect/bulletproof' confidence"
    )


def test_guide_mode_requires_reextract_after_reported_failure() -> None:
    """After a fix failed, the AI prescribed a NEW root cause without re-reading
    the current code or explaining why the prior hypothesis was wrong — the user
    had to ask 'did you extract the last code before you decided?'. Guide mode
    must require: re-extract the current code, explain in one line why the
    previous hypothesis was wrong, and reproduce the REPORTED symptom (not a
    generic one) before re-prescribing."""
    out = guide_mode_instructions().lower()
    assert "re-extract" in out or "current code" in out, (
        "guide mode must require re-extracting current code after a reported failure"
    )
    assert "hypothesis was wrong" in out, (
        "guide mode must require explaining why the prior root cause was wrong"
    )


def test_prompts_require_plcname_scoped_searches() -> None:
    """Regression (chat 2026-09-07): the agent's opening volley ran an
    UNSCOPED search_code that swept every PLC in every open project (2000
    blocks, ~100 s) although the target PLC was known, then read a block from
    the wrong PLC first (matches were mislabeled). Both prompts must demand
    scoping searches to the exact plcName and verifying match plcNames."""
    for out in (system_prompt("").lower(), guide_mode_instructions().lower()):
        assert "unscoped" in out, "prompts must warn against unscoped searches"
        assert "plcname" in out, "prompts must name the plcName scoping rule"


def test_prompts_teach_hmi_rdf_disk_search_and_bash_pitfall() -> None:
    """Regression (chat 2026-09-08, HOLD TO RUN analysis): the agent had to
    invent bash+grep workarounds because (a) HMI screen scripts (.rdf) are
    binary-ish and read_file failed, (b) nested-quote `python -c` under cmd
    stalled on stdin. The prompts must carry both lessons."""
    out = system_prompt("").lower()
    assert "im" in out and "rdf" in out, (
        "main prompt must point at the project IM folder for HMI screen scripts"
    )
    assert "bash+grep" in out, "main prompt must say NOT to drop to bash+grep"
    assert "stdin" in out, "main prompt must warn about the python -c stdin stall"


def test_guide_mode_teaches_sibling_copy_and_xref_dead_end() -> None:
    """Regression (chat 2026-09-08): with 42 export-inconsistent blocks the
    agent substituted readable sibling copies (good, but must be DISCLOSED +
    paired with compile-and-diff) and burned ~10 min proving XRef.db parts
    are undecodable. The skill must encode both."""
    out = guide_mode_instructions().lower()
    assert "sibling" in out, "skill must teach sibling-copy substitution + disclosure"
    assert "xref.db" in out, "skill must mark XRef.db parts as a known dead end"
