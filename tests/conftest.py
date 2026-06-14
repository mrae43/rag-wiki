"""
tests/conftest
------------
Shared test fixtures for the RAGWiki test suite.

Provides: database engine, per-test rollback session, mock LLM provider,
FastAPI test client, and a settings override.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ragwiki.db.base import Base
from ragwiki.main import app as fastapi_app
from ragwiki.providers.base import LLMProvider
from ragwiki.settings import Settings, get_settings

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://ragwiki:ragwiki@localhost:5432/ragwiki_test",
)


@pytest.fixture(scope="session", autouse=True)
def settings() -> Settings:
    """Override settings with test-safe values for the whole session."""
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["LLM_API_KEY"] = "test-key"
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create the test database engine and all tables."""
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets a rolled-back transaction — no test pollution."""
    async with engine.connect() as conn, conn.begin() as trans:
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await trans.rollback()


class FakeLLMProvider:
    """Test double that satisfies the LLMProvider protocol."""

    async def complete(self, prompt: str, model: str) -> str:
        return f"fake-completion-for-{prompt[:20]}"

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    async def caption_image(self, image_bytes: bytes, model: str) -> str:
        return "fake image caption"


@pytest.fixture
def mock_llm_provider() -> LLMProvider:
    """Return a deterministic LLM provider for unit tests."""
    return FakeLLMProvider()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client with DB dependency overridden."""
    # Import lazily to avoid triggering module-level get_settings() before
    # the settings fixture has patched the environment.
    from ragwiki.db.session import get_db

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
