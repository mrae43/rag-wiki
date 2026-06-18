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
    seed = await _make_entity(db, "Lonely")
    result = await traverse([seed.id], db)
    assert result.entities == []
    assert result.relations == []
    assert result.hop_map == {seed.id: 0}


@pytest.mark.asyncio
async def test_traversal_empty_seeds(db: AsyncSession) -> None:
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
async def test_traversal_bidirectional_from_target(db: AsyncSession) -> None:
    # seed is the target of a relation.
    source = await _make_entity(db, "Source")
    seed = await _make_entity(db, "Seed")
    await _make_relation(db, source, seed)

    result = await traverse([seed.id], db)
    assert result.hop_map[source.id] == 1
    assert len(result.entities) == 1
    assert len(result.relations) == 1
