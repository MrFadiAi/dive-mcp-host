"""End-to-end tests for the RAG document search pipeline."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from dive_mcp_host.rag.chunker import chunk_text, count_tokens
from dive_mcp_host.rag.embedder import Embedder, LocalEmbedder
from dive_mcp_host.rag.indexer import extract_pages, index_directory, index_document
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

    def test_source_filter_returns_up_to_topk_from_source(self, store):
        """source_filter must return up to top_k chunks FROM that source.

        Regression: the KNN LIMIT was applied across ALL documents before the
        source filter, so if another document's chunks dominated the global
        top-k, the filtered result came back short — or empty — even though the
        requested source had valid (if slightly more distant) chunks.
        """
        query = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        # doc_other: 10 chunks very close to the query — they dominate the
        # global top-k, crowding doc_target out of an unfiltered search.
        other_chunks = [
            {
                "page": 1,
                "chunk_index": i,
                "text": f"other {i}",
                "embedding": _similar_embedding(query, noise=0.01),
            }
            for i in range(10)
        ]
        store.insert_document("doc_other.pdf", "Other", 1, other_chunks)

        # doc_target: 5 chunks farther from the query but still valid hits.
        target_chunks = [
            {
                "page": 1,
                "chunk_index": i,
                "text": f"target {i}",
                "embedding": _similar_embedding(query, noise=0.3),
            }
            for i in range(5)
        ]
        store.insert_document("doc_target.pdf", "Target", 1, target_chunks)

        results = store.search(query, top_k=5, source_filter="doc_target.pdf")
        assert len(results) == 5, (
            f"expected 5 target chunks, got {len(results)} — filtered KNN under-returns"
        )
        assert all(r["source"] == "doc_target.pdf" for r in results)

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

    def test_content_hash_stored_and_listed(self, store):
        emb = _random_embedding(8)
        store.insert_document(
            "doc.pdf",
            "D",
            1,
            [{"page": 1, "chunk_index": 0, "text": "x", "embedding": emb}],
            content_hash="abc123",
        )
        docs = store.list_documents()
        assert docs[0]["content_hash"] == "abc123"
        assert store.get_content_hashes() == {"doc.pdf": "abc123"}

    def test_content_hash_defaults_none_when_unset(self, store):
        emb = _random_embedding(8)
        store.insert_document(
            "doc.pdf",
            "D",
            1,
            [{"page": 1, "chunk_index": 0, "text": "x", "embedding": emb}],
        )
        assert store.get_content_hashes() == {"doc.pdf": None}

    def test_content_hash_column_migration(self, tmpdir):
        """An existing DB created without the content_hash column must be migrated."""
        import sqlite3

        db = tmpdir / "old.sqlite"
        # Simulate an old DB: doc_meta without content_hash, no doc_vecs yet.
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE doc_meta (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " source TEXT NOT NULL UNIQUE, title TEXT, page_count INTEGER DEFAULT 0,"
            " chunk_count INTEGER DEFAULT 0, indexed_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute("INSERT INTO doc_meta(source) VALUES('old.pdf')")
        conn.commit()
        conn.close()

        # Opening with the new code must add the column (not crash).
        store = DocStore(db, embed_dims=8)
        cols = {r[1] for r in store._connect().execute("PRAGMA table_info(doc_meta)")}
        assert "content_hash" in cols
        # Existing rows survive and surface a None hash.
        assert store.get_content_hashes() == {"old.pdf": None}

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

    @pytest.mark.asyncio
    async def test_reindex_skips_unchanged_reindexes_changed(self, tmpdir):
        """Re-indexing must skip unchanged files and refresh changed ones.

        Previously ``index_directory`` skipped every file whose name was already
        indexed — so editing a doc and re-indexing silently kept the stale
        chunks. Content-hash tracking fixes that: unchanged → skip, changed →
        re-index.
        """
        docs_dir = tmpdir / "docs"
        docs_dir.mkdir()
        f = docs_dir / "guide.txt"
        f.write_text("Initial TIA Portal content.", encoding="utf-8")

        async def fake_embed(texts):
            return [[0.1] * 8 for _ in texts]

        embedder = AsyncMock(spec=Embedder)
        embedder.embed_texts = AsyncMock(side_effect=fake_embed)

        store = DocStore(tmpdir / "idx.sqlite", embed_dims=8)

        # First pass: indexes the new file.
        r1 = await index_directory(docs_dir, store, embedder)
        assert r1["indexed"] == 1
        assert r1["total_chunks"] >= 1

        # Second pass, unchanged file → skipped, nothing re-indexed.
        r2 = await index_directory(docs_dir, store, embedder)
        assert r2["indexed"] == 0
        assert r2["skipped"] == 1

        # Edit the file → must re-index.
        f.write_text("Updated TIA Portal content with new details.", encoding="utf-8")
        r3 = await index_directory(docs_dir, store, embedder)
        assert r3["indexed"] == 1
        # And the store now reflects the new hash, so a third pass skips again.
        r4 = await index_directory(docs_dir, store, embedder)
        assert r4["indexed"] == 0


class TestRetrieverSearch:
    """Tests for retriever.search, including relevance (distance) filtering."""

    def _make_retriever(self, distances):
        store = AsyncMock(spec=DocStore)
        store.search = MagicMock(
            return_value=[
                {
                    "source": "d.pdf",
                    "title": "D",
                    "page": 1,
                    "text": f"chunk at distance {d}",
                    "distance": d,
                }
                for d in distances
            ]
        )
        embedder = AsyncMock(spec=Embedder)
        embedder.embed_text = AsyncMock(return_value=[0.1] * 8)
        return DocSearchRetriever(store=store, embedder=embedder)

    @pytest.mark.asyncio
    async def test_no_threshold_returns_all(self):
        retriever = self._make_retriever([0.1, 0.4, 0.6, 0.95])
        results = await retriever.search("query", top_k=5)
        assert len(results) == 4

    @pytest.mark.asyncio
    async def test_max_distance_filters_distant_chunks(self):
        retriever = self._make_retriever([0.1, 0.4, 0.6, 0.95])
        results = await retriever.search("query", top_k=5, max_distance=0.5)
        distances = [r["distance"] for r in results]
        assert distances == [0.1, 0.4]
        assert all(d <= 0.5 for d in distances)

    @pytest.mark.asyncio
    async def test_max_distance_all_filtered_returns_empty(self):
        retriever = self._make_retriever([0.8, 0.9])
        results = await retriever.search("query", top_k=5, max_distance=0.5)
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_distance_kept_as_safe_default(self):
        # A result without a distance field must not be dropped — default to 0
        # (treat as maximally relevant) so filtering never silently discards data.
        store = AsyncMock(spec=DocStore)
        store.search = MagicMock(
            return_value=[{"source": "d", "title": "D", "page": 1, "text": "x"}]
        )
        embedder = AsyncMock(spec=Embedder)
        embedder.embed_text = AsyncMock(return_value=[0.1] * 8)
        retriever = DocSearchRetriever(store=store, embedder=embedder)
        results = await retriever.search("query", max_distance=0.5)
        assert len(results) == 1


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
            # The embedder is wrapped in a CachedEmbedder for memoisation.
            from dive_mcp_host.rag.embedder import CachedEmbedder

            assert isinstance(retriever.embedder, CachedEmbedder)
            assert retriever.embedder._inner is mock_embedder


class TestEmbedderRobustness:
    """embed_texts must reject a short/partial response instead of silently
    misaligning texts and embeddings (which the indexer's ``zip`` would then
    truncate, dropping chunks with no error)."""

    @staticmethod
    def _embeddings_response(vectors):
        """Build a fake OpenAI embeddings response with sequential indices."""
        from types import SimpleNamespace

        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=v)
                for i, v in enumerate(vectors)
            ]
        )

    @pytest.mark.asyncio
    async def test_api_embed_texts_rejects_short_response(self):
        embedder = Embedder(base_url="http://x/v1", api_key="k", model="m")
        embedder._client = AsyncMock()
        # Provider returns only 2 embeddings for 3 input texts.
        embedder._client.embeddings.create = AsyncMock(
            return_value=self._embeddings_response([[0.1] * 4, [0.2] * 4])
        )
        with pytest.raises(ValueError, match="(?i)embedding"):
            await embedder.embed_texts(["a", "b", "c"])

    @pytest.mark.asyncio
    async def test_api_embed_texts_ok_when_counts_match(self):
        embedder = Embedder(base_url="http://x/v1", api_key="k", model="m")
        embedder._client = AsyncMock()
        embedder._client.embeddings.create = AsyncMock(
            return_value=self._embeddings_response([[0.1] * 4, [0.2] * 4])
        )
        out = await embedder.embed_texts(["a", "b"])
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_local_embed_texts_rejects_short_response(self):
        # Stub the model so encode() returns fewer rows than input texts.
        class _FakeModel:
            def encode(self, texts, **kwargs):
                return np.array([[0.1] * 4, [0.2] * 4])  # 2 rows regardless

        embedder = LocalEmbedder(model_name="dummy")
        embedder._model = _FakeModel()
        embedder._dimensions = 4
        with pytest.raises(ValueError, match="(?i)embedding"):
            await embedder.embed_texts(["a", "b", "c"])

    @pytest.mark.asyncio
    async def test_local_embed_texts_ok_when_counts_match(self):
        class _FakeModel:
            def encode(self, texts, **kwargs):
                return np.array([[0.1] * 4] * len(texts))

        embedder = LocalEmbedder(model_name="dummy")
        embedder._model = _FakeModel()
        embedder._dimensions = 4
        out = await embedder.embed_texts(["a", "b", "c"])
        assert len(out) == 3


class TestCachedEmbedder:
    """CachedEmbedder memoises text→vector and only embeds cache misses."""

    @staticmethod
    def _make_inner():
        """An AsyncMock embedder whose vectors are derived from the input text,
        so cached vs fresh values are distinguishable and order is verifiable."""
        inner = AsyncMock(spec=Embedder)
        inner.embed_text = AsyncMock(side_effect=lambda t: [float(ord(t[0])), 0.0])
        inner.embed_texts = AsyncMock(
            side_effect=lambda ts: [[float(ord(t[0])), 0.0] for t in ts]
        )
        return inner

    @pytest.mark.asyncio
    async def test_embed_text_caches_repeat_calls(self):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = self._make_inner()
        cached = CachedEmbedder(inner)

        v1 = await cached.embed_text("x")
        v2 = await cached.embed_text("x")
        assert v1 == v2 == [float(ord("x")), 0.0]
        assert inner.embed_text.await_count == 1, "second call must hit the cache"

    @pytest.mark.asyncio
    async def test_embed_texts_only_embeds_misses(self):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = self._make_inner()
        cached = CachedEmbedder(inner)
        # Prime the cache for "a" and "c".
        await cached.embed_texts(["a", "c"])

        # Mixed batch: "a" and "c" are cached; "b" and "d" are misses.
        out = await cached.embed_texts(["a", "b", "c", "d"])
        assert out == [
            [float(ord("a")), 0.0],
            [float(ord("b")), 0.0],
            [float(ord("c")), 0.0],
            [float(ord("d")), 0.0],
        ]
        # The second embed_texts call should only have embedded the 2 misses.
        second_call_args = inner.embed_texts.await_args_list[-1].args[0]
        assert second_call_args == ["b", "d"]

    @pytest.mark.asyncio
    async def test_embed_texts_all_hits_skips_inner(self):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = self._make_inner()
        cached = CachedEmbedder(inner)
        await cached.embed_texts(["a", "b"])
        before = inner.embed_texts.await_count
        out = await cached.embed_texts(["a", "b"])
        assert inner.embed_texts.await_count == before, "all-cache-hit must not call inner"
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_stats_track_hits_and_misses(self):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = self._make_inner()
        cached = CachedEmbedder(inner)
        await cached.embed_texts(["a", "b"])  # 2 misses
        await cached.embed_texts(["a", "b", "c"])  # 2 hits, 1 miss
        assert cached.cache_hits == 2
        assert cached.cache_misses == 3

    @pytest.mark.asyncio
    async def test_embed_texts_dedupes_repeats_within_one_batch(self):
        """Identical texts in the SAME batch are embedded once, not per copy."""
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = self._make_inner()
        cached = CachedEmbedder(inner)
        out = await cached.embed_texts(["a", "a", "a", "b"])
        assert out == [
            [float(ord("a")), 0.0],
            [float(ord("a")), 0.0],
            [float(ord("a")), 0.0],
            [float(ord("b")), 0.0],
        ]
        # Only the 2 unique texts reach the inner embedder.
        assert inner.embed_texts.await_args_list[-1].args[0] == ["a", "b"]
        assert cached.cache_misses == 2

    def test_dimensions_delegates(self):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        class _InnerWithDims:
            dimensions = 384

        cached = CachedEmbedder(_InnerWithDims())
        assert cached.dimensions == 384

    def test_model_name_delegates(self):
        """The wrapper must expose model_name so stats/change-detection still work."""
        from dive_mcp_host.rag.embedder import CachedEmbedder

        inner = AsyncMock(spec=Embedder)
        inner.configure_mock(model_name="Qwen/Qwen3-Embedding-0.6B")
        cached = CachedEmbedder(inner)
        assert cached.model_name == "Qwen/Qwen3-Embedding-0.6B"


class TestModelStaleness:
    """Switching the embedding model invalidates stored embeddings even when the
    two models share the same dimensionality (different embedding spaces).

    Without model-aware invalidation, a same-dim model switch silently leaves the
    old model's vectors in place and searches return meaningless distances.
    """

    def test_is_model_stale_decision_logic(self):
        from dive_mcp_host.rag import is_model_stale

        # Empty store → nothing to invalidate.
        assert is_model_stale(0, "modelA", "modelB") is False
        # Same model → fine.
        assert is_model_stale(5, "modelA", "modelA") is False
        # Different model with data → stale.
        assert is_model_stale(5, "modelA", "modelB") is True
        # Legacy store with no recorded model → don't wipe (unknown, adopt).
        assert is_model_stale(5, None, "modelA") is False

    def test_store_meta_get_set(self, store):
        assert store.get_meta("indexing_model") is None
        store.set_meta("indexing_model", "modelA")
        assert store.get_meta("indexing_model") == "modelA"
        store.set_meta("indexing_model", "modelB")  # overwrite
        assert store.get_meta("indexing_model") == "modelB"
        assert store.get_meta("nonexistent") is None

    def test_store_clear_wipes_data_keeps_meta(self, store):
        emb = _random_embedding(8)
        store.insert_document("d.pdf", "D", 1, [
            {"page": 1, "chunk_index": 0, "text": "x", "embedding": emb},
        ])
        store.set_meta("indexing_model", "modelA")
        assert store.get_stats()["document_count"] == 1

        store.clear()
        assert store.get_stats()["document_count"] == 0
        assert store.get_stats()["chunk_count"] == 0
        # Meta survives a data clear so the model claim persists.
        assert store.get_meta("indexing_model") == "modelA"

    def test_init_clears_store_when_model_changes(self, tmpdir):
        """init_local_retriever with a different model must clear stale vectors."""
        from unittest.mock import patch

        from dive_mcp_host.rag import init_local_retriever

        db_path = tmpdir / "s.sqlite"
        # Pre-populate as if indexed with "modelA" (use 768 dims = the default
        # init_local_retriever picks for an unknown model, so no dim-drop fires
        # and the clearing is purely model-staleness driven).
        pre = DocStore(db_path, embed_dims=768)
        pre.insert_document(
            "d.pdf", "D", 1,
            [{"page": 1, "chunk_index": 0, "text": "x", "embedding": [0.1] * 768}],
            content_hash="x",
        )
        pre.set_meta("indexing_model", "modelA")
        assert pre.get_stats()["document_count"] == 1

        mock_embedder = AsyncMock()
        with patch("dive_mcp_host.rag.LocalEmbedder", return_value=mock_embedder):
            retriever = init_local_retriever(model_name="modelB", db_path=db_path)

        assert retriever.store.get_stats()["document_count"] == 0, "stale vectors must be cleared"
        assert retriever.store.get_meta("indexing_model") == "modelB"

    def test_init_preserves_store_when_model_same(self, tmpdir):
        """init_local_retriever with the SAME model must preserve indexed data."""
        from unittest.mock import patch

        from dive_mcp_host.rag import init_local_retriever

        db_path = tmpdir / "s.sqlite"
        pre = DocStore(db_path, embed_dims=768)
        pre.insert_document(
            "d.pdf", "D", 1,
            [{"page": 1, "chunk_index": 0, "text": "x", "embedding": [0.1] * 768}],
            content_hash="x",
        )
        pre.set_meta("indexing_model", "modelA")

        mock_embedder = AsyncMock()
        with patch("dive_mcp_host.rag.LocalEmbedder", return_value=mock_embedder):
            retriever = init_local_retriever(model_name="modelA", db_path=db_path)

        assert retriever.store.get_stats()["document_count"] == 1, "same-model data must survive"
        assert retriever.store.get_meta("indexing_model") == "modelA"


class TestEmbeddingCache:
    """The persistent (sqlite) embedding cache in the doc store, keyed by
    (text_hash, model) so re-indexing unchanged text skips embedding across
    restarts and a model change never returns another model's vectors."""

    def test_put_and_get_single(self, store):
        store.put_cached_embedding("hashA", "modelA", [1.0, 2.0, 3.0])
        assert store.get_cached_embedding("hashA", "modelA") == [1.0, 2.0, 3.0]

    def test_get_single_miss(self, store):
        assert store.get_cached_embedding("absent", "modelA") is None

    def test_put_and_get_batch(self, store):
        store.put_cached_embeddings(
            [("h1", [1.0, 0.0]), ("h2", [0.0, 2.0])], "modelA"
        )
        got = store.get_cached_embeddings(["h1", "h2", "h3"], "modelA")
        assert got["h1"] == [1.0, 0.0]
        assert got["h2"] == [0.0, 2.0]
        assert "h3" not in got  # miss omitted, not None-valued

    def test_cache_is_model_keyed(self, store):
        store.put_cached_embedding("hashA", "modelA", [1.0, 2.0])
        # A different model must NOT see modelA's vector (different embedding space).
        assert store.get_cached_embedding("hashA", "modelB") is None
        assert store.get_cached_embeddings(["hashA"], "modelB") == {}

    def test_put_overwrites_for_same_key_and_model(self, store):
        store.put_cached_embedding("hashA", "modelA", [1.0, 2.0])
        store.put_cached_embedding("hashA", "modelA", [9.0, 8.0])
        assert store.get_cached_embedding("hashA", "modelA") == [9.0, 8.0]

    def test_clear_embedding_cache_by_model(self, store):
        store.put_cached_embedding("h1", "modelA", [1.0])
        store.put_cached_embedding("h2", "modelB", [2.0])
        store.clear_embedding_cache("modelA")
        assert store.get_cached_embedding("h1", "modelA") is None
        assert store.get_cached_embedding("h2", "modelB") == [2.0]  # other model kept


