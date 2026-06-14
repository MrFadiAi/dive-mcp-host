"""Invariant tests for ``rag.chunker``.

These tests codify the contract the chunker must hold for RAG retrieval quality:

* **No data loss** — every unique word from the source appears in some chunk.
* **No empty chunks** — whitespace-only chunks waste embedding budget.
* **chunk_size is a hard ceiling** for word-splittable text — a chunk that is
  6x the limit silently degrades retrieval.
* **chunk_overlap actually overlaps** — consecutive chunks must re-include the
  tail of the previous chunk so context is not lost at boundaries.

The overlap and size-ceiling cases were found to be broken before this file
existed; the tests pin the fixes.
"""

from __future__ import annotations

import re

import pytest

from dive_mcp_host.rag.chunker import (
    _keep_last_tokens,
    _split_by_words,
    _split_large_text,
    chunk_text,
    count_tokens,
)


def _sentence_indices(chunk: str) -> set[int]:
    """Extract the ``N`` from ``Sentence N ...`` markers used in test fixtures."""
    return {int(m) for m in re.findall(r"Sentence (\d+)", chunk)}


class TestKeepLastTokens:
    """``_keep_last_tokens`` must split on ANY sentence boundary, not just ``". "``.

    Paragraph-joined text uses ``\\n\\n`` separators, so periods are followed by
    newlines. The old ``text.split(". ")`` found no boundary there and returned a
    single over-budget "sentence" — which then got dropped, silently disabling
    overlap on the main code path.
    """

    def test_handles_newline_separated_sentences(self):
        text = (
            "First sentence is here.\n\n"
            "Second one follows here.\n\n"
            "Third and final sentence."
        )
        kept = _keep_last_tokens(text, max_tokens=20, encoding=None)
        # Must keep the last sentence(s) — not collapse to empty.
        assert kept != ""
        assert "final sentence" in kept

    def test_keeps_only_what_fits_from_the_end(self):
        text = "Alpha sentence.\n\nBeta sentence.\n\nGamma sentence."
        kept = _keep_last_tokens(text, max_tokens=6, encoding=None)
        # The very last sentence must be retained.
        assert "Gamma" in kept
        # Earlier sentences that don't fit are dropped.
        assert "Alpha" not in kept

    def test_returns_empty_when_nothing_fits(self):
        # A single unsplittable block larger than the budget degrades gracefully.
        text = "word" * 200  # no sentence boundary at all
        assert _keep_last_tokens(text, max_tokens=5, encoding=None) == ""


class TestOverlapActuallyOverlaps:
    """Consecutive chunks must share content when ``chunk_overlap > 0``."""

    def test_consecutive_paragraph_chunks_overlap(self):
        paras = [f"Sentence {i} about PLC motors and drives." for i in range(40)]
        text = "\n\n".join(paras)
        chunks = chunk_text(text, chunk_size=40, chunk_overlap=20)
        assert len(chunks) >= 2

        # Overlap in index space: chunk[i+1] must re-start at or before the last
        # sentence index present in chunk[i]. Before the fix the chunks were
        # strictly disjoint (min(idx[i+1]) > max(idx[i])) — overlap was a no-op.
        for i in range(len(chunks) - 1):
            cur = _sentence_indices(chunks[i])
            nxt = _sentence_indices(chunks[i + 1])
            assert cur and nxt, f"chunk {i}/{i + 1} had no sentence markers"
            assert min(nxt) <= max(cur), (
                f"no overlap between chunk {i} (max idx {max(cur)}) and "
                f"chunk {i + 1} (min idx {min(nxt)})"
            )

    def test_overlap_zero_is_disjoint(self):
        # Sanity: with overlap=0 the chunks really are disjoint (proves the test
        # above is meaningful and not trivially true).
        paras = [f"Sentence {i} about PLC motors and drives." for i in range(40)]
        text = "\n\n".join(paras)
        chunks = chunk_text(text, chunk_size=40, chunk_overlap=0)
        for i in range(len(chunks) - 1):
            cur = _sentence_indices(chunks[i])
            nxt = _sentence_indices(chunks[i + 1])
            if cur and nxt:
                assert min(nxt) > max(cur)


class TestChunkSizeCeiling:
    """Word-splittable text must never exceed ``chunk_size`` by more than a word."""

    def test_no_punctuation_long_text_splits_by_words(self):
        # No sentence terminators anywhere — only word boundaries remain.
        text = " ".join(f"word{i}" for i in range(200))
        chunks = chunk_text(text, chunk_size=30, chunk_overlap=0)
        assert len(chunks) > 1
        for c in chunks:
            # Allow a one-word overshoot (atomic word boundary), nothing more.
            assert count_tokens(c) <= 30 + 5, (
                f"chunk of {count_tokens(c)} tokens exceeds chunk_size=30: {c[:40]!r}"
            )

    def test_single_oversized_sentence_splits(self):
        # One giant run-on sentence (no terminator) larger than chunk_size.
        runon = " ".join(f"token{i}" for i in range(100))
        chunks = _split_large_text(runon, chunk_size=20, chunk_overlap=0, encoding=None)
        assert len(chunks) > 1
        for c in chunks:
            assert count_tokens(c) <= 20 + 5


class TestSplitByWords:
    """The word-level fallback used when no sentence boundary exists."""

    def test_respects_size_with_overlap(self):
        text = " ".join(f"w{i}" for i in range(60))
        chunks = _split_by_words(text, chunk_size=20, chunk_overlap=5, encoding=None)
        assert len(chunks) >= 2
        for c in chunks:
            assert count_tokens(c) <= 20 + 5
        # Overlap: the last word of chunk[i] should appear in chunk[i+1].
        for i in range(len(chunks) - 1):
            last = chunks[i].split()[-1]
            assert last in chunks[i + 1].split()

    def test_empty_text_returns_empty(self):
        assert _split_by_words("", chunk_size=10, chunk_overlap=0, encoding=None) == []
        assert _split_by_words("   ", chunk_size=10, chunk_overlap=0, encoding=None) == []


class TestNoDataLoss:
    """Every unique token from the source must survive into some chunk."""

    def test_all_unique_markers_preserved(self):
        text = "\n\n".join(f"Unique{i} content about PLCs." for i in range(30))
        chunks = chunk_text(text, chunk_size=40, chunk_overlap=10)
        joined = " ".join(chunks)
        for i in range(30):
            assert f"Unique{i}" in joined, f"Unique{i} was lost during chunking"


class TestNoEmptyChunks:
    """No chunk may be empty or whitespace-only."""

    @pytest.mark.parametrize(
        "text,size,overlap",
        [
            (" ".join(f"word{i}" for i in range(100)), 30, 10),
            ("\n\n".join(f"P{i} ends." for i in range(50)), 20, 5),
            ("noPunctuationJustWords " * 50, 30, 0),
            ("edge\n\n\n\n   \n\ncase", 500, 0),
        ],
    )
    def test_no_empty_chunks(self, text, size, overlap):
        chunks = chunk_text(text, chunk_size=size, chunk_overlap=overlap)
        assert len(chunks) >= 1
        for c in chunks:
            assert c.strip() != "", "produced an empty/whitespace-only chunk"
