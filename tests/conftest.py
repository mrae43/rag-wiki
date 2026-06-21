"""
tests/conftest
------------
Shared test fixtures for the RAGWiki test suite.

Provides: database engine, per-test rollback session, mock LLM providers,
FastAPI test client, and a settings override.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from rag_wiki.db.base import Base
from rag_wiki.main import app as fastapi_app
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    CompletionResponse,
    EmbeddingProvider,
    ToolCall,
)
from rag_wiki.settings import Settings, get_settings

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://rag_wiki:rag_wiki@localhost:5432/rag_wiki_test",
)


@pytest.fixture(scope="session", autouse=True)
def ensure_spacy_model() -> None:
    """Ensure en_core_web_sm is available for the unstructured parser."""
    try:
        import spacy

        spacy.load("en_core_web_sm")
    except OSError:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pytest.skip(
                "en_core_web_sm unavailable — skipping tests that need the parser"
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


class FakeChatProvider:
    """Test double that satisfies the ChatProvider protocol.

    Supports per-tool canned responses via ``response_map`` so that tests
    for extraction and resolution can return different JSON payloads.
    """

    def __init__(self, response_map: dict[str, str] | None = None) -> None:
        """Init with optional response map mapping tool names to canned JSON."""
        self.response_map = response_map or {}

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return a fake response, using the response map if a tool name matches."""
        if request.tools and self.response_map:
            # Return the first matching tool response from the map.
            for tool in request.tools:
                if tool.name in self.response_map:
                    return CompletionResponse(
                        content=f"fake-completion-for-{request.model}",
                        tool_calls=[
                            ToolCall(
                                id="fake-tool-1",
                                name=tool.name,
                                arguments=self.response_map[tool.name],
                            )
                        ],
                    )
        return CompletionResponse(
            content=f"fake-completion-for-{request.model}",
            tool_calls=[
                ToolCall(
                    id="fake-tool-1",
                    name="fake_tool",
                    arguments='{"input": "test"}',
                )
            ]
            if request.tools
            else [],
        )

    async def caption_image(
        self,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> str:
        """Return a fake caption string based on the image MIME type."""
        return f"fake-caption-{image_mime_type}"


class FakeEmbeddingProvider:
    """Test double that satisfies the EmbeddingProvider protocol."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return a zero-filled embedding vector for each input text."""
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


@pytest.fixture
def mock_chat_provider() -> ChatProvider:
    """Return a deterministic chat provider for unit tests."""
    return FakeChatProvider()


@pytest.fixture
def mock_embedding_provider() -> EmbeddingProvider:
    """Return a deterministic embedding provider for unit tests."""
    return FakeEmbeddingProvider()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client with DB dependency overridden."""
    # Import lazily to avoid triggering module-level get_settings() before
    # the settings fixture has patched the environment.
    from rag_wiki.db.session import get_db

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