class TestPersistentCachedEmbedder:
    """CachedEmbedder with a store backing persists across wrapper instances."""

    @staticmethod
    def _make_inner():
        inner = AsyncMock(spec=Embedder)
        inner.embed_text = AsyncMock(side_effect=lambda t: [float(ord(t[0])), 0.0])
        inner.embed_texts = AsyncMock(
            side_effect=lambda ts: [[float(ord(t[0])), 0.0] for t in ts]
        )
        inner.configure_mock(model_name="modelA")
        return inner

    @pytest.mark.asyncio
    async def test_persists_across_wrapper_instances(self, tmpdir):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        store = DocStore(tmpdir / "cache.sqlite", embed_dims=8)
        inner1 = self._make_inner()
        c1 = CachedEmbedder(inner1, store=store)
        await c1.embed_texts(["alpha", "beta"])  # 2 misses → cached to disk

        # A fresh wrapper over the SAME store must hit the persistent cache,
        # never calling its (different) inner embedder.
        inner2 = self._make_inner()
        c2 = CachedEmbedder(inner2, store=store)
        out = await c2.embed_texts(["alpha", "beta"])
        assert out == [[float(ord("a")), 0.0], [float(ord("b")), 0.0]]
        inner2.embed_texts.assert_not_awaited()
        assert c2.cache_hits == 2 and c2.cache_misses == 0

    @pytest.mark.asyncio
    async def test_model_change_is_a_cache_miss(self, tmpdir):
        from dive_mcp_host.rag.embedder import CachedEmbedder

        store = DocStore(tmpdir / "cache.sqlite", embed_dims=8)
        innerA = self._make_inner()
        innerA.configure_mock(model_name="modelA")
        await CachedEmbedder(innerA, store=store).embed_texts(["alpha"])

        innerB = self._make_inner()
        innerB.configure_mock(model_name="modelB")
        cB = CachedEmbedder(innerB, store=store)
        await cB.embed_texts(["alpha"])
        # Different model key → miss → innerB was called.
        innerB.embed_texts.assert_awaited()
        assert cB.cache_misses >= 1


class TestToolDefinition:
    """Tests for the search_docs tool definition."""

    def test_tool_name_and_description(self):
        from dive_mcp_host.internal_tools.tools.doc_search import search_docs

        assert search_docs.name == "search_docs"
        assert "TIA Portal" in search_docs.description
        assert "documentation" in search_docs.description.lower()
