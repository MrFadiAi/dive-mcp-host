"""Embedding generation using OpenAI-compatible APIs or local models."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from dive_mcp_host.rag.vector_store import DocStore

logger = logging.getLogger(__name__)

# Default embedding dimensions for common models
MODEL_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "embedding-3": 2048,
    "all-MiniLM-L6-v2": 384,
    "all-MiniLM-L12-v2": 384,
    "paraphrase-multilingual-MiniLM-L12-v2": 384,
    "Qwen/Qwen3-Embedding-0.6B": 768,
    "Qwen/Qwen3-Embedding-4B": 2560,
    "nomic-ai/nomic-embed-text-v1.5": 768,
    "BAAI/bge-m3": 1024,
    "BAAI/bge-small-en-v1.5": 384,
}


def get_model_dims(model_name: str) -> int | None:
    """Get known dimensions for a model without loading it.

    Returns None if the model is not in the known list.
    """
    return MODEL_DIMS.get(model_name)


def _assert_embedding_count(text_count: int, embedding_count: int, model: str) -> None:
    """Validate an embedder returned one vector per input text.

    Without this guard a short/partial response silently misaligns texts and
    embeddings; the indexer's ``zip(chunks, embeddings)`` would then truncate,
    dropping chunks with no error (silent data loss). Raise a clear error so the
    failure is loud and the two indexer paths behave consistently.
    """
    if embedding_count != text_count:
        msg = (
            f"Embedding model '{model}' returned {embedding_count} vectors "
            f"for {text_count} inputs — counts must match. "
            "Refusing to store a misaligned batch (would corrupt the index)."
        )
        raise ValueError(msg)


def _hash_text(text: str) -> str:
    """Stable SHA-256 digest of ``text`` — the persistent cache key (plus model)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CachedEmbedder:
    """Wraps an embedder with an in-memory, content-addressed text→vector cache.

    Embedding (especially the local sentence-transformers path) is the slow,
    CPU-bound bottleneck of indexing. Identical text always embeds to the same
    vector, so memoising it avoids re-encoding boilerplate, repeated passages,
    or unchanged chunks when a document is re-indexed. ``embed_texts`` batches
    *only the cache misses* in a single delegate call and interleaves the cached
    vectors back in order, and dedupes identical texts within one batch.

    Two layers: an in-memory dict (fast, process-local) and, when ``store`` is
    given, a persistent sqlite cache keyed by ``(sha256(text), model)`` that
    survives restarts — so re-indexing unchanged text after a restart skips
    embedding entirely. The model key means a model change never returns another
    model's vectors (different embedding space). It is a transparent wrapper —
    anything expecting an embedder (``embed_text`` / ``embed_texts``) can use it
    unchanged. The ``dimensions`` / ``model_name`` properties delegate to inner.
    """

    def __init__(
        self,
        inner: Embedder | LocalEmbedder,
        store: DocStore | None = None,
    ) -> None:
        """Wrap ``inner`` with an in-memory cache, optionally backed by ``store``."""
        self._inner = inner
        self._store = store
        self._cache: dict[str, list[float]] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def _model_key(self) -> str:
        return self.model_name or ""

    @property
    def dimensions(self) -> int | None:
        """Delegate to the inner embedder's dimensions when available."""
        return getattr(self._inner, "dimensions", None)

    @property
    def model_name(self) -> str | None:
        """Delegate the inner embedder's model name.

        Covers both ``LocalEmbedder.model_name`` and ``Embedder.model``. Exposed
        so callers that read ``embedder.model_name`` (the stats endpoint,
        model-change detection) keep working through the wrapper.
        """
        return getattr(self._inner, "model_name", None) or getattr(
            self._inner, "model", None
        )

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text, serving from the in-memory then persistent cache."""
        cached = self._cache.get(text)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self._store is not None:
            model = self._model_key()
            persisted = self._store.get_cached_embedding(_hash_text(text), model)
            if persisted is not None:
                self._cache[text] = persisted
                self.cache_hits += 1
                return persisted
        self.cache_misses += 1
        vector = await self._inner.embed_text(text)
        self._cache[text] = vector
        if self._store is not None:
            self._store.put_cached_embedding(
                _hash_text(text), self._model_key(), vector
            )
        return vector

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, resolving in-memory then persistent then inner.

        Dedupes identical texts (within the batch and across calls) and batches
        only the true misses into one delegate call.
        """
        # Phase 1: resolve each unique text from in-memory, then persistent.
        unique_texts = list(dict.fromkeys(texts))
        resolved: dict[str, list[float]] = {}
        not_in_mem: list[str] = []
        for t in unique_texts:
            v = self._cache.get(t)
            if v is not None:
                resolved[t] = v
                self.cache_hits += 1
            else:
                not_in_mem.append(t)

        to_embed = not_in_mem
        if not_in_mem and self._store is not None:
            model = self._model_key()
            hashes = {t: _hash_text(t) for t in not_in_mem}
            persisted = self._store.get_cached_embeddings(
                [hashes[t] for t in not_in_mem], model
            )
            to_embed = []
            for t in not_in_mem:
                v = persisted.get(hashes[t])
                if v is not None:
                    resolved[t] = v
                    self._cache[t] = v
                    self.cache_hits += 1
                else:
                    to_embed.append(t)

        # Phase 2: embed the true misses (already deduped via unique_texts).
        if to_embed:
            fresh = await self._inner.embed_texts(to_embed)
            self.cache_misses += len(to_embed)
            entries: list[tuple[str, list[float]]] = []
            model = self._model_key()
            for t, v in zip(to_embed, fresh, strict=True):
                resolved[t] = v
                self._cache[t] = v
                if self._store is not None:
                    entries.append((_hash_text(t), v))
            if self._store is not None and entries:
                self._store.put_cached_embeddings(entries, model)

        return [resolved[t] for t in texts]


