"""Vector store using sqlite-vec for document chunk storage and retrieval."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import numpy as np
import sqlite_vec

logger = logging.getLogger(__name__)


class DocStore:
    """Manages vector storage for document chunks using sqlite-vec.

    Uses a dedicated SQLite database file with the sqlite-vec extension
    for efficient vector similarity search. The store keeps metadata
    (source, title, page) alongside vectors for rich retrieval results.

    Usage::

        store = DocStore(Path("doc_store.sqlite"), embed_dims=768)
        store.insert_document("tia_manual.pdf", "TIA Portal Manual", 120, chunks)
        results = store.search(query_embedding, top_k=5)
    """

    def __init__(self, db_path: Path, embed_dims: int = 768) -> None:
        self.db_path = Path(db_path)
        self.embed_dims = embed_dims
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Create a connection with sqlite-vec extension loaded."""
        db = sqlite3.connect(str(self.db_path))
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._connect()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables if they don't exist.

        If the vector table exists with mismatched dimensions, drop and
        recreate it (old indexed data is incompatible and must be re-indexed).
        """
        with self._connect() as conn:
            # Check for dimension mismatch in existing vec table
            try:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='doc_vecs'"
                ).fetchone()
                if row and f"float[{self.embed_dims}]" not in row[0]:
                    logger.warning(
                        "Dimension mismatch in doc_vecs (expected %d). Dropping and recreating.",
                        self.embed_dims,
                    )
                    conn.execute("DROP TABLE IF EXISTS doc_vecs")
                    conn.execute("DELETE FROM doc_chunks")
                    conn.execute("DELETE FROM doc_meta")
            except Exception:
                logger.debug("Could not check existing vec table dimensions", exc_info=True)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_meta (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL UNIQUE,
                    title TEXT,
                    page_count INTEGER DEFAULT 0,
                    chunk_count INTEGER DEFAULT 0,
                    content_hash TEXT,
                    indexed_at TEXT DEFAULT (datetime('now'))
                )
            """)

            # Migrate pre-content_hash databases: add the column if missing so
            # existing stores upgrade in place (old rows get NULL → treated as
            # "hash unknown" → re-indexed once, then stable).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(doc_meta)")}
            if "content_hash" not in cols:
                conn.execute("ALTER TABLE doc_meta ADD COLUMN content_hash TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL,
                    page INTEGER,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY (doc_id) REFERENCES doc_meta(id) ON DELETE CASCADE
                )
            """)
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS doc_vecs
                USING vec0(embedding float[{self.embed_dims}])
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON doc_chunks(doc_id)"
            )
            # Lightweight key/value meta (e.g. which embedding model indexed the
            # data) — survives a data clear so a model claim persists.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS store_meta ("
                "key TEXT PRIMARY KEY, value TEXT)"
            )
            # Persistent embedding cache, keyed by (text_hash, model). Lets the
            # embedder skip re-encoding unchanged text across restarts, and the
            # model key means a model change never returns another model's
            # vectors (different embedding space).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS embedding_cache ("
                "text_hash TEXT NOT NULL, model TEXT NOT NULL, "
                "embedding BLOB NOT NULL, "
                "PRIMARY KEY (text_hash, model))"
            )
            conn.commit()

    def insert_document(
        self,
        source: str,
        title: str | None,
        page_count: int,
        chunks: list[dict],
        content_hash: str | None = None,
    ) -> int:
        """Insert a document with its chunks and pre-computed embeddings.

        If a document with the same source already exists, it is replaced.

        Args:
            source: Document identifier (filename, URL, etc.).
            title: Human-readable document title.
            page_count: Number of pages in the source document.
            chunks: List of dicts with keys:
                - page (int): Page number (or None).
                - chunk_index (int): Sequence index within the document.
                - text (str): Chunk text content.
                - embedding (list[float]): Pre-computed embedding vector.
            content_hash: Optional content hash (e.g. SHA-256 of the source
                file). Stored so re-indexing can skip unchanged files and
                refresh changed ones instead of always skipping by name.

        Returns:
            The doc_id of the inserted document.
        """
        with self._get_conn() as conn:
            doc_id = self._insert_doc(
                conn, source, title, page_count, chunks, content_hash
            )
            conn.commit()
            logger.info("Indexed document '%s': %d chunks", source, len(chunks))
            return doc_id  # type: ignore[return-value]

    def insert_documents_batch(
        self,
        docs: list[dict],
    ) -> int:
        """Insert multiple documents in a single transaction.

        Much faster than calling ``insert_document`` in a loop because
        only one ``COMMIT`` is issued for all documents.

        Args:
            docs: List of dicts, each with keys:
                - source (str): Document identifier.
                - title (str | None): Human-readable title.
                - page_count (int): Number of pages.
                - chunks (list[dict]): Chunks with embedding vectors.
                - content_hash (str | None, optional): Content hash for
                  change detection on re-index.

        Returns:
            Total number of chunks inserted.
        """
        total_chunks = 0
        with self._get_conn() as conn:
            for doc in docs:
                n = len(doc["chunks"])
                self._insert_doc(
                    conn,
                    doc["source"],
                    doc["title"],
                    doc["page_count"],
                    doc["chunks"],
                    doc.get("content_hash"),
                )
                total_chunks += n
            conn.commit()
        logger.info(
            "Batch inserted %d documents, %d chunks",
            len(docs),
            total_chunks,
        )
        return total_chunks

    def _insert_doc(
        self,
        conn: sqlite3.Connection,
        source: str,
        title: str | None,
        page_count: int,
        chunks: list[dict],
        content_hash: str | None = None,
    ) -> int:
        """Insert a single document (no commit — caller commits)."""
        # Remove existing document with same source
        existing = conn.execute(
            "SELECT id FROM doc_meta WHERE source = ?", [source]
        ).fetchone()
        if existing:
            self._delete_doc(conn, existing[0])

        cursor = conn.execute(
            "INSERT INTO doc_meta(source, title, page_count, chunk_count, "
            "content_hash) VALUES(?, ?, ?, ?, ?)",
            [source, title, page_count, len(chunks), content_hash],
        )
        doc_id = cursor.lastrowid

        for chunk in chunks:
            cursor = conn.execute(
                "INSERT INTO doc_chunks(doc_id, page, chunk_index, text) "
                "VALUES(?, ?, ?, ?)",
                [doc_id, chunk["page"], chunk["chunk_index"], chunk["text"]],
            )
            chunk_id = cursor.lastrowid

            embedding = np.array(chunk["embedding"], dtype=np.float32)
            conn.execute(
                "INSERT INTO doc_vecs(rowid, embedding) VALUES(?, ?)",
                [chunk_id, embedding.tobytes()],
            )

        return doc_id  # type: ignore[return-value]

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        source_filter: str | None = None,
    ) -> list[dict]:
        """Search for similar document chunks using vector similarity.

        Args:
            query_embedding: Query vector (same dimensionality as stored vectors).
            top_k: Maximum number of results to return.
            source_filter: Optional source filename to filter results.

        Returns:
            List of dicts with keys:
                id, source, title, page, chunk_index, text, distance
        """
        with self._get_conn() as conn:
            query_vec = np.array(query_embedding, dtype=np.float32).tobytes()

            # sqlite-vec requires LIMIT directly on the vec0 virtual table scan.
            # Use a subquery so the KNN search sees the LIMIT constraint.
            #
            # The source filter is pushed INSIDE the KNN (as a rowid constraint)
            # rather than applied after. Applying it after the LIMIT would return
            # the top_k nearest vectors across ALL documents and then discard the
            # ones not in the source — so a source whose chunks sit just outside
            # the global top_k would come back short or empty despite having
            # valid hits. Restricting the rowid set first makes the LIMIT and
            # ORDER apply within the filtered source.
            sql = """
                SELECT c.id, m.source, m.title, c.page,
                       c.chunk_index, c.text, sub.distance
                FROM (
                    SELECT rowid, distance
                    FROM doc_vecs
                    WHERE embedding MATCH ?
            """
            params: list = [query_vec]

            if source_filter:
                sql += (
                    " AND rowid IN ("
                    "SELECT c.id FROM doc_chunks c "
                    "JOIN doc_meta m ON c.doc_id = m.id "
                    "WHERE m.source = ?)"
                )
                params.append(source_filter)

            sql += """
                    ORDER BY distance
                    LIMIT ?
                ) sub
                JOIN doc_chunks c ON sub.rowid = c.id
                JOIN doc_meta m ON c.doc_id = m.id
                ORDER BY sub.distance
            """
            params.append(top_k)

            rows = conn.execute(sql, params).fetchall()

            return [
                {
                    "id": row[0],
                    "source": row[1],
                    "title": row[2],
                    "page": row[3],
                    "chunk_index": row[4],
                    "text": row[5],
                    "distance": row[6],
                }
                for row in rows
            ]

    def delete_document(self, source: str) -> bool:
        """Delete a document and all its chunks by source identifier.

        Returns:
            True if a document was deleted, False if not found.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM doc_meta WHERE source = ?", [source]
            ).fetchone()
            if not row:
                return False
            self._delete_doc(conn, row[0])
            conn.commit()
            logger.info("Deleted document '%s'", source)
            return True

    def _delete_doc(self, conn: sqlite3.Connection, doc_id: int) -> None:
        """Delete a document's vectors and metadata (no commit)."""
        chunk_ids = conn.execute(
            "SELECT id FROM doc_chunks WHERE doc_id = ?", [doc_id]
        ).fetchall()
        for (chunk_id,) in chunk_ids:
            conn.execute("DELETE FROM doc_vecs WHERE rowid = ?", [chunk_id])
        conn.execute("DELETE FROM doc_meta WHERE id = ?", [doc_id])

    def list_documents(self) -> list[dict]:
        """List all indexed documents with metadata.

        Returns:
            List of dicts: id, source, title, page_count, chunk_count,
            content_hash, indexed_at.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, source, title, page_count, chunk_count, content_hash, "
                "indexed_at FROM doc_meta ORDER BY indexed_at DESC"
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "source": r[1],
                    "title": r[2],
                    "page_count": r[3],
                    "chunk_count": r[4],
                    "content_hash": r[5],
                    "indexed_at": r[6],
                }
                for r in rows
            ]

    def get_content_hashes(self) -> dict[str, str | None]:
        """Return ``{source: content_hash}`` for every indexed document.

        Used by the indexer to decide whether a file is unchanged (skip),
        changed (re-index), or new (index). A ``None`` hash means the document
        predates content-hash tracking and should be re-indexed once.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT source, content_hash FROM doc_meta"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def get_stats(self) -> dict:
        """Get statistics about the vector store."""
        with self._get_conn() as conn:
            doc_count = conn.execute("SELECT COUNT(*) FROM doc_meta").fetchone()[0]
            chunk_count = conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0]
            return {
                "document_count": doc_count,
                "chunk_count": chunk_count,
                "embed_dims": self.embed_dims,
            }

    def get_meta(self, key: str) -> str | None:
        """Read a value from the lightweight ``store_meta`` key/value table."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM store_meta WHERE key = ?", [key]
            ).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Write (upsert) a value to the ``store_meta`` key/value table."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO store_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [key, value],
            )
            conn.commit()

    def clear(self) -> None:
        """Delete every document, chunk, and vector — but keep ``store_meta``.

        Used when the stored embeddings are invalidated (e.g. the embedding
        model changed, so the vectors belong to a different embedding space).
        Meta survives so the recorded model claim persists through the wipe.
        """
        with self._get_conn() as conn:
            conn.execute("DELETE FROM doc_vecs")
            conn.execute("DELETE FROM doc_chunks")
            conn.execute("DELETE FROM doc_meta")
            conn.commit()
        logger.info("Cleared all documents and chunks from the vector store")

    # ---- persistent embedding cache (text_hash, model) -> vector -------------

    def get_cached_embedding(
        self, text_hash: str, model: str
    ) -> list[float] | None:
        """Read one cached embedding for ``(text_hash, model)``, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT embedding FROM embedding_cache "
                "WHERE text_hash = ? AND model = ?",
                [text_hash, model],
            ).fetchone()
            if not row:
                return None
            return np.frombuffer(row[0], dtype=np.float32).tolist()

    def get_cached_embeddings(
        self, text_hashes: list[str], model: str
    ) -> dict[str, list[float]]:
        """Batch-read cached embeddings.

        Returns ``{text_hash: vector}`` for hashes that are present; misses are
        simply absent from the dict.
        """
        if not text_hashes:
            return {}
        # Pass the hashes as a JSON array parameter and match via json_each, so
        # the query string is fully static (no string-building → no injection
        # surface, and bandit S608 stays quiet).
        import json

        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT text_hash, embedding FROM embedding_cache "
                "WHERE model = ? AND text_hash IN ("
                "SELECT value FROM json_each(?))",
                [model, json.dumps(text_hashes)],
            ).fetchall()
            return {
                r[0]: np.frombuffer(r[1], dtype=np.float32).tolist() for r in rows
            }

    def put_cached_embedding(
        self, text_hash: str, model: str, embedding: list[float]
    ) -> None:
        """Upsert one cached embedding."""
        self.put_cached_embeddings([(text_hash, embedding)], model)

    def put_cached_embeddings(
        self, items: list[tuple[str, list[float]]], model: str
    ) -> None:
        """Upsert many cached embeddings.

        ``items`` is ``[(text_hash, vector), ...]``.
        """
        if not items:
            return
        rows = [
            (text_hash, model, np.asarray(vec, dtype=np.float32).tobytes())
            for text_hash, vec in items
        ]
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT INTO embedding_cache(text_hash, model, embedding) "
                "VALUES(?, ?, ?) ON CONFLICT(text_hash, model) "
                "DO UPDATE SET embedding = excluded.embedding",
                rows,
            )
            conn.commit()

    def clear_embedding_cache(self, model: str | None = None) -> None:
        """Clear cached embeddings, optionally limited to one model."""
        with self._get_conn() as conn:
            if model is None:
                conn.execute("DELETE FROM embedding_cache")
            else:
                conn.execute(
                    "DELETE FROM embedding_cache WHERE model = ?", [model]
                )
            conn.commit()
