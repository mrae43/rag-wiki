"""
rag_wiki.graph
-------------
Entity/relation extraction, resolution, and graph traversal queries.

Provides the knowledge graph layer built on plain relational tables
(entities, relations) with recursive CTEs for traversal.
"""

from rag_wiki.graph.schemas import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    MergeDecision,
)

__all__ = [
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionResult",
    "MergeDecision",
]
