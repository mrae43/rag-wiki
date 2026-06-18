"""
rag_wiki.retrieval
-----------------
Public API for the hybrid retrieval pipeline.

Re-exports ``retrieve()`` from the orchestrator so callers can import it
from ``rag_wiki.retrieval``.
"""

from __future__ import annotations

from rag_wiki.retrieval.orchestrator import retrieve
from rag_wiki.retrieval.schemas import RetrievalResult

__all__ = ["retrieve", "RetrievalResult"]
