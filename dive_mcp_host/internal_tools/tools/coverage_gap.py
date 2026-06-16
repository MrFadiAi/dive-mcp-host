"""PLC↔HMI tag coverage gap tool (commissioning analysis).

Reports which PLC tags have no HMI binding (not surfaced to the operator) and
which HMI-referenced tags are absent from the PLC extraction (possible stale
references). Reads two cached extractions; the pure formatter lives in
``extraction/trace.py``.
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
        "Commissioning gap analysis between a cached PLC extraction and a cached "
        "HMI extraction. Pass the plc_cache_key (from extract_plc_blocks) and the "
        "hmi_cache_key (from extract_hmi_screens). Reports: tags bound in both; "
        "PLC tags with NO HMI binding (signals the operator can't see/controls "
        "that aren't surfaced); and HMI-referenced tags absent from the PLC "
        "(possible stale/orphan references). Use during commissioning to find "
        "missing HMI bindings or dead tag references."
    )
)
async def coverage_gap(
    plc_cache_key: Annotated[
        str, Field(description="cache_key returned by extract_plc_blocks.")
    ],
    hmi_cache_key: Annotated[
        str, Field(description="cache_key returned by extract_hmi_screens.")
    ],
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Report PLC↔HMI tag coverage gaps from two cached extractions."""
    config = ensure_config(config)

    if check_aborted(get_abort_signal(config)):
        return "Error: Operation aborted."

    from dive_mcp_host.extraction import get_cached
    from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction
    from dive_mcp_host.extraction.trace import format_coverage_gap

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

    return format_coverage_gap(plc, hmi)
