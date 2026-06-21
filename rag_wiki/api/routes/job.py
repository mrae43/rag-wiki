"""rag_wiki.api.routes.job
------------------------
Read-only job queue observability routes.

Jobs are created implicitly by ingestion and other pipeline stages; the API
only exposes list and detail views for operators.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_db
from rag_wiki.api.exceptions import NotFoundError
from rag_wiki.api.schemas import PaginatedListEnvelope
from rag_wiki.db.models import Job
from rag_wiki.exceptions import DatabaseError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class JobResponse(BaseModel):
    """Public representation of a job in the queue."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    claimed_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None


async def _get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> Job:
    """Fetch a job by id, raising a 404 Problem Detail if missing."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch job",
            job_id=str(job_id),
            error=str(exc),
        )
        raise DatabaseError(f"Failed to fetch job {job_id}") from exc
    if job is None:
        raise NotFoundError(f"Job not found: {job_id}")
    return job


@router.get(
    "",
    response_model=PaginatedListEnvelope[JobResponse],
    operation_id="list_jobs",
)
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    status: str | None = None,
    job_type: str | None = None,
) -> PaginatedListEnvelope[JobResponse]:
    """List jobs with offset/limit pagination and optional filters."""
    limit = min(limit, MAX_LIMIT)

    stmt = select(Job)
    count_stmt = select(func.count(Job.id))

    if status is not None:
        stmt = stmt.where(Job.status == status)
        count_stmt = count_stmt.where(Job.status == status)
    if job_type is not None:
        stmt = stmt.where(Job.job_type == job_type)
        count_stmt = count_stmt.where(Job.job_type == job_type)

    stmt = stmt.order_by(Job.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        jobs = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list jobs",
            offset=offset,
            limit=limit,
            status=status,
            job_type=job_type,
            error=str(exc),
        )
        raise DatabaseError("Failed to list jobs") from exc
    items = [
        JobResponse(
            id=j.id,
            job_type=j.job_type,
            status=j.status,
            payload=j.payload,
            result=j.result,
            error_message=j.error_message,
            created_at=j.created_at,
            updated_at=j.updated_at,
            claimed_at=j.claimed_at,
            completed_at=j.completed_at,
        )
        for j in jobs
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    operation_id="get_job",
)
async def get_job(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobResponse:
    """Return a single job by id."""
    try:
        job = await _get_job_or_404(db, job_id)
        return JobResponse(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            payload=job.payload,
            result=job.result,
            error_message=job.error_message,
            created_at=job.created_at,
            updated_at=job.updated_at,
            claimed_at=job.claimed_at,
            completed_at=job.completed_at,
        )
    except (NotFoundError, DatabaseError):
        raise
    except Exception as exc:
        logger.error(
            "failed_to_get_job",
            job_id=str(job_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise DatabaseError(f"Failed to fetch job {job_id}") from exc
