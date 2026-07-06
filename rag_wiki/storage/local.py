"""
rag_wiki.storage.local
----------------------
Local filesystem implementation of the StorageProvider protocol.

Writes uploaded files to a configurable upload directory
(settings.upload_dir). Used as the default storage backend in
development and single-instance deployments.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import aiofiles
import structlog

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.base import StorageProvider

logger = structlog.get_logger(__name__)

_CHUNK_SIZE: int = 65_536


class LocalStorageProvider(StorageProvider):
    """
    StorageProvider that reads/writes files to a local directory.

    Files are stored at ``{upload_dir}/{key}`` where key is
    ``sources/{source_id}``. The upload_dir defaults to ``./uploads``
    and is configured via the ``UPLOAD_DIR`` env var.
    """

    def __init__(self, settings: Settings) -> None:
        self._upload_dir = settings.upload_dir

    async def upload(self, source_id: str, file: BinaryIO, filename: str) -> str:
        """
        Write the uploaded file to local storage.

        Args:
            source_id: UUID string for the source.
            file: Open binary file-like object. Consumed immediately.
            filename: Ignored — only source_id determines the path.

        Returns:
            Storage key ``sources/{source_id}``.

        Raises:
            StorageError: If the write fails.
        """
        key = f"sources/{source_id}"
        dst = self._upload_dir / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with aiofiles.open(dst, "wb") as f:
                while chunk := file.read(_CHUNK_SIZE):
                    await f.write(chunk)
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.upload failed",
                key=key,
                path=str(dst),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.upload failed: key={key!r}"
            ) from exc
        return key

    async def download(self, key: str) -> AsyncIterator[bytes]:
        """
        Stream the file contents for the given storage key.

        Args:
            key: Storage key returned by upload().

        Yields:
            Chunks of raw bytes.

        Raises:
            StorageError: If the file does not exist or cannot be read.
        """
        path = self._upload_dir / key
        if not path.exists():
            raise StorageError(
                f"LocalStorageProvider.download failed: key={key!r} "
                f"path={str(path)!r} not found"
            )
        try:
            async with aiofiles.open(path, "rb") as f:
                while chunk := await f.read(_CHUNK_SIZE):
                    yield chunk
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.download failed",
                key=key,
                path=str(path),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.download failed: key={key!r}"
            ) from exc

    async def delete(self, key: str, root_dir: Path | None = None) -> None:
        """
        Delete the file identified by storage key.

        Args:
            key: Storage key returned by upload().
            root_dir: Optional filesystem root override. Defaults to
                ``settings.upload_dir``.

        Raises:
            StorageError: If the file does not exist or cannot be deleted.
        """
        path = self._resolve_root(root_dir) / key
        if not path.exists():
            raise StorageError(
                f"LocalStorageProvider.delete failed: key={key!r} "
                f"path={str(path)!r} not found"
            )
        try:
            path.unlink()
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.delete failed",
                key=key,
                path=str(path),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.delete failed: key={key!r}"
            ) from exc

    async def exists(self, key: str) -> bool:
        """
        Check whether a file exists for the given storage key.

        Args:
            key: Storage key returned by upload().

        Returns:
            True if the file exists, False otherwise.
        """
        return (self._upload_dir / key).exists()

    def _resolve_root(self, root_dir: Path | None) -> Path:
        """Return the effective root directory for text/list operations."""
        return root_dir if root_dir is not None else self._upload_dir

    async def write_text(
        self,
        key: str,
        content: str,
        root_dir: Path | None = None,
    ) -> None:
        """
        Write UTF-8 text to ``{root_dir}/{key}``.

        Args:
            key: Relative path under the root directory.
            content: Text content to write.
            root_dir: Optional root directory override. Defaults to
                ``settings.upload_dir``.

        Raises:
            StorageError: If the write fails.
        """
        dst = self._resolve_root(root_dir) / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with aiofiles.open(dst, "w", encoding="utf-8") as f:
                await f.write(content)
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.write_text failed",
                key=key,
                path=str(dst),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.write_text failed: key={key!r}"
            ) from exc

    async def read_text(self, key: str, root_dir: Path | None = None) -> str:
        """
        Read UTF-8 text from ``{root_dir}/{key}``.

        Args:
            key: Relative path under the root directory.
            root_dir: Optional root directory override.

        Returns:
            The decoded text content.

        Raises:
            StorageError: If the file does not exist or cannot be read.
        """
        path = self._resolve_root(root_dir) / key
        if not path.exists():
            raise StorageError(
                f"LocalStorageProvider.read_text failed: key={key!r} "
                f"path={str(path)!r} not found"
            )
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                content: str = await f.read()
                return content
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.read_text failed",
                key=key,
                path=str(path),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.read_text failed: key={key!r}"
            ) from exc

    async def list_keys(
        self,
        prefix: str = "",
        root_dir: Path | None = None,
    ) -> list[str]:
        """
        List relative paths under ``{root_dir}/{prefix}``.

        Args:
            prefix: Directory prefix to filter by.
            root_dir: Optional root directory override.

        Returns:
            Sorted list of relative keys.
        """
        base = self._resolve_root(root_dir) / prefix
        if not base.exists():
            return []
        try:
            paths = [
                p.relative_to(self._resolve_root(root_dir)).as_posix()
                for p in base.rglob("*")
                if p.is_file()
            ]
        except OSError as exc:
            logger.error(
                "LocalStorageProvider.list_keys failed",
                prefix=prefix,
                path=str(base),
                error=str(exc),
            )
            raise StorageError(
                f"LocalStorageProvider.list_keys failed: prefix={prefix!r}"
            ) from exc
        return sorted(paths)
