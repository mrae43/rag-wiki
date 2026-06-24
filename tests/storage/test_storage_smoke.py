"""
tests.storage.test_storage_smoke
--------------------------------
Protocol contract tests for StorageProvider implementations.

Every StorageProvider must pass these tests to be considered a correct
implementation. Tests verify the full CRUD cycle and the with_temp_file
convenience context manager.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO, Protocol

import pytest

from rag_wiki.exceptions import StorageError


class StorageProviderContract(Protocol):
    """
    Minimal StorageProvider protocol for contract testing.

    Duplicated here so the contract tests do not depend on the production
    protocol definition — any implementation satisfying this structural
    subtype is valid.
    """

    async def upload(self, source_id: str, file: BinaryIO, filename: str) -> str: ...
    def download(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...


async def _store_and_retrieve(provider: StorageProviderContract) -> None:
    """Upload a file and verify download returns the same content."""
    data = b"contract-test-data"
    key = await provider.upload(
        source_id="contract-test",
        file=io.BytesIO(data),
        filename="contract.txt",
    )
    assert isinstance(key, str)
    assert key == "sources/contract-test"

    chunks = [chunk async for chunk in provider.download(key)]
    assert b"".join(chunks) == data


async def _exists_after_upload(provider: StorageProviderContract) -> None:
    """Exists returns True immediately after upload."""
    key = await provider.upload(
        source_id="exists-test",
        file=io.BytesIO(b"data"),
        filename="exists.txt",
    )
    assert await provider.exists(key) is True


async def _exists_after_delete(provider: StorageProviderContract) -> None:
    """Exists returns False after delete."""
    key = await provider.upload(
        source_id="delete-test",
        file=io.BytesIO(b"data"),
        filename="delete.txt",
    )
    await provider.delete(key)
    assert await provider.exists(key) is False


async def _delete_raises_for_missing(provider: StorageProviderContract) -> None:
    """Delete raises StorageError for a non-existent key."""
    with pytest.raises(StorageError):
        await provider.delete("sources/does-not-exist")


async def _download_raises_for_missing(provider: StorageProviderContract) -> None:
    """Download raises StorageError for a non-existent key."""
    with pytest.raises(StorageError):
        async for _ in provider.download("sources/does-not-exist"):
            pass


async def _with_temp_file_roundtrip(
    provider: StorageProviderContract,
) -> None:
    """with_temp_file writes content to a temp path and cleans up."""
    data = b"temp-file-data"
    key = "sources/temp-test"
    # Direct upload — with_temp_file is read-only
    await provider.upload(
        source_id="temp-test",
        file=io.BytesIO(data),
        filename="temp.txt",
    )

    # This only works if provider has with_temp_file. For protocol
    # contract testing we check it's callable on the instance.
    with_temp = getattr(provider, "with_temp_file", None)
    if with_temp is None:
        pytest.skip("provider does not implement with_temp_file")

    tmp_path: Path | None = None
    async with with_temp(key) as p:
        tmp_path = p
        assert p.read_bytes() == data
        assert p.exists()

    # Temp file should be cleaned up after context exit
    assert tmp_path is not None
    assert not tmp_path.exists()


# ---------------------------------------------------------------------------
# Mark these so test runners can collect them and parametrize against
# concrete provider fixtures defined in test-specific conftest files
# or inline.
# ---------------------------------------------------------------------------

CONTRACT_TESTS = [
    _store_and_retrieve,
    _exists_after_upload,
    _exists_after_delete,
    _delete_raises_for_missing,
    _download_raises_for_missing,
    _with_temp_file_roundtrip,
]
