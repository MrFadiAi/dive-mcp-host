"""Tests for dive_create_skill (scaffold a skill from scratch) and the
SkillManager.refresh() regression (missing skill dir early-return)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.host.agents.agent_factory import ConfigurableKey
from dive_mcp_host.skills.manager import SkillManager
from dive_mcp_host.skills.tools import dive_create_skill


@pytest.fixture
def skill_setup(tmp_path: Path) -> SkillManager:
    """A SkillManager over an empty temp skill directory."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    return SkillManager(skill_dir=skill_dir)


@pytest.mark.asyncio
async def test_create_skill_writes_valid_skill_md(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    result = await dive_create_skill.ainvoke(
        {"skill_name": "my-skill", "description": "Does the thing.", "body": "# Steps\n"},
        config,
    )

    assert "Successfully created" in result
    md = (mgr.skill_dir / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: my-skill" in md
    assert "description: Does the thing." in md
    assert "# Steps" in md


@pytest.mark.asyncio
async def test_create_skill_is_available_immediately(skill_setup: SkillManager) -> None:
    """The created skill must be usable right away (refresh after write),
    not only after an app restart — same invariant as install."""
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    await dive_create_skill.ainvoke(
        {"skill_name": "fresh", "description": "A fresh skill."}, config
    )

    skill = mgr.get_skill("fresh")
    assert skill is not None
    assert skill.meta.name == "fresh"
    assert skill.meta.description == "A fresh skill."


@pytest.mark.asyncio
async def test_create_skill_rejects_bad_name(skill_setup: SkillManager) -> None:
    """A name that violates the SkillMeta pattern is rejected up front, so we
    never write a SKILL.md that would silently fail to load."""
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    result = await dive_create_skill.ainvoke(
        {"skill_name": "Bad_Name", "description": "x"}, config
    )

    assert "Error" in result
    assert not (mgr.skill_dir / "Bad_Name").exists()


@pytest.mark.asyncio
async def test_create_skill_rejects_path_traversal(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    result = await dive_create_skill.ainvoke(
        {"skill_name": "../evil", "description": "x"}, config
    )
    assert "Error" in result


@pytest.mark.asyncio
async def test_create_skill_refuses_overwrite_without_flag(
    skill_setup: SkillManager,
) -> None:
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    await dive_create_skill.ainvoke(
        {"skill_name": "dup", "description": "first"}, config
    )
    result = await dive_create_skill.ainvoke(
        {"skill_name": "dup", "description": "second"}, config
    )

    assert "Error" in result
    assert "already exists" in result
    assert mgr.get_skill("dup").meta.description == "first"  # original preserved


@pytest.mark.asyncio
async def test_create_skill_overwrites_with_flag(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    await dive_create_skill.ainvoke(
        {"skill_name": "dup", "description": "first"}, config
    )
    result = await dive_create_skill.ainvoke(
        {"skill_name": "dup", "description": "second", "overwrite": True}, config
    )

    assert "Successfully created" in result
    assert mgr.get_skill("dup").meta.description == "second"


@pytest.mark.asyncio
async def test_create_skill_requires_description(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    result = await dive_create_skill.ainvoke(
        {"skill_name": "no-desc", "description": "   "}, config
    )
    assert "Error" in result


def test_create_skill_is_registered() -> None:
    """dive_create_skill must reach the agent via the local-tools registry."""
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the export cycle)

    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    names = {t.name for t in get_local_tools()}
    assert "dive_create_skill" in names


# --- dive_search_skills: keyword search over the live skill cache ---


@pytest.mark.asyncio
async def test_search_skills_matches_by_name_or_description(
    skill_setup: SkillManager,
) -> None:
    """Search must match a query against either the name or the description, and
    only return the matching skill(s)."""
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    await dive_create_skill.ainvoke(
        {"skill_name": "code-review", "description": "Reviews pull requests for bugs."},
        config,
    )
    await dive_create_skill.ainvoke(
        {"skill_name": "deploy", "description": "Deploys the app to production."},
        config,
    )

    tools = {t.name: t for t in mgr.get_tools()}
    out = tools["dive_search_skills"].invoke({"query": "review"})
    assert "code-review" in out
    assert "deploy" not in out


@pytest.mark.asyncio
async def test_search_skills_reflects_mid_conversation_install(
    skill_setup: SkillManager,
) -> None:
    """Like dive_list_skills, search reads the live cache — a skill installed
    mid-conversation is findable right away."""
    mgr = skill_setup
    tools = {t.name: t for t in mgr.get_tools()}
    assert "No skills match" in tools["dive_search_skills"].invoke({"query": "deploy"})

    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    await dive_create_skill.ainvoke(
        {"skill_name": "deploy", "description": "Deploys the app to production."},
        config,
    )
    out = tools["dive_search_skills"].invoke({"query": "deploy"})
    assert "deploy" in out


def test_search_skills_no_match_message(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    tools = {t.name: t for t in mgr.get_tools()}
    out = tools["dive_search_skills"].invoke({"query": "definitely-no-such-skill"})
    assert "No skills match" in out


def test_search_skills_empty_query_is_an_error(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    tools = {t.name: t for t in mgr.get_tools()}
    out = tools["dive_search_skills"].invoke({"query": "   "})
    assert "Error" in out


def test_dive_search_skills_in_get_tools(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    mgr = SkillManager(skill_dir=skill_dir)
    assert "dive_search_skills" in {t.name for t in mgr.get_tools()}


# --- dive_skill_info: metadata without the full body ---


@pytest.mark.asyncio
async def test_skill_info_returns_metadata_without_body(
    skill_setup: SkillManager,
) -> None:
    """dive_skill_info returns a skill's metadata (name, description, base dir)
    WITHOUT the full body — lightweight discovery before dive_skill."""
    mgr = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    body = "# Steps\n\n" + ("SECRET_BODY_CONTENT" * 200)
    await dive_create_skill.ainvoke(
        {"skill_name": "code-review", "description": "Reviews pull requests.", "body": body},
        config,
    )

    tools = {t.name: t for t in mgr.get_tools()}
    assert "dive_skill_info" in tools
    out = tools["dive_skill_info"].invoke({"skill_name": "code-review"})
    assert "code-review" in out
    assert "Reviews pull requests." in out
    # metadata only — the large body must NOT be included
    assert "SECRET_BODY_CONTENT" not in out


def test_skill_info_unknown_skill_is_an_error(skill_setup: SkillManager) -> None:
    mgr = skill_setup
    tools = {t.name: t for t in mgr.get_tools()}
    out = tools["dive_skill_info"].invoke({"skill_name": "nope"})
    assert "not found" in out.lower() or "Error" in out


def test_dive_skill_info_in_get_tools(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    mgr = SkillManager(skill_dir=skill_dir)
    assert "dive_skill_info" in {t.name for t in mgr.get_tools()}


def test_dive_skill_output_respects_cap(tmp_path: Path) -> None:
    """The dive_skill tool's full response (header + body + suffix) must stay
    under MAX_SKILL_CONTENT_LENGTH — not just the body. The old code capped
    only the body, letting the header push the total ~100 chars over the cap,
    so the truncation limit was illusory."""
    from dive_mcp_host.skills.manager import MAX_SKILL_CONTENT_LENGTH

    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    mgr = SkillManager(skill_dir=skill_dir)
    big_md = "---\nname: big-skill\ndescription: big\n---\n" + "z" * 500_000
    (skill_dir / "big-skill").mkdir()
    (skill_dir / "big-skill" / "SKILL.md").write_text(big_md, encoding="utf-8")
    mgr.refresh()

    tools = {t.name: t for t in mgr.get_tools()}
    result = tools["dive_skill"].invoke({"skill_name": "big-skill"})

    assert "truncated" in result
    assert len(result) <= MAX_SKILL_CONTENT_LENGTH, (
        f"full dive_skill output must respect the cap, got {len(result)}"
    )


def test_refresh_missing_skill_dir_does_not_iterate(tmp_path: Path) -> None:
    """Regression: ``refresh()`` set ``_skills_cache={}`` but had no early
    ``return`` when the skill dir was missing, so it fell through to
    ``iterdir()`` on a non-existent path and logged a spurious
    "error when loading skill" exception.

    After the fix, a missing dir short-circuits before any iteration.
    """

    class FakePath:
        """Stand-in for a non-existent skill dir that records iteration."""

        def __init__(self) -> None:
            self.iter_called = False

        def exists(self) -> bool:
            return False

        def iterdir(self):
            self.iter_called = True
            return iter(())

        def __truediv__(self, _other: object) -> "FakePath":
            return self

        def __repr__(self) -> str:
            return "FakePath(missing)"

    mgr = SkillManager(skill_dir=tmp_path)  # real empty dir satisfies __init__
    fake = FakePath()
    mgr._skill_dir = fake  # type: ignore[assignment]
    mgr.refresh()

    assert mgr._skills_cache == {}
    assert fake.iter_called is False, (
        "refresh must not iterate a missing skill dir (missing early return)"
    )
