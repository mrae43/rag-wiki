"""
tests.mcp.test_tools
--------------------
Tests for MCP tool handlers (query_knowledge_graph, retrieve_context).

Covers happy paths (answer generation, context retrieval) and error paths
(connection refused, timeout, server errors).
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from rag_wiki.mcp.server import create_mcp_server
from rag_wiki.settings import Settings


class TestQueryKnowledgeGraph:
    """Tests for the query_knowledge_graph tool."""

    async def test_returns_answer(self, mcp_server: FastMCP) -> None:
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "query_knowledge_graph",
                {"query": "Who is Ada Lovelace?"},
            )
            assert "test answer" in result.content[0].text

    async def test_handles_connect_error(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "query_knowledge_graph",
                        {"query": "who?"},
                    )
                assert "Could not connect" in str(excinfo.value)

    async def test_handles_timeout(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Timed out")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "query_knowledge_graph",
                        {"query": "who?"},
                    )
                assert "timed out" in str(excinfo.value)

    async def test_handles_http_500(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "Internal Server Error"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "query_knowledge_graph",
                        {"query": "who?"},
                    )
                assert "500" in str(excinfo.value)

    async def test_handles_http_422(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"error": "Unprocessable"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "query_knowledge_graph",
                        {"query": "who?"},
                    )
                assert "422" in str(excinfo.value)

    async def test_has_description(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        tool = next(t for t in tools if t.name == "query_knowledge_graph")
        assert tool.description is not None
        assert "direct answer" in tool.description.lower()

    async def test_has_parameters(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        tool = next(t for t in tools if t.name == "query_knowledge_graph")
        props = tool.parameters.get("properties", {})
        assert "query" in props
        assert props["query"]["type"] == "string"


class TestRetrieveContext:
    """Tests for the retrieve_context tool."""

    async def test_returns_json(self, mcp_server: FastMCP) -> None:
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "retrieve_context",
                {"query": "What is RAG?"},
            )
            data = json.loads(result.content[0].text)
            assert "seeds" in data

    async def test_handles_connect_error(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "retrieve_context",
                        {"query": "who?"},
                    )
                assert "Could not connect" in str(excinfo.value)

    async def test_handles_timeout(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Timed out")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "retrieve_context",
                        {"query": "who?"},
                    )
                assert "timed out" in str(excinfo.value)

    async def test_handles_http_500(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "Internal Server Error"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "retrieve_context",
                        {"query": "who?"},
                    )
                assert "500" in str(excinfo.value)

    async def test_handles_http_422(self, settings: Settings) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"error": "Unprocessable"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            server = create_mcp_server(settings=settings, http_client=http_client)
            async with Client(server) as client:
                with pytest.raises(ToolError) as excinfo:
                    await client.call_tool(
                        "retrieve_context",
                        {"query": "who?"},
                    )
                assert "422" in str(excinfo.value)

    async def test_has_description(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        tool = next(t for t in tools if t.name == "retrieve_context")
        assert tool.description is not None
        assert "structured context" in tool.description.lower()

    async def test_has_parameters(self, mcp_server: FastMCP) -> None:
        tools = await mcp_server.list_tools()
        tool = next(t for t in tools if t.name == "retrieve_context")
        props = tool.parameters.get("properties", {})
        assert "query" in props
        assert props["query"]["type"] == "string"
