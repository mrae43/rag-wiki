"""
tests/ingest/conftest
--------------------
E2E test fixtures that commit to a real database.

Provides: ``persistent_db`` (truncate-backed), ``_DeterministicEmbeddingProvider``,
``make_e2e_chat_provider``, ``drain_synthesis_jobs``, and ``e2e_client``.

These fixtures intentionally live in ``tests/ingest/conftest.py`` rather than the root
conftest because they commit for real — the root ``db`` fixture uses rollback.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from rag_wiki.db.base import Base
from rag_wiki.jobs import claim_next, complete_job, fail_job
from rag_wiki.providers.base import ChatProvider, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
    synthesize_entity_page,
    synthesize_source_summary,
)
from tests.conftest import FakeChatProvider, FakeStorageProvider

# ---------------------------------------------------------------------------
# Persistent DB fixture (commits for real, truncates at teardown)
# ---------------------------------------------------------------------------


@pytest.fixture
async def persistent_db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Session that commits normally; truncates all tables after the test."""
    session = AsyncSession(bind=engine, expire_on_commit=False)
    yield session
    await session.rollback()
    async with session.begin():
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
    await session.close()


# ---------------------------------------------------------------------------
# Deterministic embedding provider (hash-based, non-zero)
# ---------------------------------------------------------------------------


class _DeterministicEmbeddingProvider:
    """Hash-based embedding provider producing stable, non-zero vectors.

    Each input text is SHA-256 hashed; the 32-byte digest is repeated to fill
    ``dimensions`` and normalized to ``[-1, 1]``.
    """

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return deterministic embeddings for each input text."""
        result: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode()).digest()
            vec = [(digest[i % 32] / 127.5) - 1.0 for i in range(self._dimensions)]
            result.append(vec)
        return result


# ---------------------------------------------------------------------------
# E2E chat provider factory
# ---------------------------------------------------------------------------


def make_e2e_chat_provider() -> ChatProvider:
    """Return a ``FakeChatProvider`` configured for E2E pipeline tests.

    Covers both tool-call patterns (extraction, resolution) and plain
    completions (synthesis, query).
    """
    extraction_json = json.dumps(
        {
            "entities": [
                {
                    "surface_form": "Apple Inc.",
                    "canonical_name": "Apple Inc.",
                    "entity_type": "organization",
                    "description": "A technology company",
                },
                {
                    "surface_form": "Tim Cook",
                    "canonical_name": "Tim Cook",
                    "entity_type": "person",
                    "description": "CEO of Apple Inc.",
                },
            ],
            "relations": [
                {
                    "source_idx": 0,
                    "target_idx": 1,
                    "relation_type": "CEO",
                },
            ],
        }
    )
    merge_json = json.dumps({"decision": "new", "reasoning": "First occurrence."})
    return FakeChatProvider(
        response_map={
            "extract_entities_and_relations": extraction_json,
            "merge_decision": merge_json,
        }
    )


# ---------------------------------------------------------------------------
# Synthesis job drainer (in-process worker)
# ---------------------------------------------------------------------------


async def drain_synthesis_jobs(
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
    max_jobs: int = 20,
) -> int:
    """Claim and dispatch synthesis jobs until the queue is empty.

    Args:
        db: Active async SQLAlchemy session.
        chat_provider: Provider for LLM completions.
        embed_provider: Provider for embeddings (entity synthesis only).
        max_jobs: Safety limit on jobs to process.

    Returns:
        Number of jobs successfully processed.
    """
    count = 0
    for _ in range(max_jobs):
        job = await claim_next(db, worker_id="test-worker")
        if job is None:
            break
        try:
            if job.job_type == JOB_TYPE_SYNTHESIZE_ENTITY:
                await synthesize_entity_page(job, db, chat_provider, embed_provider)
            elif job.job_type == JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY:
                await synthesize_source_summary(job, db, chat_provider)
            else:
                await fail_job(job, db, f"Unknown job type: {job.job_type}")
                await db.commit()
                continue
            await complete_job(job, db)
            await db.commit()
            count += 1
        except Exception:
            await fail_job(job, db, "E2E drain failure")
            await db.commit()
            raise
    return count


# ---------------------------------------------------------------------------
# E2E API client (commits to persistent_db)
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_provider() -> FakeStorageProvider:
    """Return a shared FakeStorageProvider for E2E tests."""
    return FakeStorageProvider()


@pytest.fixture
async def e2e_client(
    persistent_db: AsyncSession,
    tmp_path: Path,
    storage_provider: FakeStorageProvider,
) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client wired to ``persistent_db`` with fake providers.

    The client uses the same ``create_app()`` as the real API but overrides
    the database, chat, embedding, and storage dependencies.
    """
    from rag_wiki.api.dependencies import (
        get_chat_provider,
        get_db,
        get_embedding_provider,
        get_storage_provider,
    )
    from rag_wiki.main import create_app

    settings = get_settings()
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings)

    embedder = _DeterministicEmbeddingProvider(settings.embedding_dimensions)
    chat = make_e2e_chat_provider()

    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        yield persistent_db

    async def _override_chat() -> ChatProvider:
        return chat

    async def _override_embed() -> EmbeddingProvider:
        return embedder

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_chat_provider] = _override_chat
    app.dependency_overrides[get_embedding_provider] = _override_embed
    app.dependency_overrides[get_storage_provider] = lambda: storage_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
