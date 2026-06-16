"""search_files tool — structured grep across a directory, no shell.

Finds which files contain a regex pattern and returns ``file:line:match`` lines.
Lets the agent search code/text without ``bash`` (and without tripping the
write-command detector). ``_search_files`` is pure filesystem I/O, unit-tested
directly; ``search_files`` is a thin async wrapper.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from dive_mcp_host.host.agents.agent_factory import (
    ensure_config,
    get_abort_signal,
)
from dive_mcp_host.internal_tools.tools.common import check_aborted

logger = logging.getLogger(__name__)

# Directories never descended into (VCS, build output, heavy deps).
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
_MAX_FILE_BYTES = 2_000_000  # skip files larger than 2 MB (likely binaries/logs)
_MAX_LINE_CHARS = 200  # truncate long matched lines in the output


def _search_files(
    root: str,
    pattern: str,
    max_matches: int = 50,
    before: int = 0,
    after: int = 0,
) -> list[dict[str, Any]]:
    """Search file contents under ``root`` for ``pattern`` (regex).

    Returns a list of ``{"file" (relative), "line" (1-based), "text", "before",
    "after"}``. ``before``/``after`` (grep ``-B``/``-A``) attach the surrounding
    ``(lineno, text)`` pairs to each match so a hit can be read in context; both
    default to 0 (no context — single-line output). Skips VCS/build/dep
    directories, dotfiles, and oversized files. Binary-safe via
    ``errors="ignore"``. Raises ``re.error`` for an invalid pattern.
    """
    regex = re.compile(pattern)
    root_path = Path(root).expanduser()
    results: list[dict[str, Any]] = []

    for dirpath, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(fpath) > _MAX_FILE_BYTES:
                    continue
                # Read the whole (capped) file so we can reach lines before a
                # match for context. The 2 MB size cap bounds memory.
                with open(fpath, encoding="utf-8", errors="ignore") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for idx, line in enumerate(lines):
                if regex.search(line):
                    lineno = idx + 1
                    ctx_before = [
                        (
                            idx - before + i + 1,
                            lines[idx - before + i].rstrip("\n")[:_MAX_LINE_CHARS],
                        )
                        for i in range(before)
                        if idx - before + i >= 0
                    ]
                    ctx_after = [
                        (
                            idx + i + 1,
                            lines[idx + i].rstrip("\n")[:_MAX_LINE_CHARS],
                        )
                        for i in range(1, after + 1)
                        if idx + i < len(lines)
                    ]
                    results.append(
                        {
                            "file": os.path.relpath(fpath, root_path),
                            "line": lineno,
                            "text": line.rstrip("\n")[:_MAX_LINE_CHARS],
                            "before": ctx_before,
                            "after": ctx_after,
                        }
                    )
                    if len(results) >= max_matches:
                        return results
    return results


@tool(
    description=(
        "Search file contents under a directory for a regex pattern (structured "
        "grep — returns file:line:match lines, no shell). Use this instead of "
        "`bash grep` when you need to find code or text: it skips VCS/build/"
        "node_modules dirs and caps the match count. The pattern is a regular "
        "expression (case-sensitive). Pass `before`/`after` to include context "
        "lines around each match (grep -B/-A) — useful when a bare match line "
        "is not enough to understand the hit."
    )
)
async def search_files(
    path: Annotated[str, Field(description="Directory to search recursively.")],
    pattern: Annotated[str, Field(description="Regular expression to match (case-sensitive).")],
    max_matches: Annotated[
        int,
        Field(default=50, description="Maximum number of matches to return."),
    ] = 50,
    before: Annotated[
        int,
        Field(
            default=0,
            description="Lines of context to show BEFORE each match (grep -B).",
        ),
    ] = 0,
    after: Annotated[
        int,
        Field(
            default=0,
            description="Lines of context to show AFTER each match (grep -A).",
        ),
    ] = 0,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Search a directory for a regex pattern; return file:line:match lines."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    expanded_path = str(Path(path).expanduser())
    try:
        matches = await asyncio.to_thread(
            _search_files, expanded_path, pattern, max_matches, before, after
        )
    except (OSError, re.error, ValueError) as e:
        return f"Error searching {path}: {e}"

    if not matches:
        return f"No matches for /{pattern}/ in {path}."

    suffix = "es" if len(matches) != 1 else ""
    lines = [f"## /{pattern}/ in {path} ({len(matches)} match{suffix})"]
    for m in matches:
        lines.append(f"- {m['file']}:{m['line']}: {m['text']}")
        for ln, txt in m.get("before", []):
            lines.append(f"    {ln} | {txt}")
        for ln, txt in m.get("after", []):
            lines.append(f"    {ln} | {txt}")
    return "\n".join(lines)
