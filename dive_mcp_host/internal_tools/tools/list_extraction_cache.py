"""list_extraction_cache tool — show what's currently cached (PLC/HMI, age).

Lets the AI see which extractions are still in the in-memory cache before
querying (``query_plc_blocks`` / ``query_hmi_screens``) or re-extracting. The
formatter is pure + unit-tested; the tool is a thin async wrapper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool

from dive_mcp_host.host.agents.agent_factory import (
    ensure_config,
    get_abort_signal,
)
from dive_mcp_host.internal_tools.tools.common import check_aborted

logger = logging.getLogger(__name__)


def _format_cache_snapshot(snapshot: list[dict[str, Any]]) -> str:
    """Render a cache snapshot (from ``cache_snapshot``) as a readable string."""
    if not snapshot:
        return (
            "No cached extractions. Run extract_plc_blocks or extract_hmi_screens "
            "first."
        )
    lines = [f"Cached extractions ({len(snapshot)}):"]
    for entry in snapshot:
        rtype = entry.get("type", "")
        if "Plc" in rtype:
            label = "PLC"
        elif "Hmi" in rtype:
            label = "HMI"
        else:
            label = rtype or "unknown"
        age = int(entry.get("age_seconds", 0))
        lines.append(f"- {entry['key']}  [{label}, {age // 60}m{age % 60}s ago]")
    lines.append(
        "\nCache expires after 30 minutes. Use query_plc_blocks / "
        "query_hmi_screens with a key for follow-ups."
    )
    return "\n".join(lines)


@tool(
    description=(
        "List the TIA Portal extractions currently held in the in-memory cache "
        "(each entry's cache_key, whether it's PLC or HMI, and its age). Use this "
        "to discover what you can query instantly with query_plc_blocks / "
        "query_hmi_screens before re-running a slow extract. Entries expire after "
        "30 minutes."
    )
)
async def list_extraction_cache(
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """List cached PLC/HMI extractions (key, type, age)."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    from dive_mcp_host.extraction import cache_snapshot

    snapshot = await asyncio.to_thread(cache_snapshot)
    return _format_cache_snapshot(snapshot)
