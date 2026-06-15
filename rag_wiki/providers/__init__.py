"""
rag_wiki.providers
-----------------
LLMProvider implementations for different LLM backends (OpenAI, Anthropic, etc.).

All LLM calls in the system go through an implementation of ChatProvider or
EmbeddingProvider. This module exposes factory functions for obtaining the
configured providers and a cross-cutting retry wrapper.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    CompletionResponse,
    EmbeddingProvider,
)
from rag_wiki.providers.openai import OpenAIProvider
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)

CHAT_PROVIDERS: dict[str, Callable[[Settings], ChatProvider]] = {
    "openai": OpenAIProvider,
}

EMBEDDING_PROVIDERS: dict[str, Callable[[Settings], EmbeddingProvider]] = {
    "openai": OpenAIProvider,
}


class RetryingProvider:
    """Wraps a ChatProvider with configurable retry logic.

    Retries on transient errors (rate limits, 5xx, timeouts). Does not retry
    authentication or validation errors.
    """

    def __init__(self, inner: ChatProvider, max_retries: int = 3) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._backoff_seconds = 2.0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Call inner.complete with retry logic."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._inner.complete(request)
            except LLMProviderError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    logger.warning(
                        "ChatProvider.complete failed, retrying",
                        attempt=attempt,
                        max_retries=self._max_retries,
                        error=str(exc),
                    )
                    await asyncio.sleep(self._backoff_seconds * attempt)
                else:
                    logger.error(
                        "ChatProvider.complete failed after retries",
                        attempt=attempt,
                        max_retries=self._max_retries,
                        error=str(exc),
                    )
        raise LLMProviderError(
            f"ChatProvider.complete failed after {self._max_retries} attempts"
        ) from last_exc

    async def caption_image(
        self,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> str:
        """Call inner.caption_image with retry logic."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._inner.caption_image(
                    image_bytes,
                    image_mime_type,
                    model,
                )
            except LLMProviderError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    logger.warning(
                        "ChatProvider.caption_image failed, retrying",
                        attempt=attempt,
                        max_retries=self._max_retries,
                        error=str(exc),
                    )
                    await asyncio.sleep(self._backoff_seconds * attempt)
                else:
                    logger.error(
                        "ChatProvider.caption_image failed after retries",
                        attempt=attempt,
                        max_retries=self._max_retries,
                        error=str(exc),
                    )
        raise LLMProviderError(
            f"ChatProvider.caption_image failed after {self._max_retries} attempts"
        ) from last_exc


def get_chat_provider(settings: Settings) -> ChatProvider:
    """Return the configured ChatProvider wrapped with retry logic."""
    provider_cls = CHAT_PROVIDERS.get(settings.llm_provider)
    if provider_cls is None:
        raise LLMProviderError(f"Unknown chat provider: {settings.llm_provider!r}")
    inner = provider_cls(settings)
    return RetryingProvider(inner, max_retries=settings.worker_max_retries)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return the configured EmbeddingProvider (unwrapped — retries live in caller)."""
    provider_cls = EMBEDDING_PROVIDERS.get(settings.llm_embedding_provider)
    if provider_cls is None:
        raise LLMProviderError(
            f"Unknown embedding provider: {settings.llm_embedding_provider!r}"
        )
    return provider_cls(settings)
