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


# --- exists with root_dir override -----------------------------------------


async def test_exists_uses_root_dir_override(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """exists resolves keys relative to root_dir when provided."""
    export_root = tmp_path / "exports"
    await provider.write_text("index.md", "hello", root_dir=export_root)
    assert await provider.exists("index.md", root_dir=export_root) is True
    assert await provider.exists("missing.md", root_dir=export_root) is False


async def test_exists_without_root_dir_uses_upload_dir(
    provider: LocalStorageProvider,
) -> None:
    """exists without root_dir checks the default upload_dir."""
    await provider.upload(
        source_id="abc-123",
        file=io.BytesIO(b"data"),
        filename="test.txt",
    )
    assert await provider.exists("sources/abc-123") is True
    assert await provider.exists("sources/nope") is False


# --- Path traversal guard --------------------------------------------------


async def test_write_text_rejects_absolute_key(
    provider: LocalStorageProvider,
) -> None:
    """write_text rejects absolute keys that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.write_text("/etc/passwd", "malicious")


async def test_write_text_rejects_parent_traversal(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """write_text rejects ``..`` sequences that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.write_text("../../etc/passwd", "malicious")


async def test_read_text_rejects_absolute_key(
    provider: LocalStorageProvider,
) -> None:
    """read_text rejects absolute keys that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.read_text("/etc/passwd")


async def test_read_text_rejects_parent_traversal(
    provider: LocalStorageProvider,
) -> None:
    """read_text rejects ``..`` sequences that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.read_text("../../etc/passwd")


async def test_delete_rejects_absolute_key(
    provider: LocalStorageProvider,
) -> None:
    """delete rejects absolute keys that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.delete("/etc/passwd")


async def test_delete_rejects_parent_traversal(
    provider: LocalStorageProvider,
) -> None:
    """delete rejects ``..`` sequences that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.delete("../../etc/passwd")


async def test_exists_rejects_absolute_key(
    provider: LocalStorageProvider,
) -> None:
    """exists rejects absolute keys that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.exists("/etc/passwd")


async def test_exists_rejects_parent_traversal(
    provider: LocalStorageProvider,
) -> None:
    """exists rejects ``..`` sequences that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.exists("../../etc/passwd")


async def test_list_keys_rejects_parent_traversal(
    provider: LocalStorageProvider,
) -> None:
    """list_keys rejects prefixes that escape the root."""
    with pytest.raises(StorageError, match="escapes root"):
        await provider.list_keys(prefix="../../etc/")


async def test_safe_key_within_nested_dir_is_allowed(
    provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """A key with ``..`` that stays within the root is allowed."""
    export_root = tmp_path / "exports"
    await provider.write_text("entities/a.md", "a", root_dir=export_root)
    # ``entities/../entities/a.md`` resolves within root — should succeed.
    content = await provider.read_text(
        "entities/../entities/a.md",
        root_dir=export_root,
    )
    assert content == "a"
