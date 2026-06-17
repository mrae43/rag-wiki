"""rag_wiki.db.models.jobs
----------------------
Postgres-native job queue table.

Defines the ``jobs`` table with status/attempt tracking and retry support
per ADR-0005. Workers claim rows via ``SELECT ... FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import datetime
import enum
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from rag_wiki.db.base import Base, TimestampMixin, UUIDMixin


class JobStatus(enum.StrEnum):
    """Lifecycle of a job in the Postgres-native queue.

    ``claimed`` and ``processing`` are collapsed into a single ``processing``
    state because the ``SKIP LOCKED`` claim immediately starts work.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base, UUIDMixin, TimestampMixin):
    """A durable, retryable unit of work in the Postgres-native queue."""

    __tablename__ = "jobs"
    __table_args__ = (
        sa.Index("idx_jobs_status_scheduled_at", "status", "scheduled_at"),
        sa.Index("idx_jobs_target_entity_id", "target_entity_id"),
    )

    job_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.UUID, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        sa.dialects.postgresql.JSONB, nullable=True
    )
    status: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING,
    )
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=3)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        sa.dialects.postgresql.JSONB, nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
