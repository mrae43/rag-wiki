"""tests/ingest/test_pipeline
---------------------------
Integration tests for the full ingestion pipeline.

Exercises ``run_ingest_pipeline`` end-to-end with fake LLM providers and
temporary files. Covers happy path, total failure, and partial failure.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.db.models.jobs import Job
from rag_wiki.db.models.source import Chunk, ProcessingStatus, Source
from rag_wiki.exceptions import IngestError, LLMProviderError
from rag_wiki.ingest.pipeline import run_ingest_pipeline
from rag_wiki.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ToolCall,
)
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=suffix, mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name
    return path


class FakeChatProvider:
    """Configurable fake chat provider for pipeline tests.

    Supports per-tool canned responses via ``response_map`` so that
    extraction and resolution steps can return deterministic JSON payloads.
    """

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
        return f"fake-caption-{image_mime_type}"


class FakeEmbeddingProvider:
    """Deterministic embedding provider matching the DB vector column."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[0.0] * 2048 for _ in texts]


class CountingFailEmbedProvider:
    """Fails on the first ``embed`` call, then succeeds."""

    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderError("first embed call fails")
        return [[0.0] * 2048 for _ in texts]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_provider() -> FakeChatProvider:
    """Return a chat provider that creates new entities on every resolution."""
    extraction_json = (
        '{"entities":['
        '{"surface_form":"Apple Inc.","canonical_name":"Apple Inc.",'
        '"entity_type":"organization","description":"A tech company"},'
        '{"surface_form":"Tim Cook","canonical_name":"Tim Cook",'
        '"entity_type":"person","description":"CEO of Apple Inc."}'
        '],"relations":['
        '{"source_idx":0,"target_idx":1,"relation_type":"CEO"}'
        "]}"
    )
    merge_json = '{"decision":"new","reasoning":"Different entity"}'
    return FakeChatProvider(
        response_map={
            "extract_entities_and_relations": extraction_json,
            "merge_decision": merge_json,
        }
    )


@pytest.fixture
def embed_provider() -> FakeEmbeddingProvider:
    """Return a deterministic embedding provider."""
    return FakeEmbeddingProvider()


@pytest.fixture()
def single_chunk_txt() -> Generator[str, None, None]:
    """Text file small enough to produce a single chunk."""
    content = "Apple Inc. is a technology company. Tim Cook is the CEO."
    path = _write_temp(content, ".txt")
    yield path
    os.unlink(path)


@pytest.fixture()
def two_chunk_txt() -> Generator[str, None, None]:
    """Text file with two large sections, each exceeding half of MAX_CHARS."""
    # MAX_CHARS = 2048; two sections of ~1500 chars each will not merge.
    section1 = "SECTION ONE\n\n" + ("A" * 1500)
    section2 = "SECTION TWO\n\n" + ("B" * 1500)
    path = _write_temp(section1 + "\n\n" + section2, ".txt")
    yield path
    os.unlink(path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_ingest_pipeline_roundtrip(
    db: AsyncSession,
    chat_provider: FakeChatProvider,
    embed_provider: FakeEmbeddingProvider,
    single_chunk_txt: str,
) -> None:
    """Full pipeline creates Source, Chunk, Entity, Relation, and chunk_entities."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": single_chunk_txt},
    )
    db.add(job)
    await db.flush()

    await run_ingest_pipeline(job, db, chat_provider, embed_provider)
    await db.commit()

    # Source
    result = await db.execute(
        select(Source).where(Source.file_path == single_chunk_txt)
    )
    source = result.scalar_one()
    assert source.status == ProcessingStatus.PROCESSED

    # Chunks
    chunks_result = await db.execute(select(Chunk).where(Chunk.source_id == source.id))
    chunks = chunks_result.scalars().all()
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.status == ProcessingStatus.PROCESSED
        assert chunk.embedding is not None
        assert len(chunk.embedding) == 2048

    # Entities
    entities_result = await db.execute(select(Entity))
    entities = entities_result.scalars().all()
    assert len(entities) == 2
    names = {e.name for e in entities}
    assert "Apple Inc." in names
    assert "Tim Cook" in names

    # Relations
    relations_result = await db.execute(select(Relation))
    relations = relations_result.scalars().all()
    assert len(relations) == 1
    assert relations[0].relation_type == "CEO"

    # chunk_entities links
    for chunk in chunks:
        link_result = await db.execute(
            text("SELECT COUNT(*) FROM chunk_entities WHERE chunk_id = :chunk_id"),
            {"chunk_id": chunk.id},
        )
        count = link_result.scalar()
        assert count is not None
        assert count >= 1


# ---------------------------------------------------------------------------
# All chunks fail
# ---------------------------------------------------------------------------


async def test_ingest_pipeline_all_chunks_fail(
    db: AsyncSession,
    chat_provider: FakeChatProvider,
    single_chunk_txt: str,
) -> None:
    """Every chunk fails → source.status == failed and IngestError is raised."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": single_chunk_txt},
    )
    db.add(job)
    await db.flush()

    # Embedding provider that always raises.
    class FailEmbedProvider:
        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            raise LLMProviderError("embedding always fails")

    with pytest.raises(IngestError):
        await run_ingest_pipeline(job, db, chat_provider, FailEmbedProvider())

    await db.commit()

    result = await db.execute(
        select(Source).where(Source.file_path == single_chunk_txt)
    )
    source = result.scalar_one()
    assert source.status == ProcessingStatus.FAILED


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


