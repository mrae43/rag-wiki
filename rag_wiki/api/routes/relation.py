"""rag_wiki.api.routes.relation
-----------------------------
Read-only knowledge graph relation browsing routes.

Provides a paginated, filterable list of relations. Nested entity objects
are eager-loaded to avoid N+1 queries even though only IDs are returned.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.api.dependencies import get_db
from rag_wiki.api.schemas import PaginatedListEnvelope
from rag_wiki.db.models import Relation
from rag_wiki.exceptions import DatabaseError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/relations", tags=["relations"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class RelationResponse(BaseModel):
    """Public representation of a knowledge graph relation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    relation_type: str
    source_entity_id: uuid.UUID
    target_entity_id: uuid.UUID
    status: str
    confidence_tag: str
    confidence_score: float | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


@router.get(
    "",
    response_model=PaginatedListEnvelope[RelationResponse],
    operation_id="list_relations",
)
async def list_relations(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    relation_type: str | None = None,
    source_entity_id: uuid.UUID | None = None,
    target_entity_id: uuid.UUID | None = None,
) -> PaginatedListEnvelope[RelationResponse]:
    """List relations with offset/limit pagination and optional filters."""
    limit = min(limit, MAX_LIMIT)

    stmt = select(Relation).options(
        joinedload(Relation.source_entity),
        joinedload(Relation.target_entity),
    )
    count_stmt = select(func.count(Relation.id))

    if relation_type is not None:
        stmt = stmt.where(Relation.relation_type == relation_type)
        count_stmt = count_stmt.where(Relation.relation_type == relation_type)
    if source_entity_id is not None:
        stmt = stmt.where(Relation.source_entity_id == source_entity_id)
        count_stmt = count_stmt.where(Relation.source_entity_id == source_entity_id)
    if target_entity_id is not None:
        stmt = stmt.where(Relation.target_entity_id == target_entity_id)
        count_stmt = count_stmt.where(Relation.target_entity_id == target_entity_id)

    stmt = stmt.order_by(Relation.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        relations = result.unique().scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list relations",
            offset=offset,
            limit=limit,
            relation_type=relation_type,
            source_entity_id=str(source_entity_id) if source_entity_id else None,
            target_entity_id=str(target_entity_id) if target_entity_id else None,
            error=str(exc),
        )
        raise DatabaseError("Failed to list relations") from exc
    items = [
        RelationResponse(
            id=r.id,
            relation_type=r.relation_type,
            source_entity_id=r.source_entity_id,
            target_entity_id=r.target_entity_id,
            status=r.status,
            confidence_tag=r.confidence_tag,
            confidence_score=r.confidence_score,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in relations
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)
