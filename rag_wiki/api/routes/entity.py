"""rag_wiki.api.routes.entity
---------------------------
Read-only knowledge graph entity browsing routes.

Provides paginated entity listing, detail lookup, and nested sub-resources
for an entity's relations and primary wiki page.
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
from rag_wiki.api.exceptions import BadRequestError, NotFoundError
from rag_wiki.api.schemas import PaginatedListEnvelope
from rag_wiki.db.models import Entity, Relation, WikiPage
from rag_wiki.exceptions import DatabaseError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/entities", tags=["entities"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class EntityResponse(BaseModel):
    """Public representation of a knowledge graph entity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    entity_type: str
    status: str
    description: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EntityRelationResponse(BaseModel):
    """Public representation of a relation connected to an entity."""

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


class EntityWikiPageResponse(BaseModel):
    """Public representation of an entity's primary wiki page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    content: str
    status: str
    entity_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


async def _get_entity_or_404(db: AsyncSession, entity_id: uuid.UUID) -> Entity:
    """Fetch an entity by id, raising a 404 Problem Detail if missing."""
    try:
        result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch entity",
            entity_id=str(entity_id),
            error=str(exc),
        )
        raise DatabaseError(f"Failed to fetch entity {entity_id}") from exc
    if entity is None:
        raise NotFoundError(f"Entity not found: {entity_id}")
    return entity


@router.get(
    "",
    response_model=PaginatedListEnvelope[EntityResponse],
    operation_id="list_entities",
)
async def list_entities(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    status: str | None = None,
    entity_type: str | None = None,
    name: str | None = None,
) -> PaginatedListEnvelope[EntityResponse]:
    """List entities with offset/limit pagination and optional filters."""
    limit = min(limit, MAX_LIMIT)

    stmt = select(Entity)
    count_stmt = select(func.count(Entity.id))

    if status is not None:
        stmt = stmt.where(Entity.status == status)
        count_stmt = count_stmt.where(Entity.status == status)
    if entity_type is not None:
        stmt = stmt.where(Entity.entity_type == entity_type)
        count_stmt = count_stmt.where(Entity.entity_type == entity_type)
    if name is not None:
        like = f"%{name}%"
        stmt = stmt.where(Entity.name.ilike(like))
        count_stmt = count_stmt.where(Entity.name.ilike(like))

    stmt = stmt.order_by(Entity.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        entities = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list entities",
            offset=offset,
            limit=limit,
            status=status,
            error=str(exc),
        )
        raise DatabaseError("Failed to list entities") from exc
    items = [
        EntityResponse(
            id=e.id,
            name=e.name,
            entity_type=e.entity_type,
            status=e.status,
            description=e.description,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in entities
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)


@router.get(
    "/{entity_id}",
    response_model=EntityResponse,
    operation_id="get_entity",
)
async def get_entity(
    entity_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EntityResponse:
    """Return a single entity by id."""
    entity = await _get_entity_or_404(db, entity_id)
    return EntityResponse(
        id=entity.id,
        name=entity.name,
        entity_type=entity.entity_type,
        status=entity.status,
        description=entity.description,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


@router.get(
    "/{entity_id}/relations",
    response_model=PaginatedListEnvelope[EntityRelationResponse],
    operation_id="list_entity_relations",
)
async def list_entity_relations(
    entity_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    direction: str = "both",
) -> PaginatedListEnvelope[EntityRelationResponse]:
    """Return relations where the entity is source, target, or both.

    Args:
        entity_id: The entity whose relations are requested.
        direction: One of ``outgoing``, ``incoming``, or ``both``.
    """
    limit = min(limit, MAX_LIMIT)
    await _get_entity_or_404(db, entity_id)

    stmt = select(Relation).options(
        joinedload(Relation.source_entity),
        joinedload(Relation.target_entity),
    )
    count_stmt = select(func.count(Relation.id))

    if direction == "outgoing":
        stmt = stmt.where(Relation.source_entity_id == entity_id)
        count_stmt = count_stmt.where(Relation.source_entity_id == entity_id)
    elif direction == "incoming":
        stmt = stmt.where(Relation.target_entity_id == entity_id)
        count_stmt = count_stmt.where(Relation.target_entity_id == entity_id)
    elif direction == "both":
        filter_clause = (Relation.source_entity_id == entity_id) | (
            Relation.target_entity_id == entity_id
        )
        stmt = stmt.where(filter_clause)
        count_stmt = count_stmt.where(filter_clause)
    else:
        raise BadRequestError(f"Invalid direction: {direction}")

    stmt = stmt.order_by(Relation.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        relations = result.unique().scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list entity relations",
            entity_id=str(entity_id),
            direction=direction,
            offset=offset,
            limit=limit,
            error=str(exc),
        )
        raise DatabaseError(f"Failed to list relations for entity {entity_id}") from exc
    items = [
        EntityRelationResponse(
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


@router.get(
    "/{entity_id}/wiki-page",
    response_model=EntityWikiPageResponse,
    operation_id="get_entity_wiki_page",
)
async def get_entity_wiki_page(
    entity_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EntityWikiPageResponse:
    """Return the wiki page whose primary entity id matches the entity."""
    await _get_entity_or_404(db, entity_id)

    try:
        result = await db.execute(
            select(WikiPage).where(WikiPage.entity_id == entity_id)
        )
        page = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch wiki page for entity",
            entity_id=str(entity_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to fetch wiki page for entity {entity_id}"
        ) from exc
    if page is None:
        raise NotFoundError(f"Wiki page not found for entity: {entity_id}")

    return EntityWikiPageResponse(
        id=page.id,
        slug=page.slug,
        title=page.title,
        content=page.content,
        status=page.status,
        entity_id=page.entity_id,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )
