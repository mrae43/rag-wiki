"""
tests.mcp.test_server
---------------------
Tests for the MCP server factory (create_mcp_server).

Checks that the factory creates a properly configured FastMCP instance
with the correct name and registered tools.
"""

from __future__ import annotations

import httpx
from fastmcp import FastMCP

from rag_wiki.mcp.server import create_mcp_server
from rag_wiki.settings import Settings


class TestCreateMCPServer:
    """Tests for create_mcp_server()."""

    async def test_creates_with_default_settings(self) -> None:
        """Server can be created without explicit settings."""
        server = create_mcp_server()
        assert isinstance(server, FastMCP)

    async def test_creates_with_custom_settings(self, settings: Settings) -> None:
        """Server accepts explicit Settings instance."""
        server = create_mcp_server(settings=settings)
        assert isinstance(server, FastMCP)

    async def test_creates_with_custom_client(self, settings: Settings) -> None:
        """Server accepts an explicit httpx client."""
        async with httpx.AsyncClient() as client:
            server = create_mcp_server(settings=settings, http_client=client)
            assert isinstance(server, FastMCP)

    async def test_has_correct_name(self) -> None:
        """Server name is 'RAG Wiki Knowledge Graph'."""
        server = create_mcp_server()
        assert server.name == "RAG Wiki Knowledge Graph"

    async def test_has_both_tools_registered(self, settings: Settings) -> None:
        """Server has both expected tools registered."""
        async with httpx.AsyncClient() as client:
            server = create_mcp_server(settings=settings, http_client=client)
            tools = await server.list_tools()
            names = {t.name for t in tools}
        assert "query_knowledge_graph" in names
        assert "retrieve_context" in names
