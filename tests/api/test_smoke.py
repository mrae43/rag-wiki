"""
tests/api/test_smoke
-------------------
Top-level smoke tests for the FastAPI application.

Verifies that every planned route is reachable, returns the expected status,
and appears in the generated OpenAPI schema.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import (
    Chunk,
    Entity,
    Job,
    JobStatus,
    ProcessingStatus,
    Relation,
    Source,
    WikiPage,
)

pytestmark = pytest.mark.asyncio


async def test_health_root_is_reachable(api_client: AsyncClient) -> None:
    """GET /health is mounted at the root and returns 200."""
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_openapi_schema_is_generated(api_client: AsyncClient) -> None:
    """The OpenAPI schema contains all v1 routes."""
    response = await api_client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    paths = schema.get("paths", {})

    expected_routes = {
        "/health",
        "/api/v1/sources",
        "/api/v1/sources/{source_id}",
        "/api/v1/sources/{source_id}/chunks",
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}",
        "/api/v1/entities",
        "/api/v1/entities/{entity_id}",
        "/api/v1/entities/{entity_id}/relations",
        "/api/v1/entities/{entity_id}/wiki-page",
        "/api/v1/relations",
        "/api/v1/wiki-pages",
        "/api/v1/wiki-pages/{page_id}",
        "/api/v1/wiki-pages/slug/{slug}",
        "/api/v1/wiki-pages/{page_id}/mentions",
        "/api/v1/queries",
    }
    assert expected_routes.issubset(set(paths))


async def test_all_read_routes_return_success(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Every GET endpoint returns a success response when data exists."""
    source = Source(
        file_path="/tmp/smoke.txt",
        file_name="smoke.txt",
        file_type="text/plain",
        file_size=5,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(source)
    await db.flush()

    chunk_id: uuid.UUID | None = None
    for i in range(2):
        chunk = Chunk(
            source_id=source.id,
            chunk_index=i,
            chunk_type="text",
            text_content=f"chunk {i}",
            status=ProcessingStatus.PROCESSED,
        )
        db.add(chunk)
        await db.flush()
        if i == 0:
            chunk_id = chunk.id

    entity_a = Entity(name="Smoke A", entity_type="concept")
    entity_b = Entity(name="Smoke B", entity_type="concept")
    db.add(entity_a)
    db.add(entity_b)
    await db.flush()

    assert chunk_id is not None
    relation = Relation(
        source_entity_id=entity_a.id,
        target_entity_id=entity_b.id,
        relation_type="mentions",
        chunk_id=chunk_id,
    )
    db.add(relation)
    await db.flush()

    page = WikiPage(
        entity_id=entity_a.id,
        slug="smoke-a",
        title="Smoke A",
        content="# Smoke A",
    )
    db.add(page)
    await db.flush()

    job = Job(
        job_type="ingest_document",
        payload={"source_id": str(source.id)},
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.flush()

    checks = [
        ("get", f"/api/v1/sources/{source.id}", 200),
        ("get", "/api/v1/sources", 200),
        ("get", f"/api/v1/sources/{source.id}/chunks", 200),
        ("get", f"/api/v1/jobs/{job.id}", 200),
        ("get", "/api/v1/jobs", 200),
        ("get", f"/api/v1/entities/{entity_a.id}", 200),
        ("get", "/api/v1/entities", 200),
        ("get", f"/api/v1/entities/{entity_a.id}/relations", 200),
        ("get", f"/api/v1/entities/{entity_a.id}/wiki-page", 200),
        ("get", "/api/v1/relations", 200),
        ("get", f"/api/v1/wiki-pages/{page.id}", 200),
        ("get", "/api/v1/wiki-pages", 200),
        ("get", "/api/v1/wiki-pages/slug/smoke-a", 200),
        ("get", f"/api/v1/wiki-pages/{page.id}/mentions", 200),
    ]

    for method, path, expected in checks:
        response = await api_client.request(method, path)
        assert response.status_code == expected, f"{method.upper()} {path} failed"
