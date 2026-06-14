"""Smoke tests for LLMProvider protocol and test fixtures."""

from __future__ import annotations

from ragwiki.providers.base import LLMProvider


def test_llm_provider_protocol_methods() -> None:
    assert hasattr(LLMProvider, "complete")
    assert hasattr(LLMProvider, "embed")
    assert hasattr(LLMProvider, "caption_image")


async def test_fake_llm_provider_complete(mock_llm_provider: LLMProvider) -> None:
    result = await mock_llm_provider.complete("hello world", model="gpt-4o-mini")
    assert isinstance(result, str)
    assert "fake-completion" in result


async def test_fake_llm_provider_embed(mock_llm_provider: LLMProvider) -> None:
    result = await mock_llm_provider.embed(["hello", "world"], model="gpt-4o-mini")
    assert isinstance(result, list)
    assert len(result) == 2
    assert len(result[0]) == 1536
    assert result[0] == [0.0] * 1536
    assert result[1] == [0.0] * 1536


async def test_fake_llm_provider_caption(mock_llm_provider: LLMProvider) -> None:
    result = await mock_llm_provider.caption_image(b"fake_image", model="gpt-4o-mini")
    assert isinstance(result, str)
    assert "fake" in result
