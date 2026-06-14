"""Directory listing tool.

Provides a structured directory listing (name / type / size) without spawning a
shell — so the agent can inspect a directory without ``bash`` and without
tripping the write-command detector. ``_list_dir`` is pure filesystem I/O,
unit-tested directly; ``list_dir`` is a thin async wrapper.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from dive_mcp_host.host.agents.agent_factory import (
    ensure_config,
    get_abort_signal,
    get_stream_writer,
)
from dive_mcp_host.internal_tools.events import InstallerToolLog
from dive_mcp_host.internal_tools.tools.common import check_aborted

logger = logging.getLogger(__name__)


def _list_dir(path: str) -> list[dict[str, Any]]:
    """Return the entries of a directory, sorted (dirs first, then files).

    Each entry is ``{"name", "type"}`` where type is ``"dir"`` / ``"file"`` /
    ``"other"``; files also carry ``"size"`` (bytes). Raises ``OSError`` when
    the path is missing or not a directory.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise OSError(f"Not found: {path}")
    if not target.is_dir():
        raise OSError(f"Not a directory: {path}")

    entries: list[dict[str, Any]] = []
    for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.is_dir():
            entries.append({"name": entry.name, "type": "dir", "size": None})
        elif entry.is_file():
            entries.append(
                {"name": entry.name, "type": "file", "size": entry.stat().st_size}
            )
        else:
            entries.append({"name": entry.name, "type": "other", "size": None})
    return entries


@tool(
    description=(
        "List the contents of a directory (names, types, file sizes) without a "
        "shell. Use this instead of `bash ls` when you only need to see what's in "
        "a folder — it's structured and never trips the write-command detector. "
        "Directories are listed first, then files, each alphabetically."
    )
)
async def list_dir(
    path: Annotated[str, Field(description="Path to the directory to list.")],
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """List a directory's contents as a readable, structured summary."""
    config = ensure_config(config)

    stream_writer = get_stream_writer(config)
    abort_signal = get_abort_signal(config)

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    expanded_path = str(Path(path).expanduser())

    stream_writer(
        (
            InstallerToolLog.NAME,
            InstallerToolLog(
                tool="list_dir",
                action=f"Listing: {path}",
                details={"path": expanded_path},
            ),
        )
    )

    try:
        entries = await asyncio.to_thread(_list_dir, expanded_path)
    except (OSError, ValueError) as e:
        return f"Error listing directory {path}: {e}"

    if not entries:
        return f"Directory is empty: {path}"

    lines = [f"## {path} ({len(entries)} entries)"]
    for entry in entries:
        if entry["type"] == "dir":
            lines.append(f"- {entry['name']}/")
        elif entry["type"] == "file":
            lines.append(f"- {entry['name']} ({entry['size']} bytes)")
        else:
            lines.append(f"- {entry['name']} ({entry['type']})")
    return "\n".join(lines)
