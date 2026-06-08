"""End-to-end tests for the RAG document search pipeline."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest

from dive_mcp_host.rag.chunker import chunk_text, count_tokens
from dive_mcp_host.rag.embedder import Embedder
from dive_mcp_host.rag.indexer import extract_pages, index_document
from dive_mcp_host.rag.retriever import DocSearchRetriever
from dive_mcp_host.rag.vector_store import DocStore


@pytest.fixture
def tmpdir():
    """Create a temp directory for test databases."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def store(tmpdir):
    """Create a test DocStore with 8-dimensional vectors."""
    return DocStore(tmpdir / "test.sqlite", embed_dims=8)


def _random_embedding(dims: int = 8) -> list[float]:
    """Generate a random unit-normalized embedding."""
    vec = np.random.randn(dims).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


def _similar_embedding(base: list[float], noise: float = 0.1) -> list[float]:
    """Create an embedding similar to the base by adding small noise."""
    vec = np.array(base, dtype=np.float32) + np.random.randn(len(base)).astype(np.float32) * noise
    vec /= np.linalg.norm(vec)
    return vec.tolist()


class TestVectorStore:
    """Tests for the DocStore vector storage."""

    def test_create_and_init(self, tmpdir):
        store = DocStore(tmpdir / "new.sqlite", embed_dims=128)
        stats = store.get_stats()
        assert stats["document_count"] == 0
        assert stats["chunk_count"] == 0
        assert stats["embed_dims"] == 128

    def test_insert_and_search(self, store):
        emb = _random_embedding(8)
        store.insert_document("tia_manual.pdf", "TIA Portal Manual", 10, [
            {"page": 1, "chunk_index": 0, "text": "SCL programming guide", "embedding": emb},
            {"page": 2, "chunk_index": 1, "text": "Hardware configuration steps", "embedding": _similar_embedding(emb)},
        ])

        results = store.search(emb, top_k=2)
        assert len(results) == 2
        assert results[0]["text"] == "SCL programming guide"
        assert results[0]["distance"] < results[1]["distance"]
        assert results[0]["source"] == "tia_manual.pdf"

    def test_source_filter(self, store):
        emb = _random_embedding(8)
        store.insert_document("doc_a.pdf", "Doc A", 1, [
            {"page": 1, "chunk_index": 0, "text": "Content A", "embedding": emb},
        ])
        store.insert_document("doc_b.pdf", "Doc B", 1, [
            {"page": 1, "chunk_index": 0, "text": "Content B", "embedding": emb},
        ])

        results = store.search(emb, top_k=5, source_filter="doc_a.pdf")
        assert len(results) == 1
        assert results[0]["source"] == "doc_a.pdf"

    def test_replace_document(self, store):
        emb = _random_embedding(8)
        store.insert_document("doc.pdf", "Original", 1, [
            {"page": 1, "chunk_index": 0, "text": "Old content", "embedding": emb},
            {"page": 2, "chunk_index": 1, "text": "More old", "embedding": emb},
        ])
        assert store.get_stats()["chunk_count"] == 2

        store.insert_document("doc.pdf", "Updated", 1, [
            {"page": 1, "chunk_index": 0, "text": "New content", "embedding": emb},
        ])
        assert store.get_stats()["chunk_count"] == 1
        docs = store.list_documents()
        doc = [d for d in docs if d["source"] == "doc.pdf"][0]
        assert doc["title"] == "Updated"
        assert doc["chunk_count"] == 1

    def test_delete_document(self, store):
        emb = _random_embedding(8)
        store.insert_document("to_delete.pdf", "Delete Me", 1, [
            {"page": 1, "chunk_index": 0, "text": "Bye", "embedding": emb},
        ])
        assert store.get_stats()["document_count"] == 1

        assert store.delete_document("to_delete.pdf") is True
        assert store.get_stats()["document_count"] == 0
        assert store.delete_document("nonexistent.pdf") is False

    def test_list_documents(self, store):
        emb = _random_embedding(8)
        store.insert_document("a.pdf", "First", 1, [
            {"page": 1, "chunk_index": 0, "text": "A", "embedding": emb},
        ])
        store.insert_document("b.pdf", "Second", 2, [
            {"page": 1, "chunk_index": 0, "text": "B", "embedding": emb},
        ])

        docs = store.list_documents()
        assert len(docs) == 2
        sources = {d["source"] for d in docs}
        assert sources == {"a.pdf", "b.pdf"}

    def test_search_returns_empty_on_no_data(self, store):
        results = store.search(_random_embedding(8), top_k=5)
        assert results == []


