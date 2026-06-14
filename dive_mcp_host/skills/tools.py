"""Skill tools for LangChain agents.

Provides tools for installing skills.
"""

# ruff: noqa: PLR0911

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Annotated

import frontmatter
from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from langgraph.pregel.main import ensure_config
from pydantic import Field

from dive_mcp_host.host.agents.agent_factory import get_abort_signal, get_skill_manager
from dive_mcp_host.internal_tools.tools.common import (
    check_aborted,
)

logger = logging.getLogger(__name__)

# Mirrors SkillMeta.name so dive_create_skill can reject names that would
# silently fail to load before writing anything to disk.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@tool(
    description="""Install a skill from a local directory containing a SKILL.md file.

Copies the entire skill directory (SKILL.md and all accompanying files such as
scripts, templates, etc.) into the skill directory.

To install a skill from a remote source (e.g., GitHub), first clone or download
the repository to a temporary directory using git/bash, then use this tool to
install from the local path.

Will refuse to overwrite an existing skill unless overwrite=True.
Validates skill_name against path traversal characters (/, \\, ..).

Example:
  dive_install_skill_from_path(
    skill_name="code-review",
    skill_path="/tmp/skills-repo/skills/code-review",
  )
"""
)
async def dive_install_skill_from_path(
    skill_name: Annotated[
        str,
        Field(description="Directory name for the skill (e.g., 'code-review')."),
    ],
    skill_path: Annotated[
        str,
        Field(
            description=(
                "Absolute path to the skill directory containing SKILL.md "
                "(e.g., '/tmp/skills-repo/skills/code-review')."
            )
        ),
    ],
    overwrite: Annotated[
        bool,
        Field(
            default=False,
            description="Whether to overwrite an existing skill.",
        ),
    ] = False,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Install a skill by copying its entire directory from a local path."""
    config = ensure_config(config)
    abort_signal = get_abort_signal(config)
    skill_manager = get_skill_manager(config)

    if not skill_manager:
        return "Error: SkillManager not loaded"

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    # Validate skill_name against path traversal
    if "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return "Error: Invalid skill name. Must not contain '/', '\\', or '..'."

    if not skill_name.strip():
        return "Error: Skill name must not be empty."

    source = Path(skill_path)

    # Accept both a directory containing SKILL.md and a direct SKILL.md file path
    if source.is_file() and source.name == "SKILL.md":
        source = source.parent

    if not source.is_dir():
        return f"Error: Source path '{skill_path}' is not a directory."

    skill_md = source / "SKILL.md"
    if not skill_md.exists():
        return f"Error: No SKILL.md found in '{skill_path}'."

    target_dir = skill_manager.skill_dir / skill_name
    if target_dir.exists() and not overwrite:
        return (
            f"Error: Skill '{skill_name}' already exists. "
            "Set overwrite=True to replace it."
        )

    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source, target_dir)
        # Refresh the in-memory cache so the skill is usable immediately via
        # get_skill / the dive_skill tool. Without this the new skill is only
        # picked up on the next app start (the cache loaded at SkillManager init).
        skill_manager.refresh()
        return f"Successfully installed skill '{skill_name}' from '{source}'."
    except OSError as e:
        return f"Error installing skill '{skill_name}': {e}"


@tool(
    description="""Remove an installed skill by name.

Deletes the skill's directory and removes it from the available skills. The
skill can be reinstalled later with dive_install_skill_from_path. Use this to
remove skills that are no longer needed or that were installed incorrectly.

Returns an error if the skill is not installed.
"""
)
async def dive_uninstall_skill(
    skill_name: Annotated[
        str,
        Field(
            description="Name of the installed skill to remove (e.g., 'code-review')."
        ),
    ],
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Uninstall a skill by deleting its directory."""
    config = ensure_config(config)
    abort_signal = get_abort_signal(config)
    skill_manager = get_skill_manager(config)

    if not skill_manager:
        return "Error: SkillManager not loaded"

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    # Validate skill_name against path traversal
    if "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return "Error: Invalid skill name. Must not contain '/', '\\', or '..'."

    if not skill_name.strip():
        return "Error: Skill name must not be empty."

    target_dir = skill_manager.skill_dir / skill_name
    if not target_dir.exists():
        return f"Error: Skill '{skill_name}' is not installed."

    try:
        shutil.rmtree(target_dir)
        # Refresh the cache so the skill is immediately unavailable via get_skill
        # / the dive_skill tool (mirrors install's refresh).
        skill_manager.refresh()
        return f"Successfully uninstalled skill '{skill_name}'."
    except OSError as e:
        return f"Error uninstalling skill '{skill_name}': {e}"


@tool(
    description="""Scaffold a brand-new skill in the skill directory.

Creates <skill_dir>/<skill_name>/SKILL.md with valid YAML frontmatter
(name + description) and the supplied markdown body, then refreshes the
skill cache so the skill is immediately usable via dive_skill / get_skill.

This is for bootstrapping a skill from scratch. To copy an existing skill
directory (with scripts/templates alongside SKILL.md), use
dive_install_skill_from_path instead.

Refuses to overwrite an existing skill unless overwrite=True. Validates
skill_name against path traversal (/, \\, ..) and the skill-name pattern
(lowercase letters, digits, single dashes).

Example:
  dive_create_skill(
    skill_name="deploy-checklist",
    description="Step-by-step production deploy checks.",
    body="# Deploy\\n\\n1. Run migrations\\n2. Smoke test\\n",
  )
"""
)
async def dive_create_skill(
    skill_name: Annotated[
        str,
        Field(
            description="Directory + skill name (lowercase, digits, single "
            "dashes; e.g. 'code-review')."
        ),
    ],
    description: Annotated[
        str,
        Field(description="One-line description used for skill discovery."),
    ],
    body: Annotated[
        str,
        Field(
            default="",
            description="Markdown body of SKILL.md, placed after the frontmatter.",
        ),
    ] = "",
    overwrite: Annotated[
        bool,
        Field(
            default=False, description="Overwrite an existing skill with the same name."
        ),
    ] = False,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Scaffold a new skill by writing SKILL.md with valid frontmatter."""
    config = ensure_config(config)
    abort_signal = get_abort_signal(config)
    skill_manager = get_skill_manager(config)

    if not skill_manager:
        return "Error: SkillManager not loaded"

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    # Validate skill_name against path traversal
    if "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return "Error: Invalid skill name. Must not contain '/', '\\', or '..'."

    if not skill_name.strip():
        return "Error: Skill name must not be empty."

    # Enforce the SkillMeta name pattern up front, so we never write a
    # SKILL.md whose frontmatter would silently fail to load on refresh.
    if not _SKILL_NAME_RE.match(skill_name):
        return (
            "Error: Invalid skill name. Use only lowercase letters, digits, "
            "and single dashes (e.g., 'code-review')."
        )

    if not description.strip():
        return "Error: Skill description must not be empty."

    target_dir = skill_manager.skill_dir / skill_name
    if target_dir.exists() and not overwrite:
        return (
            f"Error: Skill '{skill_name}' already exists. "
            "Set overwrite=True to replace it."
        )

    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # frontmatter.dumps handles YAML escaping of description/body so the
        # written SKILL.md round-trips cleanly through frontmatter.load.
        post = frontmatter.Post(body, name=skill_name, description=description.strip())
        (target_dir / "SKILL.md").write_text(
            frontmatter.dumps(post), encoding="utf-8"
        )

        # Refresh the in-memory cache so the skill is usable immediately via
        # get_skill / the dive_skill tool (same invariant as install).
        skill_manager.refresh()
        return f"Successfully created skill '{skill_name}'."
    except OSError as e:
        return f"Error creating skill '{skill_name}': {e}"
