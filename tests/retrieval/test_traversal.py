"""tests/retrieval/test_traversal
------------------------------
Integration tests for recursive CTE graph traversal.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus, Relation
from rag_wiki.db.models.source import Chunk, ProcessingStatus, Source
from rag_wiki.retrieval.traversal import traverse
from rag_wiki.settings import Settings


async def _make_entity(db: AsyncSession, name: str) -> Entity:
    ent = Entity(
        name=name,
        entity_type="concept",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(ent)
    await db.commit()
    return ent


async def _make_relation(
    db: AsyncSession,
    source: Entity,
    target: Entity,
    rel_type: str = "relates_to",
) -> Relation:
    src = Source(
        file_path="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    db.add(src)
    await db.commit()

    chunk = Chunk(
        source=src,
        chunk_index=0,
        text_content="test",
        status=ProcessingStatus.PROCESSED,
    )
    db.add(chunk)
    await db.commit()

    rel = Relation(
        source_entity=source,
        target_entity=target,
        relation_type=rel_type,
        chunk=chunk,
        status=PublishedStatus.PUBLISHED,
        confidence_tag="INFERRED",
    )
    db.add(rel)
    await db.commit()
    return rel


@pytest.mark.asyncio
async def test_traversal_bidirectional_two_hops(db: AsyncSession) -> None:
    """Verify traverse walks a two-relation chain bidirectionally with correct hops."""
    # seed -> hop1 -> hop2
    seed = await _make_entity(db, "Seed")
    hop1 = await _make_entity(db, "Hop1")
    hop2 = await _make_entity(db, "Hop2")
    await _make_relation(db, seed, hop1)
    await _make_relation(db, hop1, hop2)

    result = await traverse([seed.id], db)

    # hop_map includes seed and traversed entities.
    assert result.hop_map[seed.id] == 0
    assert result.hop_map[hop1.id] == 1
    assert result.hop_map[hop2.id] == 2
    assert len(result.entities) == 2  # excludes seed
    assert len(result.relations) == 2


@pytest.mark.asyncio
async def test_traversal_no_relations(db: AsyncSession) -> None:
    """Verify traverse returns empty result for a seed with no relations."""
    seed = await _make_entity(db, "Lonely")
    result = await traverse([seed.id], db)
    assert result.entities == []
    assert result.relations == []
    assert result.hop_map == {seed.id: 0}


@pytest.mark.asyncio
async def test_traversal_empty_seeds(db: AsyncSession) -> None:
    """Verify traverse returns empty result when given an empty list of seed IDs."""
    result = await traverse([], db)
    assert result.entities == []
    assert result.relations == []
    assert result.hop_map == {}


@pytest.mark.asyncio
async def test_traversal_total_node_ceiling(db: AsyncSession) -> None:
    # Create a star: seed connected to 5 hop1 entities.
    seed = await _make_entity(db, "StarSeed")
    neighbors = [await _make_entity(db, f"N{i}") for i in range(5)]
    for n in neighbors:
        await _make_relation(db, seed, n)

    result = await traverse([seed.id], db)
    assert len(result.entities) == 5
    assert len(result.relations) == 5


@pytest.mark.asyncio
async def test_traversal_per_hop_limit(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default retrieval_max_neighbors_per_hop is 10; create 15 to exercise it.
    seed = await _make_entity(db, "StarSeed")
    neighbors = [await _make_entity(db, f"N{i}") for i in range(15)]
    for n in neighbors:
        await _make_relation(db, seed, n)

    result = await traverse([seed.id], db)
    assert len(result.entities) == 10
    assert len(result.relations) == 10


@pytest.mark.asyncio
async def test_traversal_total_node_ceiling_boundary(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Raise per-hop limit so the total-node ceiling (50) is the binding constraint.
    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        retrieval_max_neighbors_per_hop=100,
    )
    monkeypatch.setattr(
        "rag_wiki.retrieval.traversal.get_settings",
        lambda: settings,
    )

    seed = await _make_entity(db, "DenseSeed")
    neighbors = [await _make_entity(db, f"N{i}") for i in range(60)]
    for n in neighbors:
        await _make_relation(db, seed, n)

    result = await traverse([seed.id], db)
    assert len(result.entities) == 50
    assert len(result.relations) == 50


@pytest.mark.asyncio
async def test_traversal_bidirectional_from_target(db: AsyncSession) -> None:
    # seed is the target of a relation.
    source = await _make_entity(db, "Source")
    seed = await _make_entity(db, "Seed")
    await _make_relation(db, source, seed)

    result = await traverse([seed.id], db)
    assert result.hop_map[source.id] == 1
    assert len(result.entities) == 1
    assert len(result.relations) == 1
