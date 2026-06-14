"""PLC block extraction tool — parses TIA Portal exported XML blocks."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

logger = logging.getLogger(__name__)


@tool(
    description=(
        "Extract and analyze PLC program blocks from a TIA Portal exported XML directory. "
        "Parses block interfaces, SCL/STL code, tag cross-references, and call trees. "
        "Returns a summary of all found blocks. The AI can then answer questions about "
        "specific blocks, trace tag usage, explain code, or generate documentation. "
        "Provide the path to the directory containing exported block XML files "
        "(e.g. 'D:/project/DATA_Program blocks')."
    ),
)
async def extract_plc_blocks(
    blocks_path: Annotated[
        str,
        Field(
            description=(
                "Path to the directory containing exported TIA Portal block XML files. "
                "This is typically the 'DATA_Program blocks' folder from a TIA Portal export."
            ),
        ),
    ],
    tags_path: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Path to the PLC tags directory. "
                "Defaults to '<blocks_path>/PLC tags' if not specified."
            ),
        ),
    ] = None,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Extract PLC program blocks from a TIA Portal exported XML directory."""
    import os

    # Validate path
    if not os.path.isdir(blocks_path):
        return f"Error: Directory not found: {blocks_path}"

    try:
        from dive_mcp_host.extraction import cache_result, parse_plc_directory

        # Offload CPU-bound XML parsing to thread pool
        result = await asyncio.to_thread(
            parse_plc_directory,
            blocks_path,
            tags_dir=tags_path,
        )

        # Cache full result for follow-up queries
        cache_key = cache_result(blocks_path, result, prefix="plc")

        # Build compact summary for AI context
        summary = result.summary
        lines = [
            f"## PLC Block Extraction: {os.path.basename(blocks_path)}",
            f"",
            f"**Blocks found:** {summary.total_blocks} "
            f"({summary.fb_count} FB, {summary.fc_count} FC, {summary.ob_count} OB, {summary.db_count} DB, {summary.idb_count} IDB)",
            f"**Languages:** {summary.scl_count} SCL, {summary.stl_count} STL",
            f"**PLC tags loaded:** {summary.plc_tags_loaded}",
            f"**Tag references:** {summary.unique_tag_refs} unique tags across all blocks",
            f"**Block calls:** {summary.total_calls} calls between blocks",
            f"**Cache key:** `{cache_key}` (reference this for follow-up queries)",
            f"",
            f"| Block | Type | # | Lang | Interface | Calls | Tags |",
            f"|-------|------|---|------|-----------|-------|------|",
        ]

        max_blocks = 50
        shown = 0
        for block in result.blocks:
            if shown >= max_blocks:
                remaining = len(result.blocks) - shown
                lines.append(f"| ... and {remaining} more blocks | | | | | | |")
                break
            lines.append(
                f"| {block.block_name} | {block.block_type} | {block.block_number} | "
                f"{block.programming_language} | {block.interface_count} | "
                f"{len(block.calls)} | {len(block.tag_references)} |"
            )
            shown += 1

        if result.errors:
            lines.append(f"\n⚠️ {len(result.errors)} files had parse errors")

        lines.append(
            f"\nAsk follow-up questions about any block, tag, or call relationship. "
            f"For example: 'Show me the code of FC102', 'Which blocks use tag Motor_Speed?', "
            f"or 'Draw the call tree starting from OB1'."
        )

        return "\n".join(lines)

    except Exception as e:
        logger.exception("PLC extraction failed")
        return f"Error extracting PLC blocks: {e}"
