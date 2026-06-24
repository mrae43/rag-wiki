"""
tests.storage.test_storage_local
--------------------------------
Tests for LocalStorageProvider.

Covers the full CRUD cycle: upload, download, delete, exists, and
error paths for missing files.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.local import LocalStorageProvider


@pytest.fixture
def provider(tmp_path: Path) -> LocalStorageProvider:
    """Return a LocalStorageProvider backed by a temp directory."""
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        upload_dir=tmp_path,
    )
    return LocalStorageProvider(settings)


async def test_upload_returns_storage_key(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """Upload returns a key in the format 'sources/{source_id}'."""
    data = b"hello, world"
    key = await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(data),
        filename="test.txt",
    )
    assert key == "sources/abc-123"


async def test_upload_writes_to_disk(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """Upload writes the file content to the expected path on disk."""
    data = b"hello, world"
    await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(data),
        filename="test.txt",
    )
    path = tmp_path / "sources" / "abc-123"
    assert path.read_bytes() == data


async def test_download_returns_content(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """Download streams back the exact bytes that were uploaded."""
    data = b"hello, world"
    await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(data),
        filename="test.txt",
    )
    chunks = [chunk async for chunk in provider.download("sources/abc-123")]
    assert b"".join(chunks) == data


async def test_download_raises_for_missing_key(
    provider: LocalStorageProvider,
) -> None:
    """Download raises StorageError when the key does not exist."""
    with pytest.raises(StorageError, match="not found"):
        async for _ in provider.download("sources/nonexistent"):
            pass


async def test_delete_removes_file(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """Delete removes the file from disk."""
    data = b"hello, world"
    await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(data),
        filename="test.txt",
    )
    await provider.delete("sources/abc-123")
    assert not (tmp_path / "sources" / "abc-123").exists()


async def test_delete_raises_for_missing_key(
    provider: LocalStorageProvider,
) -> None:
    """Delete raises StorageError when the key does not exist."""
    with pytest.raises(StorageError, match="not found"):
        await provider.delete("sources/nonexistent")


async def test_exists_returns_true_for_existing_key(
    provider: LocalStorageProvider,
) -> None:
    """Exists returns True when the file is on disk."""
    await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(b"data"),
        filename="test.txt",
    )
    assert await provider.exists("sources/abc-123") is True


async def test_exists_returns_false_for_missing_key(
    provider: LocalStorageProvider,
) -> None:
    """Exists returns False when the file is not on disk."""
    assert await provider.exists("sources/nonexistent") is False


async def test_upload_large_file(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """Upload handles files larger than the internal chunk size."""
    data = b"x" * (65_536 * 3 + 1)
    await provider.upload(
        source_id="large",
        file=io.BytesIO(data),
        filename="large.bin",
    )
    chunks = [chunk async for chunk in provider.download("sources/large")]
    assert b"".join(chunks) == data
