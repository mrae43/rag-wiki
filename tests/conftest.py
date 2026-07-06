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
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from rag_wiki.db.base import Base
from rag_wiki.exceptions import StorageError
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
    Supports ``response_json`` for non-tool calls to simulate LLM JSON
    responses (e.g. query classification).
    """

    def __init__(
        self,
        response_map: dict[str, str] | None = None,
        response_json: str | None = None,
    ) -> None:
        """Init with optional response map and optional JSON response."""
        self.response_map = response_map or {}
        self.response_json = response_json

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
        content = (
            self.response_json
            if self.response_json is not None
            else f"fake-completion-for-{request.model}"
        )
        return CompletionResponse(
            content=content,
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

    async def embed(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        """Return a zero-filled embedding vector for each input text."""
        dims = get_settings().embedding_dimensions
        return [[0.0] * dims for _ in texts]


class FakeStorageProvider:
    """
    Test double that satisfies the StorageProvider protocol.

    Stores files in an in-memory dict keyed by storage key. Does not
    touch the filesystem. Useful for unit tests that need a storage
    provider without I/O side effects.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def upload(self, source_id: str, file: BinaryIO, filename: str) -> str:
        """Read the file into memory and return the storage key."""
        key = f"sources/{source_id}"
        self._store[key] = file.read()
        return key

    async def download(self, key: str) -> AsyncIterator[bytes]:
        """Yield the stored bytes for the given key."""
        data = self._store.get(key)
        if data is None:
            raise StorageError(
                f"FakeStorageProvider.download failed: key={key!r} not found"
            )
        yield data

    async def delete(self, key: str, root_dir: Path | None = None) -> None:
        """Remove the stored entry for the given key."""
        if key not in self._store:
            raise StorageError(
                f"FakeStorageProvider.delete failed: key={key!r} not found"
            )
        del self._store[key]

    async def exists(self, key: str) -> bool:
        """Return whether the key exists in the in-memory store."""
        return key in self._store

    async def write_text(
        self,
        key: str,
        content: str,
        root_dir: Path | None = None,
    ) -> None:
        """Store UTF-8 text in memory keyed by ``key``."""
        self._store[key] = content.encode("utf-8")

    async def read_text(self, key: str, root_dir: Path | None = None) -> str:
        """Read and decode UTF-8 text from the in-memory store."""
        data = self._store.get(key)
        if data is None:
            raise StorageError(
                f"FakeStorageProvider.read_text failed: key={key!r} not found"
            )
        return data.decode("utf-8")

    async def list_keys(
        self,
        prefix: str = "",
        root_dir: Path | None = None,
    ) -> list[str]:
        """Return sorted keys matching the given prefix."""
        return sorted(k for k in self._store if k.startswith(prefix))

    @asynccontextmanager
    async def with_temp_file(self, key: str) -> AsyncIterator[Path]:
        """Write in-memory content to a temp file, yield it, clean up."""
        data = self._store.get(key)
        if data is None:
            raise StorageError(
                f"FakeStorageProvider.with_temp_file: key={key!r} not found"
            )
        tmp = Path(tempfile.mktemp(suffix=".bin"))
        tmp.write_bytes(data)
        try:
            yield tmp
        finally:
            if tmp.exists():
                tmp.unlink()


@pytest.fixture
def mock_chat_provider() -> ChatProvider:
    """Return a deterministic chat provider for unit tests."""
    return FakeChatProvider()


@pytest.fixture
def mock_embedding_provider() -> EmbeddingProvider:
    """Return a deterministic embedding provider for unit tests."""
    return FakeEmbeddingProvider()


@pytest.fixture
def mock_storage_provider() -> FakeStorageProvider:
    """Return an in-memory storage provider for unit tests."""
    return FakeStorageProvider()
