"""search_docs tool — lets the AI agent query indexed TIA Portal documentation."""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

logger = logging.getLogger(__name__)


@tool(
    description=(
        "Search indexed TIA Portal and PLC programming documentation for relevant information. "
        "Use this tool when you need to look up official documentation, reference material, "
        "programming guidelines, hardware configuration details, or best practices related to "
        "TIA Portal, S7-1200/S7-1500 PLCs, SCL, STL, or IEC 61131-3. "
        "Returns relevant excerpts with source and page references."
    )
)
async def search_docs(
    query: Annotated[
        str,
        Field(
            description=(
                "Search query describing what information you need. "
                "Be specific: e.g. 'how to configure PROFINET communication' "
                "or 'TIA Portal structured data type best practices'"
            ),
        ),
    ],
    top_k: Annotated[
        int,
        Field(
            default=5,
            description="Maximum number of results to return (default: 5).",
        ),
    ] = 5,
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Search indexed TIA Portal documentation for relevant information."""
    from dive_mcp_host.rag import get_retriever

    retriever = get_retriever()
    if retriever is None:
        return (
            "⚠️ Document search is not configured. "
            "Please configure the embedding model in Settings first "
            "(Settings → Embedding Model), then index documents using "
            "the CLI: dive_cli --index-docs <directory>"
        )

    try:
        results = await retriever.search(query, top_k=top_k)
        return retriever.format_results(results, query)
    except Exception as e:
        logger.exception("Document search failed")
        return f"❌ Document search error: {e}"
