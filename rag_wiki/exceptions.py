"""
rag_wiki.exceptions
------------------
Domain exception hierarchy rooted in RagWikiError.

Every sub-package defines its own specific exceptions inheriting from the base
class. Never raise bare Exception or RuntimeError from domain code.
"""

from __future__ import annotations


class RagWikiError(Exception):
    """Base exception for all rag-wiki errors."""


class LLMProviderError(RagWikiError):
    """Raised when an LLM provider call fails after retries."""


class EntityResolutionError(RagWikiError):
    """Raised when entity resolution cannot make a merge/new decision."""


class IngestError(RagWikiError):
    """Raised when a source document cannot be processed."""
