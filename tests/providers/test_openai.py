"""Tests for OpenAIProvider implementation.

All tests mock the openai.AsyncClient to avoid real API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from rag_wiki.exceptions import LLMProviderError
from rag_wiki.providers.base import (
    CompletionRequest,
    Message,
    ToolDefinition,
)
from rag_wiki.providers.openai import OpenAIProvider
from rag_wiki.settings import Settings


def _make_settings() -> Settings:
    """Return a minimal Settings instance with a test API key and database URL."""
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        llm_api_key="test-key",
        llm_embedding_provider="openai",
        send_dimensions=True,
    )


async def test_openai_provider_complete_basic() -> None:
    """Test complete() returns the expected content string with no tool calls."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="Hello world",
                tool_calls=None,
            )
        )
    ]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    result = await provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="Hello")],
        ),
    )

    assert result.content == "Hello world"
    assert len(result.tool_calls) == 0
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"] == [{"role": "user", "content": "Hello"}]


async def test_openai_provider_complete_with_system() -> None:
    """Test complete() prepends a system message when system prompt is provided."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="Response",
                tool_calls=None,
            )
        )
    ]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    await provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            system="You are a helpful assistant",
            messages=[Message(role="user", content="Hello")],
        ),
    )

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
    ]


async def test_openai_provider_complete_with_tools() -> None:
    """Test complete() returns ToolCall results when tools are provided."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    function_mock = MagicMock()
    function_mock.name = "extract_entities"
    function_mock.arguments = '{"entities": []}'
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content=None,
                tool_calls=[
                    MagicMock(
                        id="call-1",
                        type="function",
                        function=function_mock,
                    )
                ],
            )
        )
    ]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    result = await provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="Hello")],
            tools=[
                ToolDefinition(
                    name="extract_entities",
                    description="Extract entities",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
        ),
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].name == "extract_entities"
    assert result.tool_calls[0].arguments == '{"entities": []}'

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "extract_entities",
                "description": "Extract entities",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


async def test_openai_provider_complete_passes_temperature_and_max_tokens() -> None:
    """Test complete() forwards temperature and max_tokens to the API call."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="OK",
                tool_calls=None,
            )
        )
    ]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    await provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="Hello")],
            temperature=0.5,
            max_tokens=100,
        ),
    )

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.5
    assert call_kwargs["max_tokens"] == 100


async def test_openai_provider_complete_api_error_raises_llm_provider_error() -> None:
    """Test complete() raises LLMProviderError on OpenAI SDK APIError."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIError(
            message="Rate limited",
            request=MagicMock(),
            body={"error": {"message": "Rate limited"}},
        )
    )

    provider = OpenAIProvider(settings, client=mock_client)
    with pytest.raises(LLMProviderError):
        await provider.complete(
            CompletionRequest(
                model="gpt-4o-mini",
                messages=[Message(role="user", content="Hello")],
            ),
        )


async def test_openai_provider_caption_image() -> None:
    """Test caption_image() sends base64 image bytes and returns caption text."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="A red apple",
                tool_calls=None,
            )
        )
    ]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    result = await provider.caption_image(
        b"\x89PNG\r\n\x1a\n",
        "image/png",
        "gpt-4o-mini",
    )

    assert result == "A red apple"
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    messages = call_kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0]["type"] == "text"
    assert messages[0]["content"][1]["type"] == "image_url"
    assert "data:image/png;base64," in messages[0]["content"][1]["image_url"]["url"]


async def test_openai_provider_caption_image_api_error_raises() -> None:
    """Test caption_image() raises LLMProviderError on OpenAI SDK APIError."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIError(
            message="Vision error",
            request=MagicMock(),
            body={"error": {"message": "Vision error"}},
        )
    )

    provider = OpenAIProvider(settings, client=mock_client)
    with pytest.raises(LLMProviderError):
        await provider.caption_image(
            b"\x89PNG\r\n\x1a\n",
            "image/png",
            "gpt-4o-mini",
        )


async def test_openai_provider_embed() -> None:
    """Test embed() returns a list of embedding vectors for each input string."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=[0.1, 0.2, 0.3]),
        MagicMock(embedding=[0.4, 0.5, 0.6]),
    ]
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    result = await provider.embed(
        ["hello", "world"],
        "text-embedding-3-small",
    )

    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    call_kwargs = mock_client.embeddings.create.call_args.kwargs
    assert call_kwargs["model"] == "text-embedding-3-small"
    assert call_kwargs["input"] == ["hello", "world"]


async def test_openai_provider_embed_passes_dimensions() -> None:
    """Test embed() sends the dimensions parameter when embedding_dimensions is set."""
    settings = _make_settings()
    settings.embedding_dimensions = 512
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1] * 512)]
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    await provider.embed(["hello"], "text-embedding-3-small")

    call_kwargs = mock_client.embeddings.create.call_args.kwargs
    assert call_kwargs["dimensions"] == 512


async def test_openai_provider_embed_api_error_raises() -> None:
    """Test embed() raises LLMProviderError when the OpenAI SDK returns an APIError."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_client.embeddings.create = AsyncMock(
        side_effect=openai.APIError(
            message="Embed error",
            request=MagicMock(),
            body={"error": {"message": "Embed error"}},
        )
    )

    provider = OpenAIProvider(settings, client=mock_client)
    with pytest.raises(LLMProviderError):
        await provider.embed(
            ["hello"],
            "text-embedding-3-small",
        )


async def test_openai_provider_complete_unexpected_error_raises() -> None:
    """Test complete() raises LLMProviderError for non-API exceptions."""
    settings = _make_settings()
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))

    provider = OpenAIProvider(settings, client=mock_client)
    with pytest.raises(LLMProviderError):
        await provider.complete(
            CompletionRequest(
                model="gpt-4o-mini",
                messages=[Message(role="user", content="Hello")],
            ),
        )


async def test_openai_provider_embed_no_dimensions_when_zero() -> None:
    """Test embed() omits the dimensions parameter when embedding_dimensions is 0."""
    settings = _make_settings()
    settings.embedding_dimensions = 0
    mock_client = MagicMock(spec=openai.AsyncClient)
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1, 0.2])]
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(settings, client=mock_client)
    await provider.embed(["hello"], "text-embedding-3-small")

    call_kwargs = mock_client.embeddings.create.call_args.kwargs
    # dimensions should not be passed when 0
    assert "dimensions" not in call_kwargs
