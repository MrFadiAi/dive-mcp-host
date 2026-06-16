"""find_files tool — locate files by name glob, no shell.

Finds files whose NAME matches a glob pattern (e.g. ``**/*.json``, ``*.py``),
complementing ``search_files`` (content grep) and ``file_tree`` (structure).
Lets the agent answer "where are the config files?" without ``bash``.
``_find_files`` is pure filesystem I/O, unit-tested directly; ``find_files``
is a thin async wrapper.
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

# Directories never surfaced (VCS, build output, heavy deps).
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
_MAX_RESULTS = 200  # hard cap so a giant tree can't flood the context


def _find_files(
    root: str, pattern: str, max_results: int = _MAX_RESULTS
) -> list[str]:
    """Return paths under ``root`` whose name matches ``pattern`` (glob).

    Paths are POSIX-normalised (forward slashes) and relative to ``root``,
    sorted. ``**`` recurses; a bare ``*.ext`` matches the top level only
    (standard ``pathlib`` glob semantics). Skips VCS/build/dep dirs and
    dotfiles, caps at ``max_results``. Raises ``OSError`` when ``root`` is
    missing or not a directory.
    """
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise OSError(f"Not a directory: {root}")

    results: list[str] = []
    for p in root_path.glob(pattern):
        if any(part in _SKIP_DIRS for part in p.relative_to(root_path).parts):
            continue
        if p.name.startswith("."):
            continue
        rel = str(p.relative_to(root_path)).replace("\\", "/")
        results.append(rel)
        if len(results) >= max_results:
            break
    return sorted(results)


@tool(
    description=(
        "Find files by NAME glob pattern (no shell) — e.g. '**/*.json' (all JSON, "
        "recursive) or '*.py' (top-level Python). Use this to answer 'where are the "
        "config/skill/docs files?' without bash. Complements search_files (content "
        "grep) and file_tree (structure). Skips VCS/build/node_modules dirs. The "
        "pattern uses pathlib glob syntax: '**' recurses, a bare '*.ext' matches the "
        "top level only."
    )
)
async def find_files(
    path: Annotated[str, Field(description="Directory to search under.")],
    pattern: Annotated[
        str,
        Field(
            description=(
                "Glob pattern, e.g. '**/*.json' (recursive) or '*.py' (top-level)."
            )
        ),
    ],
    max_results: Annotated[
        int,
        Field(default=_MAX_RESULTS, description="Maximum number of files to return."),
    ] = _MAX_RESULTS,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Find files matching a name glob; return relative POSIX paths."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    expanded_path = str(Path(path).expanduser())
    try:
        matches = await asyncio.to_thread(
            _find_files, expanded_path, pattern, max_results
        )
    except OSError as e:
        return f"Error finding files in {path}: {e}"

    if not matches:
        return f"No files matching '{pattern}' in {path}."

    lines = [f"## Files matching '{pattern}' in {path} ({len(matches)})"]
    for m in matches:
        lines.append(f"- {m}")
    return "\n".join(lines)
