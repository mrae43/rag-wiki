"""rag_wiki.graph.merge
---------------------
Hard-merge one entity into another, re-pointing all dependent tables and
writing an audit log. Does not commit — the caller owns the transaction.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, EntityMergeLog
from rag_wiki.exceptions import DatabaseError, EntityResolutionError

logger = structlog.get_logger(__name__)


async def merge_entity(
    from_id: uuid.UUID,
    into_id: uuid.UUID,
    chunk_id: uuid.UUID | None,
    job_id: uuid.UUID | None,
    reason: str,
    db: AsyncSession,
) -> None:
    """Merge entity ``from_id`` into ``into_id`` and hard-delete the source.

    Re-points relations, chunk_entities, wiki_pages, and wiki_page_entities
    from the absorbed entity to the surviving one, deduplicates each table,
    deletes the absorbed entity, and writes an ``EntityMergeLog`` row.

    Args:
        from_id: UUID of the entity to be absorbed and deleted.
        into_id: UUID of the surviving entity.
        chunk_id: The chunk that triggered the merge, if known.
        job_id: The ingestion job that triggered the merge, if known.
        reason: Human-readable explanation for the merge (e.g., LLM reasoning).
        db: An active async SQLAlchemy session. Caller must commit.

    Raises:
        EntityResolutionError: If either entity does not exist or a database
            operation fails during the merge.
    """
    # Validate both entities exist.
    try:
        from_entity = await db.get(Entity, from_id)
    except Exception as exc:
        logger.error(
            "Failed to fetch source entity for merge",
            from_id=str(from_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to fetch source entity {from_id} for merge"
        ) from exc
    if from_entity is None:
        raise EntityResolutionError(
            f"Cannot merge: source entity not found: from_id={from_id}"
        )
    try:
        into_entity = await db.get(Entity, into_id)
    except Exception as exc:
        logger.error(
            "Failed to fetch target entity for merge",
            into_id=str(into_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to fetch target entity {into_id} for merge"
        ) from exc
    if into_entity is None:
        raise EntityResolutionError(
            f"Cannot merge: target entity not found: into_id={into_id}"
        )

    # Re-point and deduplicate relations.
    try:
        await db.execute(
            text(
                """
                UPDATE relations
                SET source_entity_id = :into_id
                WHERE source_entity_id = :from_id
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                UPDATE relations
                SET target_entity_id = :into_id
                WHERE target_entity_id = :from_id
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                DELETE FROM relations
                WHERE ctid NOT IN (
                    SELECT min(ctid)
                    FROM relations
                    GROUP BY source_entity_id, target_entity_id, relation_type, chunk_id
                )
                """
            ),
        )
    except Exception as exc:
        logger.error(
            "Failed to re-point relations during merge",
            from_id=str(from_id),
            into_id=str(into_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to re-point relations merging {from_id} into {into_id}"
        ) from exc

    # Re-point chunk_entities, wiki_pages, and wiki_page_entities.
    try:
        await db.execute(
            text(
                """
                DELETE FROM chunk_entities
                WHERE entity_id = :from_id
                  AND chunk_id IN (
                      SELECT chunk_id FROM chunk_entities WHERE entity_id = :into_id
                  )
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                UPDATE chunk_entities
                SET entity_id = :into_id
                WHERE entity_id = :from_id
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                UPDATE wiki_pages
                SET entity_id = NULL
                WHERE entity_id = :from_id
                  AND EXISTS (
                      SELECT 1 FROM wiki_pages WHERE entity_id = :into_id
                  )
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                UPDATE wiki_pages
                SET entity_id = :into_id
                WHERE entity_id = :from_id
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )

        await db.execute(
            text(
                """
                UPDATE wiki_page_entities
                SET entity_id = :into_id
                WHERE entity_id = :from_id
                """
            ),
            {"from_id": from_id, "into_id": into_id},
        )
    except Exception as exc:
        logger.error(
            "Failed to re-point dependent records during merge",
            from_id=str(from_id),
            into_id=str(into_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to re-point dependent records merging {from_id} into {into_id}"
        ) from exc

    # Deduplicate wiki_page_entities, write audit log, hard-delete source entity.
    try:
        await db.execute(
            text(
                """
                DELETE FROM wiki_page_entities
                WHERE ctid NOT IN (
                    SELECT min(ctid)
                    FROM wiki_page_entities
                    GROUP BY wiki_page_id, entity_id
                )
                """
            ),
        )

        merge_log = EntityMergeLog(
            id=uuid.uuid4(),
            merged_from_id=from_id,
            merged_into_id=into_id,
            chunk_id=chunk_id,
            job_id=job_id,
            reason=reason,
        )
        db.add(merge_log)
        await db.flush()

        await db.execute(
            text("DELETE FROM entities WHERE id = :from_id"),
            {"from_id": from_id},
        )
    except Exception as exc:
        logger.error(
            "Failed to finalize merge (dedup/audit/delete)",
            from_id=str(from_id),
            into_id=str(into_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to finalize merge of {from_id} into {into_id}"
        ) from exc

    logger.info(
        "entity merged",
        from_id=str(from_id),
        into_id=str(into_id),
        chunk_id=str(chunk_id) if chunk_id else None,
        job_id=str(job_id) if job_id else None,
        reason=reason,
    )
