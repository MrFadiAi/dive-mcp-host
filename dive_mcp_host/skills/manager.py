"""Skill manager for reading and managing skills."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import frontmatter
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from dive_mcp_host.env import DIVE_SKILL_DIR
from dive_mcp_host.skills.models import Skill, SkillMeta

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SKILL_CONTENT_LENGTH = 100_000


class _DiveSkillInput(BaseModel):
    """Input schema for the dive_skill tool."""

    skill_name: str = Field(description="Name of the skill to load.")


class _DiveSearchSkillInput(BaseModel):
    """Input schema for the dive_search_skills tool."""

    query: str = Field(
        description="Keyword(s) to search for across skill names and descriptions."
    )


class SkillManager:
    """Manager for reading and listing skills from the skill directory."""

    def __init__(self, skill_dir: Path = DIVE_SKILL_DIR) -> None:
        """Initialize the skill manager.

        Args:
            skill_dir: Path to the directory containing skill folders.
        """
        self._skill_dir = skill_dir
        self._skills_cache: dict[str, Skill] = {}
        self.refresh()

    def refresh(self) -> None:
        """Reload skills."""
        if not self._skill_dir.exists():
            logger.warning("skill dir not found: %s", self._skill_dir)
            self._skills_cache = {}
            # Early return: without it, control falls through to iterdir() on
            # the missing dir, raises, and logs a spurious "error when loading
            # skill" exception on top of the warning above.
            return

        result: dict[str, Skill] = {}
        try:
            for entry in sorted(self._skill_dir.iterdir()):
                if not entry.is_dir():
                    continue

                skill = self._load_skill(entry.name)

                if skill is None:
                    logger.warning(
                        "no SKILL.md found in %s", self._skill_dir / entry.name
                    )
                    continue

                if skill.meta.name in result:
                    logger.warning(
                        "Found duplicate skill names, will shadow previous skill: %s",
                        skill.meta.name,
                    )

                result[skill.meta.name] = skill

        except Exception:
            logger.exception("error when loading skill")

        logger.debug("refresh found %s skills under: %s", len(result), self._skill_dir)
        self._skills_cache = result

    @property
    def skill_dir(self) -> Path:
        """Return the skill directory path."""
        return self._skill_dir

    def list_skills(self) -> list[Skill]:
        """List all installed skills."""
        return list(self._skills_cache.values())

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills_cache.get(name)

    def _load_skill(self, entry: str) -> Skill | None:
        """Load a skill from its directory."""
        skill_file = self._skill_dir / entry / "SKILL.md"
        logger.debug("load skill: %s", skill_file)

        if not skill_file.exists():
            logger.warning("skill file not found: %s", skill_file)
            return None

        try:
            with skill_file.open("r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            return Skill(
                meta=SkillMeta.model_validate(post.metadata),
                content=post.content,
                base_dir=skill_file,
            )
        except Exception:
            logger.exception("Failed to load skill: %s", skill_file)
            return None

    def get_tools(self, skill_names: list[str] | None = None) -> list[BaseTool]:
        """Transform skills into tools.

        Args:
            skill_names: Optional list of skill names to include.
                         If None, all skills are included.
        """
        if skill_names is not None:
            skills = [
                s for name in skill_names if (s := self.get_skill(name)) is not None
            ]
        else:
            skills = self.list_skills()

        base_desc = (
            "Load a skill to get detailed instructions for a specific task.\n"
            "Skills provide specialized knowledge and step-by-step guidance.\n"
            "Use this when a task matches an available skill's description."
        )
        if not skills:
            description = base_desc + "\n\nNo skills are currently available."
        else:
            lines = ["<available_skills>"]
            for skill in skills:
                lines.append("  <skill>")
                lines.append(f"    <name>{skill.meta.name}</name>")
                if skill.meta.description:
                    lines.append(
                        f"    <description>{skill.meta.description}</description>"
                    )
                lines.append("  </skill>")
            lines.append("</available_skills>")
            description = (
                base_desc
                + "\nOnly the skills listed here are available:\n"
                + "\n".join(lines)
            )

        skills_dict = {s.meta.name: s for s in skills}

        def read_skill_content(skill_name: str) -> str:
            """Read a skill's content."""
            skill = skills_dict.get(skill_name)

            if skill is None:
                if skills_dict:
                    available = ", ".join(skills_dict)
                    return (
                        f"Error: Skill '{skill_name}' not found. "
                        f"Available skills: {available}"
                    )
                return (
                    f"Error: Skill '{skill_name}' not found. No skills are installed."
                )

            content = skill.content
            header = f"""
## Skill: {skill_name}

**Base directory**: {skill.base_dir}

"""
            # Cap the ENTIRE response (header + body) at the limit so a huge
            # skill never floods the agent's context. The previous check
            # capped only the body, letting the header push the total ~100
            # chars past MAX_SKILL_CONTENT_LENGTH.
            if len(header) + len(content) > MAX_SKILL_CONTENT_LENGTH:
                suffix = "\n... (truncated)"
                budget = max(0, MAX_SKILL_CONTENT_LENGTH - len(header) - len(suffix))
                content = content[:budget] + suffix
            return header + content

        def list_skills_content() -> str:
            """List the currently-installed skills.

            Reflects skills installed or removed during the conversation (the
            ``dive_skill`` tool's description is frozen at chat start, so call
            this to discover skills added since).
            """
            current = list(self._skills_cache.values())
            if not current:
                return "No skills are currently installed."
            lines = [f"Installed skills ({len(current)}):"]
            for s in current:
                raw = (s.meta.description or "").strip().splitlines()
                desc = raw[0] if raw else ""
                lines.append(f"- {s.meta.name}: {desc}".rstrip())
            return "\n".join(lines)

        def search_skills_content(query: str) -> str:
            """Search installed skills by keyword across name + description.

            Useful when many skills are installed and ``dive_list_skills`` is
            too noisy — find the skill whose name/description matches a task
            before loading it with ``dive_skill``. Reflects mid-conversation
            installs (reads the live cache).
            """
            needle = (query or "").strip().lower()
            if not needle:
                return "Error: search query must not be empty."
            current = list(self._skills_cache.values())
            matches = [
                s
                for s in current
                if needle in (s.meta.name + " " + (s.meta.description or "")).lower()
            ]
            if not matches:
                return f"No skills match '{query}'."
            lines = [f"Skills matching '{query}' ({len(matches)}):"]
            for s in matches:
                raw = (s.meta.description or "").strip().splitlines()
                desc = raw[0] if raw else ""
                lines.append(f"- {s.meta.name}: {desc}".rstrip())
            return "\n".join(lines)

        def skill_info_content(skill_name: str) -> str:
            """Return a skill's metadata (name, description, license, etc.)
            WITHOUT loading the full body — lightweight discovery before
            ``dive_skill``. Reflects mid-conversation installs (live cache).
            """
            skill = self._skills_cache.get(skill_name)
            if skill is None:
                available = (
                    ", ".join(self._skills_cache) if self._skills_cache else "(none)"
                )
                return (
                    f"Error: Skill '{skill_name}' not found. "
                    f"Installed skills: {available}"
                )
            meta = skill.meta
            lines = [f"## Skill info: {meta.name}"]
            lines.append(f"**Description:** {meta.description}")
            if meta.license:
                lines.append(f"**License:** {meta.license}")
            if meta.compatibility:
                lines.append(f"**Compatibility:** {meta.compatibility}")
            if meta.allowed_tools:
                lines.append(f"**Allowed tools:** {meta.allowed_tools}")
            if meta.metadata:
                lines.append("**Metadata:**")
                for key, value in meta.metadata.items():
                    lines.append(f"- {key}: {value}")
            lines.append(f"**Base directory:** {skill.base_dir}")
            lines.append("(Use dive_skill to load the full instructions.)")
            return "\n".join(lines)

        return [
            StructuredTool.from_function(
                func=read_skill_content,
                name="dive_skill",
                description=description,
                args_schema=_DiveSkillInput,
            ),
            StructuredTool.from_function(
                func=list_skills_content,
                name="dive_list_skills",
                description=(
                    "List all currently-installed skills with their names and "
                    "descriptions. Use before dive_skill to discover which skills "
                    "are available right now (reflects skills installed or removed "
                    "during the conversation)."
                ),
            ),
            StructuredTool.from_function(
                func=search_skills_content,
                name="dive_search_skills",
                description=(
                    "Search installed skills by keyword across name + description. "
                    "Returns the matching skills with their first description line. "
                    "Use when many skills are installed and you need to find the "
                    "right one for a task (reflects mid-conversation installs)."
                ),
                args_schema=_DiveSearchSkillInput,
            ),
            StructuredTool.from_function(
                func=skill_info_content,
                name="dive_skill_info",
                description=(
                    "Get a skill's metadata (name, description, license, allowed "
                    "tools, base dir) WITHOUT loading the full body. Use for quick "
                    "discovery before dive_skill. Reflects mid-conversation installs."
                ),
                args_schema=_DiveSkillInput,
            ),
        ]
