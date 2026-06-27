"""
rag_wiki.providers.googleai
---------------------------
Google AI embedding provider using the batchEmbedContents REST API.

Uses raw httpx (not the Google GenAI SDK) to keep the provider thin and
consistent with the existing provider pattern. Auth is via x-goog-api-key
header and ?key= query parameter.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)


class GoogleAIProvider(EmbeddingProvider):
    """Google AI embedding provider.

    Implements only EmbeddingProvider, never ChatProvider. Uses the
    batchEmbedContents REST API to embed multiple texts in a single request.
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._api_key = settings.gemini_api_key or settings.llm_api_key or ""
        self._client = client or httpx.AsyncClient()

    async def embed(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        """Return embedding vectors via the Google AI batchEmbedContents API.

        Args:
            texts: List of text strings to embed.
            model: The embedding model identifier (e.g. "gemini-embedding-2").

        Returns:
            A list of embedding vectors, one per input text.

        Raises:
            LLMProviderError: If the provider call fails.
        """
        if not texts:
            return []

        url = f"{self.BASE_URL}/models/{model}:batchEmbedContents"

        requests_data: list[dict[str, Any]] = []
        for text in texts:
            req: dict[str, Any] = {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
            }
            requests_data.append(req)

        body: dict[str, Any] = {"requests": requests_data}

        if self._settings.send_dimensions:
            config: dict[str, Any] = {}
            if self._settings.embedding_dimensions:
                config["outputDimensionality"] = self._settings.embedding_dimensions
            if self._settings.embedding_task_type:
                config["taskType"] = self._settings.embedding_task_type
            if config:
                body["embedContentConfig"] = config

        headers = {"x-goog-api-key": self._api_key}
        params = {"key": self._api_key}

        try:
            response = await self._client.post(
                url,
                headers=headers,
                params=params,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = ""
            try:
                detail = exc.response.json().get("error", {}).get("message", "")
            except Exception:
                detail = exc.response.text
            logger.error(
                "Google AI embedding failed",
                model=model,
                status_code=status,
                detail=detail,
            )
            raise LLMProviderError(
                f"Google AI embedding failed: model={model!r} "
                f"status={status} detail={detail}"
            ) from exc
        except Exception as exc:
            logger.error(
                "Google AI embedding unexpected failure",
                model=model,
                texts_count=len(texts),
                error=str(exc),
            )
            raise LLMProviderError(
                f"Google AI embedding unexpected failure: model={model!r}"
            ) from exc

        return [e["values"] for e in data["embeddings"]]
