"""tests/graph/test_merge
---------------------
Unit tests for rag_wiki.graph.merge.
"""

from __future__ import annotations

import uuid
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, EntityMergeLog, PublishedStatus, Relation
from rag_wiki.db.models.source import Chunk, ChunkEntity, Source
from rag_wiki.exceptions import EntityResolutionError
from rag_wiki.graph.merge import merge_entity


async def test_merge_entity_repoints_relations_and_deletes_source(
    db: AsyncSession,
) -> None:
    """Merge two entities and verify relations are re-pointed and source is deleted."""
    # Create source entities.
    entity_a = Entity(
        name="Apple Inc.",
        entity_type="organization",
        description="Tech company",
        status=PublishedStatus.PUBLISHED,
    )
    entity_b = Entity(
        name="Apple Computer",
        entity_type="organization",
        description="Former name of Apple Inc.",
        status=PublishedStatus.PUBLISHED,
    )
    db.add_all([entity_a, entity_b])
    await db.flush()

    # Create a chunk and relation from B → A.
    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(source_id=source.id, chunk_index=0, text_content="test")
    db.add(chunk)
    await db.flush()

    relation = Relation(
        source_entity_id=entity_b.id,
        target_entity_id=entity_a.id,
        relation_type="formerly_known_as",
        chunk_id=chunk.id,
        status=PublishedStatus.PUBLISHED,
    )
    db.add(relation)
    await db.flush()

    # Merge B into A.
    await merge_entity(
        from_id=entity_b.id,
        into_id=entity_a.id,
        chunk_id=chunk.id,
        job_id=None,
        reason="Duplicate entity",
        db=db,
    )
    await db.commit()

    # Verify entity B is gone.
    result = await db.execute(select(Entity).where(Entity.id == entity_b.id))
    assert result.scalar_one_or_none() is None

    # Verify entity A still exists.
    result = await db.execute(select(Entity).where(Entity.id == entity_a.id))
    assert result.scalar_one_or_none() is not None

    # Verify relation was re-pointed: source should now be A.
    result = await db.execute(
        select(Relation).where(Relation.source_entity_id == entity_a.id)
    )
    rel = cast(Relation | None, result.scalar_one_or_none())
    assert rel is not None
    assert rel.target_entity_id == entity_a.id


async def test_merge_entity_deduplicates_chunk_entities(db: AsyncSession) -> None:
    """Merge two entities that both link to the same chunk; dedup removes duplicates."""
    entity_a = Entity(
        name="Apple Inc.",
        entity_type="organization",
        description="Tech company",
        status=PublishedStatus.PUBLISHED,
    )
    entity_b = Entity(
        name="Apple",
        entity_type="organization",
        description="Same company",
        status=PublishedStatus.PUBLISHED,
    )
    db.add_all([entity_a, entity_b])
    await db.flush()

    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(source_id=source.id, chunk_index=0, text_content="test")
    db.add(chunk)
    await db.flush()

    # Both entities link to the same chunk.
    db.add_all(
        [
            ChunkEntity(chunk_id=chunk.id, entity_id=entity_a.id),
            ChunkEntity(chunk_id=chunk.id, entity_id=entity_b.id),
        ]
    )
    await db.flush()

    # Merge B into A.
    await merge_entity(
        from_id=entity_b.id,
        into_id=entity_a.id,
        chunk_id=chunk.id,
        job_id=None,
        reason="Duplicate",
        db=db,
    )
    await db.commit()

    # Verify only one chunk_entity row remains.
    result = await db.execute(
        select(ChunkEntity).where(ChunkEntity.chunk_id == chunk.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].entity_id == entity_a.id


async def test_merge_entity_writes_audit_log(db: AsyncSession) -> None:
    """Merge writes an EntityMergeLog row."""
    entity_a = Entity(
        name="Apple Inc.",
        entity_type="organization",
        description="Tech company",
        status=PublishedStatus.PUBLISHED,
    )
    entity_b = Entity(
        name="Apple",
        entity_type="organization",
        description="Same company",
        status=PublishedStatus.PUBLISHED,
    )
    db.add_all([entity_a, entity_b])
    await db.flush()

    await merge_entity(
        from_id=entity_b.id,
        into_id=entity_a.id,
        chunk_id=None,
        job_id=None,
        reason="LLM said duplicate",
        db=db,
    )
    # Do not call db.commit() here — the fixture wraps each test in a
    # rolled-back transaction and the flush inside merge_entity already
    # persisted the audit log row.

    # Verify the audit log exists.
    result = await db.execute(
        select(EntityMergeLog).where(EntityMergeLog.merged_into_id == entity_a.id)
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.reason == "LLM said duplicate"


async def test_merge_entity_raises_when_from_entity_not_found(
    db: AsyncSession,
) -> None:
    """Merge with a non-existent from_id raises EntityResolutionError."""
    entity_a = Entity(
        name="Apple Inc.",
        entity_type="organization",
        description="Tech company",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(entity_a)
    await db.flush()

    with pytest.raises(EntityResolutionError, match="source entity not found"):
        await merge_entity(
            from_id=uuid.uuid4(),
            into_id=entity_a.id,
            chunk_id=None,
            job_id=None,
            reason="test",
            db=db,
        )
