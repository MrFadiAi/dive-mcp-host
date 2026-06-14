"""High-level document search retriever.

Combines embedding generation and vector store search into a single
search interface for the agent tool.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dive_mcp_host.rag.embedder import Embedder
    from dive_mcp_host.rag.vector_store import DocStore

logger = logging.getLogger(__name__)


class DocSearchRetriever:
    """Searches indexed documents using semantic similarity.

    Wraps an Embedder and DocStore together to provide end-to-end
    search: query text → embedding → vector search → formatted results.

    Usage::

        retriever = DocSearchRetriever(store=my_store, embedder=my_embedder)
        results = await retriever.search("how to configure TIA Portal hardware")
    """

    def __init__(self, store: DocStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    async def search(
        self,
        query: str,
        top_k: int = 5,
        source_filter: str | None = None,
        max_distance: float | None = None,
    ) -> list[dict]:
        """Search for relevant document chunks.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results.
            source_filter: Optional source filename to restrict search.
            max_distance: Optional relevance ceiling. Results whose vector
                ``distance`` exceeds this are dropped — a top_k search can
                return barely-related chunks, and returning those to the LLM as
                "relevant" (see ``format_results``) misleads it. ``None`` keeps
                the original behaviour (return everything up to top_k). A result
                missing the ``distance`` field defaults to 0.0 (treated as
                maximally relevant) so filtering never silently discards data.

        Returns:
            List of result dicts with: source, title, page, text, distance.
        """
        # Embed the query
        query_embedding = await self.embedder.embed_text(query)

        # Search vector store
        results = self.store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            source_filter=source_filter,
        )

        # Drop low-relevance chunks when a relevance ceiling is requested.
        if max_distance is not None:
            results = [
                r for r in results if r.get("distance", 0.0) <= max_distance
            ]

        return results

    def format_results(self, results: list[dict], query: str) -> str:
        """Format search results into a readable string for the LLM.

        Args:
            results: Search results from self.search().
            query: Original query (for context).

        Returns:
            Formatted string with document excerpts.
        """
        if not results:
            return "No relevant documentation found for the query."

        lines = [f"📚 Found {len(results)} relevant excerpts for: \"{query}\"\n"]

        for i, result in enumerate(results, 1):
            source = result.get("source", "Unknown")
            title = result.get("title", "")
            page = result.get("page", "?")
            text = result.get("text", "")
            distance = result.get("distance", 0)

            relevance = f"(relevance: {1 - distance:.2f})" if distance else ""
            label = f"{title} ({source})" if title else source

            lines.append(f"--- Excerpt {i} ---")
            lines.append(f"📖 Source: {label}, Page {page} {relevance}")
            lines.append(f"{text}")
            lines.append("")

        return "\n".join(lines)
