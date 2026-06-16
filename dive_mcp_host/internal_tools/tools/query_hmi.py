"""HMI extraction follow-up query tool.

Mirrors ``query_plc_blocks`` for HMI: ``extract_hmi_screens`` parses a TIA
Portal project's HMI screens and caches a rich ``HmiExtraction`` by a
``cache_key``. This tool reads that cache so follow-up questions about screens,
navigation, tag bindings, and JS events no longer require re-parsing the
project.
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
        "Query a CACHED HMI extraction for instant follow-ups — no re-parse. "
        "After extract_hmi_screens returns a cache_key, pass it here with one of: "
        "detail='summary' (stats); 'navigation' (screen->target map); "
        "'screens' (list all screens + element counts); "
        "'orphans' (screens with no inbound navigation — unreachable candidates); "
        "'screen' + screen_name (elements, tag bindings hmi->plc, JS events, navigation); "
        "'tag' + tag_name (which screens use the PLC tag + connection/data type); "
        "'mermaid' + optional screen_name (navigation graph as a Mermaid flowchart — "
        "omit the name for the whole graph, or pass a root to focus its sub-tree). "
        "Use this instead of re-running extract_hmi_screens for any question about an "
        "already-extracted HMI project."
    )
)
async def query_hmi_screens(
    cache_key: Annotated[
        str,
        Field(description="The cache_key returned by extract_hmi_screens."),
    ],
    detail: Annotated[
        str,
        Field(
            description=(
                "What to retrieve: 'summary', 'navigation', 'screens', "
                "'screen', or 'tag'."
            )
        ),
    ],
    name: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Screen name (for 'screen') or PLC tag name (for 'tag'). "
                "Case-insensitive. Not needed for 'summary'/'navigation'/'screens'."
            ),
        ),
    ] = None,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Answer a follow-up query against a cached HMI extraction."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    from dive_mcp_host.extraction import get_cached
    from dive_mcp_host.extraction.hmi_query import format_hmi_query
    from dive_mcp_host.extraction.models import HmiExtraction

    result = get_cached(cache_key)
    if result is None:
        return (
            f"Error: No cached extraction for key '{cache_key}'. The cache expires "
            f"after 30 minutes — re-run extract_hmi_screens to refresh it."
        )
    if not isinstance(result, HmiExtraction):
        return (
            f"Error: cache_key '{cache_key}' holds a PLC extraction, not HMI. "
            f"Use query_plc_blocks for block queries."
        )

    return format_hmi_query(result, detail, name)