class Embedder:
    """Generates text embeddings using an OpenAI-compatible API.

    Works with any provider that exposes the ``/v1/embeddings`` endpoint
    (OpenAI, Azure, local models via LiteLLM/Ollama, etc.).

    Usage::

        embedder = Embedder(
            base_url="https://api.openai.com/v1",
            api_key="sk-...",
            model="text-embedding-3-small",
        )
        vector = await embedder.embed_text("Hello world")
        vectors = await embedder.embed_texts(["Hello", "World"])
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text string.

        Args:
            text: Input text to embed.

        Returns:
            Embedding vector as a list of floats.
        """
        response = await self._client.embeddings.create(
            input=text,
            model=self.model,
        )
        return response.data[0].embedding

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int = 64,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts in batches.

        Args:
            texts: List of input texts.
            batch_size: Number of texts per API call.

        Returns:
            List of embedding vectors, one per input text.
        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                input=batch,
                model=self.model,
            )
            # Sort by index to maintain order
            sorted_data = sorted(response.data, key=lambda d: d.index)
            _assert_embedding_count(len(batch), len(sorted_data), self.model)
            all_embeddings.extend([d.embedding for d in sorted_data])

            if len(texts) > batch_size:
                logger.debug(
                    "Embedded batch %d/%d (%d texts)",
                    i // batch_size + 1,
                    (len(texts) + batch_size - 1) // batch_size,
                    len(batch),
                )

        return all_embeddings


class LocalEmbedder:
    """Generates text embeddings using a local sentence-transformers model.

    Runs entirely on your machine — no API key or internet needed.
    Uses the ``sentence-transformers`` library to load models from HuggingFace.

    First run downloads the model; subsequent runs use the cached version.

    Supported models (any HuggingFace sentence-transformers model):

    - ``Qwen/Qwen3-Embedding-4B`` — 4B params, excellent quality (recommended)
    - ``Qwen/Qwen3-Embedding-0.6B`` — 0.6B params, very fast
    - ``nomic-ai/nomic-embed-text-v1.5`` — 274MB, fast, good quality
    - ``BAAI/bge-m3`` — 570MB, good multilingual support

    Usage::

        embedder = LocalEmbedder(model_name="Qwen/Qwen3-Embedding-4B")
        vector = await embedder.embed_text("Hello world")
        dims = embedder.dimensions  # auto-detected from model
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-4B",
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device  # None = auto-detect (CUDA/MPS/CPU)
        self._model = None
        self._dimensions: int | None = None

    def _load_model(self) -> None:
        """Lazily load the model on first use."""
        if self._model is not None:
            return

        logger.info("Loading local embedding model: %s", self.model_name)
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
            )
            # get_embedding_dimension is the new name (sentence-transformers >= 3.5)
            get_dims = getattr(self._model, "get_embedding_dimension", None)
            if get_dims is not None:
                self._dimensions = get_dims()
            else:
                self._dimensions = self._model.get_sentence_embedding_dimension()
            logger.info(
                "Local embedding model loaded: %s (dims=%d, device=%s)",
                self.model_name,
                self._dimensions,
                self._model.device,
            )
        except ImportError:
            msg = (
                "sentence-transformers is required for local embeddings. "
                "Install it with: pip install sentence-transformers"
            )
            raise ImportError(msg) from None

    @property
    def dimensions(self) -> int:
        """Get the embedding dimensionality (loads model if needed)."""
        self._load_model()
        return self._dimensions  # type: ignore[return-value]

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text string.

        Args:
            text: Input text to embed.

        Returns:
            Embedding vector as a list of floats.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        embedding = await loop.run_in_executor(
            None,
            lambda: self._model.encode(text, normalize_embeddings=True),
        )
        return embedding.tolist()

    async def embed_texts(
        self,
        texts: list[str],
        batch_size: int = 128,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts in batches.

        Runs the CPU-bound ``model.encode()`` in a thread pool so the
        asyncio event loop is not blocked.

        Args:
            texts: List of input texts.
            batch_size: Number of texts per batch. Larger values use more
                RAM but are significantly faster for many small texts.

        Returns:
            List of embedding vectors, one per input text.
        """
        import time

        loop = asyncio.get_event_loop()
        logger.info("embed_texts: loading model (%d texts)...", len(texts))
        t0 = time.monotonic()
        await loop.run_in_executor(None, self._load_model)
        logger.info("embed_texts: model loaded in %.1fs", time.monotonic() - t0)

        logger.info("embed_texts: encoding %d texts (batch_size=%d)...", len(texts), batch_size)
        t1 = time.monotonic()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=len(texts) > batch_size * 2,
            ),
        )
        logger.info("embed_texts: encoded %d texts in %.1fs", len(texts), time.monotonic() - t1)
        result = embeddings.tolist()
        _assert_embedding_count(len(texts), len(result), self.model_name)
        return result
