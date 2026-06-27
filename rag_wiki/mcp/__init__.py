"""
rag_wiki.mcp
------------
MCP (Model Context Protocol) server wrapper for the RAG Wiki knowledge graph.

Exports the server factory and tool registration function used by the
transport layer and external test harnesses.
"""

from rag_wiki.mcp.server import create_mcp_server
from rag_wiki.mcp.tools import register_tools

__all__ = [
    "create_mcp_server",
    "register_tools",
]
