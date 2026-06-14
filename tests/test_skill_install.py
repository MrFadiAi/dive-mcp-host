"""Tests for dive_install_skill_from_path — an installed skill must be usable
immediately (the SkillManager cache refreshed), not only after an app restart."""

from __future__ import annotations

from pathlib import Path

import pytest

from dive_mcp_host.host.agents.agent_factory import ConfigurableKey
from dive_mcp_host.skills.manager import SkillManager
from dive_mcp_host.skills.tools import dive_install_skill_from_path, dive_uninstall_skill

VALID_SKILL_MD = """\
---
name: test-skill
description: A test skill for unit testing install + refresh.
---
# Test Skill

Body content here.
"""


@pytest.fixture
def skill_setup(tmp_path: Path):
    """A SkillManager over an empty temp skill dir + a source skill to install."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    mgr = SkillManager(skill_dir=skill_dir)

    source = tmp_path / "src" / "test-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(VALID_SKILL_MD, encoding="utf-8")
    return mgr, source


@pytest.mark.asyncio
async def test_installed_skill_is_available_immediately(skill_setup):
    """After install returns success, the skill must be in the manager's cache.

    Regression: ``dive_install_skill_from_path`` copied the directory but never
    called ``skill_manager.refresh()``, so the just-installed skill was missing
    from the cache and unusable via ``get_skill`` / the ``dive_skill`` tool until
    the app restarted.
    """
    mgr, source = skill_setup
    assert mgr.get_skill("test-skill") is None  # not present before install

    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    result = await dive_install_skill_from_path.ainvoke(
        {"skill_name": "test-skill", "skill_path": str(source)},
        config,
    )

    assert "Successfully installed" in result
    installed = mgr.get_skill("test-skill")
    assert installed is not None, "installed skill must be available immediately"
    assert installed.meta.name == "test-skill"


@pytest.mark.asyncio
async def test_install_reports_failure_for_missing_skill_md(tmp_path: Path):
    """A source dir without SKILL.md is rejected with a clear error."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    mgr = SkillManager(skill_dir=skill_dir)
    bad_source = tmp_path / "bad"
    bad_source.mkdir()

    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    result = await dive_install_skill_from_path.ainvoke(
        {"skill_name": "no-md", "skill_path": str(bad_source)},
        config,
    )
    assert "Error" in result and "SKILL.md" in result


@pytest.mark.asyncio
async def test_uninstall_removes_skill_from_cache_and_dir(skill_setup):
    """Uninstall deletes the directory AND refreshes the cache so the skill is
    immediately unavailable (mirrors install's refresh)."""
    mgr, source = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}

    await dive_install_skill_from_path.ainvoke(
        {"skill_name": "test-skill", "skill_path": str(source)}, config
    )
    assert mgr.get_skill("test-skill") is not None
    assert (mgr.skill_dir / "test-skill").exists()

    result = await dive_uninstall_skill.ainvoke({"skill_name": "test-skill"}, config)
    assert "Successfully uninstalled" in result
    assert mgr.get_skill("test-skill") is None  # cache refreshed → gone
    assert not (mgr.skill_dir / "test-skill").exists()


@pytest.mark.asyncio
async def test_uninstall_missing_skill_reports_error(skill_setup):
    """Uninstalling a skill that isn't installed is a clear error, not a crash."""
    mgr, _ = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    result = await dive_uninstall_skill.ainvoke({"skill_name": "nonexistent"}, config)
    assert "Error" in result
    assert "not installed" in result


@pytest.mark.asyncio
async def test_uninstall_rejects_path_traversal(skill_setup):
    """A skill name with path-traversal characters is rejected."""
    mgr, _ = skill_setup
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    result = await dive_uninstall_skill.ainvoke({"skill_name": "../evil"}, config)
    assert "Error" in result


def test_skill_management_tools_are_registered():
    """The install/uninstall tools must reach the agent.

    The system prompt instructs the agent to use ``dive_install_skill_from_path``,
    so it must be in the local-tools registry the agent is built from (otherwise
    the prompt references a tool the agent doesn't have). Importing
    ``host.tools`` first resolves the pre-existing export↔plugin circular import.
    """
    import dive_mcp_host.host.tools  # noqa: F401  (resolves the cycle in app order)
    from dive_mcp_host.internal_tools.tools.export import get_local_tools

    names = {t.name for t in get_local_tools()}
    assert "dive_install_skill_from_path" in names, (
        "install tool must be registered — the system prompt tells the agent to use it"
    )
    assert "dive_uninstall_skill" in names


@pytest.mark.asyncio
async def test_list_skills_tool_reflects_runtime_installs(skill_setup):
    """dive_list_skills lists the CURRENT skill set, including skills installed
    during the conversation (dive_skill's description is frozen at chat start)."""
    mgr, source = skill_setup
    tools = {t.name: t for t in mgr.get_tools()}
    assert "dive_list_skills" in tools, "SkillManager must expose a dive_list_skills tool"

    # Before install: no skills.
    before = tools["dive_list_skills"].invoke({})
    assert "No skills" in before or "test-skill" not in before

    # Install (refreshes the cache), then list_skills reflects it.
    config = {"configurable": {ConfigurableKey.SKILL_MANAGER: mgr}}
    await dive_install_skill_from_path.ainvoke(
        {"skill_name": "test-skill", "skill_path": str(source)}, config
    )
    after = tools["dive_list_skills"].invoke({})
    assert "test-skill" in after


def test_list_skills_tool_empty_when_none_installed(tmp_path):
    """With no skills installed, dive_list_skills says so clearly."""
    from pathlib import Path

    from dive_mcp_host.skills.manager import SkillManager

    mgr = SkillManager(skill_dir=Path(tmp_path))
    tools = {t.name: t for t in mgr.get_tools()}
    out = tools["dive_list_skills"].invoke({})
    assert "No skills" in out

