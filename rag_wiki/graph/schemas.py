"""rag_wiki.graph.schemas
-----------------------
Pydantic models for entity/relation extraction and merge decision outputs.

These are the data shapes that cross the LLM boundary (extraction prompt,
resolution prompt). They are NOT SQLAlchemy ORM models.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator


class ExtractedEntity(BaseModel):
    """Entity as it appears in a single chunk of text.

    Attributes:
        surface_form: The text as it appeared in the chunk.
        canonical_name: The normalized entity name used for resolution.
        entity_type: The category of the entity (e.g. "person", "organization").
        description: One-sentence summary of the entity.
    """

    surface_form: str
    canonical_name: str
    entity_type: str
    description: str


class ExtractedRelation(BaseModel):
    """Relation between two entities in a single chunk.

    Attributes:
        source_idx: Index into the entity list in the parent extraction result.
        target_idx: Index into the entity list in the parent extraction result.
        relation_type: The category of the relation (e.g. "CEO", "founded").
    """

    source_idx: int
    target_idx: int
    relation_type: str


class ExtractionResult(BaseModel):
    """Structured output from the LLM extraction step.

    Attributes:
        entities: List of extracted entities from the chunk.
        relations: List of relations between entities in the chunk.
    """

    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation]


class MergeDecision(BaseModel):
    """LLM decision for whether an extracted entity should merge into an existing one.

    Attributes:
        decision: Whether to merge into an existing entity or create a new one.
        merged_into_id: The UUID of the existing entity to merge into, or None if new.
        reasoning: The LLM's explanation for the decision.
    """

    decision: Literal["merge", "new"]
    merged_into_id: UUID | None = None
    reasoning: str

    @model_validator(mode="after")
    def _validate_merge_id(self) -> MergeDecision:
        if self.decision == "merge" and self.merged_into_id is None:
            raise ValueError("merged_into_id required when decision is 'merge'")
        if self.decision == "new" and self.merged_into_id is not None:
            raise ValueError("merged_into_id must be None when decision is 'new'")
        return self


__all__ = [
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionResult",
    "MergeDecision",
]
