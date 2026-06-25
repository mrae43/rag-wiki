"""
rag_wiki.api.dependencies
------------------------
FastAPI dependencies shared across API routes.

Re-exports ``get_db`` from the existing session module so that dependency
overrides continue to work, and provides thin wrappers around the configured
LLM providers.
"""

from __future__ import annotations

from rag_wiki.db.session import get_db
from rag_wiki.providers import get_chat_provider as _get_chat_provider
from rag_wiki.providers import get_embedding_provider as _get_embedding_provider
from rag_wiki.providers.base import ChatProvider, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.storage import get_storage_provider as _get_storage_provider
from rag_wiki.storage.base import StorageProvider

__all__ = [
    "get_db",
    "get_chat_provider",
    "get_embedding_provider",
    "get_storage_provider",
]


async def get_chat_provider() -> ChatProvider:
    """Return the configured chat provider for injection into routes."""
    return _get_chat_provider(get_settings())


async def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider for injection into routes."""
    return _get_embedding_provider(get_settings())


async def get_storage_provider() -> StorageProvider:
    """Return the configured storage provider for injection into routes."""
    return _get_storage_provider(get_settings())
