"""
rag_wiki.graph
-------------
Entity/relation extraction, resolution, and graph traversal queries.

Provides the knowledge graph layer built on plain relational tables
(entities, relations) with recursive CTEs for traversal.
"""

from rag_wiki.graph.extraction import extract_entities
from rag_wiki.graph.merge import merge_entity
from rag_wiki.graph.resolution import resolve_entities
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
    "extract_entities",
    "merge_entity",
    "resolve_entities",
]
