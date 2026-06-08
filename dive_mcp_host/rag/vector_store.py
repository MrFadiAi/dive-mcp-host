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
                    indexed_at TEXT DEFAULT (datetime('now'))
                )
            """)
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
            conn.commit()

    def insert_document(
        self,
        source: str,
        title: str | None,
        page_count: int,
        chunks: list[dict],
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

        Returns:
            The doc_id of the inserted document.
        """
        with self._get_conn() as conn:
            doc_id = self._insert_doc(conn, source, title, page_count, chunks)
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
    ) -> int:
        """Insert a single document (no commit — caller commits)."""
        # Remove existing document with same source
        existing = conn.execute(
            "SELECT id FROM doc_meta WHERE source = ?", [source]
        ).fetchone()
        if existing:
            self._delete_doc(conn, existing[0])

        cursor = conn.execute(
            "INSERT INTO doc_meta(source, title, page_count, chunk_count) "
            "VALUES(?, ?, ?, ?)",
            [source, title, page_count, len(chunks)],
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
            sql = """
                SELECT c.id, m.source, m.title, c.page,
                       c.chunk_index, c.text, sub.distance
                FROM (
                    SELECT rowid, distance
                    FROM doc_vecs
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                ) sub
                JOIN doc_chunks c ON sub.rowid = c.id
                JOIN doc_meta m ON c.doc_id = m.id
            """
            params: list = [query_vec, top_k]

            if source_filter:
                sql += " WHERE m.source = ?"
                params.append(source_filter)

            sql += " ORDER BY sub.distance"

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
            List of dicts: id, source, title, page_count, chunk_count, indexed_at.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, source, title, page_count, chunk_count, indexed_at "
                "FROM doc_meta ORDER BY indexed_at DESC"
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "source": r[1],
                    "title": r[2],
                    "page_count": r[3],
                    "chunk_count": r[4],
                    "indexed_at": r[5],
                }
                for r in rows
            ]

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
