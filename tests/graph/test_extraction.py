"""tests/graph/test_extraction
--------------------------
Unit tests for rag_wiki.graph.extraction.
"""

from __future__ import annotations

import uuid

import pytest

from rag_wiki.db.models.source import Chunk
from rag_wiki.exceptions import ExtractionError
from rag_wiki.graph.extraction import EXTRACTION_TOOL, extract_entities
from rag_wiki.graph.schemas import ExtractionResult
from rag_wiki.providers.base import CompletionRequest, CompletionResponse, ToolCall


class FakeChatProvider:
    """Minimal fake that can be configured per-test."""

    def __init__(self, response: CompletionResponse) -> None:
        self.response = response

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return self.response

    async def caption_image(
        self, image_bytes: bytes, image_mime_type: str, model: str
    ) -> str:
        return "fake-caption"


@pytest.fixture
def sample_chunk() -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        chunk_index=0,
        text_content="Apple Inc. is headquartered in Cupertino.",
    )


async def test_extract_entities_returns_extraction_result(sample_chunk: Chunk) -> None:
    """Happy path: provider returns a valid tool call → ExtractionResult."""
    valid_json = (
        '{"entities": [{'
        '"surface_form": "Apple Inc.", '
        '"canonical_name": "Apple Inc.", '
        '"entity_type": "organization", '
        '"description": "A technology company."}], '
        '"relations": []}'
    )
    provider = FakeChatProvider(
        response=CompletionResponse(
            content="ok",
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    name=EXTRACTION_TOOL.name,
                    arguments=valid_json,
                )
            ],
        )
    )

    result = await extract_entities(sample_chunk, provider, model="gpt-4o-mini")

    assert isinstance(result, ExtractionResult)
    assert len(result.entities) == 1
    assert result.entities[0].canonical_name == "Apple Inc."
    assert result.entities[0].entity_type == "organization"
    assert result.relations == []


async def test_extract_entities_raises_on_missing_tool_call(
    sample_chunk: Chunk,
) -> None:
    """Provider returns no tool calls → ExtractionError."""
    provider = FakeChatProvider(
        response=CompletionResponse(content="no tools", tool_calls=[])
    )

    with pytest.raises(ExtractionError, match="No tool call"):
        await extract_entities(sample_chunk, provider, model="gpt-4o-mini")


async def test_extract_entities_raises_on_invalid_json(sample_chunk: Chunk) -> None:
    """Provider returns malformed JSON → ExtractionError."""
    provider = FakeChatProvider(
        response=CompletionResponse(
            content="ok",
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    name=EXTRACTION_TOOL.name,
                    arguments="not valid json",
                )
            ],
        )
    )

    with pytest.raises(ExtractionError, match="Invalid JSON"):
        await extract_entities(sample_chunk, provider, model="gpt-4o-mini")


async def test_extract_entities_raises_on_empty_chunk() -> None:
    """Chunk with empty text_content → ExtractionError."""
    empty_chunk = Chunk(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        chunk_index=0,
        text_content="",
    )
    provider = FakeChatProvider(
        response=CompletionResponse(content="ok", tool_calls=[])
    )

    with pytest.raises(ExtractionError, match="empty text_content"):
        await extract_entities(empty_chunk, provider, model="gpt-4o-mini")
