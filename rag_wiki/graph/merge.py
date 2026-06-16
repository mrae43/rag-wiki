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
from rag_wiki.exceptions import EntityResolutionError

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
    from_entity = await db.get(Entity, from_id)
    if from_entity is None:
        raise EntityResolutionError(
            f"Cannot merge: source entity not found: from_id={from_id}"
        )
    into_entity = await db.get(Entity, into_id)
    if into_entity is None:
        raise EntityResolutionError(
            f"Cannot merge: target entity not found: into_id={into_id}"
        )

    # Re-point relations.source_entity_id
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

    # Re-point relations.target_entity_id
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

    # Deduplicate relations after re-pointing.
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

    # Remove chunk_entities rows that would create duplicates after re-pointing.
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

    # Re-point remaining chunk_entities.entity_id.
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

    # Re-point wiki_pages.entity_id (nullable, unique constraint).
    # If the target already has a wiki_page, we must set the source's page
    # to NULL to avoid unique-constraint violations.
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

    # Re-point wiki_page_entities.entity_id
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

    # Deduplicate wiki_page_entities after re-pointing.
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

    # Write audit log using the ORM but flush before the hard delete so
    # the insert is executed while the foreign key entity still exists.
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

    # Hard-delete the absorbed entity.
    await db.execute(
        text("DELETE FROM entities WHERE id = :from_id"),
        {"from_id": from_id},
    )

    logger.info(
        "entity merged",
        from_id=str(from_id),
        into_id=str(into_id),
        chunk_id=str(chunk_id) if chunk_id else None,
        job_id=str(job_id) if job_id else None,
        reason=reason,
    )
