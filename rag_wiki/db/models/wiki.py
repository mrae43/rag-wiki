"""rag_wiki.db.models.wiki
----------------------
Wiki page storage and backlink join table.

Defines the ``wiki_pages`` table (source of truth per ADR-0006) and the
``wiki_page_entities`` join table for backlink traversal.
"""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_wiki.db.base import Base, TimestampMixin, UUIDMixin
from rag_wiki.db.models.graph import PublishedStatus

if TYPE_CHECKING:
    from rag_wiki.db.models.graph import Entity


class WikiPage(Base, UUIDMixin, TimestampMixin):
    """LLM-maintained markdown page for an entity or topic."""

    __tablename__ = "wiki_pages"
    __table_args__ = (
        sa.UniqueConstraint("entity_id"),
        sa.UniqueConstraint("slug"),
    )

    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    slug: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=PublishedStatus.PUBLISHED,
        server_default=PublishedStatus.PUBLISHED,
    )
    synthesized_from_sources: Mapped[list[str] | None] = mapped_column(
        sa.dialects.postgresql.JSONB, nullable=True
    )
    synthesized_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    entity: Mapped[Entity | None] = relationship("Entity", back_populates="wiki_pages")
    mentioned_entities: Mapped[list[Entity]] = relationship(
        "Entity", secondary="wiki_page_entities", back_populates="mentioning_pages"
    )


class WikiPageEntity(Base):
    """Join table for backlink traversal: which entities a wiki page mentions."""

    __tablename__ = "wiki_page_entities"
    __table_args__ = (
        sa.Index("idx_wiki_page_entities_wiki_page_id", "wiki_page_id"),
        sa.Index("idx_wiki_page_entities_entity_id", "entity_id"),
    )

    wiki_page_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
