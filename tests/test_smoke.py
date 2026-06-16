"""Smoke tests for core package-level imports and basic functionality."""

from __future__ import annotations

import rag_wiki
from rag_wiki.exceptions import (
    EntityResolutionError,
    IngestError,
    LLMProviderError,
    RagWikiError,
)
from rag_wiki.main import app as fastapi_app
from rag_wiki.settings import Settings, get_settings


def test_package_imports() -> None:
    assert rag_wiki.__doc__ is not None


def test_exception_hierarchy() -> None:
    assert issubclass(LLMProviderError, RagWikiError)
    assert issubclass(EntityResolutionError, RagWikiError)
    assert issubclass(IngestError, RagWikiError)
    assert LLMProviderError is not RagWikiError
    assert EntityResolutionError is not RagWikiError
    assert IngestError is not RagWikiError


def test_settings_defaults() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.llm_provider == "openai"
    assert settings.embedding_dimensions == 2048
    assert settings.parser == "lightweight"
    assert settings.llm_model_caption == "meta/llama-3.1-8b-instruct"
    assert settings.llm_model_extraction == "meta/llama-3.1-8b-instruct"
    assert settings.llm_model_wiki_synthesis == "meta/llama-3.1-8b-instruct"
    assert settings.llm_model_query == "meta/llama-3.1-8b-instruct"


def test_fastapi_app_creates() -> None:
    assert fastapi_app.title == "RagWiki"
    assert fastapi_app is not None
