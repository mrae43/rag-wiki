"""rag_wiki.api.routes.wiki_page
------------------------------
Read-only wiki page browsing routes.

Provides paginated wiki page listing, detail lookup by id or slug, and a
nested endpoint returning the entities that mention a page.
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

from rag_wiki.api.dependencies import get_db
from rag_wiki.api.exceptions import NotFoundError
from rag_wiki.api.schemas import PaginatedListEnvelope
from rag_wiki.db.models import Entity, WikiPage, WikiPageEntity
from rag_wiki.exceptions import DatabaseError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/wiki-pages", tags=["wiki-pages"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class WikiPageResponse(BaseModel):
    """Public representation of a wiki page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    content: str
    status: str
    entity_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WikiPageMentionResponse(BaseModel):
    """Public representation of an entity that mentions a wiki page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    entity_type: str


async def _get_page_or_404(db: AsyncSession, page_id: uuid.UUID) -> WikiPage:
    """Fetch a wiki page by id, raising a 404 Problem Detail if missing."""
    try:
        result = await db.execute(select(WikiPage).where(WikiPage.id == page_id))
        page = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch wiki page",
            page_id=str(page_id),
            error=str(exc),
        )
        raise DatabaseError(f"Failed to fetch wiki page {page_id}") from exc
    if page is None:
        raise NotFoundError(f"Wiki page not found: {page_id}")
    return page


def _page_to_response(page: WikiPage) -> WikiPageResponse:
    """Convert a WikiPage ORM object to the public response model."""
    return WikiPageResponse(
        id=page.id,
        slug=page.slug,
        title=page.title,
        content=page.content,
        status=page.status,
        entity_id=page.entity_id,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


@router.get(
    "",
    response_model=PaginatedListEnvelope[WikiPageResponse],
    operation_id="list_wiki_pages",
)
async def list_wiki_pages(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    status: str | None = None,
    title: str | None = None,
) -> PaginatedListEnvelope[WikiPageResponse]:
    """List wiki pages with offset/limit pagination and optional filters."""
    limit = min(limit, MAX_LIMIT)

    stmt = select(WikiPage)
    count_stmt = select(func.count(WikiPage.id))

    if status is not None:
        stmt = stmt.where(WikiPage.status == status)
        count_stmt = count_stmt.where(WikiPage.status == status)
    if title is not None:
        like = f"%{title}%"
        stmt = stmt.where(WikiPage.title.ilike(like))
        count_stmt = count_stmt.where(WikiPage.title.ilike(like))

    stmt = stmt.order_by(WikiPage.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        pages = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list wiki pages",
            offset=offset,
            limit=limit,
            status=status,
            error=str(exc),
        )
        raise DatabaseError("Failed to list wiki pages") from exc
    items = [_page_to_response(p) for p in pages]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)


@router.get(
    "/{page_id}",
    response_model=WikiPageResponse,
    operation_id="get_wiki_page",
)
async def get_wiki_page(
    page_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WikiPageResponse:
    """Return a single wiki page by id."""
    page = await _get_page_or_404(db, page_id)
    return _page_to_response(page)


@router.get(
    "/slug/{slug}",
    response_model=WikiPageResponse,
    operation_id="get_wiki_page_by_slug",
)
async def get_wiki_page_by_slug(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WikiPageResponse:
    """Return a single wiki page by slug.

    Slug lookup is case-insensitive so human-readable URLs are forgiving.
    """
    try:
        result = await db.execute(
            select(WikiPage).where(func.lower(WikiPage.slug) == slug.lower())
        )
        page = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch wiki page by slug",
            slug=slug,
            error=str(exc),
        )
        raise DatabaseError(f"Failed to fetch wiki page by slug {slug}") from exc
    if page is None:
        raise NotFoundError(f"Wiki page not found: {slug}")
    return _page_to_response(page)


@router.get(
    "/{page_id}/mentions",
    response_model=PaginatedListEnvelope[WikiPageMentionResponse],
    operation_id="list_wiki_page_mentions",
)
async def list_wiki_page_mentions(
    page_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> PaginatedListEnvelope[WikiPageMentionResponse]:
    """Return entities that mention the wiki page."""
    limit = min(limit, MAX_LIMIT)
    await _get_page_or_404(db, page_id)

    stmt = (
        select(Entity)
        .join(WikiPageEntity, WikiPageEntity.entity_id == Entity.id)
        .where(WikiPageEntity.wiki_page_id == page_id)
        .order_by(Entity.name)
        .offset(offset)
        .limit(limit)
    )
    count_stmt = (
        select(func.count(Entity.id))
        .join(WikiPageEntity, WikiPageEntity.entity_id == Entity.id)
        .where(WikiPageEntity.wiki_page_id == page_id)
    )

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        entities = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list wiki page mentions",
            page_id=str(page_id),
            offset=offset,
            limit=limit,
            error=str(exc),
        )
        raise DatabaseError(f"Failed to list mentions for wiki page {page_id}") from exc
    items = [
        WikiPageMentionResponse(
            id=e.id,
            name=e.name,
            entity_type=e.entity_type,
        )
        for e in entities
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)
