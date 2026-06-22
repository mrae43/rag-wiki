"""rag_wiki.graph.extraction
------------------------
Extracts entities and relations from a chunk of text using the configured
LLM provider. Does NOT perform entity resolution or write to the database —
callers are responsible for passing results to the resolver.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from rag_wiki.db.models.source import Chunk
from rag_wiki.exceptions import ExtractionError
from rag_wiki.graph.schemas import ExtractionResult
from rag_wiki.prompts.constants import EXTRACTION_PROMPT
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    Message,
    ToolDefinition,
)

logger = structlog.get_logger(__name__)

EXTRACTION_TOOL = ToolDefinition(
    name="extract_entities_and_relations",
    description="Extract structured entities and relations from a text chunk.",
    parameters={
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "surface_form": {"type": "string"},
                        "canonical_name": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": [
                        "surface_form",
                        "canonical_name",
                        "entity_type",
                        "description",
                    ],
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_idx": {"type": "integer"},
                        "target_idx": {"type": "integer"},
                        "relation_type": {"type": "string"},
                    },
                    "required": ["source_idx", "target_idx", "relation_type"],
                },
            },
        },
        "required": ["entities", "relations"],
    },
)


async def extract_entities(
    chunk: Chunk,
    provider: ChatProvider,
    model: str,
) -> ExtractionResult:
    """Extract entities and relations from a single chunk via the LLM.

    Args:
        chunk: The chunk to process. Must have a non-empty text_content field.
        provider: The LLM provider used for extraction calls.
        model: The model identifier to pass to the provider.

    Returns:
        An ExtractionResult containing the extracted entities and relations.

    Raises:
        ExtractionError: If the provider returns no tool call, invalid JSON,
            or a JSON payload that does not match the ExtractionResult schema.
    """
    if not chunk.text_content:
        raise ExtractionError(f"Chunk has empty text_content: chunk_id={chunk.id}")

    request = CompletionRequest(
        system=EXTRACTION_PROMPT,
        messages=[
            Message(
                role="user",
                content=chunk.text_content,
            )
        ],
        model=model,
        tools=[EXTRACTION_TOOL],
    )

    response = await provider.complete(request)

    if not response.tool_calls:
        raise ExtractionError(f"No tool call in LLM response for chunk_id={chunk.id}")

    tool_call = response.tool_calls[0]
    try:
        raw: dict[str, Any] = json.loads(tool_call.arguments)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"Invalid JSON in tool call arguments for chunk_id={chunk.id}: {exc}"
        ) from exc

    try:
        result = ExtractionResult.model_validate(raw)
    except Exception as exc:
        raise ExtractionError(
            f"Tool call arguments do not match ExtractionResult schema "
            f"for chunk_id={chunk.id}: {exc}"
        ) from exc

    logger.info(
        "entities extracted",
        chunk_id=str(chunk.id),
        entity_count=len(result.entities),
        relation_count=len(result.relations),
        model=model,
    )
    return result
