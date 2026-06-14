"""HMI screen extraction tool — parses TIA Portal HMI project data."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

logger = logging.getLogger(__name__)


def _resolve_hmi_tags_path(
    project_path: str, hmi_tags_path: str | None
) -> str | None:
    """Resolve the HMITags.xlsx path.

    An explicit ``hmi_tags_path`` wins (honoured as-is even if absent —
    ``load_hmi_tags`` handles a missing file). When omitted, probe the common
    TIA locations relative to the project (``DATA_HMI`` first, then the project
    root) and return the first that exists. Returns ``None`` if none found.

    Implements the auto-discovery the ``hmi_tags_path`` field documents but the
    tool previously did not perform (it passed ``None`` straight through, so tag
    resolution was silently skipped).
    """
    if hmi_tags_path:
        return hmi_tags_path
    base = Path(project_path)
    for candidate in (
        base / "DATA_HMI" / "HMITags.xlsx",
        base / "HMITags.xlsx",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


@tool(
    description=(
        "Extract and analyze HMI screens from a TIA Portal project directory. "
        "Parses binary RDF screen files and HMITags.xlsx to extract UI elements, "
        "JavaScript events, PLC tag bindings, and screen navigation. "
        "Returns a summary of all screens and elements. The AI can then help with "
        "HMI design, trace PLC tag bindings, or explain screen interactions. "
        "Provide the path to the TIA Portal project root directory."
    ),
)
async def extract_hmi_screens(
    project_path: Annotated[
        str,
        Field(
            description=(
                "Path to the TIA Portal project root directory. "
                "Must contain the 'IM/HMI/I/' subdirectory structure with screen RDF files."
            ),
        ),
    ],
    hmi_tags_path: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Path to HMITags.xlsx file. "
                "If not specified, will look for DATA_HMI/HMITags.xlsx relative to the project."
            ),
        ),
    ] = None,
    instance_id: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "HMI instance ID to extract. "
                "Auto-detects the latest instance if not specified."
            ),
        ),
    ] = None,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Extract HMI screens from a TIA Portal project directory."""
    import os

    # Validate path
    if not os.path.isdir(project_path):
        return f"Error: Directory not found: {project_path}"

    try:
        from dive_mcp_host.extraction import cache_result, parse_hmi_project

        # Offload CPU-bound binary parsing to thread pool
        result = await asyncio.to_thread(
            parse_hmi_project,
            project_path,
            hmi_tags_path=_resolve_hmi_tags_path(project_path, hmi_tags_path),
            instance_id=instance_id,
        )

        # Cache full result for follow-up queries
        cache_key = cache_result(project_path, result, prefix="hmi")

        # Build compact summary for AI context
        summary = result.summary
        device_info = result.hmi_device or {}
        device_name = device_info.get("device_name", "Unknown")

        lines = [
            f"## HMI Screen Extraction: {os.path.basename(project_path)}",
            "",
            f"**Device:** {device_name}",
            f"**Screens found:** {summary.total_screens}",
            f"**Total elements:** {summary.total_elements} "
            f"({summary.total_elements_with_events} with JS events)",
            f"**Tag bindings:** {summary.total_tag_bindings} "
            f"({summary.total_unique_plc_tags} unique PLC tags)",
            f"**JS functions:** {summary.total_js_functions}",
            f"**Navigation links:** {summary.total_navigation_links}",
            f"**Cache key:** `{cache_key}` (reference this for follow-up queries)",
            "",
            "| Screen | Elements | Tags | JS | Nav |",
            "|--------|----------|------|----|-----|",
        ]

        max_screens = 30
        shown = 0
        for screen in result.screens:
            if shown >= max_screens:
                remaining = len(result.screens) - shown
                lines.append(f"| ... and {remaining} more screens | | | | |")
                break
            n_tags = sum(len(e.tag_bindings) for e in screen.elements)
            n_js = len(screen.javascript_functions)
            lines.append(
                f"| {screen.screen_name} | {screen.element_count} | "
                f"{n_tags} | {n_js} | {len(screen.screen_navigations)} |"
            )
            shown += 1

        if result.errors:
            lines.append(f"\n⚠️ {len(result.errors)} screens had parse errors")

        # Navigation map summary
        if result.navigation_map:
            lines.append("\n**Navigation map:**")
            for src, targets in result.navigation_map.items():
                lines.append(f"- {src} → {', '.join(targets)}")

        lines.append(
            "\nAsk follow-up questions about any screen, element, or tag binding. "
            "For example: 'Show me the elements on START_SCHERM', "
            "'Which screens use Motor_Speed tag?', "
            "or 'List all navigation buttons on the main screen'."
        )

        return "\n".join(lines)

    except Exception as e:
        logger.exception("HMI extraction failed")
        return f"Error extracting HMI screens: {e}"
