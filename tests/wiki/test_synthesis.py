"""Tests for rag_wiki.wiki.synthesis."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity
from rag_wiki.db.models.jobs import Job
from rag_wiki.db.models.source import Chunk, ChunkEntity, Source
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import AdvisoryLockExhausted
from rag_wiki.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingProvider,
)
from rag_wiki.settings import get_settings
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
    _advisory_lock_key,
    _cancel_duplicate_jobs,
    _merge_duplicate_jobs,
    synthesize_entity_page,
    synthesize_source_summary,
)


class FakeEmbeddingProvider(EmbeddingProvider):
    """Returns zero vectors for any input text."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed texts into zero vectors of configured dimensions."""
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


class ReturningChatProvider:
    """Returns canned markdown content for synthesis tests."""

    def __init__(self, content: str = "# Test Page\n\nGenerated content.") -> None:
        """Store canned content for complete() to return."""
        self.content = content

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return the pre-configured canned content."""
        return CompletionResponse(content=self.content)

    async def caption_image(
        self, image_bytes: bytes, image_mime_type: str, model: str
    ) -> str:
        """Return empty string (no-op for tests)."""
        return ""


class FailingChatProvider:
    """Raises LLMProviderError on complete."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Raise LLMProviderError to simulate provider failure."""
        from rag_wiki.exceptions import LLMProviderError

        raise LLMProviderError("provider failure")

    async def caption_image(
        self, image_bytes: bytes, image_mime_type: str, model: str
    ) -> str:
        """Return empty string (no-op for tests)."""
        return ""


# ---------------------------------------------------------------------------
# _advisory_lock_key
# ---------------------------------------------------------------------------


def test_advisory_lock_key_deterministic() -> None:
    """Same entity_id produces same lock key."""
    key1 = _advisory_lock_key("test-entity")
    key2 = _advisory_lock_key("test-entity")
    assert key1 == key2
    assert isinstance(key1, int)
    assert key1 > 0


def test_advisory_lock_key_different_for_different_ids() -> None:
    """Different entity_id strings produce different keys."""
    assert _advisory_lock_key("entity-a") != _advisory_lock_key("entity-b")


# ---------------------------------------------------------------------------
# _merge_duplicate_jobs
# ---------------------------------------------------------------------------


async def test_merge_duplicate_jobs_coalesces_source_ids(
    db: AsyncSession,
) -> None:
    """Duplicate pending jobs have their source_ids merged into the current job."""
    entity_id = uuid.uuid4()
    source_ids_a = [str(uuid.uuid4())]
    source_ids_b = [str(uuid.uuid4())]
    source_ids_c = [str(uuid.uuid4())]

    current = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity_id,
        payload={"source_ids": source_ids_a},
        status="processing",
    )
    db.add(current)
    dup1 = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity_id,
        payload={"source_ids": source_ids_b},
        status="pending",
    )
    dup2 = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity_id,
        payload={"source_ids": source_ids_c},
        status="pending",
    )
    db.add(dup1)
    db.add(dup2)
    await db.flush()

    merged = await _merge_duplicate_jobs(current, db)

    all_expected = set(source_ids_a + source_ids_b + source_ids_c)
    assert set(merged) == all_expected

    await db.commit()
    assert dup1.status == "completed"
    assert dup2.status == "completed"
    assert current.status == "processing"


async def test_merge_duplicate_jobs_no_duplicates(db: AsyncSession) -> None:
    """When no duplicates exist, returns own source_ids unchanged."""
    entity_id = uuid.uuid4()
    source_ids = [str(uuid.uuid4())]
    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity_id,
        payload={"source_ids": source_ids},
        status="processing",
    )
    db.add(job)
    await db.flush()

    merged = await _merge_duplicate_jobs(job, db)
    assert merged == source_ids


# ---------------------------------------------------------------------------
# _cancel_duplicate_jobs
# ---------------------------------------------------------------------------


