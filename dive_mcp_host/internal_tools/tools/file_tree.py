"""file_tree tool — depth-limited recursive directory tree, no shell.

A project-structure overview (indented tree) without spawning ``bash``.
Complements ``list_dir`` (flat one-level). ``_file_tree`` is pure filesystem
I/O, unit-tested directly; ``file_tree`` is a thin async wrapper.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from dive_mcp_host.host.agents.agent_factory import (
    ensure_config,
    get_abort_signal,
)
from dive_mcp_host.internal_tools.tools.common import check_aborted

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    "target",
}
_MAX_ENTRIES = 500  # hard cap so a giant tree can't flood the context


def _file_tree(root: str, max_depth: int = 3, max_entries: int = _MAX_ENTRIES) -> list[str]:
    """Return indented tree lines under ``root`` (depth-limited).

    Directories are listed first (alphabetical) with a trailing ``/``; each
    nesting level adds two spaces of indent. Skips VCS/build/dep dirs and
    dotfiles. Returns ``[]`` for an empty/all-skipped directory. Raises
    ``OSError`` when the root is missing or not a directory.
    """
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise OSError(f"Not a directory: {root}")

    lines: list[str] = []
    count = 0

    def walk(path: Path, depth: int, prefix: str) -> None:
        nonlocal count
        if count >= max_entries:
            return
        try:
            children = sorted(
                path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except OSError:
            return
        for child in children:
            if count >= max_entries:
                break
            name = child.name
            if name.startswith(".") or name in _SKIP_DIRS:
                continue
            is_dir = child.is_dir()
            lines.append(f"{prefix}{name}{'/' if is_dir else ''}")
            count += 1
            if is_dir and depth + 1 < max_depth:
                walk(child, depth + 1, prefix + "  ")

    walk(root_path, 0, "")
    return lines


@tool(
    description=(
        "Render a depth-limited directory tree (indented, dirs first) without a "
        "shell — a quick project-structure overview. Use this instead of `bash "
        "tree`/`find`: it skips VCS/build/node_modules dirs and dotfiles. Set "
        "max_depth to control how deep to recurse (default 3)."
    )
)
async def file_tree(
    path: Annotated[str, Field(description="Directory to render as a tree.")],
    max_depth: Annotated[
        int,
        Field(default=3, description="Maximum nesting depth to show (default 3)."),
    ] = 3,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Render a directory as an indented tree."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    expanded_path = str(Path(path).expanduser())
    try:
        lines = await asyncio.to_thread(_file_tree, expanded_path, max_depth)
    except (OSError, ValueError) as e:
        return f"Error building tree for {path}: {e}"

    if not lines:
        return f"Directory is empty (or all entries skipped): {path}"
    return f"## {path}\n" + "\n".join(lines)
