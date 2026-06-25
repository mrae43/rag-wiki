"""Tests for rag_wiki.wiki.context."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.db.models.source import Chunk, ChunkEntity, Source
from rag_wiki.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingProvider,
)
from rag_wiki.retrieval.scoring import (
    cosine_similarity,
    deduplicate_chunks,
    score_chunks,
    truncate_to_budget,
)
from rag_wiki.wiki.context import (
    _CHARS_PER_TOKEN,
    _TIER_2_BUDGET,
    build_entity_context,
    build_source_summary_context,
)


class FakeEmbeddingProvider(EmbeddingProvider):
    """Test double returning deterministic one-hot embeddings."""

    def __init__(self, dim: int = 3072) -> None:
        """Store embedding dimension for one-hot vector generation."""
        self.dim = dim

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return one-hot embeddings for each input text."""
        return [
            [1.0 if j == i % self.dim else 0.0 for j in range(self.dim)]
            for i in range(len(texts))
        ]


class DummyChatProvider:
    """Minimal stand-in satisfying the ChatProvider protocol."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Stand-in that always raises; override in test if needed."""
        raise NotImplementedError

    async def caption_image(
        self, image_bytes: bytes, image_mime_type: str, model: str
    ) -> str:
        """Return empty string — no image captioning in tests."""
        return ""


# ---------------------------------------------------------------------------
# truncate_to_budget
# ---------------------------------------------------------------------------


def test_truncate_to_budget_respects_limit() -> None:
    """Only whole items that fit within the token budget are kept."""
    items = ["a" * 40, "b" * 40, "c" * 40]  # ~10 tok each
    result = truncate_to_budget(items, 25)
    assert result == [items[0], items[1]]


def test_truncate_to_budget_empty() -> None:
    """An empty list returns an empty list."""
    assert truncate_to_budget([], 100) == []


def test_truncate_to_budget_single_item_exceeds_budget() -> None:
    """If the very first item exceeds the budget, nothing is returned."""
    items = ["x" * 1000]  # ~250 tok
    result = truncate_to_budget(items, 10)
    assert result == []


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical() -> None:
    """Identical vectors have cosine similarity 1.0."""
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    """Orthogonal vectors have cosine similarity 0.0."""
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_dimension_mismatch() -> None:
    """A dimension mismatch raises ValueError."""
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# score_chunks
# ---------------------------------------------------------------------------


async def test_score_chunks_sorts_by_similarity() -> None:
    """Chunks are ordered by descending cosine similarity to the description."""
    provider = FakeEmbeddingProvider(dim=4)
    chunks = [
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=0,
            text_content="chunk a",
            embedding=[1.0, 0.0, 0.0, 0.0],
        ),
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=1,
            text_content="chunk b",
            embedding=[0.0, 1.0, 0.0, 0.0],
        ),
    ]
    scored = await score_chunks("chunk a", chunks, provider, model="fake")
    assert len(scored) == 2
    assert scored[0][1].text_content == "chunk a"
    assert scored[0][0] == pytest.approx(1.0)
    assert scored[1][1].text_content == "chunk b"
    assert scored[1][0] == pytest.approx(0.0)


async def test_score_chunks_embeds_missing_embeddings() -> None:
    """Chunks without embeddings are embedded on-the-fly."""
    provider = FakeEmbeddingProvider(dim=4)
    chunk = Chunk(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        chunk_index=0,
        text_content="text",
        embedding=None,
    )
    scored = await score_chunks("text", [chunk], provider, model="fake")
    assert len(scored) == 1
    assert chunk.embedding is not None  # mutated in-place
    assert len(chunk.embedding) == 4


async def test_score_chunks_empty() -> None:
    """An empty chunk list returns an empty scored list."""
    provider = FakeEmbeddingProvider(dim=4)
    result = await score_chunks("desc", [], provider, model="fake")
    assert result == []


# ---------------------------------------------------------------------------
# deduplicate_chunks
# ---------------------------------------------------------------------------


def test_deduplicate_chunks_removes_similar() -> None:
    """Chunks with cosine similarity >= threshold are deduplicated."""
    chunks = [
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=0,
            text_content="a",
            embedding=[1.0, 0.0, 0.0, 0.0],
        ),
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=1,
            text_content="b",
            embedding=[0.99, 0.01, 0.0, 0.0],
        ),
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=2,
            text_content="c",
            embedding=[0.0, 1.0, 0.0, 0.0],
        ),
    ]
    scored = [(1.0, chunks[0]), (0.9, chunks[1]), (0.8, chunks[2])]
    deduped = deduplicate_chunks(scored, threshold=0.95)
    assert len(deduped) == 2
    assert deduped[0][1].text_content == "a"
    assert deduped[1][1].text_content == "c"


def test_deduplicate_chunks_keeps_all_when_dissimilar() -> None:
    """When no chunks exceed the threshold, all are kept."""
    chunks = [
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=0,
            text_content="a",
            embedding=[1.0, 0.0, 0.0, 0.0],
        ),
        Chunk(
            id=uuid.uuid4(),
            source_id=uuid.uuid4(),
            chunk_index=1,
            text_content="b",
            embedding=[0.0, 1.0, 0.0, 0.0],
        ),
    ]
    scored = [(1.0, chunks[0]), (0.9, chunks[1])]
    deduped = deduplicate_chunks(scored, threshold=0.95)
    assert len(deduped) == 2