async def test_cancel_duplicate_jobs_cancels_pending(db: AsyncSession) -> None:
    """Pending duplicate source-summary jobs are marked completed."""
    source_id = str(uuid.uuid4())
    current = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={"source_id": source_id},
        status="processing",
    )
    db.add(current)
    dup = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={"source_id": source_id},
        status="pending",
    )
    db.add(dup)
    await db.flush()

    cancelled = await _cancel_duplicate_jobs(db, current)
    assert len(cancelled) == 1
    assert cancelled[0] == dup.id

    await db.commit()
    assert dup.status == "completed"


async def test_cancel_duplicate_jobs_no_duplicates(db: AsyncSession) -> None:
    """When no duplicates exist, returns empty list."""
    current = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={"source_id": str(uuid.uuid4())},
        status="processing",
    )
    db.add(current)
    await db.flush()

    cancelled = await _cancel_duplicate_jobs(db, current)
    assert cancelled == []


# ---------------------------------------------------------------------------
# synthesize_entity_page
# ---------------------------------------------------------------------------


async def test_synthesize_entity_page_creates_page(db: AsyncSession) -> None:
    """Happy path: creates WikiPage and marks job completed."""
    entity = Entity(name="Test Entity", entity_type="concept", description="A test")
    db.add(entity)
    await db.flush()

    source = Source(
        storage_key="/tmp/test.txt",
        file_name="test.txt",
        file_type="text/plain",
        file_size=100,
    )
    db.add(source)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Test content about Test Entity.",
    )
    db.add(chunk)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity.id,
        payload={"source_ids": [str(source.id)]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()
    embed = FakeEmbeddingProvider()

    await synthesize_entity_page(job, db, chat, embed)

    page_result = await db.execute(
        select(WikiPage).where(WikiPage.entity_id == entity.id)
    )
    page = page_result.scalar_one_or_none()
    assert page is not None
    assert page.title == "Test Entity"
    assert page.content == "# Test Page\n\nGenerated content."
    assert page.slug.startswith("test-entity-")
    assert page.synthesized_from_sources == [str(source.id)]
    assert page.synthesized_at is not None


async def test_synthesize_entity_page_with_null_entity_id(
    db: AsyncSession,
) -> None:
    """Null target_entity_id raises ValueError."""
    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=None,
        payload={"source_ids": [str(uuid.uuid4())]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()
    embed = FakeEmbeddingProvider()

    with pytest.raises(ValueError, match="target_entity_id is null"):
        await synthesize_entity_page(job, db, chat, embed)


async def test_synthesize_entity_page_updates_existing(db: AsyncSession) -> None:
    """When a WikiPage already exists, it is updated in place."""
    entity = Entity(name="Test Entity", entity_type="concept")
    db.add(entity)
    await db.flush()

    source = Source(
        storage_key="/tmp/test.txt",
        file_name="test.txt",
        file_type="text/plain",
        file_size=100,
    )
    db.add(source)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Updated content about Test Entity.",
    )
    db.add(chunk)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    existing_page = WikiPage(
        entity_id=entity.id,
        title="Test Entity",
        slug="test-entity-old",
        content="# Old content",
    )
    db.add(existing_page)
    await db.flush()

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity.id,
        payload={"source_ids": [str(source.id)]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()
    embed = FakeEmbeddingProvider()

    await synthesize_entity_page(job, db, chat, embed)

    page_result = await db.execute(
        select(WikiPage).where(WikiPage.entity_id == entity.id)
    )
    page = page_result.scalar_one()
    assert page.id == existing_page.id
    assert page.content == "# Test Page\n\nGenerated content."
    assert page.slug == "test-entity-old"
    assert page.synthesized_at is not None


async def test_synthesize_entity_page_advisory_lock_exhausted(
    db: AsyncSession,
) -> None:
    """When advisory lock cannot be acquired, raises AdvisoryLockExhausted."""
    entity = Entity(name="E", entity_type="concept")
    db.add(entity)
    await db.flush()

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity.id,
        payload={"source_ids": [str(uuid.uuid4())]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()
    embed = FakeEmbeddingProvider()

    with (
        pytest.raises(AdvisoryLockExhausted),
        patch(
            "rag_wiki.wiki.synthesis._acquire_advisory_lock_with_retry",
            return_value=False,
        ),
    ):
        await synthesize_entity_page(job, db, chat, embed)


# ---------------------------------------------------------------------------
# synthesize_source_summary
# ---------------------------------------------------------------------------


async def test_synthesize_source_summary_creates_page(db: AsyncSession) -> None:
    """Happy path: creates source summary WikiPage."""
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
        text_content="Document content.",
    )
    db.add(chunk)
    await db.flush()

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={"source_id": str(source.id)},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()

    await synthesize_source_summary(job, db, chat)

    page_result = await db.execute(select(WikiPage).where(WikiPage.title == "doc.txt"))
    page = page_result.scalar_one_or_none()
    assert page is not None
    assert page.entity_id is None
    assert page.content == "# Test Page\n\nGenerated content."


async def test_synthesize_source_summary_skips_on_llm_error(
    db: AsyncSession,
) -> None:
    """LLM error -> returns normally (worker completes job per PRD section 9)."""
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
        text_content="Content.",
    )
    db.add(chunk)
    await db.flush()

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={"source_id": str(source.id)},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = FailingChatProvider()

    # Function returns normally (skip behavior) — no exception raised.
    await synthesize_source_summary(job, db, chat)

    # No wiki page was written.
    page_result = await db.execute(select(WikiPage).where(WikiPage.title == "doc.txt"))
    page = page_result.scalar_one_or_none()
    assert page is None


async def test_synthesize_source_summary_missing_source_id(
    db: AsyncSession,
) -> None:
    """Missing source_id in payload raises ValueError."""
    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
        payload={},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()

    with pytest.raises(ValueError, match="invalid source_id"):
        await synthesize_source_summary(job, db, chat)


# ---------------------------------------------------------------------------
# Handoff tests — verify synthesis functions signal correctly for the worker
# ---------------------------------------------------------------------------


async def test_synthesize_entity_page_entity_not_found(
    db: AsyncSession,
) -> None:
    """Entity not found raises ValueError for the worker to fail_job."""
    entity = Entity(name="Ghost", entity_type="concept")
    db.add(entity)
    await db.flush()

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity.id,
        payload={"source_ids": [str(uuid.uuid4())]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = ReturningChatProvider()
    embed = FakeEmbeddingProvider()

    # Delete the entity so the lookup fails.
    await db.execute(sa.delete(Entity).where(Entity.id == entity.id))
    await db.commit()

    with pytest.raises(ValueError, match="Entity not found"):
        await synthesize_entity_page(job, db, chat, embed)


async def test_synthesize_entity_page_llm_error_skips(
    db: AsyncSession,
) -> None:
    """LLM error leads to normal return (skip behavior) — no page written."""
    entity = Entity(name="SkipTest", entity_type="concept")
    db.add(entity)
    await db.flush()

    source = Source(
        storage_key="/tmp/skip.txt",
        file_name="skip.txt",
        file_type="text/plain",
        file_size=50,
    )
    db.add(source)
    await db.flush()

    chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Content.",
    )
    db.add(chunk)
    await db.flush()

    await db.execute(
        sa.insert(ChunkEntity).values(chunk_id=chunk.id, entity_id=entity.id)
    )

    job = Job(
        job_type=JOB_TYPE_SYNTHESIZE_ENTITY,
        target_entity_id=entity.id,
        payload={"source_ids": [str(source.id)]},
        status="processing",
    )
    db.add(job)
    await db.flush()

    chat = FailingChatProvider()
    embed = FakeEmbeddingProvider()

    # Should return normally (skip) — no exception.
    await synthesize_entity_page(job, db, chat, embed)

    # No wiki page was written.
    page_result = await db.execute(
        select(WikiPage).where(WikiPage.entity_id == entity.id)
    )
    page = page_result.scalar_one_or_none()
    assert page is None
