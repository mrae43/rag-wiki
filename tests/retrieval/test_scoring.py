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
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite() -> None:
    assert cosine_similarity([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_dimension_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_basic() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 1


# ---------------------------------------------------------------------------
# truncate_to_budget
# ---------------------------------------------------------------------------


def test_truncate_to_budget_exact_fit() -> None:
    # Each item costs 1 token (4 chars).
    items = ["aaaa", "bbbb", "cccc"]
    assert truncate_to_budget(items, 3) == items


def test_truncate_to_budget_one_over() -> None:
    items = ["aaaa", "bbbb", "cccc"]
    assert truncate_to_budget(items, 2) == ["aaaa", "bbbb"]


def test_truncate_to_budget_empty() -> None:
    assert truncate_to_budget([], 10) == []


def test_truncate_to_budget_single_item() -> None:
    assert truncate_to_budget(["x"], 1) == ["x"]


# ---------------------------------------------------------------------------
# deduplicate_chunks
# ---------------------------------------------------------------------------


def _make_chunk(text: str, embedding: list[float] | None) -> Chunk:
    return Chunk(text_content=text, embedding=embedding)


def test_deduplicate_chunks_removes_near_duplicates() -> None:
    a = _make_chunk("a", [1.0, 0.0, 0.0])
    b = _make_chunk("b", [0.9999, 0.0, 0.0])  # nearly identical
    c = _make_chunk("c", [0.0, 1.0, 0.0])  # orthogonal
    scored = [(1.0, a), (0.9, b), (0.8, c)]
    result = deduplicate_chunks(scored, threshold=0.999)
    assert len(result) == 2
    assert result[0][1] == a
    assert result[1][1] == c


def test_deduplicate_chunks_keeps_diverse() -> None:
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
    provider = _FakeEmbedProvider()
    result = await score_chunks("query", [], provider, model="test")
    assert result == []


@pytest.mark.asyncio
async def test_score_chunks_sorts_descending() -> None:
    provider = _FakeEmbedProvider()
    c1 = _make_chunk("foo", [1.0, 0.0, 0.0])
    c2 = _make_chunk("bar", [0.0, 1.0, 0.0])
    # Query embedding is [1,0,0] because FakeEmbedProvider returns that.
    result = await score_chunks("q", [c1, c2], provider, model="test")
    assert len(result) == 2
    scores = [r[0] for r in result]
    assert scores[0] >= scores[1]


@pytest.mark.asyncio
async def test_score_chunks_embeds_missing_on_the_fly() -> None:
    provider = _FakeEmbedProvider()
    c1 = _make_chunk("foo", None)
    result = await score_chunks("q", [c1], provider, model="test")
    assert len(result) == 1
    assert c1.embedding is not None


@pytest.mark.asyncio
async def test_score_chunks_skips_dimension_mismatch() -> None:
    provider = _FakeEmbedProvider(dim=3)
    c1 = _make_chunk("foo", [1.0, 0.0])  # wrong dim
    result = await score_chunks("q", [c1], provider, model="test")
    assert result == []
