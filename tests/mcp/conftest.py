"""
tests.mcp.conftest
------------------
Shared fixtures for MCP server tests.

Provides a fake backend handler, a mock HTTP client, and a configured
MCP server for in-memory tool testing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine

import httpx
import pytest
from fastmcp import FastMCP

from rag_wiki.mcp.server import create_mcp_server
from rag_wiki.settings import Settings

MockHandler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


@pytest.fixture
def settings() -> Settings:
    """Return minimal Settings with a test backend URL."""
    return Settings(
        mcp_api_url="http://test/api",
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=None,
        database_url="sqlite+aiosqlite://",
        llm_api_key="test-key",
    )


@pytest.fixture
def fake_backend() -> MockHandler:
    """Return an async httpx request handler that simulates the backend."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("generate_answer"):
            return httpx.Response(
                200,
                json={
                    "query": body["query"],
                    "answer": "test answer",
                    "retrieval": {},
                    "plan": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "query": body["query"],
                "answer": None,
                "retrieval": {"seeds": []},
                "plan": None,
            },
        )

    return handler


@pytest.fixture
def mock_http_client(fake_backend: MockHandler) -> httpx.AsyncClient:
    """Return an httpx AsyncClient with a mock transport."""
    transport = httpx.MockTransport(fake_backend)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture
def mcp_server(settings: Settings, mock_http_client: httpx.AsyncClient) -> FastMCP:
    """Return a FastMCP server wired to test settings and mocked HTTP."""
    return create_mcp_server(settings=settings, http_client=mock_http_client)
