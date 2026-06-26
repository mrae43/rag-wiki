"""
rag_wiki.retrieval.seeds
-----------------------
Find seed entities for the retrieval pipeline.

Supports two paths:
1. Vector search on ``entities.embedding`` (default).
2. Direct lookup by ``seed_entity_ids`` (bypasses vector search).

Computes ``StructuralAnchor`` metadata and ``seed_quality`` tags for each
seed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.exceptions import DatabaseError
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.schemas import SeedResult, StructuralAnchor
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)


def _seed_quality(distance: float) -> str:
    if distance < 0.2:
        return "high"
    if distance <= 0.4:
        return "low"
    return "poor"


def _relative_centrality(degree: int) -> str:
    if degree > 20:
        return "high"
    if degree >= 5:
        return "medium"
    return "low"


def _relation_summary(entity: Entity) -> str:
    outgoing = entity.outgoing_relations or []
    incoming = entity.incoming_relations or []
    all_rels = outgoing + incoming
    if not all_rels:
        return "No relations"

    neighbor_types: dict[str, int] = {}
    for rel in all_rels:
        if rel.source_entity_id == entity.id:
            neighbor = rel.target_entity
        else:
            neighbor = rel.source_entity
        if neighbor is None:
            continue
        neighbor_types[neighbor.entity_type] = (
            neighbor_types.get(neighbor.entity_type, 0) + 1
        )

    parts = [f"{count} {etype.title()}" for etype, count in neighbor_types.items()]
    return "Connected to " + ", ".join(parts)


def _make_anchor(entity: Entity, hop_distance: int = 0) -> StructuralAnchor:
    degree = len(entity.outgoing_relations or []) + len(entity.incoming_relations or [])
    return StructuralAnchor(
        name=entity.name,
        type=entity.entity_type,
        description=entity.description or "",
        degree=degree,
        relative_centrality=_relative_centrality(degree),
        hop_distance=hop_distance,
        relation_summary=_relation_summary(entity),
    )


async def find_seeds(
    query_embedding: list[float],
    db: AsyncSession,
    embed_provider: EmbeddingProvider | None = None,
    seed_entity_ids: list[uuid.UUID] | None = None,
) -> list[SeedResult]:
    """Return seed entities for retrieval.

    Args:
        query_embedding: Embedding of the user query. Used for vector search
            when *seed_entity_ids* is not provided.
        db: Async SQLAlchemy session.
        embed_provider: Unused but reserved for future seeding modes that
            need on-the-fly embedding.
        seed_entity_ids: If provided, skip vector search and load these
            entities directly.

    Returns:
        List of ``SeedResult`` objects, up to ``retrieval_seed_count``.
    """
    settings = get_settings()

    if seed_entity_ids:
        try:
            result = await db.execute(
                sa.select(Entity)
                .options(
                    joinedload(Entity.outgoing_relations).joinedload(Relation.target_entity),
                    joinedload(Entity.incoming_relations).joinedload(Relation.source_entity),
                    joinedload(Entity.outgoing_relations).joinedload(Relation.source_entity),
                    joinedload(Entity.incoming_relations).joinedload(Relation.target_entity),
                )
                .where(Entity.id.in_(seed_entity_ids))
            )
            entities = list(result.unique().scalars().all())
        except Exception as exc:
            logger.error(
                "Failed to fetch seed entities by IDs",
                seed_ids=[str(eid) for eid in seed_entity_ids],
                error=str(exc),
            )
            raise DatabaseError(
                f"Failed to fetch seed entities by IDs: {seed_entity_ids}"
            ) from exc
        seeds: list[SeedResult] = []
        for ent in entities:
            anchor = _make_anchor(ent, hop_distance=0)
            seeds.append(
                SeedResult(
                    entity_id=ent.id,
                    similarity_score=1.0,
                    seed_quality="high",
                    anchor=anchor,
                )
            )
        return seeds

    # Vector search on entity embeddings (exclude entities without embeddings).
    k = settings.retrieval_seed_count
    try:
        result = await db.execute(
            sa.select(Entity)
            .options(
                joinedload(Entity.outgoing_relations).joinedload(Relation.target_entity),
                joinedload(Entity.incoming_relations).joinedload(Relation.source_entity),
                joinedload(Entity.outgoing_relations).joinedload(Relation.source_entity),
                joinedload(Entity.incoming_relations).joinedload(Relation.target_entity),
            )
            .where(Entity.embedding.is_not(None))
            .order_by(Entity.embedding.cosine_distance(query_embedding))
            .limit(k)
        )
        entities = list(result.unique().scalars().all())
    except Exception as exc:
        logger.error(
            "Failed to find seeds by vector search",
            error=str(exc),
        )
        raise DatabaseError("Failed to find seeds by vector search") from exc

    seeds = []
    for ent in entities:
        distance = 0.0
        if ent.embedding is not None:
            # Cosine distance = 1 - cosine similarity.
            # pgvector cosine_distance operator returns the distance directly.
            # We can compute it locally to avoid a second DB call.
            # Using simple dot product and norms for distance.
            import math

            a = query_embedding
            b = ent.embedding
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a > 0.0 and norm_b > 0.0:
                distance = 1.0 - (dot / (norm_a * norm_b))

        anchor = _make_anchor(ent, hop_distance=0)
        seeds.append(
            SeedResult(
                entity_id=ent.id,
                similarity_score=distance,
                seed_quality=_seed_quality(distance),
                anchor=anchor,
            )
        )

    logger.info(
        "seeds_found",
        count=len(seeds),
        qualities=[s.seed_quality for s in seeds],
    )
    return seeds
