"""tests/retrieval/test_seeds
--------------------------
Integration tests for seed-finding against a real database.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus
from rag_wiki.retrieval.seeds import _seed_quality, find_seeds
from rag_wiki.settings import get_settings


def _embedding(dim: int, value: float = 1.0) -> list[float]:
    return [value] + [0.0] * (dim - 1)


async def _make_entity(
    db: AsyncSession,
    name: str,
    embedding: list[float] | None = None,
) -> Entity:
    ent = Entity(
        name=name,
        entity_type="person",
        description=f"Desc of {name}",
        embedding=embedding,
        status=PublishedStatus.PUBLISHED,
    )
    db.add(ent)
    await db.commit()
    return ent


def test_seed_quality_thresholds() -> None:
    """Verify _seed_quality returns high/low/poor based on cosine-distance threshold."""
    assert _seed_quality(0.15) == "high"
    assert _seed_quality(0.2) == "low"
    assert _seed_quality(0.3) == "low"
    assert _seed_quality(0.4) == "low"
    assert _seed_quality(0.41) == "poor"


@pytest.mark.asyncio
async def test_find_seeds_by_vector_search(db: AsyncSession) -> None:
    """Verify find_seeds ranks entities by embedding distance and assigns quality."""
    dims = get_settings().embedding_dimensions
    # close: parallel to query, distance 0.0
    close_emb = [1.0] + [0.0] * (dims - 1)
    # medium: similarity 0.8, distance 0.2
    medium_emb = [0.8, 0.6] + [0.0] * (dims - 2)
    # far: orthogonal, distance 1.0
    far_emb = [0.0, 1.0] + [0.0] * (dims - 2)

    close = await _make_entity(db, "Close", close_emb)
    medium = await _make_entity(db, "Medium", medium_emb)
    far = await _make_entity(db, "Far", far_emb)

    query_emb = [1.0] + [0.0] * (dims - 1)
    seeds = await find_seeds(query_emb, db)

    assert len(seeds) == 3
    assert seeds[0].entity_id == close.id
    assert seeds[0].seed_quality == "high"
    assert seeds[1].entity_id == medium.id
    # Distance is exactly 0.2 in theory, but float32 rounding in pgvector
    # may push it slightly below the threshold; accept either.
    assert seeds[1].seed_quality in ("high", "low")
    assert seeds[2].entity_id == far.id
    assert seeds[2].seed_quality == "poor"


@pytest.mark.asyncio
async def test_find_seeds_by_entity_ids_bypasses_search(db: AsyncSession) -> None:
    """Verify find_seeds bypasses vector search when seed_entity_ids is provided."""
    dims = get_settings().embedding_dimensions
    ent = await _make_entity(db, "Direct", _embedding(dims, 0.5))

    seeds = await find_seeds(
        query_embedding=[0.0] * dims,
        db=db,
        seed_entity_ids=[ent.id],
    )

    assert len(seeds) == 1
    assert seeds[0].entity_id == ent.id
    assert seeds[0].seed_quality == "high"
    assert seeds[0].similarity_score == 1.0


@pytest.mark.asyncio
async def test_find_seeds_empty_results(db: AsyncSession) -> None:
    """Verify find_seeds returns an empty list when no entities exist."""
    dims = get_settings().embedding_dimensions
    seeds = await find_seeds([0.0] * dims, db)
    assert seeds == []


@pytest.mark.asyncio
async def test_find_seeds_anchor_metadata(db: AsyncSession) -> None:
    """Verify find_seeds populates anchor metadata (name, hop_distance, centrality)."""
    dims = get_settings().embedding_dimensions
    await _make_entity(db, "AnchorTest", _embedding(dims, 1.0))

    seeds = await find_seeds(_embedding(dims, 1.0), db)
    assert len(seeds) == 1
    anchor = seeds[0].anchor
    assert anchor.name == "AnchorTest"
    assert anchor.hop_distance == 0
    assert anchor.relation_summary == "No relations"
    assert anchor.relative_centrality == "low"
