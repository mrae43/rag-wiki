"""tests/api/routes/test_relation
-------------------------------
Tests for the read-only relation browsing endpoints.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import Chunk, Entity, ProcessingStatus, Relation, Source


async def _seed_entity(db: AsyncSession, name: str, entity_type: str) -> Entity:
    """Create and flush a minimal entity for tests."""
    entity = Entity(name=name, entity_type=entity_type)
    db.add(entity)
    await db.flush()
    return entity


async def _seed_chunk(db: AsyncSession) -> Chunk:
    """Create and flush a minimal source + chunk for relation provenance."""
    source = Source(
        file_path="/tmp/relation-chunk.txt",
        file_name="relation-chunk.txt",
        file_type="text/plain",
        file_size=10,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        chunk_type="text",
        text_content="relation provenance",
        status=ProcessingStatus.PROCESSED,
    )
    db.add(chunk)
    await db.flush()
    return chunk


async def test_list_relations_paginated_and_filtered(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /relations supports offset/limit and type/source/target filters."""
    a = await _seed_entity(db, "A", "concept")
    b = await _seed_entity(db, "B", "concept")
    c = await _seed_entity(db, "C", "concept")
    chunk = await _seed_chunk(db)

    rel_ab = Relation(
        source_entity_id=a.id,
        target_entity_id=b.id,
        relation_type="mentions",
        chunk_id=chunk.id,
    )
    rel_bc = Relation(
        source_entity_id=b.id,
        target_entity_id=c.id,
        relation_type="relates_to",
        chunk_id=chunk.id,
    )
    db.add(rel_ab)
    db.add(rel_bc)
    await db.flush()

    response = await api_client.get("/api/v1/relations?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1

    by_type = await api_client.get("/api/v1/relations?relation_type=mentions")
    assert by_type.status_code == 200
    data = by_type.json()
    assert data["total"] == 1
    assert data["items"][0]["relation_type"] == "mentions"

    by_source = await api_client.get(f"/api/v1/relations?source_entity_id={a.id}")
    assert by_source.status_code == 200
    data = by_source.json()
    assert data["total"] == 1
    assert data["items"][0]["target_entity_id"] == str(b.id)

    by_target = await api_client.get(f"/api/v1/relations?target_entity_id={c.id}")
    assert by_target.status_code == 200
    data = by_target.json()
    assert data["total"] == 1
    assert data["items"][0]["source_entity_id"] == str(b.id)
