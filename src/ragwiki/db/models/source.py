"""ragwiki.db.models.source
------------------------
Source, chunk, and chunk-to-entity join models.

Defines the ingestion pipeline storage: ``sources`` (document metadata),
``chunks`` (atomic text units with embeddings), and the ``chunk_entities``
join table linking chunks to extracted entities.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ragwiki.db.base import Base, TimestampMixin, UUIDMixin
from ragwiki.settings import get_settings

if TYPE_CHECKING:
    from ragwiki.db.models.graph import Entity


class ProcessingStatus(enum.StrEnum):
    """Lifecycle of a source or chunk through the ingestion pipeline."""

    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class Source(Base, UUIDMixin, TimestampMixin):
    """Metadata about an ingested document. File content lives on disk."""

    __tablename__ = "sources"

    file_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_size: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        sa.dialects.postgresql.JSONB, nullable=True
    )

    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", back_populates="source", cascade="all, delete-orphan"
    )


class Chunk(Base, UUIDMixin, TimestampMixin):
    """Atomic text unit extracted from a source, with embedding."""

    __tablename__ = "chunks"
    __table_args__ = (
        sa.Index("idx_chunks_source_id_chunk_index", "source_id", "chunk_index"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("sources.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embedding_dimensions), nullable=True
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING,
    )

    source: Mapped[Source] = relationship("Source", back_populates="chunks")
    entities: Mapped[list[Entity]] = relationship(
        "Entity", secondary="chunk_entities", back_populates="chunks"
    )


class ChunkEntity(Base):
    """Join table linking chunks to the entities they mention."""

    __tablename__ = "chunk_entities"
    __table_args__ = (
        sa.Index("idx_chunk_entities_chunk_id", "chunk_id"),
        sa.Index("idx_chunk_entities_entity_id", "entity_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
