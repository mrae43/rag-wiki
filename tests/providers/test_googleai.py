"""Tests for GoogleAIProvider implementation.

All tests mock the HTTP transport layer via httpx.MockTransport
to avoid real API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.providers.googleai import GoogleAIProvider
from rag_wiki.settings import Settings


def _make_settings(
    gemini_api_key: str | None = "test-gemini-key",
    send_dimensions: bool = True,
    embedding_dimensions: int = 3072,
    embedding_task_type: str = "RETRIEVAL_DOCUMENT",
) -> Settings:
    """Return a minimal Settings instance configured for Google AI."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        llm_api_key="fallback-key",
        gemini_api_key=gemini_api_key,
        llm_embedding_provider="googleai",
        send_dimensions=send_dimensions,
        embedding_dimensions=embedding_dimensions,
        embedding_task_type=embedding_task_type,
    )


async def test_embed_returns_vectors() -> None:
    """Test embed() returns one embedding vector per input text."""
    settings = _make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert len(body["requests"]) == 2
        assert body["requests"][0]["model"] == "models/gemini-embedding-2"
        assert body["requests"][0]["content"]["parts"][0]["text"] == "hello"
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [0.1, 0.2, 0.3]},
                    {"values": [0.4, 0.5, 0.6]},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        result = await provider.embed(["hello", "world"], "gemini-embedding-2")

    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


async def test_embed_sends_auth_headers() -> None:
    """Test embed() sends x-goog-api-key header and ?key= query param."""
    settings = _make_settings(gemini_api_key="my-secret-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "my-secret-key"
        assert "key=my-secret-key" in str(request.url)
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_sends_dimensions_and_task_type() -> None:
    """Test embed() sends outputDimensionality and taskType."""
    settings = _make_settings(
        send_dimensions=True,
        embedding_dimensions=768,
        embedding_task_type="RETRIEVAL_DOCUMENT",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        config = body["embedContentConfig"]
        assert config["outputDimensionality"] == 768
        assert config["taskType"] == "RETRIEVAL_DOCUMENT"
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_omits_config_when_send_dimensions_false() -> None:
    """Test embed() omits embedContentConfig when send_dimensions is False."""
    settings = _make_settings(send_dimensions=False)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "embedContentConfig" not in body
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_omits_task_type_when_none() -> None:
    """Test embed() omits taskType when embedding_task_type is empty string."""
    settings = _make_settings(embedding_task_type="")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        config = body.get("embedContentConfig", {})
        assert "outputDimensionality" in config
        assert "taskType" not in config
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_empty_input() -> None:
    """Test embed() with empty texts returns empty list without making HTTP call."""
    settings = _make_settings()

    async with httpx.AsyncClient() as client:
        provider = GoogleAIProvider(settings, client=client)
        result = await provider.embed([], "gemini-embedding-2")

    assert result == []


async def test_embed_http_error_raises_llm_provider_error() -> None:
    """Test embed() raises LLMProviderError on HTTP 4xx/5xx."""
    settings = _make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "API key not valid"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        with pytest.raises(LLMProviderError, match="Google AI embedding failed"):
            await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_http_error_without_json_body() -> None:
    """Test embed() handles HTTP errors without a JSON response body."""
    settings = _make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        with pytest.raises(LLMProviderError, match="Google AI embedding failed"):
            await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_unexpected_error_raises_llm_provider_error() -> None:
    """Test embed() raises LLMProviderError on non-HTTP exceptions."""
    settings = _make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        msg = "connection refused"
        raise httpx.ConnectError(msg)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        msg = "Google AI embedding unexpected failure"
        with pytest.raises(LLMProviderError, match=msg):
            await provider.embed(["hello"], "gemini-embedding-2")


async def test_embed_falls_back_to_llm_api_key() -> None:
    """Test embed() uses llm_api_key when gemini_api_key is None."""
    settings = _make_settings(gemini_api_key=None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "fallback-key"
        return httpx.Response(
            200,
            json={"embeddings": [{"values": [0.1, 0.2, 0.3]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GoogleAIProvider(settings, client=client)
        await provider.embed(["hello"], "gemini-embedding-2")


async def test_factory_resolves_googleai() -> None:
    """Test get_embedding_provider returns GoogleAIProvider for 'googleai'."""
    from rag_wiki.providers import get_embedding_provider

    settings = _make_settings()
    provider = get_embedding_provider(settings)
    assert isinstance(provider, GoogleAIProvider)


async def test_factory_rejects_unknown_provider() -> None:
    """Test get_embedding_provider raises for unknown provider name."""
    from rag_wiki.providers import get_embedding_provider

    settings = _make_settings()
    settings.llm_embedding_provider = "nonexistent"
    with pytest.raises(LLMProviderError, match="Unknown embedding provider"):
        get_embedding_provider(settings)
