"""rag_wiki.graph.resolution
--------------------------
Real-time entity resolution pipeline.

For each extracted entity candidate:
  1. Embed the canonical name + description.
  2. Acquire a Postgres advisory lock on the hash of the canonical name.
  3. Vector-search existing entities within a distance threshold.
  4. If no candidates → create a new Entity.
  5. If candidates exist → ask the LLM (via tool call) whether to merge or create new.
  6. Persist the canonical entity and link it to the chunk.

After all entities are resolved, creates Relation rows from the extracted
relation indices. Returns a mapping of original index → resolved Entity.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus, Relation
from rag_wiki.db.models.source import Chunk
from rag_wiki.exceptions import EntityResolutionError, ExtractionError, LLMProviderError
from rag_wiki.graph.merge import merge_entity
from rag_wiki.graph.schemas import ExtractedEntity, ExtractedRelation, MergeDecision
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    EmbeddingProvider,
    ToolDefinition,
)
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)

_RESOLUTION_PROMPT = """\
You are an entity resolution engine. Decide whether the extracted entity
should be merged into an existing entity or created as a new one.

Source chunk:
{chunk_text}

Extracted entity:
- canonical_name: {canonical_name}
- entity_type: {entity_type}
- description: {description}

Existing candidates (most similar first):
{candidates}

