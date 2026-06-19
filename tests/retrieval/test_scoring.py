"""tests/retrieval/test_scoring
----------------------------
Unit tests for shared scoring and budget utilities.
"""

from __future__ import annotations

import pytest

from rag_wiki.db.models.source import Chunk
from rag_wiki.retrieval.scoring import (
    cosine_similarity,
    deduplicate_chunks,
    estimate_tokens,
    score_chunks,
    truncate_to_budget,
)


class _FakeEmbedProvider:
    """Return deterministic embeddings for testing."""

    def __init__(self, dim: int = 3) -> None:
        self.dim = dim

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical() -> None:
    """Verify cosine_similarity returns 1.0 for identical vectors."""
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    """Verify cosine_similarity returns 0.0 for orthogonal vectors."""
    assert cosine_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite() -> None:
    """Verify cosine_similarity returns -1.0 for opposite vectors."""
    assert cosine_similarity([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_dimension_mismatch_raises() -> None:
    """Verify cosine_similarity raises ValueError when dimensions differ."""
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_basic() -> None:
    """Verify estimate_tokens counts tokens as ceil(len/4)."""
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


def test_estimate_tokens_empty() -> None:
    """Verify estimate_tokens returns 1 for empty string (always at least 1 token)."""
    assert estimate_tokens("") == 1


# ---------------------------------------------------------------------------
# truncate_to_budget
# ---------------------------------------------------------------------------


def test_truncate_to_budget_exact_fit() -> None:
    """Verify truncate_to_budget returns all items when sum fits budget exactly."""
    items = ["aaaa", "bbbb", "cccc"]
    assert truncate_to_budget(items, 3) == items


def test_truncate_to_budget_one_over() -> None:
    """Verify truncate_to_budget drops the last item when budget is exceeded by one."""
    items = ["aaaa", "bbbb", "cccc"]
    assert truncate_to_budget(items, 2) == ["aaaa", "bbbb"]


def test_truncate_to_budget_empty() -> None:
    """Verify truncate_to_budget returns empty list given empty input."""
    assert truncate_to_budget([], 10) == []


def test_truncate_to_budget_single_item() -> None:
    """Verify truncate_to_budget preserves a single item within budget."""
    assert truncate_to_budget(["x"], 1) == ["x"]


# ---------------------------------------------------------------------------
# deduplicate_chunks
# ---------------------------------------------------------------------------


def _make_chunk(text: str, embedding: list[float] | None) -> Chunk:
    """Build a Chunk with the given text and optional embedding for test use."""
    return Chunk(text_content=text, embedding=embedding)


def test_deduplicate_chunks_removes_near_duplicates() -> None:
    """Verify deduplicate_chunks removes chunks above the similarity threshold."""
    a = _make_chunk("a", [1.0, 0.0, 0.0])
    b = _make_chunk("b", [0.9999, 0.0, 0.0])
    c = _make_chunk("c", [0.0, 1.0, 0.0])
    scored = [(1.0, a), (0.9, b), (0.8, c)]
    result = deduplicate_chunks(scored, threshold=0.999)
    assert len(result) == 2
    assert result[0][1] == a
    assert result[1][1] == c


def test_deduplicate_chunks_keeps_diverse() -> None:
    """Verify deduplicate_chunks keeps all chunks when none exceed the threshold."""
    a = _make_chunk("a", [1.0, 0.0, 0.0])
    b = _make_chunk("b", [0.0, 1.0, 0.0])
    c = _make_chunk("c", [0.0, 0.0, 1.0])
    scored = [(1.0, a), (0.9, b), (0.8, c)]
    result = deduplicate_chunks(scored, threshold=0.92)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# score_chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_chunks_empty() -> None:
    """Verify score_chunks returns empty list when given no chunks."""
    provider = _FakeEmbedProvider()
    result = await score_chunks("query", [], provider, model="test")
    assert result == []


@pytest.mark.asyncio
async def test_score_chunks_sorts_descending() -> None:
    """Verify score_chunks returns results sorted by score descending."""
    provider = _FakeEmbedProvider()
    c1 = _make_chunk("foo", [1.0, 0.0, 0.0])
    c2 = _make_chunk("bar", [0.0, 1.0, 0.0])
    result = await score_chunks("q", [c1, c2], provider, model="test")
    assert len(result) == 2
    scores = [r[0] for r in result]
    assert scores[0] >= scores[1]


@pytest.mark.asyncio
async def test_score_chunks_embeds_missing_on_the_fly() -> None:
    """Verify score_chunks embeds chunks that have no pre-computed embedding."""
    provider = _FakeEmbedProvider()
    c1 = _make_chunk("foo", None)
    result = await score_chunks("q", [c1], provider, model="test")
    assert len(result) == 1
    assert c1.embedding is not None


@pytest.mark.asyncio
async def test_score_chunks_skips_dimension_mismatch() -> None:
    """Verify score_chunks skips chunks with a mismatched embedding dimension."""
    provider = _FakeEmbedProvider(dim=3)
    c1 = _make_chunk("foo", [1.0, 0.0])
    result = await score_chunks("q", [c1], provider, model="test")
    assert result == []
