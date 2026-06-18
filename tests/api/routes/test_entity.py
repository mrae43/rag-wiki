"""tests/api/routes/test_entity
-----------------------------
Tests for the read-only entity browsing endpoints.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import (
    Chunk,
    Entity,
    ProcessingStatus,
    PublishedStatus,
    Relation,
    Source,
    WikiPage,
)


async def _seed_entity(
    db: AsyncSession,
    name: str,
    entity_type: str,
    status: str = PublishedStatus.PUBLISHED,
) -> Entity:
    """Create and flush a minimal entity for tests."""
    entity = Entity(
        name=name,
        entity_type=entity_type,
        description=f"Description of {name}",
        status=status,
    )
    db.add(entity)
    await db.flush()
    return entity


async def test_list_entities_paginated_and_filtered(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /entities supports offset/limit and status/type/name filters."""
    for i in range(3):
        await _seed_entity(db, f"Entity {i}", "concept")
    await _seed_entity(db, "Special Person", "person")
    await db.flush()

    response = await api_client.get("/api/v1/entities?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert len(body["items"]) == 2

    by_type = await api_client.get("/api/v1/entities?entity_type=person")
    assert by_type.status_code == 200
    data = by_type.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Special Person"

    by_name = await api_client.get("/api/v1/entities?name=Entity 1")
    assert by_name.status_code == 200
    data = by_name.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Entity 1"


async def test_get_entity_by_id(api_client: AsyncClient, db: AsyncSession) -> None:
    """GET /entities/{id} returns the entity; unknown id returns 404."""
    entity = await _seed_entity(db, "Alpha", "concept")
    await db.flush()

    response = await api_client.get(f"/api/v1/entities/{entity.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(entity.id)
    assert body["name"] == "Alpha"
    assert body["entity_type"] == "concept"
    assert body["description"] == "Description of Alpha"

    missing = await api_client.get(f"/api/v1/entities/{uuid.uuid4()}")
    assert missing.status_code == 404


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


async def test_list_entity_relations(api_client: AsyncClient, db: AsyncSession) -> None:
    """GET /entities/{id}/relations returns outgoing, incoming, and both."""
    source = await _seed_entity(db, "Source", "concept")
    target = await _seed_entity(db, "Target", "concept")
    other = await _seed_entity(db, "Other", "concept")
    chunk = await _seed_chunk(db)

    outgoing = Relation(
        source_entity_id=source.id,
        target_entity_id=target.id,
        relation_type="relates_to",
        chunk_id=chunk.id,
    )
    incoming = Relation(
        source_entity_id=other.id,
        target_entity_id=source.id,
        relation_type="mentions",
        chunk_id=chunk.id,
    )
    db.add(outgoing)
    db.add(incoming)
    await db.flush()

    both_resp = await api_client.get(f"/api/v1/entities/{source.id}/relations")
    assert both_resp.status_code == 200
    data = both_resp.json()
    assert data["total"] == 2

    outgoing_resp = await api_client.get(
        f"/api/v1/entities/{source.id}/relations?direction=outgoing"
    )
    assert outgoing_resp.status_code == 200
    data = outgoing_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["relation_type"] == "relates_to"

    incoming_resp = await api_client.get(
        f"/api/v1/entities/{source.id}/relations?direction=incoming"
    )
    assert incoming_resp.status_code == 200
    data = incoming_resp.json()
    assert data["total"] == 1
    assert data["items"][0]["relation_type"] == "mentions"


async def test_list_entity_relations_invalid_direction(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """An invalid direction filter returns a 400 Problem Detail."""
    entity = await _seed_entity(db, "Dir", "concept")
    await db.flush()

    response = await api_client.get(
        f"/api/v1/entities/{entity.id}/relations?direction=upward"
    )
    assert response.status_code == 400
    body = response.json()
    assert body["status"] == 400
    assert "direction" in body["detail"].lower()


async def test_get_entity_wiki_page(api_client: AsyncClient, db: AsyncSession) -> None:
    """GET /entities/{id}/wiki-page returns the entity's primary page."""
    entity = await _seed_entity(db, "PageOwner", "concept")
    await db.flush()

    page = WikiPage(
        entity_id=entity.id,
        slug="page-owner",
        title="Page Owner",
        content="# Page Owner\n\nSome content.",
    )
    db.add(page)
    await db.flush()

    response = await api_client.get(f"/api/v1/entities/{entity.id}/wiki-page")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Page Owner"
    assert body["slug"] == "page-owner"
    assert body["entity_id"] == str(entity.id)

    missing = await api_client.get(f"/api/v1/entities/{uuid.uuid4()}/wiki-page")
    assert missing.status_code == 404


async def test_get_entity_wiki_page_missing(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /entities/{id}/wiki-page returns 404 when no page exists."""
    entity = await _seed_entity(db, "NoPage", "concept")
    await db.flush()

    response = await api_client.get(f"/api/v1/entities/{entity.id}/wiki-page")
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == 404
    assert "wiki page" in body["detail"].lower()
