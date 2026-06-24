from __future__ import annotations

from collections.abc import AsyncIterator
from typing import BinaryIO

import aioboto3
import structlog
from botocore.exceptions import ClientError

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.base import StorageProvider

logger = structlog.get_logger(__name__)

_CHUNK_SIZE: int = 65_536


class S3StorageProvider(StorageProvider):
    """
    StorageProvider backed by an S3-compatible object store.

    Uses aioboto3 for async S3 operations. Supports any S3-compatible
    backend (AWS S3, SeaweedFS, MinIO, etc.) via the ``endpoint_url``
    setting. Files are stored with keys like ``sources/{source_id}``.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._endpoint_url = settings.s3_endpoint_url or None
        self._session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )

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

    async def delete(self, key: str) -> None:
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            logger.error(
                "S3StorageProvider.delete failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(f"S3StorageProvider.delete failed: key={key!r}") from exc

    async def exists(self, key: str) -> bool:
        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as s3:
                await s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchKey"):
                return False
            logger.error(
                "S3StorageProvider.exists failed",
                key=key,
                bucket=self._bucket,
                error=str(exc),
            )
            raise StorageError(f"S3StorageProvider.exists failed: key={key!r}") from exc