Return your decision using the merge_decision tool."""

_MERGE_DECISION_TOOL = ToolDefinition(
    name="merge_decision",
    description=(
        "Decide whether to merge an extracted entity into an existing one "
        "or create a new entity."
    ),
    parameters={
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["merge", "new"],
            },
            "merged_into_id": {
                "type": "string",
                "format": "uuid",
            },
            "reasoning": {"type": "string"},
        },
        "required": ["decision", "reasoning"],
    },
)


def _advisory_lock_key(canonical_name: str) -> int:
    """Return a 64-bit positive integer suitable for pg_try_advisory_lock."""
    return (
        int.from_bytes(hashlib.md5(canonical_name.encode()).digest()[:8], "big")
        & 0x7FFFFFFFFFFFFFFF
    )


def _build_candidate_block(candidates: list[Entity]) -> str:
    """Format a list of existing entities for the resolution prompt."""
    lines: list[str] = []
    for i, ent in enumerate(candidates, start=1):
        lines.append(
            f"{i}. ID={ent.id}  name={ent.name!r}  type={ent.entity_type!r}  "
            f"description={ent.description!r}"
        )
    return "\n".join(lines) if lines else "(none)"


async def resolve_entities(
    candidates: list[ExtractedEntity],
    chunk: Chunk,
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
    job_id: uuid.UUID | None = None,
    relations: list[ExtractedRelation] | None = None,
) -> dict[int, Entity]:
    """Resolve a list of extracted entities against the existing graph.

    Args:
        candidates: Entities extracted from the chunk (in positional order).
        chunk: The chunk that produced the candidates.
        db: Active async SQLAlchemy session. Caller must commit.
        chat_provider: LLM provider for merge decisions.
        embed_provider: Embedding provider for candidate similarity search.
        job_id: Optional ingestion job ID for audit logging.
        relations: Optional list of ExtractedRelation to persist after
            entity resolution.

    Returns:
        Mapping from original candidate index (0-based) to the resolved
        canonical Entity record.

    Raises:
        EntityResolutionError: If a database or LLM operation fails during
            resolution and cannot be recovered.
        ExtractionError: If the LLM returns an invalid merge decision.
    """
    settings = get_settings()
    resolved: dict[int, Entity] = {}

    for idx, candidate in enumerate(candidates):
        # 1. Compute embedding.
        embed_text = f"{candidate.canonical_name} {candidate.description}"
        try:
            embeddings = await embed_provider.embed(
                [embed_text],
                model=settings.embedding_model,
            )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise EntityResolutionError(
                f"Embedding failed for candidate={candidate.canonical_name!r} "
                f"chunk_id={chunk.id}: {exc}"
            ) from exc

        embedding = embeddings[0]

        # 2. Acquire advisory lock.
        lock_key = _advisory_lock_key(candidate.canonical_name)
        lock_result = await db.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        acquired = lock_result.scalar()
        if not acquired:
            logger.warning(
                "advisory lock not acquired, skipping candidate",
                canonical_name=candidate.canonical_name,
                chunk_id=str(chunk.id),
                lock_key=lock_key,
            )
            continue

        try:
            # 3. Vector search.
            distance_threshold = settings.entity_resolution_distance_threshold
            top_k = settings.entity_resolution_top_k

            vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"
            search_result = await db.execute(
                text(
                    """
                    SELECT id, name, entity_type, description,
                           embedding <-> :vec AS distance
                    FROM entities
                    WHERE embedding <-> :vec <= :threshold
                    ORDER BY distance
                    LIMIT :top_k
                    """
                ),
                {
                    "vec": vec_literal,
                    "threshold": distance_threshold,
                    "top_k": top_k,
                },
            )
            rows = search_result.all()
            existing_candidates: list[Entity] = []
            for row in rows:
                ent = Entity(
                    id=row.id,
                    name=row.name,
                    entity_type=row.entity_type,
                    description=row.description,
                )
                existing_candidates.append(ent)

            if not existing_candidates:
                # 4. No candidates → create new Entity.
                entity = Entity(
                    name=candidate.canonical_name,
                    entity_type=candidate.entity_type,
                    description=candidate.description,
                    embedding=embedding,
                    status=PublishedStatus.PUBLISHED,
                )
                db.add(entity)
                await db.flush()
                resolved[idx] = entity
                logger.info(
                    "entity created (no candidates)",
                    canonical_name=candidate.canonical_name,
                    entity_id=str(entity.id),
                    chunk_id=str(chunk.id),
                )
            else:
                # 5. Ask LLM for merge decision.
                candidate_block = _build_candidate_block(existing_candidates)
                prompt = _RESOLUTION_PROMPT.format(
                    canonical_name=candidate.canonical_name,
                    entity_type=candidate.entity_type,
                    description=candidate.description,
                    candidates=candidate_block,
                    chunk_text=chunk.text_content or "",
                )
                request = CompletionRequest(
                    system=prompt,
                    messages=[],
                    model=settings.llm_model_resolution,
                    tools=[_MERGE_DECISION_TOOL],
                )
                response = await chat_provider.complete(request)

                if not response.tool_calls:
                    raise ExtractionError(
                        f"No tool call in merge decision response for "
                        f"candidate={candidate.canonical_name!r} chunk_id={chunk.id}"
                    )

                tool_call = response.tool_calls[0]
                try:
                    raw: dict[str, Any] = json.loads(tool_call.arguments)
                except json.JSONDecodeError as exc:
                    raise ExtractionError(
                        f"Invalid JSON in merge decision for "
                        f"candidate={candidate.canonical_name!r} "
                        f"chunk_id={chunk.id}: {exc}"
                    ) from exc

                try:
                    decision = MergeDecision.model_validate(raw)
                except Exception as exc:
                    raise ExtractionError(
                        f"Merge decision does not match schema for "
                        f"candidate={candidate.canonical_name!r} "
                        f"chunk_id={chunk.id}: {exc}"
                    ) from exc

                if decision.decision == "merge" and decision.merged_into_id:
                    # 7. Merge into existing entity.
                    # Create a new entity first so merge_entity has a source to absorb.
                    new_entity = Entity(
                        name=candidate.canonical_name,
                        entity_type=candidate.entity_type,
                        description=candidate.description,
                        embedding=embedding,
                        status=PublishedStatus.PUBLISHED,
                    )
                    db.add(new_entity)
                    await db.flush()
                    await merge_entity(
                        from_id=new_entity.id,
                        into_id=decision.merged_into_id,
                        chunk_id=chunk.id,
                        job_id=job_id,
                        reason=decision.reasoning,
                        db=db,
                    )
                    # Refresh to get the surviving entity.
                    surviving = await db.get(Entity, decision.merged_into_id)
                    if surviving is None:
                        raise EntityResolutionError(
                            f"Merge target disappeared after merge: "
                            f"merged_into_id={decision.merged_into_id}"
                        )
                    resolved[idx] = surviving
                    logger.info(
                        "entity merged",
                        canonical_name=candidate.canonical_name,
                        merged_into_id=str(decision.merged_into_id),
                        chunk_id=str(chunk.id),
                    )
                else:
                    # 8. Create new entity.
                    entity = Entity(
                        name=candidate.canonical_name,
                        entity_type=candidate.entity_type,
                        description=candidate.description,
                        embedding=embedding,
                        status=PublishedStatus.PUBLISHED,
                    )
                    db.add(entity)
                    await db.flush()
                    resolved[idx] = entity
                    logger.info(
                        "entity created (LLM decided new)",
                        canonical_name=candidate.canonical_name,
                        entity_id=str(entity.id),
                        chunk_id=str(chunk.id),
                    )

            # 9. Link the resolved entity to the chunk.
            entity_to_link = resolved[idx]
            await db.execute(
                text(
                    """
                    INSERT INTO chunk_entities (chunk_id, entity_id)
                    VALUES (:chunk_id, :entity_id)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"chunk_id": chunk.id, "entity_id": entity_to_link.id},
            )
        finally:
            # Release advisory lock.
            await db.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )

    # After all entities resolved, create Relation rows.
    if relations:
        for rel in relations:
            source_entity = resolved.get(rel.source_idx)
            target_entity = resolved.get(rel.target_idx)
            if source_entity is None or target_entity is None:
                source_status = "resolved" if source_entity else "missing"
                target_status = "resolved" if target_entity else "missing"
                raise EntityResolutionError(
                    "relation references unresolved entity index: "
                    f"source_idx={rel.source_idx} ({source_status}), "
                    f"target_idx={rel.target_idx} ({target_status}), "
                    f"chunk_id={chunk.id}"
                )
            relation = Relation(
                source_entity_id=source_entity.id,
                target_entity_id=target_entity.id,
                relation_type=rel.relation_type,
                chunk_id=chunk.id,
                status=PublishedStatus.PUBLISHED,
                confidence_tag="INFERRED",
            )
            db.add(relation)
        logger.info(
            "relations created",
            chunk_id=str(chunk.id),
            relation_count=len(relations),
        )

    return resolved
