"""Tests for the RAG docs HTTP router (endpoint functions called directly).

These call the endpoint async functions with explicit args (bypassing the
FastAPI TestClient, which would spin up real MCP subprocesses). The endpoints
resolve the retriever via the module singleton, which we monkeypatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_retriever(results=None):
    retriever = AsyncMock()
    retriever.search = AsyncMock(return_value=results or [])
    retriever.format_results = MagicMock(return_value="formatted")
    return retriever


@pytest.mark.asyncio
async def test_search_forwards_source_filter_and_max_distance(monkeypatch):
    """The /search endpoint must forward source_filter + max_distance to the retriever."""
    from dive_mcp_host.httpd.routers import docs as docs_router

    retriever = _mock_retriever(
        results=[{"source": "a.pdf", "title": "A", "page": 1, "text": "x", "distance": 0.1}]
    )
    monkeypatch.setattr("dive_mcp_host.rag._retriever", retriever)

    resp = await docs_router.search_documents(
        query="plc",
        top_k=5,
        source_filter="a.pdf",
        max_distance=0.5,
        app=None,
    )

    retriever.search.assert_awaited_once_with(
        "plc", top_k=5, source_filter="a.pdf", max_distance=0.5
    )
    assert resp.success is True
    assert resp.results[0].source == "a.pdf"


@pytest.mark.asyncio
async def test_search_defaults_to_no_filter(monkeypatch):
    """Without filtering, None is forwarded unchanged (no filter, no ceiling).

    Called with explicit None to mirror how FastAPI resolves the optional Query
    params in a real request (direct calls otherwise see the Query sentinel).
    """
    from dive_mcp_host.httpd.routers import docs as docs_router

    retriever = _mock_retriever(results=[])
    monkeypatch.setattr("dive_mcp_host.rag._retriever", retriever)

    await docs_router.search_documents(
        query="plc", top_k=5, source_filter=None, max_distance=None, app=None
    )

    retriever.search.assert_awaited_once_with(
        "plc", top_k=5, source_filter=None, max_distance=None
    )


@pytest.mark.asyncio
async def test_search_returns_failure_when_retriever_unconfigured(monkeypatch):
    from dive_mcp_host.httpd.routers import docs as docs_router

    monkeypatch.setattr("dive_mcp_host.rag._retriever", None)
    resp = await docs_router.search_documents(query="plc", top_k=5, app=None)
    assert resp.success is False


def test_search_docs_tool_exposes_source_filter():
    """The agent-facing search_docs tool must accept source_filter + max_distance."""
    from dive_mcp_host.internal_tools.tools.doc_search import search_docs

    props = search_docs.args_schema.model_json_schema()["properties"]
    assert "source_filter" in props
    assert "max_distance" in props
    # Both optional with default None — backwards compatible.
    assert props["source_filter"]["default"] is None
    assert props["max_distance"]["default"] is None
