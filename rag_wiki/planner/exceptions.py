"""
rag_wiki.planner.exceptions
---------------------------
Domain exceptions for the planner subsystem.

Does NOT cover ingestion or retrieval exceptions — those live in
``rag_wiki.exceptions`` (IngestError, ParseError, RetrievalError).
"""

from __future__ import annotations

from rag_wiki.exceptions import RagWikiError


class PlannerError(RagWikiError):
    """Base exception for all planner errors."""


class PlannerClassificationError(PlannerError):
    """Raised when a classification decision cannot be made or fails validation.

    For example, if the LLM returns an unparseable response for query
    classification, or if the confidence is below the minimum threshold
    and no explicit override was provided.
    """