class TestChunker:
    """Tests for text chunking."""

    def test_count_tokens(self):
        assert count_tokens("Hello world") > 0
        assert count_tokens("") == 0

    def test_short_text_single_chunk(self):
        chunks = chunk_text("Short text", chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == "Short text"

    def test_empty_text(self):
        chunks = chunk_text("")
        assert chunks == []

    def test_long_text_produces_multiple_chunks(self):
        paragraphs = [f"Paragraph {i} with some content about PLC programming topic {i}." * 5 for i in range(10)]
        text = "\n\n".join(paragraphs)
        chunks = chunk_text(text, chunk_size=30, chunk_overlap=10)
        assert len(chunks) >= 2

    def test_chunks_preserve_content(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, chunk_size=500)
        # Should fit in one chunk
        assert len(chunks) == 1
        assert "First paragraph" in chunks[0]
        assert "Third paragraph" in chunks[0]


class TestIndexer:
    """Tests for document indexing pipeline."""

    def test_extract_text_file(self, tmpdir):
        sample = tmpdir / "test.txt"
        sample.write_text("Hello TIA Portal\n\nSecond paragraph.", encoding="utf-8")
        pages = extract_pages(sample)
        assert len(pages) == 1
        assert "Hello TIA Portal" in pages[0]["text"]

    def test_extract_markdown_file(self, tmpdir):
        sample = tmpdir / "guide.md"
        sample.write_text("# TIA Portal Guide\n\n## Setup\n\nConfigure hardware.", encoding="utf-8")
        pages = extract_pages(sample)
        assert "TIA Portal Guide" in pages[0]["text"]

    def test_extract_unsupported_file(self, tmpdir):
        sample = tmpdir / "test.xyz"
        sample.write_text("content", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported file type"):
            extract_pages(sample)


class TestRetrieverFormat:
    """Tests for retriever result formatting."""

    def test_format_empty_results(self):
        store = AsyncMock(spec=DocStore)
        embedder = AsyncMock(spec=Embedder)
        retriever = DocSearchRetriever(store=store, embedder=embedder)
        result = retriever.format_results([], "test query")
        assert "No relevant" in result

    def test_format_with_results(self):
        store = AsyncMock(spec=DocStore)
        embedder = AsyncMock(spec=Embedder)
        retriever = DocSearchRetriever(store=store, embedder=embedder)
        result = retriever.format_results(
            [
                {
                    "source": "tia_manual.pdf",
                    "title": "TIA Manual",
                    "page": 42,
                    "text": "Configure PROFINET communication",
                    "distance": 0.15,
                }
            ],
            "PROFINET setup",
        )
        assert "tia_manual.pdf" in result
        assert "TIA Manual" in result
        assert "Configure PROFINET" in result
        assert "relevance" in result


class TestEmbedConfig:
    """Tests for the EmbedConfig backward compatibility."""

    def test_embed_config_with_base_url(self):
        from dive_mcp_host.host.conf import EmbedConfig

        cfg = EmbedConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_key="sk-test",
            embed_dims=1536,
            base_url="https://custom.api/v1",
        )
        assert cfg.base_url == "https://custom.api/v1"

    def test_embed_config_without_base_url(self):
        from dive_mcp_host.host.conf import EmbedConfig

        cfg = EmbedConfig(provider="openai", model="text-embedding-3-small")
        assert cfg.base_url is None

    def test_embed_config_backward_compatible(self):
        """Old JSON without base_url should still parse."""
        from dive_mcp_host.host.conf import EmbedConfig

        cfg = EmbedConfig.model_validate({
            "provider": "openai",
            "model": "text-embedding-3-small",
            "embed_dims": 1536,
            "api_key": "sk-test",
        })
        assert cfg.base_url is None


class TestResolveBaseUrl:
    """Tests for the base URL resolution logic."""

    def test_explicit_base_url_wins(self):
        from dive_mcp_host.rag import _resolve_base_url

        assert _resolve_base_url("http://custom", "http://llm", "openai") == "http://custom"

    def test_llm_base_url_fallback(self):
        from dive_mcp_host.rag import _resolve_base_url

        assert _resolve_base_url(None, "http://llm", None) == "http://llm"

    def test_known_provider_default(self):
        from dive_mcp_host.rag import _resolve_base_url

        assert _resolve_base_url(None, None, "openai") == "https://api.openai.com/v1"

    def test_unknown_provider_default(self):
        from dive_mcp_host.rag import _resolve_base_url

        result = _resolve_base_url(None, None, "unknown_provider")
        assert "openai.com" in result  # falls back to OpenAI


class TestLocalEmbedder:
    """Tests for the LocalEmbedder offline embedding class."""

    def test_import(self):
        from dive_mcp_host.rag.embedder import LocalEmbedder

        embedder = LocalEmbedder(model_name="test-model")
        assert embedder.model_name == "test-model"
        assert embedder._model is None  # lazy — not loaded yet

    def test_init_retriever_local_mode(self, tmpdir):
        """init_local_retriever should create a retriever with LocalEmbedder."""
        from unittest.mock import patch

        from dive_mcp_host.rag import init_local_retriever

        # Patch LocalEmbedder to avoid downloading a real model
        mock_embedder = AsyncMock()
        mock_embedder.dimensions = 256
        mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 256)

        with patch("dive_mcp_host.rag.LocalEmbedder", return_value=mock_embedder):
            retriever = init_local_retriever(
                model_name="test-model",
                db_path=tmpdir / "test_local.sqlite",
            )
            assert retriever is not None
            assert retriever.embedder is mock_embedder


class TestToolDefinition:
    """Tests for the search_docs tool definition."""

    def test_tool_name_and_description(self):
        from dive_mcp_host.internal_tools.tools.doc_search import search_docs

        assert search_docs.name == "search_docs"
        assert "TIA Portal" in search_docs.description
        assert "documentation" in search_docs.description.lower()
