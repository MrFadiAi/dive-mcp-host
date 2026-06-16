"""Combined PLC↔HMI tag trace tool.

Connects the HMI UI to PLC logic: given a tag that exists in both an extracted
PLC project and HMI project, report where it's used on each side (PLC blocks
that read/write it + HMI screens that display/control it). Lets the AI answer
"which button drives this logic" without re-extracting.
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
        "Trace a single tag across BOTH a cached PLC extraction and a cached HMI "
        "extraction — connect the HMI UI to the PLC logic. Pass the plc_cache_key "
        "(from extract_plc_blocks), the hmi_cache_key (from extract_hmi_screens), "
        "and the tag name. Returns where the tag is read/written in PLC blocks "
        "(address, data type) AND displayed/controlled on HMI screens (connection). "
        "Use this to answer 'which HMI element drives this PLC tag' or 'where is "
        "this button's tag used in the code'."
    )
)
async def trace_tag(
    plc_cache_key: Annotated[
        str, Field(description="cache_key returned by extract_plc_blocks.")
    ],
    hmi_cache_key: Annotated[
        str, Field(description="cache_key returned by extract_hmi_screens.")
    ],
    tag: Annotated[str, Field(description="The PLC tag name to trace.")],
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Trace a tag across cached PLC + HMI extractions."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    from dive_mcp_host.extraction import get_cached
    from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction
    from dive_mcp_host.extraction.trace import format_tag_trace

    plc = get_cached(plc_cache_key)
    hmi = get_cached(hmi_cache_key)

    if plc is None:
        return (
            f"Error: No cached PLC extraction for key '{plc_cache_key}' "
            f"(expired after 30 min? re-run extract_plc_blocks)."
        )
    if hmi is None:
        return (
            f"Error: No cached HMI extraction for key '{hmi_cache_key}' "
            f"(expired after 30 min? re-run extract_hmi_screens)."
        )
    if not isinstance(plc, PlcExtraction):
        return f"Error: '{plc_cache_key}' is not a PLC extraction."
    if not isinstance(hmi, HmiExtraction):
        return f"Error: '{hmi_cache_key}' is not an HMI extraction."

    return format_tag_trace(plc, hmi, tag)
