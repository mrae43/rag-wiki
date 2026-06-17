"""Smoke tests for LLMProvider protocol and test fixtures."""

from __future__ import annotations

from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    EmbeddingProvider,
    Message,
)


async def test_fake_chat_provider_complete(mock_chat_provider: ChatProvider) -> None:
    result = await mock_chat_provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="hello")],
        ),
    )
    assert isinstance(result.content, str)
    assert "fake-completion" in result.content


async def test_fake_chat_provider_caption(mock_chat_provider: ChatProvider) -> None:
    result = await mock_chat_provider.caption_image(
        b"fake_image",
        "image/png",
        "gpt-4o-mini",
    )
    assert isinstance(result, str)
    assert "fake-caption" in result


async def test_fake_embedding_provider_embed(
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    result = await mock_embedding_provider.embed(
        ["hello", "world"],
        "text-embedding-3-small",
    )
    assert isinstance(result, list)
    assert len(result) == 2
    assert len(result[0]) == 2048
    assert result[0] == [0.0] * 2048
    assert result[1] == [0.0] * 2048


async def test_fake_chat_provider_with_tools(mock_chat_provider: ChatProvider) -> None:
    from rag_wiki.providers.base import ToolDefinition

    result = await mock_chat_provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="hello")],
            tools=[
                ToolDefinition(
                    name="fake_tool",
                    description="A fake tool",
                    parameters={"type": "object", "properties": {}},
                ),
            ],
        ),
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "fake_tool"
    assert result.tool_calls[0].arguments == '{"input": "test"}'


async def test_fake_chat_provider_without_tools(
    mock_chat_provider: ChatProvider,
) -> None:
    result = await mock_chat_provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=[Message(role="user", content="hello")],
        ),
    )
    assert len(result.tool_calls) == 0
