"""
rag_wiki.storage
----------------
Storage abstraction for source documents.

Defines StorageProvider protocol and a factory for obtaining the configured
provider. Mirrors the ChatProvider pattern from rag_wiki/providers.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from rag_wiki.exceptions import StorageError
from rag_wiki.settings import Settings
from rag_wiki.storage.base import StorageProvider
from rag_wiki.storage.local import LocalStorageProvider
from rag_wiki.storage.s3 import S3StorageProvider

logger = structlog.get_logger(__name__)


STORAGE_PROVIDERS: dict[str, Callable[[Settings], StorageProvider]] = {
    "local": LocalStorageProvider,
    "s3": S3StorageProvider,
}


def get_storage_provider(settings: Settings) -> StorageProvider:
    """
    Return the configured StorageProvider instance.

    Args:
        settings: App settings with storage_provider field.

    Returns:
        An instance of the configured storage provider.

    Raises:
        StorageError: If the provider name is unknown.
    """
    cls = STORAGE_PROVIDERS.get(settings.storage_provider)
    if cls is None:
        raise StorageError(f"Unknown storage provider: {settings.storage_provider!r}")
    return cls(settings)
