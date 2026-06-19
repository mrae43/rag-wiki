"""
tests/api/routes/test_query
--------------------------
Tests for the ``POST /api/v1/queries`` endpoint.

Covers answer generation, context-only retrieval, validation errors, and
seed-entity bypass of vector search.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_embedding_provider
from rag_wiki.db.models import Entity, ProcessingStatus, Source
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.settings import get_settings


class _DeterministicEmbeddingProvider:
    """Test double returning a one-hot vector keyed by the input text."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return a deterministic unit-vector embedding for each text."""
        return [self._vector_for(t) for t in texts]

    def _vector_for(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        vec[hash(text) % self._dimensions] = 1.0
        return vec


@pytest.fixture
def deterministic_embed_provider() -> EmbeddingProvider:
    """Return a deterministic embedding provider sized from settings."""
    return _DeterministicEmbeddingProvider(get_settings().embedding_dimensions)


async def _seed_entity(
    db: AsyncSession,
    name: str,
    entity_type: str = "Concept",
    with_embedding: bool = True,
) -> Entity:
    """Create a minimal entity for query tests."""
    source = Source(
        file_path="/tmp/query.txt",
        file_name="query.txt",
        file_type="text/plain",
        file_size=10,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(source)
    await db.flush()

    embedding: list[float] | None = None
    if with_embedding:
        provider = _DeterministicEmbeddingProvider(get_settings().embedding_dimensions)
        embedding = provider._vector_for(name)

    entity = Entity(
        name=name,
        entity_type=entity_type,
        description=f"Description for {name}.",
        embedding=embedding,
    )
    db.add(entity)
    await db.flush()
    return entity


async def test_query_returns_answer_and_retrieval(
    api_client: AsyncClient,
    db: AsyncSession,
    deterministic_embed_provider: EmbeddingProvider,
) -> None:
    """POST /queries returns both an answer and structured retrieval context."""
    entity = await _seed_entity(db, "Query Subject")
    api_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_embedding_provider
    ] = lambda: deterministic_embed_provider

    try:
        response = await api_client.post(
            "/api/v1/queries",
            json={
                "query": "What is Query Subject?",
                "generate_answer": True,
                "seed_entity_ids": [str(entity.id)],
            },
        )
    finally:
        api_client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
            get_embedding_provider, None
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "What is Query Subject?"
    assert body["answer"] is not None
    assert "fake-completion-for-" in body["answer"]
    assert "retrieval" in body
    assert body["retrieval"]["query"] == "What is Query Subject?"
    assert len(body["retrieval"]["seeds"]) == 1
    assert body["retrieval"]["seeds"][0]["entity_id"] == str(entity.id)


async def test_query_generate_answer_false_omits_answer(
    api_client: AsyncClient,
    db: AsyncSession,
    deterministic_embed_provider: EmbeddingProvider,
) -> None:
    """POST /queries with generate_answer=false returns context only."""
    entity = await _seed_entity(db, "Context Only Subject")
    api_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_embedding_provider
    ] = lambda: deterministic_embed_provider

    try:
        response = await api_client.post(
            "/api/v1/queries",
            json={
                "query": "Tell me about Context Only Subject",
                "generate_answer": False,
                "seed_entity_ids": [str(entity.id)],
            },
        )
    finally:
        api_client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
            get_embedding_provider, None
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Tell me about Context Only Subject"
    assert body["answer"] is None
    assert body["retrieval"] is not None


async def test_query_invalid_body_returns_422(
    api_client: AsyncClient,
) -> None:
    """An invalid request body is returned as a 422 Problem Detail."""
    response = await api_client.post(
        "/api/v1/queries",
        json={},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["status"] == 422
    assert body["title"] == "Unprocessable Entity"
    assert body["instance"] == "/api/v1/queries"


async def test_query_seed_entity_ids_bypass_vector_search(
    api_client: AsyncClient,
    db: AsyncSession,
    deterministic_embed_provider: EmbeddingProvider,
) -> None:
    """Passing seed_entity_ids skips vector search and uses the given entity."""
    entity = await _seed_entity(db, "Seed Bypass Subject", with_embedding=False)
    api_client.app.dependency_overrides[  # type: ignore[attr-defined]
        get_embedding_provider
    ] = lambda: deterministic_embed_provider

    try:
        response = await api_client.post(
            "/api/v1/queries",
            json={
                "query": "irrelevant",
                "generate_answer": False,
                "seed_entity_ids": [str(entity.id)],
            },
        )
    finally:
        api_client.app.dependency_overrides.pop(  # type: ignore[attr-defined]
            get_embedding_provider, None
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["retrieval"]["seeds"]) == 1
    assert body["retrieval"]["seeds"][0]["entity_id"] == str(entity.id)
    assert body["retrieval"]["seeds"][0]["similarity_score"] == 1.0
