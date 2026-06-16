"""PLC extraction follow-up query tool.

``extract_plc_blocks`` parses exported TIA Portal XML, caches the rich
``PlcExtraction`` by a ``cache_key``, and tells the AI to "reference this
cache_key for follow-up queries". This tool is what actually reads that cache,
so follow-up questions (block source, interface, calls, tag usage, call tree)
no longer require re-parsing the XML directory.
"""

from __future__ import annotations

import logging
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


@tool(
    description=(
        "Query a CACHED PLC extraction for instant follow-ups — no XML re-parse. "
        "After extract_plc_blocks returns a cache_key, pass it here with one of: "
        "detail='summary' (stats); 'call_tree' (caller->callees map); "
        "'dead_code' (FB/FC blocks with zero callers — dead-code candidates); "
        "'block' + block_name (full reconstructed code + interface IN/OUT/IN_OUT/STAT/TEMP + calls); "
        "'calls' + block_name (what a block calls); "
        "'callers' + block_name (who calls a block); "
        "'tag' + tag_name (every block that reads/writes the tag); "
        "'search' + term (free-text grep across all reconstructed block code); "
        "'path' + block_name (call chain from an entry OB down to the block); "
        "'mermaid' + optional block_name (call graph as a Mermaid flowchart — "
        "omit the name for the whole graph from OBs, or pass a root to focus its sub-tree); "
        "'cycles' (mutually-recursive call-cycle groups + self-recursive blocks — PLC anti-pattern check). "
        "Use this instead of re-running extract_plc_blocks for any question about an "
        "already-extracted project."
    )
)
async def query_plc_blocks(
    cache_key: Annotated[
        str,
        Field(description="The cache_key returned by extract_plc_blocks."),
    ],
    detail: Annotated[
        str,
        Field(
            description=(
                "What to retrieve: 'summary', 'call_tree', 'block', 'calls', "
                "'callers', or 'tag'."
            )
        ),
    ],
    name: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Block name (for 'block'/'calls'/'callers') or tag name (for 'tag'). "
                "Case-insensitive. Not needed for 'summary'/'call_tree'."
            ),
        ),
    ] = None,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Answer a follow-up query against a cached PLC extraction."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    from dive_mcp_host.extraction import get_cached
    from dive_mcp_host.extraction.models import PlcExtraction
    from dive_mcp_host.extraction.query import format_plc_query

    result = get_cached(cache_key)
    if result is None:
        return (
            f"Error: No cached extraction for key '{cache_key}'. The cache expires "
            f"after 30 minutes — re-run extract_plc_blocks to refresh it."
        )
    if not isinstance(result, PlcExtraction):
        return (
            f"Error: cache_key '{cache_key}' holds an HMI extraction, not PLC. "
            f"Use the HMI extraction tool for screen queries."
        )

    return format_plc_query(result, detail, name)
