"""
rag_wiki.providers.base
------------------------
Protocol definitions and shared models for LLM provider abstractions.

Defines ChatProvider (text completions and image captioning) and
EmbeddingProvider (text embeddings). Concrete implementations live in
rag_wiki.providers.*. Callers depend on these interfaces, never on a
concrete implementation.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel


class Message(BaseModel):
    """A single message in a conversation."""

    role: Literal["system", "user", "assistant"]
    content: str


class ToolDefinition(BaseModel):
    """Definition of a tool available for the model to call."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """A tool call emitted by the model."""

    id: str
    name: str
    arguments: str


class CompletionRequest(BaseModel):
    """Request for a chat completion."""

    system: str | None = None
    messages: list[Message] = []
    model: str
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_ms: int | None = None
    tools: list[ToolDefinition] | None = None
    tool_choice: str | None = None


class CompletionResponse(BaseModel):
    """Response from a chat completion."""

    content: str | None = None
    tool_calls: list[ToolCall] = []


class ChatProvider(Protocol):
    """
    Protocol defining chat-based LLM operations.

    Covers text completion (via CompletionRequest) and image captioning.
    """

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """
        Return a chat completion for the given request.

        Args:
            request: The completion request. Must include a model name.

        Returns:
            A CompletionResponse. May include tool_calls if tools were requested.

        Raises:
            LLMProviderError: If the provider call fails after retries.
        """
        ...

    async def caption_image(
        self,
        image_bytes: bytes,
        image_mime_type: str,
        model: str,
    ) -> str:
        """
        Return a text caption for the given image bytes.

        Args:
            image_bytes: Raw image bytes to caption.
            image_mime_type: MIME type of the image (e.g. "image/png", "image/jpeg").
            model: The vision model identifier to use.

        Returns:
            A text description of the image.

        Raises:
            LLMProviderError: If the provider call fails after retries.
        """
        ...


class EmbeddingProvider(Protocol):
    """
    Protocol defining text embedding operations.

    Separated from ChatProvider because not all providers offer embeddings.
    """

    async def embed(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        """
        Return embedding vectors for the given texts.

        Args:
            texts: List of text strings to embed.
            model: The embedding model identifier to use.

        Returns:
            A list of embedding vectors, one per input text.

        Raises:
            LLMProviderError: If the provider call fails after retries.
        """
        ...
