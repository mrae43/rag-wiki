"""
tests/api/conftest
-----------------
Shared fixtures for API route tests.

Builds a fresh FastAPI app per test with a real database session and mock
LLM providers wired in via dependency overrides.

NOTE: This module defines its own ``db`` fixture so that API tests do not
pull in the session-scoped engine fixture from the root conftest. The root
engine must remain lazy until after the migration tests, which drop tables.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from rag_wiki.api.dependencies import (
    get_chat_provider,
    get_db,
    get_embedding_provider,
    get_storage_provider,
)
from rag_wiki.db.base import Base
from rag_wiki.db.models import (
    Chunk,
    Entity,
    ProcessingStatus,
    Relation,
    Source,
    WikiPage,
)
from rag_wiki.main import create_app
from rag_wiki.settings import Settings, get_settings

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://rag_wiki:rag_wiki@localhost:5432/rag_wiki_test",
)


@pytest.fixture(scope="session")
async def api_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Session-scoped engine with tables for API tests only."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db(api_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Each API test gets a rolled-back transaction."""
    async with api_engine.connect() as conn, conn.begin() as trans:
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await trans.rollback()


@pytest.fixture
async def api_client(
    db: AsyncSession,
    mock_chat_provider: object,
    mock_embedding_provider: object,
    mock_storage_provider: object,
    tmp_path: Path,
) -> AsyncGenerator[AsyncClient, None]:
    """Return an httpx.AsyncClient for a test app with dependencies overridden."""
    settings = Settings.model_validate(get_settings())
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_max_file_size_bytes = 1024 * 1024  # 1 MB for most tests
    await asyncio.to_thread(
        lambda: settings.upload_dir.mkdir(parents=True, exist_ok=True)
    )
    app = create_app(settings)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_chat_provider] = lambda: mock_chat_provider
    app.dependency_overrides[get_embedding_provider] = lambda: mock_embedding_provider
    app.dependency_overrides[get_storage_provider] = lambda: mock_storage_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_source(db: AsyncSession) -> Source:
    """A source with two processed text chunks."""
    source = Source(
        storage_key="/tmp/seeded.txt",
        file_name="seeded.txt",
        file_type="text/plain",
        file_size=12,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(source)
    await db.flush()

    for i in range(2):
        db.add(
            Chunk(
                source_id=source.id,
                chunk_index=i,
                chunk_type="text",
                text_content=f"seeded chunk {i}",
                status=ProcessingStatus.PROCESSED,
            )
        )
    await db.flush()
    return source


@pytest.fixture
async def seeded_entities(db: AsyncSession) -> tuple[Entity, Entity]:
    """Two connected entities for graph browsing tests."""
    source_entity = Entity(name="Seed Source", entity_type="concept")
    target_entity = Entity(name="Seed Target", entity_type="concept")
    db.add(source_entity)
    db.add(target_entity)
    await db.flush()
    return source_entity, target_entity


@pytest.fixture
async def seeded_relation(
    db: AsyncSession, seeded_entities: tuple[Entity, Entity], seeded_source: Source
) -> Relation:
    """A relation between the two seeded entities, provenanced by a chunk."""
    source_entity, target_entity = seeded_entities
    chunk = seeded_source.chunks[0]
    relation = Relation(
        source_entity_id=source_entity.id,
        target_entity_id=target_entity.id,
        relation_type="relates_to",
        chunk_id=chunk.id,
    )
    db.add(relation)
    await db.flush()
    return relation


@pytest.fixture
async def seeded_wiki_page(
    db: AsyncSession, seeded_entities: tuple[Entity, Entity]
) -> WikiPage:
    """A wiki page associated with the first seeded entity."""
    source_entity, _ = seeded_entities
    page = WikiPage(
        entity_id=source_entity.id,
        slug="seed-source",
        title="Seed Source",
        content="# Seed Source\n\nSeeded wiki content.",
    )
    db.add(page)
    await db.flush()
    return page
