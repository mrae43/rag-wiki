"""
tests/api/test_exceptions
------------------------
Tests for RFC 7807 Problem Details error responses.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_db
from rag_wiki.api.exceptions import NotFoundError

pytestmark = pytest.mark.asyncio


async def test_ragwiki_error_returns_problem_details(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A ``RagWikiError`` subclass is returned as a Problem Detail response."""

    async def _failing_db() -> AsyncSession:
        raise NotFoundError("source not found")

    api_client.app.dependency_overrides[get_db] = _failing_db  # type: ignore[attr-defined]
    try:
        response = await api_client.get("/health")
    finally:
        api_client.app.dependency_overrides.pop(get_db, None)  # type: ignore[attr-defined]

    assert response.status_code == 404
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert "source not found" in body["detail"]
    assert body["instance"] == "/health"
    assert response.headers["content-type"] == "application/problem+json"


async def test_nonexistent_route_returns_problem_details(
    api_client: AsyncClient,
) -> None:
    """A request to an unknown path returns a 404 Problem Detail."""
    response = await api_client.get("/api/v1/nonexistent")
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert body["instance"] == "/api/v1/nonexistent"
