"""
rag_wiki.mcp.tools
------------------
MCP tool registration and backend proxy for the RAG Wiki knowledge graph.

Provides two MCP tools:
- query_knowledge_graph: Ask a question and get a synthesized answer.
- retrieve_context: Retrieve raw context for reasoning.

All HTTP calls to the backend go through _call_backend().
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP

from rag_wiki.mcp.errors import backend_error_message
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)


async def _call_backend(
    client: httpx.AsyncClient,
    body: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """
    POST the request body to the backend query API.

    Args:
        client: HTTP client to use.
        body: JSON-serializable request body.
        settings: Application settings (provides mcp_api_url).

    Returns:
        Parsed JSON response as a dict.

    Raises:
        ValueError: If the backend request fails (wraps httpx error
            messages via backend_error_message).
    """
    url = str(settings.mcp_api_url) + "/api/v1/queries"
    try:
        response = await client.post(
            url,
            json=body,
            timeout=httpx.Timeout(None, connect=5.0, read=30.0, write=30.0, pool=5.0),
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data
    except httpx.HTTPError as exc:
        msg = backend_error_message(exc, url)
        logger.error("backend_call_failed", url=url, error=msg)
        raise ValueError(msg) from exc


def register_tools(
    mcp: FastMCP,
    client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """
    Register MCP tools for querying the RAG Wiki knowledge graph.

    Registers two tools:
    - query_knowledge_graph
    - retrieve_context

    Args:
        mcp: FastMCP server instance.
        client: HTTP client for backend calls.
        settings: Application settings.
    """

    @mcp.tool(
        name="query_knowledge_graph",
        description=(
            "Use this tool when you need a direct answer to a factual question "
            "about topics covered in the knowledge wiki. The tool will retrieve "
            "relevant context from the knowledge graph and synthesize a "
            "natural-language answer. Good for: entity lookups, factual queries, "
            "topic exploration. Pass the user's question as the query parameter."
        ),
    )
    async def query_knowledge_graph(
        query: str,
        seed_entity_ids: list[str] | None = None,
        max_context_tokens: int | None = None,
    ) -> str:
        """
        Ask a question and get a synthesized answer from the knowledge wiki.

        Args:
            query: The user's question.
            seed_entity_ids: Optional entity UUIDs to narrow retrieval scope.
            max_context_tokens: Optional token budget for retrieved context.

        Returns:
            A natural-language answer string.
        """
        body: dict[str, Any] = {
            "query": query,
            "generate_answer": True,
        }
        if seed_entity_ids is not None:
            body["seed_entity_ids"] = [uuid.UUID(e) for e in seed_entity_ids]
        if max_context_tokens is not None:
            body["max_context_tokens"] = max_context_tokens
        result = await _call_backend(client, body, settings)
        return result.get("answer") or ""

    @mcp.tool(
        name="retrieve_context",
        description=(
            "Use this tool when you want to retrieve structured context from "
            "the knowledge wiki without generating an answer. The tool returns "
            "the raw retrieval result as JSON, which you can reason over "
            "yourself. Good for: when you need to see the evidence, when the "
            "question is complex and requires multi-step reasoning, or when you "
            "need structured data (entities, relations, chunks)."
        ),
    )
    async def retrieve_context(
        query: str,
        seed_entity_ids: list[str] | None = None,
        max_context_tokens: int | None = None,
    ) -> str:
        """
        Retrieve raw context from the knowledge wiki.

        Args:
            query: The user's question or search terms.
            seed_entity_ids: Optional entity UUIDs to narrow retrieval scope.
            max_context_tokens: Optional token budget for retrieved context.

        Returns:
            JSON string of the RetrievalResult.
        """
        body: dict[str, Any] = {
            "query": query,
            "generate_answer": False,
        }
        if seed_entity_ids is not None:
            body["seed_entity_ids"] = [uuid.UUID(e) for e in seed_entity_ids]
        if max_context_tokens is not None:
            body["max_context_tokens"] = max_context_tokens
        result = await _call_backend(client, body, settings)
        return json.dumps(result.get("retrieval", {}))
