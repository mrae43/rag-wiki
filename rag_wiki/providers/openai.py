"""
rag_wiki.providers.openai
------------------------
OpenAI-compatible implementation of ChatProvider and EmbeddingProvider.

Uses the official openai SDK. All direct openai imports are confined to this module.
Supports OpenAI, Azure OpenAI, vLLM, Ollama, NVIDIA NIM, and any other
OpenAI-compatible endpoint configured via LLM_BASE_URL.
"""

from __future__ import annotations

import base64
from typing import Any

import openai
import structlog

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    CompletionResponse,
    EmbeddingProvider,
    ToolCall,
)
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)


def _map_request_tools(
    tools: list[Any] | None,
) -> list[dict[str, Any]] | None:
    """Map our ToolDefinition to OpenAI SDK tool format."""
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _map_response(raw: openai.types.chat.ChatCompletion) -> CompletionResponse:
    """Map OpenAI ChatCompletion to our CompletionResponse."""
    choice = raw.choices[0]
    message = choice.message

    content = message.content
    tool_calls = []
    if message.tool_calls:
        for tc in message.tool_calls:
            if tc.type == "function":
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )

    return CompletionResponse(content=content, tool_calls=tool_calls)


class OpenAIProvider(ChatProvider, EmbeddingProvider):
    """OpenAI-compatible provider implementing both ChatProvider
    and EmbeddingProvider.
    """

    def __init__(
        self,
        settings: Settings,
        client: openai.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or openai.AsyncClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a chat completion request via the OpenAI SDK."""
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for msg in request.messages:
            messages.append({"role": msg.role, "content": msg.content})

        tools = _map_request_tools(request.tools)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "tools": tools,
        }
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        try:
            raw = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            logger.error(
                "OpenAI completion failed",
                model=request.model,
                error=str(exc),
            )
            raise LLMProviderError(
                f"OpenAI completion failed: model={request.model!r}"
            ) from exc
        except Exception as exc:
            logger.error(
                "OpenAI completion unexpected failure",
                model=request.model,
                error=str(exc),
            )
            raise LLMProviderError(
                f"OpenAI completion unexpected failure: model={request.model!r}"
            ) from exc

        return _map_response(raw)

    async def caption_image(
        self,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> str:
        """Caption an image by delegating to the OpenAI vision API."""
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{image_mime_type};base64,{b64}"

        # OpenAI vision format: inject image URL into the message
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        try:
            raw = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
            )
        except openai.APIError as exc:
            logger.error(
                "OpenAI caption_image failed",
                model=model,
                error=str(exc),
            )
            raise LLMProviderError(
                f"OpenAI caption_image failed: model={model!r}"
            ) from exc
        except Exception as exc:
            logger.error(
                "OpenAI caption_image unexpected failure",
                model=model,
                error=str(exc),
            )
            raise LLMProviderError(
                f"OpenAI caption_image unexpected failure: model={model!r}"
            ) from exc

        content = raw.choices[0].message.content
        return content or ""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return embeddings via the OpenAI embedding API."""
        kwargs: dict[str, Any] = {"model": model, "input": texts}
        if self._settings.send_dimensions and self._settings.embedding_dimensions:
            kwargs["dimensions"] = self._settings.embedding_dimensions

        try:
            raw = await self._client.embeddings.create(**kwargs)
        except openai.APIError as exc:
            logger.error(
                "OpenAI embedding failed",
                model=model,
                texts_count=len(texts),
                error=str(exc),
            )
            raise LLMProviderError(f"OpenAI embedding failed: model={model!r}") from exc
        except Exception as exc:
            logger.error(
                "OpenAI embedding unexpected failure",
                model=model,
                texts_count=len(texts),
                error=str(exc),
            )
            raise LLMProviderError(
                f"OpenAI embedding unexpected failure: model={model!r}"
            ) from exc

        return [d.embedding for d in raw.data]
