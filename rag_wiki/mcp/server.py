"""
rag_wiki.mcp.server
-------------------
FastMCP server factory for the RAG Wiki MCP server.

Creates a configured FastMCP instance with registered tools for querying
the RAG Wiki knowledge graph. Does NOT start the server or manage
transports — callers (transport.py, CLI) are responsible for that.
"""

from __future__ import annotations

import httpx
from fastmcp import FastMCP

from rag_wiki.mcp.tools import register_tools
from rag_wiki.settings import Settings, get_settings


def create_mcp_server(
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastMCP:
    """
    Create and configure a FastMCP server for the RAG Wiki knowledge graph.

    Args:
        settings: Application settings. Defaults to cached settings.
        http_client: Optional HTTP client for backend calls. A default client
            is created if none is provided.

    Returns:
        A configured FastMCP instance with all tools registered.
    """
    settings = settings or get_settings()
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=5.0, read=30.0, write=30.0, pool=5.0),
    )
    mcp = FastMCP(name="RAG Wiki Knowledge Graph")
    register_tools(mcp, client, settings)
    return mcp
