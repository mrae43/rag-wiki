"""
rag_wiki.retrieval.traversal
---------------------------
Recursive CTE graph traversal for the retrieval pipeline.

A raw SQL recursive CTE (per coding standards §7.3) walks relations
bidirectionally from seed entities. Results are truncated at the entity-ID
level before ORM loading, then degree-ranked and total-node limited in
Python.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)

_TRAVERSAL_SQL = """
WITH RECURSIVE traversal AS (
    -- Base case: seed entities at hop 0
    SELECT id AS entity_id, 0 AS hop_distance
    FROM entities
    WHERE id = ANY(:seed_ids)

    UNION

    -- Recursive step: bidirectional neighbors
    SELECT
        CASE
            WHEN r.source_entity_id = t.entity_id THEN r.target_entity_id
            ELSE r.source_entity_id
        END AS entity_id,
        t.hop_distance + 1
    FROM traversal t
    JOIN relations r ON (
        r.source_entity_id = t.entity_id OR r.target_entity_id = t.entity_id
    )
    WHERE t.hop_distance < :max_hops
)
SELECT entity_id, MIN(hop_distance) AS hop_distance
FROM traversal
GROUP BY entity_id
"""


@dataclass
class TraversalResult:
    """Result of a graph traversal."""

    entities: list[Entity]  # all traversed entities (excluding seeds)
    relations: list[Relation]  # all traversed relations
    hop_map: dict[uuid.UUID, int]  # entity_id → hop_distance


async def traverse(
    seed_entity_ids: list[uuid.UUID],
    db: AsyncSession,
) -> TraversalResult:
    """Traverse the knowledge graph starting from *seed_entity_ids*.

    Args:
        seed_entity_ids: Starting entity IDs.
        db: Async SQLAlchemy session.

    Returns:
        TraversalResult containing traversed entities, relations, and hop
        distances. Empty if no seeds are provided.
    """
    settings = get_settings()

    if not seed_entity_ids:
        return TraversalResult(entities=[], relations=[], hop_map={})

    # Run the raw CTE.
    rows = await db.execute(
        sa.text(_TRAVERSAL_SQL),
        {
            "seed_ids": seed_entity_ids,
            "max_hops": settings.retrieval_max_hops,
        },
    )

    all_hops: dict[uuid.UUID, int] = {}
    for row in rows:
        entity_id = row[0]
        hop = row[1]
        all_hops[entity_id] = hop

    # Exclude seeds from the traversed set.
    non_seed_ids = [eid for eid, hop in all_hops.items() if eid not in seed_entity_ids]

    if not non_seed_ids:
        return TraversalResult(entities=[], relations=[], hop_map=all_hops)

    # Apply per-hop neighbor limit at the ID level before ORM loading to avoid
    # pulling huge dense neighborhoods into memory.
    max_per_hop = settings.retrieval_max_neighbors_per_hop
    hop_buckets: dict[int, list[uuid.UUID]] = {}
    for eid in non_seed_ids:
        hop = all_hops[eid]
        hop_buckets.setdefault(hop, []).append(eid)

    limited_ids: list[uuid.UUID] = []
    for hop in sorted(hop_buckets):
        limited_ids.extend(hop_buckets[hop][:max_per_hop])

    # Load full entity objects with relations.
    result = await db.execute(
        sa.select(Entity)
        .options(
            joinedload(Entity.outgoing_relations).joinedload(Relation.target_entity),
            joinedload(Entity.incoming_relations).joinedload(Relation.source_entity),
        )
        .where(Entity.id.in_(limited_ids))
    )
    all_entities = list(result.unique().scalars().all())

    # Rank each hop by degree descending. The per-hop ceiling was already
    # applied at the ID level before ORM loading; this step keeps the
    # degree-based ordering consistent with the documented behavior.
    max_per_hop = settings.retrieval_max_neighbors_per_hop
    hop_groups: dict[int, list[Entity]] = {}
    for ent in all_entities:
        hop = all_hops.get(ent.id, settings.retrieval_max_hops)
        hop_groups.setdefault(hop, []).append(ent)

    kept_entities: list[Entity] = []
    for hop in sorted(hop_groups):
        group = hop_groups[hop]
        # Rank by degree descending.
        group.sort(
            key=lambda e: (
                len(e.outgoing_relations or []) + len(e.incoming_relations or [])
            ),
            reverse=True,
        )
        kept_entities.extend(group[:max_per_hop])

    # Total node ceiling.
    max_total = settings.retrieval_max_total_nodes
    if len(kept_entities) > max_total:
        # Re-rank globally by hop asc, degree desc.
        kept_entities.sort(
            key=lambda e: (
                all_hops.get(e.id, settings.retrieval_max_hops),
                -(len(e.outgoing_relations or []) + len(e.incoming_relations or [])),
            ),
        )
        kept_entities = kept_entities[:max_total]

    kept_ids = {e.id for e in kept_entities}
    kept_ids.update(seed_entity_ids)

    # Collect unique relations among kept entities.
    seen_rel_ids: set[uuid.UUID] = set()
    relations: list[Relation] = []
    for ent in kept_entities:
        for rel in ent.outgoing_relations or []:
            if rel.id in seen_rel_ids:
                continue
            other_id = rel.target_entity_id
            if other_id in kept_ids:
                seen_rel_ids.add(rel.id)
                relations.append(rel)
        for rel in ent.incoming_relations or []:
            if rel.id in seen_rel_ids:
                continue
            other_id = rel.source_entity_id
            if other_id in kept_ids:
                seen_rel_ids.add(rel.id)
                relations.append(rel)

    logger.info(
        "traversal_complete",
        seeds=len(seed_entity_ids),
        entities_traversed=len(all_entities),
        entities_kept=len(kept_entities),
        relations_kept=len(relations),
    )

    return TraversalResult(
        entities=kept_entities,
        relations=relations,
        hop_map={eid: hop for eid, hop in all_hops.items() if eid in kept_ids},
    )
