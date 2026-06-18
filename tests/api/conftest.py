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

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from rag_wiki.api.dependencies import get_chat_provider, get_db, get_embedding_provider
from rag_wiki.db.base import Base
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
) -> AsyncGenerator[AsyncClient, None]:
    """Return an httpx.AsyncClient for a test app with dependencies overridden."""
    settings = Settings.model_validate(get_settings())
    app = create_app(settings)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_chat_provider] = lambda: mock_chat_provider
    app.dependency_overrides[get_embedding_provider] = lambda: mock_embedding_provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    app.dependency_overrides.clear()
