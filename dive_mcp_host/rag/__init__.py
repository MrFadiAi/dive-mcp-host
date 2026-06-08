"""RAG module for TIA Portal documentation retrieval.

Provides vector storage, embedding, indexing, and search capabilities
using sqlite-vec and OpenAI-compatible embedding APIs or local models.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from dive_mcp_host.rag.embedder import Embedder, LocalEmbedder
from dive_mcp_host.rag.retriever import DocSearchRetriever
from dive_mcp_host.rag.vector_store import DocStore

if TYPE_CHECKING:
    from dive_mcp_host.httpd.conf.models import ModelManager

logger = logging.getLogger(__name__)

__all__ = [
    "DocStore",
    "Embedder",
    "LocalEmbedder",
    "DocSearchRetriever",
    "get_retriever",
    "init_retriever",
    "init_retriever_from_config",
    "init_local_retriever",
]

# Module-level singleton retriever
_retriever: DocSearchRetriever | None = None

# Known provider base URLs for embedding endpoints
_PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def _resolve_base_url(
    embed_base_url: str | None,
    active_llm_base_url: str | None,
    provider: str | None,
) -> str:
    """Resolve the embedding API base URL.

    Priority:
    1. Explicit embed_base_url from EmbedConfig
    2. Active LLM provider's base_url
    3. Known provider defaults
    """
    if embed_base_url:
        return embed_base_url
    if active_llm_base_url:
        return active_llm_base_url
    if provider and provider in _PROVIDER_BASE_URLS:
        return _PROVIDER_BASE_URLS[provider]
    # Last resort — most OpenAI-compatible APIs use this convention
    return "https://api.openai.com/v1"


def _get_doc_store_path() -> Path:
    """Get the doc store database path, colocated with the main config."""
    from dive_mcp_host.env import DIVE_CONFIG_DIR

    return DIVE_CONFIG_DIR / "doc_store.sqlite"


def init_retriever(
    db_path: Path,
    base_url: str,
    api_key: str,
    model: str,
    embed_dims: int = 768,
) -> DocSearchRetriever:
    """Initialize the global retriever with an API-based embedder.

    Called when embedding config is saved or at app startup.
    """
    global _retriever  # noqa: PLW0603
    store = DocStore(db_path, embed_dims)
    embedder = Embedder(base_url=base_url, api_key=api_key, model=model)
    _retriever = DocSearchRetriever(store=store, embedder=embedder)
    logger.info(
        "RAG retriever initialized (api model=%s, dims=%d, db=%s)",
        model,
        embed_dims,
        db_path,
    )
    return _retriever


def init_local_retriever(
    model_name: str = "Qwen/Qwen3-Embedding-4B",
    db_path: Path | None = None,
) -> DocSearchRetriever:
    """Initialize the global retriever with a local sentence-transformers model.

    No API key or internet needed after the first model download.

    Args:
        model_name: HuggingFace model name (e.g. ``Qwen/Qwen3-Embedding-4B``).
        db_path: Path to the vector store database. Defaults to the config dir.

    Returns:
        The initialized retriever.
    """
    global _retriever  # noqa: PLW0603
    if db_path is None:
        db_path = _get_doc_store_path()

    embedder = LocalEmbedder(model_name=model_name)
    # Use known dimensions to avoid loading the model at startup
    from dive_mcp_host.rag.embedder import get_model_dims

    embed_dims = get_model_dims(model_name) or 768
    store = DocStore(db_path, embed_dims)
    _retriever = DocSearchRetriever(store=store, embedder=embedder)
    logger.info(
        "RAG retriever initialized (local model=%s, dims=%d, db=%s)",
        model_name,
        embed_dims,
        db_path,
    )
    return _retriever


def init_retriever_from_config(model_manager: ModelManager) -> DocSearchRetriever | None:
    """Initialize the retriever from the saved model configuration.

    Reads EmbedConfig from the model manager. Supports:

    - ``provider="local"``: Uses LocalEmbedder (sentence-transformers, no API key needed)
    - Any other provider: Uses API-based Embedder

    Returns:
        The initialized retriever, or None if embedding is not configured.
    """
    full_config = model_manager.full_config
    if full_config is None or full_config.embed_config is None:
        logger.debug("No embedding config found — RAG retriever not initialized")
        return None

    embed_cfg = full_config.embed_config

    if not embed_cfg.model:
        logger.warning("Embedding config incomplete: model not set")
        return None

    # Local embedding (sentence-transformers, no API key needed)
    if embed_cfg.provider == "local":
        db_path = _get_doc_store_path()
        return init_local_retriever(
            model_name=embed_cfg.model,
            db_path=db_path,
        )

    # API-based embedding (requires api_key)
    if not embed_cfg.api_key:
        logger.warning(
            "Embedding config incomplete (model=%s, api_key=missing) — RAG not initialized",
            embed_cfg.model,
        )
        return None

    # Resolve base URL from embed config -> active LLM config -> provider defaults
    active_base_url: str | None = None
    if full_config.active_provider:
        active_settings = model_manager.get_settings_by_provider(full_config.active_provider)
        if active_settings and active_settings.configuration and active_settings.configuration.base_url:
            active_base_url = active_settings.configuration.base_url

    base_url = _resolve_base_url(
        embed_base_url=embed_cfg.base_url,
        active_llm_base_url=active_base_url,
        provider=embed_cfg.provider,
    )

    embed_dims = embed_cfg.embed_dims or 768
    db_path = _get_doc_store_path()

    return init_retriever(
        db_path=db_path,
        base_url=base_url,
        api_key=embed_cfg.api_key,
        model=embed_cfg.model,
        embed_dims=embed_dims,
    )


def get_retriever() -> DocSearchRetriever | None:
    """Get the global retriever, or None if not configured."""
    return _retriever
