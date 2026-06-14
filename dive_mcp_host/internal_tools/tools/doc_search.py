"""search_docs tool — lets the AI agent query indexed TIA Portal documentation."""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from pydantic import Field

from dive_mcp_host.rag import get_retriever

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
    source_filter: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional source filename to restrict the search to a single "
                "indexed document (e.g. 'S7-1500_manual.pdf'). Leave unset to "
                "search across all indexed documents."
            ),
        ),
    ] = None,
    max_distance: Annotated[
        float | None,
        Field(
            default=None,
            description=(
                "Optional relevance ceiling (0-2, lower is more relevant). "
                "Drop results whose distance exceeds this to avoid returning "
                "barely-related excerpts. Leave unset to return all top_k."
            ),
        ),
    ] = None,
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
        results = await retriever.search(
            query, top_k=top_k, source_filter=source_filter, max_distance=max_distance
        )
        return retriever.format_results(results, query)
    except Exception as e:
        logger.exception("Document search failed")
        return f"❌ Document search error: {e}"


async def _list_indexed_docs_impl() -> str:
    """Build the indexed-documents listing string (testable, no langchain wrapper).

    Returns a not-configured / empty message when appropriate, otherwise one
    line per document with its source filename, title, chunk and page counts —
    so the agent can pick a ``source_filter`` for ``search_docs``.
    """
    retriever = get_retriever()
    if retriever is None:
        return (
            "Document search is not configured. Configure the embedding model "
            "in Settings and index documents first."
        )

    docs = retriever.store.list_documents()
    if not docs:
        return "No documents are currently indexed."

    lines = [f"Indexed documents ({len(docs)}):"]
    for d in docs:
        title = d.get("title") or d["source"]
        lines.append(
            f"- {d['source']} ({title}) "
            f"— {d.get('chunk_count', 0)} chunks, {d.get('page_count', 0)} pages"
        )
    return "\n".join(lines)


@tool(
    description=(
        "List all indexed TIA Portal and PLC documents available to search_docs, "
        "with each document's source filename, chunk count, and page count. "
        "Use this to discover which documents are indexed before calling "
        "search_docs with a source_filename to restrict the search to one document."
    )
)
async def list_indexed_docs(
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """List all indexed documents (source, chunks, pages)."""
    return await _list_indexed_docs_impl()
