"""API endpoints for RAG document management."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from dive_mcp_host.httpd.dependencies import get_app
from dive_mcp_host.httpd.server import DiveHostAPI

logger = logging.getLogger(__name__)

docs = APIRouter(tags=["docs"])


@docs.get("/progress")
async def get_index_progress() -> dict:
    """Get current indexing progress (total, indexed, errors, active)."""
    from dive_mcp_host.rag.indexer import get_indexing_progress

    return get_indexing_progress()


class DocInfo(BaseModel):
    """Information about an indexed document."""

    source: str
    title: str | None = None
    page_count: int = 0
    chunk_count: int = 0
    indexed_at: str | None = None


class DocStats(BaseModel):
    """Statistics about the document store."""

    document_count: int = 0
    chunk_count: int = 0
    embed_dims: int = 768
    retriever_ready: bool = False
    model_name: str = ""


class ModelSwitchRequest(BaseModel):
    """Request to switch the embedding model."""

    model: str = Field(description="HuggingFace model name, e.g. 'Qwen/Qwen3-Embedding-0.6B'")


class IndexRequest(BaseModel):
    """Request to index documents from a directory."""

    directory: str = Field(description="Path to directory containing documents to index.")
    reindex: bool = Field(default=False, description="Force re-index already indexed documents.")
    chunk_size: int = Field(default=500, description="Maximum tokens per chunk.")
    chunk_overlap: int = Field(default=100, description="Overlap tokens between chunks.")


class IndexResult(BaseModel):
    """Result of an indexing operation."""

    success: bool
    message: str
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    total_chunks: int = 0


class SearchResult(BaseModel):
    """A single search result."""

    source: str
    title: str | None = None
    page: int | None = None
    text: str
    distance: float = 0.0


class SearchResponse(BaseModel):
    """Response from a document search."""

    success: bool
    query: str
    results: list[SearchResult] = []
    formatted: str = ""


@docs.get("/stats", response_model=DocStats)
async def get_doc_stats(
    app: DiveHostAPI = Depends(get_app),
) -> DocStats:
    """Get document store statistics."""
    from dive_mcp_host.rag import get_retriever

    retriever = get_retriever()
    if retriever is None:
        return DocStats(retriever_ready=False)

    stats = retriever.store.get_stats()
    model_name = getattr(retriever.embedder, "model_name", "")
    return DocStats(
        document_count=stats["document_count"],
        chunk_count=stats["chunk_count"],
        embed_dims=stats["embed_dims"],
        retriever_ready=True,
        model_name=model_name,
    )


@docs.post("/model", response_model=dict)
async def switch_model(
    request: ModelSwitchRequest,
    app: DiveHostAPI = Depends(get_app),
) -> dict:
    """Switch the embedding model. Auto re-indexes saved source files."""
    from dive_mcp_host.env import DIVE_CONFIG_DIR
    from dive_mcp_host.rag import get_retriever, init_local_retriever
    from dive_mcp_host.rag.indexer import SUPPORTED_EXTENSIONS, index_document

    # Detect a model change (not just a dimensionality change): switching to a
    # different model invalidates stored embeddings even at the same dimension
    # count, because the vectors live in a different embedding space. init below
    # clears the store in that case; we then re-index saved sources.
    old_retriever = get_retriever()
    old_model = (
        getattr(old_retriever.embedder, "model_name", None) if old_retriever else None
    )

    try:
        new_retriever = init_local_retriever(model_name=request.model)
    except Exception as e:
        logger.exception("Failed to switch model to %s", request.model)
        return {"success": False, "message": f"Failed to load model: {e}"}

    new_dims = new_retriever.store.embed_dims
    model_changed = old_model is not None and old_model != request.model

    # Auto re-index saved source files if the model changed (the store was just
    # cleared by init for a different embedding space).
    reindexed = 0
    total_chunks = 0
    if model_changed:
        source_dir = DIVE_CONFIG_DIR / "rag_sources"
        if source_dir.exists():
            saved_files = [
                f for f in source_dir.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            ]
            for file_path in saved_files:
                try:
                    n_chunks = await index_document(
                        file_path=file_path,
                        store=new_retriever.store,
                        embedder=new_retriever.embedder,
                    )
                    if n_chunks > 0:
                        reindexed += 1
                        total_chunks += n_chunks
                except Exception:
                    logger.exception("Failed to re-index %s", file_path.name)

    msg = f"Switched to {request.model} ({new_dims} dims)"
    if model_changed and reindexed > 0:
        msg += f". Re-indexed {reindexed} documents ({total_chunks} chunks)."
    elif model_changed:
        msg += ". Index cleared (model changed — re-index to rebuild)."

    return {
        "success": True,
        "message": msg,
        "embed_dims": new_dims,
        "reindexed": reindexed,
        "total_chunks": total_chunks,
    }


@docs.get("/list", response_model=list[DocInfo])
async def list_documents(
    app: DiveHostAPI = Depends(get_app),
) -> list[DocInfo]:
    """List all indexed documents."""
    from dive_mcp_host.rag import get_retriever

    retriever = get_retriever()
    if retriever is None:
        return []

    docs_list = retriever.store.list_documents()
    return [
        DocInfo(
            source=d["source"],
            title=d["title"],
            page_count=d["page_count"],
            chunk_count=d["chunk_count"],
            indexed_at=d["indexed_at"],
        )
        for d in docs_list
    ]


@docs.post("/index", response_model=IndexResult)
async def index_documents(
    request: IndexRequest,
    app: DiveHostAPI = Depends(get_app),
) -> IndexResult:
    """Index documents from a directory for RAG retrieval."""
    from dive_mcp_host.rag import get_retriever
    from dive_mcp_host.rag.indexer import index_directory

    retriever = get_retriever()
    if retriever is None:
        return IndexResult(
            success=False,
            message="Embedding model not configured. Configure it in Settings first.",
        )

    doc_dir = Path(request.directory)
    if not doc_dir.exists() or not doc_dir.is_dir():
        return IndexResult(
            success=False,
            message=f"Directory not found: {request.directory}",
        )

    # If reindex, clear existing documents
    if request.reindex:
        existing = retriever.store.list_documents()
        for doc in existing:
            retriever.store.delete_document(doc["source"])

    try:
        result = await index_directory(
            directory=doc_dir,
            store=retriever.store,
            embedder=retriever.embedder,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
        )
        return IndexResult(
            success=True,
            message=f"Indexed {result['indexed']} documents with {result['total_chunks']} chunks.",
            **result,
        )
    except Exception as e:
        logger.exception("Indexing failed")
        return IndexResult(success=False, message=f"Indexing failed: {e}")


@docs.post("/upload", response_model=IndexResult)
async def upload_document(
    file: UploadFile = File(..., description="Document file to upload and index."),
    chunk_size: int = Query(default=500, description="Maximum tokens per chunk."),
    chunk_overlap: int = Query(default=100, description="Overlap tokens between chunks."),
    app: DiveHostAPI = Depends(get_app),
) -> IndexResult:
    """Upload a document file, save it locally, and index it for RAG retrieval."""
    from dive_mcp_host.rag import get_retriever
    from dive_mcp_host.rag.indexer import SUPPORTED_EXTENSIONS, index_document

    retriever = get_retriever()
    if retriever is None:
        return IndexResult(
            success=False,
            message="Embedding model not ready. Please wait for the model to load.",
        )

    # Validate file extension
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        return IndexResult(
            success=False,
            message=f"Unsupported file type: {suffix}. Supported: {supported}",
        )

    # Save uploaded file to persistent storage
    from dive_mcp_host.env import DIVE_CONFIG_DIR

    upload_dir = DIVE_CONFIG_DIR / "rag_sources"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename to avoid path traversal
    safe_name = Path(filename).name
    save_path = upload_dir / safe_name

    content = await file.read()
    save_path.write_bytes(content)
    logger.info("Saved uploaded file: %s (%d bytes)", save_path, len(content))

    # Index the saved file
    try:
        n_chunks = await index_document(
            file_path=save_path,
            store=retriever.store,
            embedder=retriever.embedder,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return IndexResult(
            success=True,
            message=f"Indexed {safe_name}: {n_chunks} chunks.",
            indexed=1 if n_chunks > 0 else 0,
            skipped=0 if n_chunks > 0 else 1,
            total_chunks=n_chunks,
        )
    except Exception as e:
        logger.exception("Failed to index uploaded file: %s", safe_name)
        return IndexResult(success=False, message=f"Failed to index {safe_name}: {e}")


@docs.delete("/delete/{source:path}", response_model=dict)
async def delete_document(
    source: str,
    app: DiveHostAPI = Depends(get_app),
) -> dict:
    """Delete an indexed document by source path."""
    from dive_mcp_host.rag import get_retriever

    retriever = get_retriever()
    if retriever is None:
        return {"success": False, "message": "Retriever not configured."}

    deleted = retriever.store.delete_document(source)
    if deleted:
        return {"success": True, "message": f"Deleted document: {source}"}
    return {"success": False, "message": f"Document not found: {source}"}


@docs.post("/search", response_model=SearchResponse)
async def search_documents(
    query: str = Query(description="Search query"),
    top_k: int = Query(default=5, description="Max results"),
    source_filter: str | None = Query(
        default=None, description="Restrict search to a single source filename."
    ),
    max_distance: float | None = Query(
        default=None,
        description="Relevance ceiling (0-2, lower is more relevant). "
        "Drop results whose distance exceeds this.",
    ),
    app: DiveHostAPI = Depends(get_app),
) -> SearchResponse:
    """Search indexed documents (for testing/debugging, the agent uses the tool directly)."""
    from dive_mcp_host.rag import get_retriever

    retriever = get_retriever()
    if retriever is None:
        return SearchResponse(success=False, query=query, formatted="Retriever not configured.")

    try:
        results = await retriever.search(
            query, top_k=top_k, source_filter=source_filter, max_distance=max_distance
        )
        formatted = retriever.format_results(results, query)
        return SearchResponse(
            success=True,
            query=query,
            results=[
                SearchResult(
                    source=r["source"],
                    title=r.get("title"),
                    page=r.get("page"),
                    text=r["text"],
                    distance=r.get("distance", 0),
                )
                for r in results
            ],
            formatted=formatted,
        )
    except Exception as e:
        logger.exception("Search failed")
        return SearchResponse(success=False, query=query, formatted=f"Search error: {e}")
