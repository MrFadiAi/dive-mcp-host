"""Text chunking utilities for RAG document processing."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tiktoken

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default encoding for token counting
# cl100k_base works for GPT-4/3.5 and most modern embedding models
_DEFAULT_ENCODING = "cl100k_base"


def _get_token_encoder(encoding_name: str = _DEFAULT_ENCODING) -> tiktoken.Encoding:
    """Get a tiktoken encoder, falling back to a simple estimator."""
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        logger.warning("Failed to load tiktoken encoding '%s', using fallback", encoding_name)
        return None


def count_tokens(text: str, encoding: tiktoken.Encoding | None = None) -> int:
    """Count the number of tokens in a text string.

    Args:
        text: Input text.
        encoding: Optional tiktoken encoder. Uses default if not provided.

    Returns:
        Approximate token count.
    """
    if encoding is None:
        encoding = _get_token_encoder()
    if encoding is not None:
        return len(encoding.encode(text))
    # Fallback: ~4 chars per token (rough estimate)
    return len(text) // 4


def chunk_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
    encoding_name: str = _DEFAULT_ENCODING,
) -> list[str]:
    """Split text into overlapping chunks based on token count.

    Uses tiktoken for accurate token counting. Splits on paragraph
    boundaries when possible, falling back to sentence boundaries,
    then word boundaries.

    Args:
        text: Input text to chunk.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Number of overlapping tokens between chunks.
        encoding_name: Tiktoken encoding name for token counting.

    Returns:
        List of text chunks.
    """
    if not text.strip():
        return []

    encoding = _get_token_encoder(encoding_name)

    # If text fits in one chunk, return as-is
    total_tokens = count_tokens(text, encoding)
    if total_tokens <= chunk_size:
        return [text.strip()]

    # Split into paragraphs first
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_tokens = count_tokens(para, encoding)

        # If a single paragraph exceeds chunk_size, split it further
        if para_tokens > chunk_size:
            # Flush current chunk
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_tokens = 0

            # Split large paragraph by sentences
            sub_chunks = _split_large_text(para, chunk_size, chunk_overlap, encoding)
            chunks.extend(sub_chunks)
            continue

        # Check if adding this paragraph would exceed the limit
        if current_tokens + para_tokens > chunk_size and current_chunk:
            chunks.append("\n\n".join(current_chunk))

            # Keep overlap from the end of current chunk
            overlap_text = "\n\n".join(current_chunk)
            overlap_tokens = count_tokens(overlap_text, encoding)

            # Keep sentences from the end for overlap
            if chunk_overlap > 0 and overlap_tokens > chunk_overlap:
                overlap_chunks = _keep_last_tokens(
                    overlap_text, chunk_overlap, encoding
                )
                current_chunk = [overlap_chunks] if overlap_chunks else []
                current_tokens = (
                    count_tokens(overlap_chunks, encoding) if overlap_chunks else 0
                )
            else:
                current_chunk = []
                current_tokens = 0

        current_chunk.append(para)
        current_tokens += para_tokens

    # Flush remaining
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    logger.debug("Chunked text (%d tokens) into %d chunks", total_tokens, len(chunks))
    return chunks


def _split_large_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    encoding: tiktoken.Encoding | None,
) -> list[str]:
    """Split a large text block by sentences, respecting token limits.

    A single sentence that itself exceeds ``chunk_size`` (e.g. a run-on with
    no terminator) is handed to :func:`_split_by_words` so the size limit is
    still honoured — without that fallback the whole sentence became one
    oversized chunk.
    """
    # Split on sentence boundaries
    sentences = _SENTENCE_BOUNDARY.split(text)

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sent_tokens = count_tokens(sentence, encoding)

        # A single sentence larger than chunk_size cannot stay atomic — split it
        # by words so chunk_size remains a hard ceiling for splittable text.
        if sent_tokens > chunk_size:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            word_chunks = _split_by_words(
                sentence, chunk_size, chunk_overlap, encoding
            )
            chunks.extend(word_chunks)
            continue

        if current_tokens + sent_tokens > chunk_size and current:
            chunks.append(" ".join(current))
            # Handle overlap
            if chunk_overlap > 0:
                overlap_text = " ".join(current)
                kept = _keep_last_tokens(overlap_text, chunk_overlap, encoding)
                current = [kept] if kept else []
                current_tokens = count_tokens(kept, encoding) if kept else 0
            else:
                current = []
                current_tokens = 0

        current.append(sentence)
        current_tokens += sent_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks


def _split_by_words(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    encoding: tiktoken.Encoding | None,
) -> list[str]:
    """Word-level fallback splitter — guarantees ``chunk_size`` is a hard ceiling.

    Used when no sentence boundary is available (e.g. token streams, run-on
    text). Word boundaries are atomic, so a single very long word can overshoot
    by a few tokens; for normal prose every chunk stays within the limit.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for word in words:
        word_tokens = count_tokens(word, encoding)
        if current_tokens + word_tokens > chunk_size and current:
            chunks.append(" ".join(current))
            # Keep the last chunk_overlap tokens for overlap context.
            if chunk_overlap > 0:
                kept: list[str] = []
                kept_tokens = 0
                for w in reversed(current):
                    wtk = count_tokens(w, encoding)
                    if kept_tokens + wtk > chunk_overlap:
                        break
                    kept.insert(0, w)
                    kept_tokens += wtk
                current = kept
                current_tokens = kept_tokens
            else:
                current = []
                current_tokens = 0

        current.append(word)
        current_tokens += word_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _keep_last_tokens(
    text: str,
    max_tokens: int,
    encoding: tiktoken.Encoding | None,
) -> str:
    r"""Keep the last N tokens from text, splitting on sentence boundaries.

    Splits on any sentence-ending punctuation followed by whitespace — including
    the ``\n\n`` separators produced when paragraphs are joined. The previous
    ``text.split(". ")`` only matched period-space, so for paragraph-joined text
    (periods followed by newlines) it found no boundary, produced one over-budget
    "sentence", and returned ``""`` — silently disabling overlap.
    """
    sentences = _SENTENCE_BOUNDARY.split(text)
    result: list[str] = []
    token_count = 0

    # Work backwards through sentences
    for sentence in reversed(sentences):
        sent_tokens = count_tokens(sentence, encoding)
        if token_count + sent_tokens > max_tokens:
            break
        result.insert(0, sentence)
        token_count += sent_tokens

    # Sentences retain their trailing punctuation (the split consumes only the
    # whitespace), so rejoin with a plain space — not ". ", which would double
    # the periods.
    return " ".join(result).strip()
