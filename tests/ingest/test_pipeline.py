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
from pathlib import Path

import pytest
from httpx import AsyncClient
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
from rag_wiki.settings import get_settings
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
)
from tests.ingest.conftest import (
    _DeterministicEmbeddingProvider,
    drain_synthesis_jobs,
    make_e2e_chat_provider,
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
        """Initialise with optional response_map mapping tool names to canned JSON."""
        self.response_map = response_map or {}

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return a canned CompletionResponse matching response_map, or a default."""
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
        """Return a deterministic fake caption string from the image MIME type."""
        return f"fake-caption-{image_mime_type}"


class FakeEmbeddingProvider:
    """Deterministic embedding provider matching the DB vector column."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return zero-filled embedding vectors of configured dims for each text."""
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


class CountingFailEmbedProvider:
    """Fails on the first ``embed`` call, then succeeds."""

    def __init__(self) -> None:
        """Initialise call_count at zero so the first embed call raises."""
        self.call_count = 0

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Raise on first call, then return zero-vector embeddings on later calls."""
        self.call_count += 1
        if self.call_count == 1:
            raise LLMProviderError("first embed call fails")
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


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
        select(Source).where(Source.storage_key == single_chunk_txt)
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
        assert len(chunk.embedding) == get_settings().embedding_dimensions

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
        """Always raises on embed."""

        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            """Raise LLMProviderError unconditionally."""
            raise LLMProviderError("embedding always fails")

    with pytest.raises(IngestError):
        await run_ingest_pipeline(job, db, chat_provider, FailEmbedProvider())

    await db.commit()

    result = await db.execute(
        select(Source).where(Source.storage_key == single_chunk_txt)
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

    result = await db.execute(select(Source).where(Source.storage_key == two_chunk_txt))
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
        """Always raises on embed."""

        async def embed(self, texts: list[str], model: str) -> list[list[float]]:
            """Raise LLMProviderError unconditionally."""
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


# ── E2E Tests ───────────────────────────────────────────────────────────


@pytest.mark.e2e
async def test_e2e_full_pipeline(
    persistent_db: AsyncSession,
    e2e_client: AsyncClient,
    single_chunk_txt: str,
) -> None:
    """Full end-to-end pipeline: upload → ingest → synthesize → query."""
    from rag_wiki.db.models.wiki import WikiPage
    from rag_wiki.ingest.pipeline import run_ingest_pipeline
    from rag_wiki.jobs import claim_next, complete_job

    chat = make_e2e_chat_provider()
    embed = _DeterministicEmbeddingProvider(get_settings().embedding_dimensions)

    # 1. Upload via API.
    with open(single_chunk_txt, "rb") as f:
        response = await e2e_client.post(
            "/api/v1/sources",
            files={"file": ("test.txt", f, "text/plain")},
        )
    assert response.status_code == 201, response.text

    # 2. Claim the ingest job.
    ingest_job = await claim_next(persistent_db, worker_id="test-worker")
    assert ingest_job is not None
    assert ingest_job.job_type == "ingest_document"

    # 3. Run ingestion pipeline.
    await run_ingest_pipeline(ingest_job, persistent_db, chat, embed)

    # 4. Complete the ingest job.
    await complete_job(ingest_job, persistent_db)
    await persistent_db.commit()

    # 5. Drain all synthesis jobs.
    n = await drain_synthesis_jobs(persistent_db, chat, embed)
    assert n >= 1  # at least source summary, plus entity pages

    # 6. WikiPage assertions.
    page_result = await persistent_db.execute(select(WikiPage))
    pages = page_result.scalars().all()
    assert len(pages) >= 1
    for page in pages:
        assert page.content
        assert page.synthesized_at is not None

    # 7. Entity assertions.
    entity_result = await persistent_db.execute(select(Entity))
    entities = entity_result.scalars().all()
    entity_names = {e.name for e in entities}
    assert "Apple Inc." in entity_names
    assert "Tim Cook" in entity_names

    # 8. Relation assertion.
    relation_result = await persistent_db.execute(select(Relation))
    relations = relation_result.scalars().all()
    assert len(relations) >= 1

    # 9. Query via API.
    response = await e2e_client.post(
        "/api/v1/queries",
        json={"query": "Who is the CEO of Apple Inc.?", "generate_answer": True},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["answer"] is not None
    assert len(data["answer"]) > 0


@pytest.mark.e2e
async def test_e2e_entity_resolution_across_documents(
    persistent_db: AsyncSession,
    e2e_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """
    Two documents mentioning the same entity create distinct
    entities with separate source provenance.
    """
    from rag_wiki.db.models.wiki import WikiPage
    from rag_wiki.ingest.pipeline import run_ingest_pipeline
    from rag_wiki.jobs import claim_next, complete_job

    chat = make_e2e_chat_provider()
    embed = _DeterministicEmbeddingProvider(get_settings().embedding_dimensions)

    # Helper to upload a document and run the full pipeline.
    async def _ingest_doc(content: str, filename: str) -> str:
        doc_path = tmp_path / filename
        doc_path.write_text(content, encoding="utf-8")
        with open(doc_path, "rb") as f:
            response = await e2e_client.post(
                "/api/v1/sources",
                files={"file": (filename, f, "text/plain")},
            )
        assert response.status_code == 201, response.text
        source_id: str = response.json()["id"]
        job = await claim_next(persistent_db, worker_id="test-worker")
        assert job is not None
        await run_ingest_pipeline(job, persistent_db, chat, embed)
        await complete_job(job, persistent_db)
        await persistent_db.commit()
        return source_id

    # 1. First ingest.
    source_a_id = await _ingest_doc(
        "Apple Inc. is a technology company. Tim Cook is the CEO.",
        "doc_a.txt",
    )

    # Drain synthesis jobs from first ingest so claim_next in second
    # ingest picks up the correct ingest_document job.
    n1 = await drain_synthesis_jobs(persistent_db, chat, embed)
    assert n1 >= 1

    # 2. Second ingest with same entity name.
    source_b_id = await _ingest_doc(
        "Apple Inc. was founded by Steve Jobs. Tim Cook is the current CEO.",
        "doc_b.txt",
    )

    # 3. Drain remaining synthesis jobs (includes entities from second ingest).
    n2 = await drain_synthesis_jobs(persistent_db, chat, embed)
    assert n2 >= 1

    # 4. Both sources are referenced across wiki pages.
    page_result = await persistent_db.execute(select(WikiPage))
    pages = page_result.scalars().all()
    all_sources: set[str] = set()
    for page in pages:
        if page.synthesized_from_sources:
            all_sources.update(page.synthesized_from_sources)
    assert source_a_id in all_sources
    assert source_b_id in all_sources

    # 5. Two entities exist (both "new", so separate rows).
    entity_result = await persistent_db.execute(select(Entity))
    entities = entity_result.scalars().all()
    apple_entities = [e for e in entities if e.name == "Apple Inc."]
    assert len(apple_entities) >= 1
    tim_cook_entities = [e for e in entities if e.name == "Tim Cook"]
    assert len(tim_cook_entities) >= 1
