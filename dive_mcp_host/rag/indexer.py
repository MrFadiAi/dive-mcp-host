"""Document indexing pipeline: PDF/text → chunks → embeddings → vector store."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from dive_mcp_host.rag.chunker import chunk_text
from dive_mcp_host.rag.vector_store import DocStore

if TYPE_CHECKING:
    from dive_mcp_host.rag.embedder import Embedder

logger = logging.getLogger(__name__)

# Supported file extensions
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".rst", ".html", ".htm"}

# Module-level indexing progress state (read by /api/docs/progress)
_indexing_state: dict = {}


def _file_hash(file_path: Path) -> str:
    """Return the SHA-256 hex digest of a file's raw bytes.

    Used to detect whether a source file changed since it was last indexed, so
    re-indexing can skip unchanged files and refresh changed ones instead of
    skipping every already-known filename (which left stale chunks in place).
    """
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _extract_text_from_pdf(pdf_path: Path) -> list[dict]:
    """Extract text from a PDF file, page by page.

    Returns:
        List of dicts with keys: page (1-based), text.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        msg = (
            "PyMuPDF is required for PDF indexing. "
            "Install it with: pip install pymupdf"
        )
        raise ImportError(msg) from None

    doc = fitz.open(str(pdf_path))
    pages: list[dict] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if text.strip():
            pages.append({"page": page_num + 1, "text": text})
    doc.close()
    return pages


def _extract_text_from_file(file_path: Path) -> list[dict]:
    """Extract text from a plain text or markdown file.

    Returns:
        List with a single dict: page=1, text=content.
    """
    # Try UTF-8 first, fall back to latin-1
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = file_path.read_text(encoding="latin-1")

    return [{"page": 1, "text": content}]


def extract_pages(file_path: Path) -> list[dict]:
    """Extract text pages from a supported document file.

    Args:
        file_path: Path to the document file.

    Returns:
        List of dicts with keys: page (int), text (str).

    Raises:
        ValueError: If the file type is not supported.
    """
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(file_path)
    if suffix in {".txt", ".md", ".markdown", ".rst", ".html", ".htm"}:
        return _extract_text_from_file(file_path)
    msg = f"Unsupported file type: {suffix}. Supported: {SUPPORTED_EXTENSIONS}"
    raise ValueError(msg)


