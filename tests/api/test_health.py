"""
tests/api/test_health
--------------------
Smoke tests for the ``GET /health`` endpoint.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_health_returns_ok(api_client: AsyncClient) -> None:
    """GET /health returns a 200 JSON payload when the DB is reachable."""
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