async def test_ingest_pipeline_partial_fail(
    db: AsyncSession,
    chat_provider: FakeChatProvider,
    two_chunk_txt: str,
) -> None:
    """One chunk fails, the other succeeds → source is still processed."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": two_chunk_txt},
    )
    db.add(job)
    await db.flush()

    counting_provider = CountingFailEmbedProvider()

    await run_ingest_pipeline(job, db, chat_provider, counting_provider)
    await db.commit()

    result = await db.execute(select(Source).where(Source.file_path == two_chunk_txt))
    source = result.scalar_one()
    assert source.status == ProcessingStatus.PROCESSED

    chunks_result = await db.execute(select(Chunk).where(Chunk.source_id == source.id))
    chunks = chunks_result.scalars().all()
    assert len(chunks) == 2

    statuses = {chunk.status for chunk in chunks}
    assert ProcessingStatus.PROCESSED in statuses
    assert ProcessingStatus.FAILED in statuses

    # At least one entity was created from the successful chunk.
    entities_result = await db.execute(select(Entity))
    entities = entities_result.scalars().all()
    assert len(entities) >= 1


# ---------------------------------------------------------------------------
# Synthesis job enqueue
# ---------------------------------------------------------------------------


async def test_ingest_enqueues_synthesis_jobs(
    db: AsyncSession,
    chat_provider: FakeChatProvider,
    embed_provider: FakeEmbeddingProvider,
    single_chunk_txt: str,
) -> None:
    """After a successful ingest, synthesis jobs are enqueued for each entity."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": single_chunk_txt},
    )
    db.add(job)
    await db.flush()

    await run_ingest_pipeline(job, db, chat_provider, embed_provider)
    await db.commit()

    # Check entity synthesis jobs.
    entity_jobs = await db.execute(
        select(Job).where(Job.job_type == JOB_TYPE_SYNTHESIZE_ENTITY)
    )
    entity_job_list = entity_jobs.scalars().all()
    assert len(entity_job_list) == 2
    for j in entity_job_list:
        assert j.target_entity_id is not None
        assert j.payload is not None
        assert "source_ids" in j.payload

    # Check source summary job.
    summary_jobs = await db.execute(
        select(Job).where(Job.job_type == JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY)
    )
    summary_job_list = summary_jobs.scalars().all()
    assert len(summary_job_list) == 1
    assert summary_job_list[0].target_entity_id is None
    assert "source_id" in (summary_job_list[0].payload or {})


async def test_ingest_no_synthesis_jobs_on_all_fail(
    db: AsyncSession,
    chat_provider: FakeChatProvider,
    single_chunk_txt: str,
) -> None:
    """When all chunks fail, no synthesis jobs are enqueued."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": single_chunk_txt},
    )
    db.add(job)
    await db.flush()

    class FailEmbedProvider:
        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            raise LLMProviderError("always fails")

    with pytest.raises(IngestError):
        await run_ingest_pipeline(job, db, chat_provider, FailEmbedProvider())

    await db.commit()

    entity_jobs = await db.execute(
        select(Job).where(Job.job_type == JOB_TYPE_SYNTHESIZE_ENTITY)
    )
    assert len(entity_jobs.scalars().all()) == 0

    summary_jobs = await db.execute(
        select(Job).where(Job.job_type == JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY)
    )
    assert len(summary_jobs.scalars().all()) == 0