async def index_document(
    file_path: Path,
    store: DocStore,
    embedder: Embedder,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> int:
    """Index a single document file into the vector store.

    Pipeline: extract text → chunk → embed → store.

    Args:
        file_path: Path to the document.
        store: Vector store instance.
        embedder: Embedder instance.
        chunk_size: Max tokens per chunk.
        chunk_overlap: Overlap tokens between chunks.

    Returns:
        Number of chunks indexed.
    """
    logger.info("Indexing document: %s", file_path.name)

    # Step 1: Extract text
    pages = extract_pages(file_path)
    if not pages:
        logger.warning("No text extracted from %s", file_path.name)
        return 0

    # Step 2: Chunk each page
    all_chunks: list[dict] = []
    chunk_index = 0

    for page_data in pages:
        page_num = page_data["page"]
        page_text = page_data["text"]

        text_chunks = chunk_text(
            page_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for text in text_chunks:
            all_chunks.append({
                "page": page_num,
                "chunk_index": chunk_index,
                "text": text,
            })
            chunk_index += 1

    if not all_chunks:
        logger.warning("No chunks produced from %s", file_path.name)
        return 0

    # Step 3: Embed all chunks in batches
    texts = [c["text"] for c in all_chunks]
    logger.info("Embedding %d chunks from %s...", len(texts), file_path.name)
    embeddings = await embedder.embed_texts(texts)

    # Step 4: Combine chunks with embeddings and store
    chunks_with_embeddings = []
    for chunk, embedding in zip(all_chunks, embeddings):
        chunks_with_embeddings.append({
            "page": chunk["page"],
            "chunk_index": chunk["chunk_index"],
            "text": chunk["text"],
            "embedding": embedding,
        })

    page_count = max(p["page"] for p in pages)
    title = file_path.stem.replace("_", " ").replace("-", " ").title()

    store.insert_document(
        source=file_path.name,
        title=title,
        page_count=page_count,
        chunks=chunks_with_embeddings,
        content_hash=_file_hash(file_path),
    )

    logger.info(
        "Indexed %s: %d pages, %d chunks",
        file_path.name,
        page_count,
        len(chunks_with_embeddings),
    )
    return len(chunks_with_embeddings)


def get_indexing_progress() -> dict:
    """Return the current indexing progress state."""
    return dict(_indexing_state)


async def index_directory(
    directory: Path,
    store: DocStore,
    embedder: Embedder,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
    recursive: bool = True,
    file_batch_size: int = 50,
) -> dict:
    """Index all supported documents in a directory using batched pipeline.

    Processes files in small batches for responsive progress:
    1. Read & chunk ``file_batch_size`` files at once (fast I/O)
    2. Embed all chunks from the batch in a single model call
    3. Bulk-insert all documents in one DB transaction
    4. Yield to event loop between files so progress API stays responsive

    Args:
        directory: Directory to scan for documents.
        store: Vector store instance.
        embedder: Embedder instance.
        chunk_size: Max tokens per chunk.
        chunk_overlap: Overlap tokens between chunks.
        recursive: Whether to scan subdirectories.
        file_batch_size: Number of files to process per embedding batch.

    Returns:
        Summary dict: indexed, skipped, errors, total_chunks.
    """
    global _indexing_state  # noqa: PLW0603

    logger.info("Scanning directory: %s (recursive=%s)", directory, recursive)

    # Collect files
    if recursive:
        files = [
            f
            for f in directory.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    else:
        files = [
            f
            for f in directory.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

    total_files = len(files)
    if total_files == 0:
        logger.warning("No supported files found in %s", directory)
        return {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}

    # Check which files are already indexed and unchanged. A file is skipped
    # only when its name is known AND its content hash matches — editing a
    # document and re-indexing must refresh it, not silently keep stale chunks.
    existing_hashes = store.get_content_hashes()

    pending_files: list[Path] = []
    file_hashes: dict[Path, str] = {}
    skipped = 0
    for f in sorted(files):
        fhash = _file_hash(f)
        if f.name in existing_hashes and existing_hashes[f.name] == fhash:
            skipped += 1
            continue
        pending_files.append(f)
        file_hashes[f] = fhash

    # Initialize progress state
    _indexing_state = {
        "active": True,
        "total": total_files,
        "indexed": 0,
        "skipped": skipped,
        "errors": 0,
        "total_chunks": 0,
        "phase": "loading",
    }

    indexed = 0
    errors = 0
    total_chunks = 0
    processed = 0

    # Pre-warm the embedding model (loads into memory, may take 30-60s first time)
    logger.info("Pre-loading embedding model...")
    try:
        # Use a dummy embed call which handles model loading in executor
        await embedder.embed_texts(["__warmup__"])
        # Discard the warmup embedding — just needed to load the model
    except Exception:
        logger.exception("Failed to load embedding model")
        _indexing_state["active"] = False
        _indexing_state["phase"] = "done"
        return {"indexed": 0, "skipped": skipped, "errors": 1, "total_chunks": 0}
    logger.info("Embedding model ready")
    _indexing_state["phase"] = "reading"

    try:
        for batch_start in range(0, len(pending_files), file_batch_size):
            batch_files = pending_files[batch_start : batch_start + file_batch_size]

            # Step 1: Read & chunk all files in this batch
            _indexing_state["phase"] = "reading"
            batch_docs: list[dict] = []
            for file_path in batch_files:
                try:
                    pages = extract_pages(file_path)
                    if not pages:
                        skipped += 1
                        _indexing_state["skipped"] = skipped
                        processed += 1
                        _indexing_state["indexed"] = indexed
                        _indexing_state["total_chunks"] = total_chunks
                        await asyncio.sleep(0)  # yield to event loop
                        continue

                    all_chunks: list[dict] = []
                    chunk_index = 0
                    for page_data in pages:
                        text_chunks = chunk_text(
                            page_data["text"],
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                        )
                        for text in text_chunks:
                            all_chunks.append({
                                "page": page_data["page"],
                                "chunk_index": chunk_index,
                                "text": text,
                            })
                            chunk_index += 1

                    if not all_chunks:
                        skipped += 1
                        _indexing_state["skipped"] = skipped
                        processed += 1
                        _indexing_state["indexed"] = indexed
                        _indexing_state["total_chunks"] = total_chunks
                        await asyncio.sleep(0)
                        continue

                    page_count = max(p["page"] for p in pages)
                    title = file_path.stem.replace("_", " ").replace("-", " ").title()

                    batch_docs.append({
                        "source": file_path.name,
                        "title": title,
                        "page_count": page_count,
                        "chunks_text": all_chunks,
                        "content_hash": file_hashes[file_path],
                    })
                except Exception:
                    logger.exception("Error reading %s", file_path.name)
                    errors += 1
                    _indexing_state["errors"] = errors

                processed += 1
                await asyncio.sleep(0)  # yield so /progress can respond

            if not batch_docs:
                _indexing_state["indexed"] = indexed
                _indexing_state["total_chunks"] = total_chunks
                continue

            # Step 2: Embed all chunks from all docs in one call
            _indexing_state["phase"] = "embedding"
            await asyncio.sleep(0)  # ensure event loop processes the phase update

            all_texts = []
            for doc in batch_docs:
                all_texts.extend(c["text"] for c in doc["chunks_text"])

            try:
                all_embeddings = await embedder.embed_texts(all_texts)
            except Exception:
                logger.exception("Embedding failed for batch starting at %d", batch_start)
                errors += len(batch_docs)
                _indexing_state["errors"] = errors
                continue

            # Step 3: Distribute embeddings back to documents
            embed_offset = 0
            ready_docs: list[dict] = []
            for doc in batch_docs:
                n = len(doc["chunks_text"])
                chunks_with_embeddings = []
                for j, chunk in enumerate(doc["chunks_text"]):
                    chunks_with_embeddings.append({
                        "page": chunk["page"],
                        "chunk_index": chunk["chunk_index"],
                        "text": chunk["text"],
                        "embedding": all_embeddings[embed_offset + j],
                    })
                embed_offset += n
                ready_docs.append({
                    "source": doc["source"],
                    "title": doc["title"],
                    "page_count": doc["page_count"],
                    "content_hash": doc.get("content_hash"),
                    "chunks": chunks_with_embeddings,
                })

            # Step 4: Bulk insert into DB
            _indexing_state["phase"] = "saving"
            await asyncio.sleep(0)

            try:
                store.insert_documents_batch(ready_docs)
                batch_chunks = sum(len(d["chunks"]) for d in ready_docs)
                indexed += len(ready_docs)
                total_chunks += batch_chunks
            except Exception:
                logger.exception("DB insert failed for batch starting at %d", batch_start)
                errors += len(ready_docs)
                _indexing_state["errors"] = errors

            # Update progress state
            _indexing_state["indexed"] = indexed
            _indexing_state["total_chunks"] = total_chunks
            _indexing_state["phase"] = "reading"

            # Log progress
            pct = processed / total_files * 100
            logger.info(
                "Progress: %d/%d files (%.1f%%) — %d indexed, %d chunks, %d errors",
                processed,
                total_files,
                pct,
                indexed,
                total_chunks,
                errors,
            )
    finally:
        _indexing_state["active"] = False
        _indexing_state["phase"] = "done"

    logger.info(
        "Directory indexing complete: %d indexed, %d skipped, %d errors, %d total chunks",
        indexed,
        skipped,
        errors,
        total_chunks,
    )
    return {
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "total_chunks": total_chunks,
    }
