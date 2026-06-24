"""rag_wiki.db.models.planner
-------------------------
SQLAlchemy model for the ``query_plans`` table.

Persists query plans produced by the query planner at ingest/query time
per ADR-0014. Rows are auto-expired after ``ttl_days`` (default 30).
"""

from __future__ import annotations

import datetime
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from rag_wiki.db.base import Base


class QueryPlanRecord(Base):
    """Persisted query plan for a single user query.

    One row per query, written at classification time before retrieval
    begins. The plan encodes the retrieval strategy derived from the
    query type classification.
    """

    __tablename__ = "query_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_query: Mapped[str] = mapped_column(sa.Text, nullable=False)
    classified_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    retrieval_depth: Mapped[str] = mapped_column(sa.Text, nullable=False)
    seed_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    termination_condition: Mapped[str] = mapped_column(sa.Text, nullable=False)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)
    classification_source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    rationale: Mapped[str] = mapped_column(sa.Text, nullable=False)
    planner_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    ttl_days: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=30)