# ---------------------------------------------------------------------------
# build_entity_context
# ---------------------------------------------------------------------------


async def test_build_entity_context_create_mode(
    db: AsyncSession,
) -> None:
    """Context contains expected keys when creating a new page."""
    source = Source(
        storage_key="/tmp/test.txt",
        file_name="test.txt",
        file_type="text/plain",
        file_size=100,
    )
    db.add(source)
    await db.flush()

    entity = Entity(
        name="Test Entity",
        entity_type="concept",
        description="A test entity",
    )
    db.add(entity)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Test content",
        embedding=None,
    )
    db.add(chunk)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    target = Entity(name="Target", entity_type="concept")
    db.add(target)
    await db.flush()

    relation = Relation(
        source_entity_id=entity.id,
        target_entity_id=target.id,
        relation_type="relates_to",
        chunk_id=chunk.id,
    )
    db.add(relation)
    await db.flush()

    provider = FakeEmbeddingProvider(dim=3072)
    chat_provider = DummyChatProvider()
    context = await build_entity_context(
        entity=entity,
        db=db,
        chat_provider=chat_provider,
        embed_provider=provider,
        source_ids=[source.id],
        existing_page=None,
    )

    assert context["entity_name"] == "Test Entity"
    assert context["existing_page"] is None
    assert len(context["edges"]) == 1
    assert context["edges"][0]["label"] == "relates_to"
    assert len(context["source_chunks"]) == 1
    assert context["source_chunks"][0]["text"] == "Test content"
    assert len(context["known_entities"]) == 1
    assert context["known_entities"][0]["name"] == "Target"


async def test_build_entity_context_truncates_existing_page(
    db: AsyncSession,
) -> None:
    """An existing page is head-truncated to the Tier-2 budget."""
    source = Source(
        storage_key="/tmp/test.txt",
        file_name="test.txt",
        file_type="text/plain",
        file_size=100,
    )
    db.add(source)
    await db.flush()

    entity = Entity(name="E", entity_type="concept")
    db.add(entity)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="content",
        embedding=None,
    )
    db.add(chunk)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    provider = FakeEmbeddingProvider(dim=3072)
    chat_provider = DummyChatProvider()
    long_page = "x" * 5000
    context = await build_entity_context(
        entity=entity,
        db=db,
        chat_provider=chat_provider,
        embed_provider=provider,
        source_ids=[source.id],
        existing_page=long_page,
    )

    assert context["existing_page"] is not None
    assert len(context["existing_page"]) <= _TIER_2_BUDGET * _CHARS_PER_TOKEN


async def test_build_entity_context_no_source_ids(
    db: AsyncSession,
) -> None:
    """When no source IDs are provided, the context has no chunks."""
    entity = Entity(name="Orphan", entity_type="concept")
    db.add(entity)
    await db.flush()

    provider = FakeEmbeddingProvider(dim=3072)
    chat_provider = DummyChatProvider()
    context = await build_entity_context(
        entity=entity,
        db=db,
        chat_provider=chat_provider,
        embed_provider=provider,
        source_ids=[],
        existing_page=None,
    )

    assert context["source_chunks"] == []
    assert context["edges"] == []
    assert context["known_entities"] == []


# ---------------------------------------------------------------------------
# build_source_summary_context
# ---------------------------------------------------------------------------


async def test_build_source_summary_context_returns_keys(
    db: AsyncSession,
) -> None:
    """Source summary context contains all expected template keys."""
    source = Source(
        storage_key="/tmp/doc.txt",
        file_name="doc.txt",
        file_type="text/plain",
        file_size=50,
    )
    db.add(source)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Hello world",
    )
    db.add(chunk)
    await db.flush()

    entity = Entity(name="Alice", entity_type="person")
    db.add(entity)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    target = Entity(name="Bob", entity_type="person")
    db.add(target)
    await db.flush()

    relation = Relation(
        source_entity_id=entity.id,
        target_entity_id=target.id,
        relation_type="knows",
        chunk_id=chunk.id,
    )
    db.add(relation)
    await db.flush()

    chat_provider = DummyChatProvider()
    context = await build_source_summary_context(
        source=source, db=db, chat_provider=chat_provider
    )

    assert context["source_file_name"] == "doc.txt"
    assert context["chunk_count"] == 1
    assert len(context["chunks"]) == 1
    assert context["chunks"][0]["text"] == "Hello world"
    assert context["chunks"][0]["summary_or_first_line"] == "Hello world"
    assert len(context["touched_entities"]) == 1
    assert context["touched_entities"][0]["name"] == "Alice"
    assert len(context["source_relations"]) == 1
    assert context["source_relations"][0]["label"] == "knows"
    assert context["reingest_count"] == 0
    assert context["previous_ingested_at"] is None
