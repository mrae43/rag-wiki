"""tests/graph/test_resolution
----------------------------
Unit tests for rag_wiki.graph.resolution.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus, Relation
from rag_wiki.db.models.source import Chunk, Source
from rag_wiki.graph.resolution import resolve_entities
from rag_wiki.graph.schemas import ExtractedEntity, ExtractedRelation
from rag_wiki.providers.base import CompletionRequest, CompletionResponse, ToolCall
from rag_wiki.settings import get_settings


class FakeChatProvider:
    """Configurable fake chat provider for resolution tests."""

    def __init__(self, response_map: dict[str, str] | None = None) -> None:
        self.response_map = response_map or {}

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.tools and self.response_map:
            for tool in request.tools:
                if tool.name in self.response_map:
                    return CompletionResponse(
                        content="ok",
                        tool_calls=[
                            ToolCall(
                                id="tc-1",
                                name=tool.name,
                                arguments=self.response_map[tool.name],
                            )
                        ],
                    )
        return CompletionResponse(
            content="ok",
            tool_calls=[
                ToolCall(id="tc-1", name="fake_tool", arguments='{"input": "test"}')
            ]
            if request.tools
            else [],
        )

    async def caption_image(
        self, image_bytes: bytes, image_mime_type: str, model: str
    ) -> str:
        return "fake-caption"


class FakeEmbeddingProvider:
    """Deterministic embedding provider."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


@pytest.fixture
def mock_chat_provider() -> FakeChatProvider:
    return FakeChatProvider()


@pytest.fixture
def mock_embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


async def test_resolve_entities_creates_new_when_no_candidates(
    db: AsyncSession,
    mock_chat_provider: FakeChatProvider,
    mock_embedding_provider: FakeEmbeddingProvider,
) -> None:
    """Empty entities table → new Entity is created."""
    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Apple Inc. is a tech company.",
    )
    db.add(chunk)
    await db.flush()

    candidates = [
        ExtractedEntity(
            surface_form="Apple Inc.",
            canonical_name="Apple Inc.",
            entity_type="organization",
            description="A technology company.",
        )
    ]

    resolved = await resolve_entities(
        candidates=candidates,
        chunk=chunk,
        db=db,
        chat_provider=mock_chat_provider,
        embed_provider=mock_embedding_provider,
    )
    await db.commit()

    assert len(resolved) == 1
    entity = resolved[0]
    assert isinstance(entity, Entity)
    assert entity.name == "Apple Inc."
    assert entity.entity_type == "organization"

    # Verify it exists in the DB.
    result = await db.execute(select(Entity).where(Entity.id == entity.id))
    assert result.scalar_one_or_none() is not None


async def test_resolve_entities_merges_when_llm_decides_merge(
    db: AsyncSession,
    mock_embedding_provider: FakeEmbeddingProvider,
) -> None:
    """Seed one entity, extract similar candidate, LLM says merge → verify merge."""
    # Seed an existing entity.
    existing = Entity(
        name="Apple Inc.",
        entity_type="organization",
        description="Tech company",
        embedding=[0.0] * get_settings().embedding_dimensions,
        status=PublishedStatus.PUBLISHED,
    )
    db.add(existing)
    await db.flush()

    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Apple is a tech company.",
    )
    db.add(chunk)
    await db.flush()

    candidates = [
        ExtractedEntity(
            surface_form="Apple",
            canonical_name="Apple Inc.",
            entity_type="organization",
            description="A technology company headquartered in Cupertino.",
        )
    ]

    merge_json = (
        f'{{"decision": "merge", "merged_into_id": "{existing.id}", '
        f'"reasoning": "Same company"}}'
    )
    chat_provider = FakeChatProvider(response_map={"merge_decision": merge_json})

    resolved = await resolve_entities(
        candidates=candidates,
        chunk=chunk,
        db=db,
        chat_provider=chat_provider,
        embed_provider=mock_embedding_provider,
    )
    await db.commit()

    assert len(resolved) == 1
    assert resolved[0].id == existing.id

    # Verify only one entity remains.
    result = await db.execute(select(Entity))
    all_entities = result.scalars().all()
    assert len(all_entities) == 1
    assert all_entities[0].id == existing.id


async def test_resolve_entities_creates_relations_after_entities(
    db: AsyncSession,
    mock_chat_provider: FakeChatProvider,
    mock_embedding_provider: FakeEmbeddingProvider,
) -> None:
    """Extract 2 entities + 1 relation → verify relation row exists."""
    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Tim Cook is the CEO of Apple Inc.",
    )
    db.add(chunk)
    await db.flush()

    candidates = [
        ExtractedEntity(
            surface_form="Tim Cook",
            canonical_name="Tim Cook",
            entity_type="person",
            description="CEO of Apple Inc.",
        ),
        ExtractedEntity(
            surface_form="Apple Inc.",
            canonical_name="Apple Inc.",
            entity_type="organization",
            description="A technology company.",
        ),
    ]
    relations = [ExtractedRelation(source_idx=0, target_idx=1, relation_type="CEO")]

    # Provide a canned "new" decision so the second entity (Apple Inc.) is
    # created as a new entity even though the vector search sees Tim Cook.
    new_json = '{"decision": "new", "reasoning": "Different entity"}'
    chat_provider = FakeChatProvider(response_map={"merge_decision": new_json})

    resolved = await resolve_entities(
        candidates=candidates,
        chunk=chunk,
        db=db,
        chat_provider=chat_provider,
        embed_provider=mock_embedding_provider,
        relations=relations,
    )
    await db.commit()

    assert len(resolved) == 2

    # Verify relation exists.
    result = await db.execute(select(Relation))
    rel_rows = result.scalars().all()
    assert len(rel_rows) == 1
    assert rel_rows[0].relation_type == "CEO"
    assert rel_rows[0].source_entity_id == resolved[0].id
    assert rel_rows[0].target_entity_id == resolved[1].id
    assert rel_rows[0].chunk_id == chunk.id


@pytest.mark.skip(
    reason="Advisory locks are session-scoped; same session can re-acquire."
)
async def test_resolve_entities_skips_when_advisory_lock_fails(
    db: AsyncSession,
    mock_embedding_provider: FakeEmbeddingProvider,
) -> None:
    """Simulate lock contention by using a name that is already locked."""
    source = Source(
        file_path="/tmp/test",
        file_name="test",
        file_type="txt",
        file_size=0,
    )
    db.add(source)
    await db.flush()
    chunk = Chunk(source_id=source.id, chunk_index=0, text_content="Apple Inc.")
    db.add(chunk)
    await db.flush()

    candidates = [
        ExtractedEntity(
            surface_form="Apple Inc.",
            canonical_name="Apple Inc.",
            entity_type="organization",
            description="Tech company",
        )
    ]

    # Acquire the lock manually before resolution.
    from rag_wiki.graph.resolution import _advisory_lock_key

    lock_key = _advisory_lock_key("Apple Inc.")
    lock_result = await db.execute(
        text("SELECT pg_try_advisory_lock(:lock_key)"),
        {"lock_key": lock_key},
    )
    acquired = lock_result.scalar()
    assert acquired

    try:
        chat_provider = FakeChatProvider()
        resolved = await resolve_entities(
            candidates=candidates,
            chunk=chunk,
            db=db,
            chat_provider=chat_provider,
            embed_provider=mock_embedding_provider,
        )
        await db.commit()

        # Candidate should be skipped because lock was not acquired.
        assert len(resolved) == 0
    finally:
        await db.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": lock_key},
        )
        await db.commit()
