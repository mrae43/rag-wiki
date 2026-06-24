"""
rag_wiki.storage.base
---------------------
Protocol definition and default implementations for storage providers.

Defines StorageProvider (the abstract interface) and the default
with_temp_file convenience context manager. Concrete implementations
live in rag_wiki.storage.* and explicitly inherit from StorageProvider.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO, Protocol

import aiofiles


class StorageProvider(Protocol):
    """
    Protocol for storing and retrieving source documents.

    Two implementations: LocalStorageProvider (dev default) and
    S3StorageProvider (S3-compatible backends like SeaweedFS).
    """

    async def upload(self, source_id: str, file: BinaryIO, filename: str) -> str:
        """
        Upload a file and return its storage key.

        Args:
            source_id: UUID string for the source.
            file: Open binary file-like object to upload.
            filename: Original filename (for metadata, not path).

        Returns:
            Opaque storage key (e.g. 'sources/{source_id}').

        Raises:
            StorageError: If the upload fails.
        """
        ...

    def download(self, key: str) -> AsyncIterator[bytes]:
        """
        Stream file contents for the given storage key.

        Args:
            key: Storage key returned by upload().

        Yields:
            Chunks of raw bytes.

        Raises:
            StorageError: If the download fails or the key does not exist.
        """
        ...

    async def delete(self, key: str) -> None:
        """
        Delete the file identified by storage key.

        Args:
            key: Storage key returned by upload().

        Raises:
            StorageError: If the deletion fails.
        """
        ...

    async def exists(self, key: str) -> bool:
        """
        Check whether a file exists for the given storage key.

        Args:
            key: Storage key returned by upload().

        Returns:
            True if the file exists, False otherwise.
        """
        ...

    @asynccontextmanager
    async def with_temp_file(self, key: str) -> AsyncIterator[Path]:
        """
        Download to a temp file, yield the path, clean up on exit.

        Convenience wrapper around download() for synchronous parsers that
        need a filesystem path (e.g. pymupdf, unstructured). Implementations
        may override this for efficiency (e.g. direct S3 download).

        Args:
            key: Storage key returned by upload().

        Yields:
            Path to a temporary file containing the downloaded content.

        Raises:
            StorageError: If the download fails.
        """
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            async with aiofiles.open(tmp_path, "wb") as f:
                async for chunk in self.download(key):
                    await f.write(chunk)
            yield tmp_path
        finally:
            if tmp_path.exists():
                os.unlink(tmp_path)
