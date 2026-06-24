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
from rag_wiki.settings import Settings


def test_package_imports() -> None:
    """Verify the rag_wiki package imports successfully and has a docstring."""
    assert rag_wiki.__doc__ is not None


def test_exception_hierarchy() -> None:
    """Verify all custom exceptions inherit from RagWikiError and are distinct types."""
    assert issubclass(LLMProviderError, RagWikiError)
    assert issubclass(EntityResolutionError, RagWikiError)
    assert issubclass(IngestError, RagWikiError)
    assert LLMProviderError is not RagWikiError
    assert EntityResolutionError is not RagWikiError
    assert IngestError is not RagWikiError


def test_settings_defaults() -> None:
    """Verify default values of critical Settings fields match expectations."""
    fields = Settings.model_fields
    assert fields["llm_provider"].default == "openai"
    assert fields["embedding_dimensions"].default == 3072
    assert fields["planner_version"].default == "1.0.0"
    assert fields["planner_confidence_high"].default == 0.8
    assert fields["planner_confidence_low"].default == 0.5
    assert fields["planner_confidence_minimum"].default == 0.5
    assert fields["llm_model_caption"].default == "gpt-4o-mini"
    assert fields["llm_model_extraction"].default == "gpt-4o-mini"
    assert fields["llm_model_resolution"].default == "gpt-4o"
    assert fields["llm_model_wiki_synthesis"].default == "gpt-4o"
    assert fields["llm_model_query"].default == "gpt-4o"


def test_fastapi_app_creates() -> None:
    """Verify the FastAPI app is created with the expected title and is not None."""
    assert fastapi_app.title == "RagWiki API"
    assert fastapi_app is not None
