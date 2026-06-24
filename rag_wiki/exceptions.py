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


class ExtractionError(RagWikiError):
    """Raised when entity/relation extraction fails or returns invalid data."""


class AdvisoryLockExhausted(RagWikiError):
    """Raised when PG advisory lock retries are exhausted during synthesis."""


class IngestError(RagWikiError):
    """Raised when a source document cannot be processed."""


class ParseError(IngestError):
    """Raised when a document cannot be parsed by any available parser."""


class DatabaseError(RagWikiError):
    """Raised when a database operation fails in domain code."""


class RetrievalError(RagWikiError):
    """Raised when the retrieval pipeline cannot assemble context.

    Base class for hierarchy completeness; the pipeline returns a valid
    RetrievalResult even when no seeds are found rather than raising.
    """


class StorageError(RagWikiError):
    """Raised when a StorageProvider operation fails."""
