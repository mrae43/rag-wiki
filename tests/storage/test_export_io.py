"""
tests.storage.test_export_io
----------------------------
Tests for the StorageProvider text-IO extensions used by the OKF exporter:
write_text, read_text, list_keys, and the root_dir key-prefix support.

LocalStorageProvider is exercised fully. S3StorageProvider is covered by
two test groups:

1. Live contract tests — skipped unless ``rag-wiki[s3]`` is installed AND
   ``RAG_WIKI_TEST_S3_ENDPOINT`` is set (hits a real S3-compatible backend).
2. Mocked unit tests — skipped only when the ``[s3]`` extra is absent; they
   patch ``provider._session`` so no network calls are made and assert that
   the root_dir prefix and dynamic ContentType reach the S3 client.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


# --- S3StorageProvider mocked unit tests (no live endpoint) -----------------
# These run whenever the [s3] extra is installed. They patch _session so no
# network calls are made, and assert that root_dir prefixing and the dynamic
# ContentType reach the underlying S3 client.

pytestmark_s3_mockable = pytest.mark.skipif(
    S3StorageProvider is None,
    reason="rag-wiki[s3] extra not installed",
)


def _make_mocked_s3_provider() -> S3StorageProvider:
    """Return an S3StorageProvider whose aioboto3 session is a mock.

    The client is a ``MagicMock`` so sync methods like ``get_paginator``
    return plain objects; the async S3 operations (``put_object``,
    ``get_object``, ``delete_object``, ``head_object``) are individually
    set to ``AsyncMock`` so they can be awaited.
    """
    settings = Settings(
        database_url=_DATABASE_URL,
        s3_endpoint_url="http://test-s3.local",
        s3_access_key_id="test",
        s3_secret_access_key="test",
        s3_bucket="rag-wiki-test",
        s3_region="us-east-1",
    )
    provider = S3StorageProvider(settings)
    mock_client = MagicMock()
    mock_client.put_object = AsyncMock()
    mock_client.get_object = AsyncMock()
    mock_client.delete_object = AsyncMock()
    mock_client.head_object = AsyncMock()
    mock_client.upload_fileobj = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_session = MagicMock()
    mock_session.client.return_value = mock_client
    provider._session = mock_session
    return provider


def _client(provider: S3StorageProvider) -> AsyncMock:
    """Return the mock S3 client from a mocked provider."""
    return provider._session.client.return_value  # type: ignore[no-any-return]


@pytestmark_s3_mockable
async def test_s3_resolve_prefix_none() -> None:
    """_resolve_prefix(None) returns an empty string (bare keys)."""
    assert S3StorageProvider._resolve_prefix(None) == ""


@pytestmark_s3_mockable
async def test_s3_resolve_prefix_dot() -> None:
    """_resolve_prefix(Path('.')) returns an empty string."""
    assert S3StorageProvider._resolve_prefix(Path(".")) == ""


@pytestmark_s3_mockable
async def test_s3_resolve_prefix_simple_dir() -> None:
    """_resolve_prefix(Path('exports')) returns 'exports/'."""
    assert S3StorageProvider._resolve_prefix(Path("exports")) == "exports/"


@pytestmark_s3_mockable
async def test_s3_resolve_prefix_nested_dir() -> None:
    """_resolve_prefix(Path('a/b')) returns 'a/b/'."""
    assert S3StorageProvider._resolve_prefix(Path("a/b")) == "a/b/"


@pytestmark_s3_mockable
async def test_s3_resolve_prefix_strips_trailing_slash() -> None:
    """_resolve_prefix normalizes trailing slashes to a single one."""
    assert S3StorageProvider._resolve_prefix(Path("exports/")) == "exports/"


@pytestmark_s3_mockable
async def test_s3_write_text_prefixes_key_with_root_dir() -> None:
    """write_text prepends the root_dir prefix to the S3 key."""
    provider = _make_mocked_s3_provider()
    await provider.write_text("entities/acme.md", "# Acme\n", root_dir=Path("exports"))
    client = _client(provider)
    client.put_object.assert_awaited_once()
    assert client.put_object.call_args.kwargs["Key"] == "exports/entities/acme.md"


@pytestmark_s3_mockable
async def test_s3_write_text_no_root_dir_uses_bare_key() -> None:
    """write_text without root_dir writes to the bare key (no prefix)."""
    provider = _make_mocked_s3_provider()
    await provider.write_text("index.md", "# Index\n")
    client = _client(provider)
    client.put_object.assert_awaited_once()
    assert client.put_object.call_args.kwargs["Key"] == "index.md"


@pytestmark_s3_mockable
async def test_s3_write_text_content_type_markdown() -> None:
    """write_text sets text/markdown content type for .md files."""
    provider = _make_mocked_s3_provider()
    await provider.write_text("page.md", "# Page\n")
    client = _client(provider)
    assert (
        client.put_object.call_args.kwargs["ContentType"]
        == "text/markdown; charset=utf-8"
    )


@pytestmark_s3_mockable
async def test_s3_write_text_content_type_json() -> None:
    """write_text sets application/json content type for .json files."""
    provider = _make_mocked_s3_provider()
    await provider.write_text("manifest.json", "{}")
    client = _client(provider)
    assert client.put_object.call_args.kwargs["ContentType"] == "application/json"


@pytestmark_s3_mockable
async def test_s3_write_text_content_type_fallback() -> None:
    """write_text falls back to text/plain for unknown extensions."""
    provider = _make_mocked_s3_provider()
    await provider.write_text("notes.txt", "notes")
    client = _client(provider)
    assert (
        client.put_object.call_args.kwargs["ContentType"] == "text/plain; charset=utf-8"
    )


@pytestmark_s3_mockable
async def test_s3_read_text_prefixes_key() -> None:
    """read_text prepends the root_dir prefix."""
    provider = _make_mocked_s3_provider()
    body = AsyncMock()
    body.read.return_value = b"hello"
    _client(provider).get_object.return_value = {"Body": body}

    result = await provider.read_text("acme.md", root_dir=Path("exports/entities"))
    assert result == "hello"
    assert _client(provider).get_object.call_args.kwargs["Key"] == (
        "exports/entities/acme.md"
    )


@pytestmark_s3_mockable
async def test_s3_delete_prefixes_key() -> None:
    """delete prepends the root_dir prefix."""
    provider = _make_mocked_s3_provider()
    await provider.delete("acme.md", root_dir=Path("exports"))
    client = _client(provider)
    client.delete_object.assert_awaited_once()
    assert client.delete_object.call_args.kwargs["Key"] == "exports/acme.md"


@pytestmark_s3_mockable
async def test_s3_exists_prefixes_key() -> None:
    """exists prepends the root_dir prefix."""
    provider = _make_mocked_s3_provider()
    _client(provider).head_object.return_value = {}
    result = await provider.exists("acme.md", root_dir=Path("exports"))
    assert result is True
    client = _client(provider)
    client.head_object.assert_awaited_once()
    assert client.head_object.call_args.kwargs["Key"] == "exports/acme.md"


@pytestmark_s3_mockable
async def test_s3_exists_returns_false_for_404() -> None:
    """exists returns False when S3 returns a 404 NoSuchKey error."""
    from botocore.exceptions import ClientError

    provider = _make_mocked_s3_provider()
    error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
    _client(provider).head_object.side_effect = ClientError(
        error_response, "HeadObject"
    )
    result = await provider.exists("missing.md", root_dir=Path("exports"))
    assert result is False


@pytestmark_s3_mockable
async def test_s3_list_keys_strips_root_prefix() -> None:
    """list_keys returns keys relative to the root_dir prefix."""
    provider = _make_mocked_s3_provider()

    async def _pages() -> AsyncGenerator[dict[str, list[dict[str, str]]], None]:
        yield {
            "Contents": [
                {"Key": "exports/entities/a.md"},
                {"Key": "exports/entities/b.md"},
            ]
        }

    paginator = MagicMock()
    paginator.paginate.return_value = _pages()
    _client(provider).get_paginator.return_value = paginator

    keys = await provider.list_keys(prefix="entities/", root_dir=Path("exports"))
    assert keys == ["entities/a.md", "entities/b.md"]


@pytestmark_s3_mockable
async def test_s3_list_keys_no_root_dir_returns_full_keys() -> None:
    """list_keys without root_dir returns full keys (no stripping)."""
    provider = _make_mocked_s3_provider()

    async def _pages() -> AsyncGenerator[dict[str, list[dict[str, str]]], None]:
        yield {
            "Contents": [
                {"Key": "exports/entities/a.md"},
                {"Key": "exports/sources/b.md"},
            ]
        }

    paginator = MagicMock()
    paginator.paginate.return_value = _pages()
    _client(provider).get_paginator.return_value = paginator

    keys = await provider.list_keys(prefix="exports/")
    assert keys == ["exports/entities/a.md", "exports/sources/b.md"]
