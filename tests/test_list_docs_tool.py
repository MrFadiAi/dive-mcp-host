"""Tests for the list_indexed_docs tool — lets the agent enumerate indexed docs."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dive_mcp_host.rag.vector_store import DocStore


@pytest.fixture
def store(tmpdir):
    """A DocStore with two indexed documents."""
    from pathlib import Path

    s = DocStore(Path(tmpdir) / "t.sqlite", embed_dims=8)
    s.insert_document(
        "s7-1500_manual.pdf",
        "S7 1500 Manual",
        10,
        [{"page": 1, "chunk_index": i, "text": f"chunk {i}", "embedding": [0.1] * 8} for i in range(3)],
    )
    s.insert_document(
        "profinet_guide.md",
        "PROFINET Guide",
        2,
        [{"page": 1, "chunk_index": 0, "text": "pnet", "embedding": [0.1] * 8}],
    )
    return s


class TestListIndexedDocsTool:
    """The agent-facing list_indexed_docs tool."""

    def test_tool_name_and_description(self):
        from dive_mcp_host.internal_tools.tools.doc_search import list_indexed_docs

        assert list_indexed_docs.name == "list_indexed_docs"
        assert "document" in list_indexed_docs.description.lower()

    @pytest.mark.asyncio
    async def test_lists_documents_when_configured(self, store, monkeypatch):
        from dive_mcp_host.internal_tools.tools import doc_search as mod

        retriever = MagicMock()
        retriever.store = store
        monkeypatch.setattr(mod, "get_retriever", lambda: retriever)

        out = await mod._list_indexed_docs_impl()
        assert "s7-1500_manual.pdf" in out
        assert "profinet_guide.md" in out
        assert "S7 1500 Manual" in out

    @pytest.mark.asyncio
    async def test_reports_chunk_counts(self, store, monkeypatch):
        from dive_mcp_host.internal_tools.tools import doc_search as mod

        retriever = MagicMock()
        retriever.store = store
        monkeypatch.setattr(mod, "get_retriever", lambda: retriever)

        out = await mod._list_indexed_docs_impl()
        # s7-1500_manual.pdf has 3 chunks; profinet_guide.md has 1.
        assert "3" in out
        assert "1" in out

    @pytest.mark.asyncio
    async def test_not_configured_message(self, monkeypatch):
        from dive_mcp_host.internal_tools.tools import doc_search as mod

        monkeypatch.setattr(mod, "get_retriever", lambda: None)
        out = await mod._list_indexed_docs_impl()
        assert "not" in out.lower() or "no" in out.lower()
