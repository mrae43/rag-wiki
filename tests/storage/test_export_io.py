"""
tests.storage.test_export_io
----------------------------
Tests for the StorageProvider text-IO extensions used by the OKF exporter:
write_text, read_text, and list_keys.

LocalStorageProvider is exercised fully. S3StorageProvider tests are skipped
unless the rag-wiki[s3] extra is installed and RAG_WIKI_TEST_S3_ENDPOINT is set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.local import LocalStorageProvider

try:
    from rag_wiki.storage.s3 import S3StorageProvider
except ImportError:
    S3StorageProvider = None  # type: ignore[assignment,misc]

_DATABASE_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest.fixture
def local_provider(tmp_path: Path) -> LocalStorageProvider:
    """Return a LocalStorageProvider backed by a temp directory."""
    settings = Settings(
        database_url=_DATABASE_URL,
        upload_dir=tmp_path,
    )
    return LocalStorageProvider(settings)


# --- LocalStorageProvider text IO -------------------------------------------


async def test_write_text_creates_file(
    local_provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """write_text creates nested directories and writes UTF-8 content."""
    await local_provider.write_text("entities/acme.md", "# Acme\n")
    path = tmp_path / "entities" / "acme.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Acme\n"


async def test_write_text_uses_root_dir_override(
    local_provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """write_text respects an explicit root_dir override."""
    export_root = tmp_path / "exports"
    await local_provider.write_text(
        "entities/acme.md",
        "# Acme\n",
        root_dir=export_root,
    )
    assert (export_root / "entities" / "acme.md").exists()
    assert not (tmp_path / "entities" / "acme.md").exists()


async def test_read_text_roundtrip(
    local_provider: LocalStorageProvider,
) -> None:
    """read_text returns exactly what write_text stored."""
    await local_provider.write_text("sources/log.md", "- entry\n")
    content = await local_provider.read_text("sources/log.md")
    assert content == "- entry\n"


async def test_read_text_uses_root_dir_override(
    local_provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """read_text resolves paths relative to root_dir when provided."""
    export_root = tmp_path / "exports"
    await local_provider.write_text("index.md", "hello", root_dir=export_root)
    assert await local_provider.read_text("index.md", root_dir=export_root) == "hello"


async def test_read_text_raises_for_missing_key(
    local_provider: LocalStorageProvider,
) -> None:
    """read_text raises StorageError when the file does not exist."""
    with pytest.raises(StorageError, match="not found"):
        await local_provider.read_text("entities/missing.md")


async def test_list_keys_returns_sorted_relative_paths(
    local_provider: LocalStorageProvider,
) -> None:
    """list_keys returns relative file paths sorted lexicographically."""
    await local_provider.write_text("entities/b.md", "b")
    await local_provider.write_text("entities/a.md", "a")
    await local_provider.write_text("sources/c.md", "c")

    keys = await local_provider.list_keys()
    assert keys == ["entities/a.md", "entities/b.md", "sources/c.md"]


async def test_list_keys_with_prefix(
    local_provider: LocalStorageProvider,
) -> None:
    """list_keys filters by prefix."""
    await local_provider.write_text("entities/a.md", "a")
    await local_provider.write_text("sources/b.md", "b")

    assert await local_provider.list_keys(prefix="entities/") == ["entities/a.md"]


async def test_list_keys_uses_root_dir_override(
    local_provider: LocalStorageProvider,
    tmp_path: Path,
) -> None:
    """list_keys scopes to root_dir when provided."""
    export_root = tmp_path.parent / "exports"
    await local_provider.write_text("index.md", "i", root_dir=export_root)

    assert await local_provider.list_keys(root_dir=export_root) == ["index.md"]
    assert await local_provider.list_keys() == []


async def test_list_keys_returns_empty_for_missing_prefix(
    local_provider: LocalStorageProvider,
) -> None:
    """list_keys returns an empty list when the prefix directory does not exist."""
    assert await local_provider.list_keys(prefix="nonexistent/") == []


# --- S3StorageProvider contract tests (optional) ----------------------------


pytestmark_s3 = pytest.mark.skipif(
    S3StorageProvider is None or not os.getenv("RAG_WIKI_TEST_S3_ENDPOINT"),
    reason="rag-wiki[s3] extra not installed or RAG_WIKI_TEST_S3_ENDPOINT not set",
)


@pytest.fixture
def s3_provider() -> S3StorageProvider:
    settings = Settings(
        database_url=_DATABASE_URL,
        s3_endpoint_url=os.environ["RAG_WIKI_TEST_S3_ENDPOINT"],
        s3_access_key_id=os.getenv("RAG_WIKI_TEST_S3_ACCESS_KEY_ID", "test"),
        s3_secret_access_key=os.getenv("RAG_WIKI_TEST_S3_SECRET_ACCESS_KEY", "test"),
        s3_bucket=os.getenv("RAG_WIKI_TEST_S3_BUCKET", "rag-wiki-test"),
        s3_region=os.getenv("RAG_WIKI_TEST_S3_REGION", "us-east-1"),
    )
    return S3StorageProvider(settings)


@pytest.mark.parametrize(
    "key",
    ["exports/entities/a.md", "exports/index.md"],
)
@pytestmark_s3
async def test_s3_write_read_text_roundtrip(
    s3_provider: S3StorageProvider,
    key: str,
) -> None:
    """S3 write_text and read_text round-trip UTF-8 content."""
    content = f"# {key}\n"
    await s3_provider.write_text(key, content)
    assert await s3_provider.read_text(key) == content


@pytestmark_s3
async def test_s3_list_keys_with_prefix(
    s3_provider: S3StorageProvider,
) -> None:
    """S3 list_keys returns sorted keys filtered by prefix."""
    await s3_provider.write_text("exports/entities/a.md", "a")
    await s3_provider.write_text("exports/sources/b.md", "b")
    await s3_provider.write_text("uploads/c.bin", "c")

    keys = await s3_provider.list_keys(prefix="exports/")
    assert keys == ["exports/entities/a.md", "exports/sources/b.md"]
