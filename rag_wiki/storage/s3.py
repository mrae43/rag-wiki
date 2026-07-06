"""
rag_wiki.storage.s3
------------------
S3-compatible storage provider using aioboto3.
Supports AWS S3, MinIO, SeaweedFS, and any S3-compatible backend.
Does NOT handle local filesystem storage — use LocalStorageProvider for that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import aioboto3
import structlog
from botocore.exceptions import ClientError

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.base import StorageProvider

logger = structlog.get_logger(__name__)

_CHUNK_SIZE: int = 65_536


def _content_type_for(key: str) -> str:
    """Return the MIME content type for a storage key based on its extension.

    Args:
        key: Storage key (e.g. ``entities/slug.md`` or ``manifest.json``).

    Returns:
        ``text/markdown; charset=utf-8`` for ``.md`` keys,
        ``application/json`` for ``.json`` keys, and
        ``text/plain; charset=utf-8`` as the fallback.
    """
    if key.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if key.endswith(".json"):
        return "application/json"
    return "text/plain; charset=utf-8"


class S3StorageProvider(StorageProvider):
    """
    StorageProvider backed by an S3-compatible object store.

    Uses aioboto3 for async S3 operations. Supports any S3-compatible
    backend (AWS S3, SeaweedFS, MinIO, etc.) via the ``endpoint_url``
    setting. Source files are stored with keys like ``sources/{source_id}``.
    OKF bundle exports are written under a key prefix derived from the
    ``root_dir`` argument (e.g. ``Path("exports")`` → ``"exports/"``),
    keeping the abstraction symmetric with ``LocalStorageProvider``.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._endpoint_url = settings.s3_endpoint_url or None
        self._session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )

    @staticmethod
    def _resolve_prefix(root_dir: Path | None) -> str:
        """Normalize a ``root_dir`` to an S3 key prefix.

        ``Path("exports")`` becomes ``"exports/"`` so that text-IO methods
        write to ``exports/entities/slug.md`` etc. ``None`` or ``Path(".")``
        become ``""`` (bare keys, no prefix). Trailing slashes are stripped
        before re-adding exactly one, so ``Path("exports/")`` and
        ``Path("exports")`` produce the same prefix.

        Args:
            root_dir: Bundle root directory (filesystem-style path) or None.

        Returns:
            An S3 key prefix string (possibly empty).
        """
        if root_dir is None:
            return ""
        normalized = Path(root_dir)
        if str(normalized) in (".", "", "/"):
            return ""
        return f"{normalized.as_posix().rstrip('/')}/"

    async def upload(self, source_id: str, file: BinaryIO, filename: str) -> str:
        key = f"sources/{source_id}"
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.upload_fileobj(file, self._bucket, key)
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.upload failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(f"S3StorageProvider.upload failed: key={key!r}") from exc
        return key

    async def download(self, key: str) -> AsyncIterator[bytes]:
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
                body = response["Body"]
                chunk = await body.read(_CHUNK_SIZE)
                while chunk:
                    yield chunk
                    chunk = await body.read(_CHUNK_SIZE)
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.download failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(
                f"S3StorageProvider.download failed: key={key!r}"
            ) from exc

    async def delete(self, key: str, root_dir: Path | None = None) -> None:
        """Delete the object identified by key, honoring the root_dir prefix."""
        prefix = self._resolve_prefix(root_dir)
        full_key = f"{prefix}{key}"
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.delete_object(Bucket=self._bucket, Key=full_key)
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.delete failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(f"S3StorageProvider.delete failed: key={key!r}") from exc

    async def exists(self, key: str, root_dir: Path | None = None) -> bool:
        """Check whether an object exists for the given key, honoring the prefix."""
        prefix = self._resolve_prefix(root_dir)
        full_key = f"{prefix}{key}"
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.head_object(Bucket=self._bucket, Key=full_key)
            return True
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            logger.error(
                "S3StorageProvider.exists failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(f"S3StorageProvider.exists failed: key={key!r}") from exc

    async def write_text(
        self,
        key: str,
        content: str,
        root_dir: Path | None = None,
    ) -> None:
        """Write UTF-8 text to S3 as a single object under the root_dir prefix."""
        prefix = self._resolve_prefix(root_dir)
        full_key = f"{prefix}{key}"
        content_type = _content_type_for(key)
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=full_key,
                    Body=content.encode("utf-8"),
                    ContentType=content_type,
                )
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.write_text failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(
                f"S3StorageProvider.write_text failed: key={key!r}"
            ) from exc

    async def read_text(self, key: str, root_dir: Path | None = None) -> str:
        """Read and decode UTF-8 text from S3, honoring the root_dir prefix."""
        prefix = self._resolve_prefix(root_dir)
        full_key = f"{prefix}{key}"
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                response = await s3.get_object(Bucket=self._bucket, Key=full_key)
                body: bytes = await response["Body"].read()
                return body.decode("utf-8")
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "")
            if code in ("404", "NoSuchKey"):
                raise StorageError(
                    f"S3StorageProvider.read_text failed: key={key!r} not found"
                ) from exc
            logger.error(
                "S3StorageProvider.read_text failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(
                f"S3StorageProvider.read_text failed: key={key!r}"
            ) from exc

    async def list_keys(
        self,
        prefix: str = "",
        root_dir: Path | None = None,
    ) -> list[str]:
        """List object keys under the root_dir prefix plus the given prefix.

        The returned keys are relative to ``root_dir`` (the root_dir prefix
        is stripped), so callers see paths like ``entities/slug.md`` rather
        than ``exports/entities/slug.md``.

        Args:
            prefix: Sub-prefix to filter keys by (e.g. ``"entities/"``).
            root_dir: Bundle root directory override.

        Returns:
            Sorted list of relative keys (root_dir prefix stripped).
        """
        root_prefix = self._resolve_prefix(root_dir)
        full_prefix = f"{root_prefix}{prefix}"
        keys: list[str] = []
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=self._bucket, Prefix=full_prefix
                ):
                    for obj in page.get("Contents", []):
                        keys.append(obj["Key"])
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.list_keys failed",
                prefix=prefix,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(
                f"S3StorageProvider.list_keys failed: prefix={prefix!r}"
            ) from exc
        strip = len(root_prefix)
        relative = [k[strip:] if k.startswith(root_prefix) else k for k in keys]
        return sorted(relative)
