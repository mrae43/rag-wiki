"""
rag_wiki.retrieval.schemas
-------------------------
Data models for the retrieval pipeline.

All models are plain ``@dataclass`` objects — no ORM mapping. They carry
structured context from the pipeline to the caller.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass


@dataclass
class StructuralAnchor:
    """Metadata describing a seed entity's position in the graph."""

    name: str
    type: str  # Person, Concept, Organization, etc.
    description: str  # 1-2 sentence LLM-extracted description
    degree: int  # Total edge count (in + out)
    relative_centrality: str  # "high" | "medium" | "low"
    hop_distance: int  # 0 for seed entities
    relation_summary: str  # e.g. "Connected to 2 People, 1 Organization"


@dataclass
class SeedResult:
    """A single seed entity returned by the seed-finding step."""

    entity_id: uuid.UUID
    similarity_score: float
    seed_quality: str  # "high" | "low" | "poor"
    anchor: StructuralAnchor


@dataclass
class SubgraphEdge:
    """One traversed relation in the subgraph returned to the caller."""

    source_name: str
    source_type: str
    relation: str
    target_name: str
    target_type: str
    hop: int
    confidence_tag: str  # "INFERRED" (v1 default)
    confidence_score: float | None


@dataclass
class WikiPageSnapshot:
    """Truncated wiki page included in retrieval context."""

    entity_id: uuid.UUID
    content: str
    synthesized_at: datetime.datetime | None
    contributing_source_count: int
    was_truncated: bool
    original_token_count: int
    sections_included: list[str]
    sections_dropped: list[str]


@dataclass
class ScoredChunk:
    """A chunk scored for relevance to the user query."""

    chunk_id: uuid.UUID
    entity_id: uuid.UUID
    source_file: str
    source_name: str
    ingested_at: str
    text: str
    similarity_score: float
    hop_distance: int


@dataclass
class SlotTokenCounts:
    """Per-slot token accounting for debugging and observability."""

    anchor: int
    subgraph: int
    wiki_page: int
    seed_chunks: int
    hop_chunks: int
    instruction: int
    total: int
    budget: int
    utilization: float


@dataclass
class RetrievalResult:
    """Top-level return value from the retrieval pipeline."""

    query: str
    retrieved_at: datetime.datetime
    seeds: list[SeedResult]
    subgraph: list[SubgraphEdge]
    wiki_page: WikiPageSnapshot | None
    seed_chunks: list[ScoredChunk]
    hop1_chunks: list[ScoredChunk]
    token_counts: SlotTokenCounts
    total_tokens_used: int
    entities_traversed: int
    entities_after_truncation: int
    chunks_fetched: int
    chunks_after_dedup: int
