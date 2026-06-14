"""ragwiki.db.models.graph
-----------------------
Knowledge graph entities and relations.

Defines the ``entities`` and ``relations`` tables (plain relational tables
per ADR-0001) with recursive-CTE-friendly indexes.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ragwiki.db.base import Base, TimestampMixin, UUIDMixin
from ragwiki.settings import get_settings

if TYPE_CHECKING:
    from ragwiki.db.models.source import Chunk
    from ragwiki.db.models.wiki import WikiPage


class PublishedStatus(enum.StrEnum):
    """Publication state for entities, relations, and wiki pages.

    v1 only writes ``published``; ``pending_review`` is reserved for a
    future review-queue feature (ADR-0010).
    """

    PUBLISHED = "published"
    PENDING_REVIEW = "pending_review"


class Entity(Base, UUIDMixin, TimestampMixin):
    """Knowledge graph node: a canonical entity with name, type, and embedding."""

    __tablename__ = "entities"

    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embedding_dimensions), nullable=True
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=PublishedStatus.PUBLISHED,
        server_default=PublishedStatus.PUBLISHED,
    )

    # Outgoing edges
    outgoing_relations: Mapped[list[Relation]] = relationship(
        "Relation",
        foreign_keys="Relation.source_entity_id",
        back_populates="source_entity",
        cascade="all, delete-orphan",
    )
    # Incoming edges
    incoming_relations: Mapped[list[Relation]] = relationship(
        "Relation",
        foreign_keys="Relation.target_entity_id",
        back_populates="target_entity",
        cascade="all, delete-orphan",
    )

    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", secondary="chunk_entities", back_populates="entities"
    )
    wiki_pages: Mapped[list[WikiPage]] = relationship(
        "WikiPage", back_populates="entity"
    )
    mentioning_pages: Mapped[list[WikiPage]] = relationship(
        "WikiPage",
        secondary="wiki_page_entities",
        back_populates="mentioned_entities",
    )


class Relation(Base, UUIDMixin, TimestampMixin):
    """Knowledge graph edge linking two entities, with provenance via chunk_id."""

    __tablename__ = "relations"
    __table_args__ = (
        sa.Index(
            "idx_relations_source_target_type",
            "source_entity_id",
            "target_entity_id",
            "relation_type",
        ),
    )

    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=PublishedStatus.PUBLISHED,
        server_default=PublishedStatus.PUBLISHED,
    )

    chunk: Mapped[Chunk] = relationship("Chunk")
    source_entity: Mapped[Entity] = relationship(
        "Entity", foreign_keys=[source_entity_id], back_populates="outgoing_relations"
    )
    target_entity: Mapped[Entity] = relationship(
        "Entity", foreign_keys=[target_entity_id], back_populates="incoming_relations"
    )
