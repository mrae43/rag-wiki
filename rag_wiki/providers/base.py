"""
rag_wiki.providers.base
----------------------
Protocol defining the LLM operations the system needs.

All LLM calls in this codebase go through an implementation of this protocol.
Concrete implementations live in rag_wiki.providers.*. Callers depend on this
interface, never on a concrete implementation.
"""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    """
    Protocol defining the LLM operations the system needs.

    All LLM calls in this codebase go through an implementation of this
    protocol. Concrete implementations live in rag_wiki.providers.*
    Callers depend on this interface, never on a concrete implementation.
    """

    async def complete(self, prompt: str, model: str) -> str:
        """
        Return a text completion for the given prompt.

        Args:
            prompt: The prompt text to send to the LLM.
            model: The model identifier to use.

        Returns:
            The generated text response.

        Raises:
            LLMProviderError: If the provider call fails after retries.
        """
        ...

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
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

    async def caption_image(self, image_bytes: bytes, model: str) -> str:
        """
        Return a text caption for the given image bytes.

        Args:
            image_bytes: Raw image bytes to caption.
            model: The vision model identifier to use.

        Returns:
            A text description of the image.

        Raises:
            LLMProviderError: If the provider call fails after retries.
        """
        ...
