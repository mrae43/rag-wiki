"""
tests/api/test_middleware
------------------------
Tests for request-ID propagation and CORS middleware.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from rag_wiki.main import create_app
from rag_wiki.settings import Settings, get_settings

pytestmark = pytest.mark.asyncio


async def test_request_id_is_generated_when_not_provided(
    api_client: AsyncClient,
) -> None:
    """A request ID is generated and echoed when the client omits the header."""
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


async def test_request_id_is_propagated(api_client: AsyncClient) -> None:
    """An X-Request-ID supplied by the client is echoed back."""
    request_id = "test-request-id-123"
    response = await api_client.get("/health", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


async def test_cors_headers_present_when_configured() -> None:
    """CORS preflight returns access-control headers when origins are configured."""
    base = get_settings()
    settings = Settings.model_validate(base)
    settings.cors_origins = "http://localhost:3000"

    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Request-ID",
            },
        )

    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    )
