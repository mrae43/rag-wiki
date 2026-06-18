"""tests/api/routes/test_wiki_page
--------------------------------
Tests for the read-only wiki page browsing endpoints.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import Entity, WikiPage, WikiPageEntity


async def _seed_entity(db: AsyncSession, name: str, entity_type: str) -> Entity:
    """Create and flush a minimal entity for tests."""
    entity = Entity(name=name, entity_type=entity_type)
    db.add(entity)
    await db.flush()
    return entity


async def test_list_wiki_pages_paginated_and_filtered(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /wiki-pages supports offset/limit and status/title filters."""
    for i in range(3):
        db.add(
            WikiPage(
                entity_id=None,
                slug=f"page-{i}",
                title=f"Report {i}",
                content=f"Content {i}",
            )
        )
    await db.flush()

    response = await api_client.get("/api/v1/wiki-pages?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    filtered = await api_client.get("/api/v1/wiki-pages?title=Report 1")
    assert filtered.status_code == 200
    data = filtered.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Report 1"


async def test_get_wiki_page_by_id(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /wiki-pages/{id} returns the page; unknown id returns 404."""
    entity = await _seed_entity(db, "ById", "concept")
    await db.flush()

    page = WikiPage(
        entity_id=entity.id,
        slug="by-id",
        title="By Id",
        content="# By Id\n\nContent.",
    )
    db.add(page)
    await db.flush()

    response = await api_client.get(f"/api/v1/wiki-pages/{page.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(page.id)
    assert body["title"] == "By Id"

    missing = await api_client.get(f"/api/v1/wiki-pages/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_get_wiki_page_by_slug(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /wiki-pages/slug/{slug} returns the page; lookup is case-insensitive."""
    entity = await _seed_entity(db, "BySlug", "concept")
    await db.flush()

    page = WikiPage(
        entity_id=entity.id,
        slug="my-slug",
        title="My Slug",
        content="# My Slug\n\nContent.",
    )
    db.add(page)
    await db.flush()

    response = await api_client.get("/api/v1/wiki-pages/slug/my-slug")
    assert response.status_code == 200
    assert response.json()["title"] == "My Slug"

    case_insensitive = await api_client.get("/api/v1/wiki-pages/slug/MY-SLUG")
    assert case_insensitive.status_code == 200
    assert case_insensitive.json()["title"] == "My Slug"

    missing = await api_client.get("/api/v1/wiki-pages/slug/does-not-exist")
    assert missing.status_code == 404


async def test_list_wiki_page_mentions(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /wiki-pages/{id}/mentions returns entities that mention the page."""
    entity = await _seed_entity(db, "PageSubject", "concept")
    await db.flush()

    page = WikiPage(
        entity_id=entity.id,
        slug="subject",
        title="Subject",
        content="# Subject",
    )
    db.add(page)
    await db.flush()

    mentioner = await _seed_entity(db, "Mentioner", "person")
    await db.flush()

    db.add(WikiPageEntity(wiki_page_id=page.id, entity_id=mentioner.id))
    await db.flush()

    response = await api_client.get(f"/api/v1/wiki-pages/{page.id}/mentions")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(mentioner.id)
    assert body["items"][0]["name"] == "Mentioner"


async def test_list_wiki_page_mentions_missing_page(
    api_client: AsyncClient,
) -> None:
    """GET /wiki-pages/{id}/mentions returns 404 when the page does not exist."""
    response = await api_client.get(f"/api/v1/wiki-pages/{uuid.uuid4()}/mentions")
    assert response.status_code == 404
