"""
rag_wiki.jobs
------------
Postgres-native job queue implementation.

Provides enqueue, claim, complete, and fail operations backed by a `jobs` table
with `SELECT FOR UPDATE SKIP LOCKED` claiming. Worker entrypoint is in rag_wiki.worker.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.jobs import Job

logger = structlog.get_logger(__name__)

# Claim the next available job atomically. SKIP LOCKED prevents multiple
# workers from blocking on the same row.
_CLAIM_JOB_SQL = """
    UPDATE jobs
    SET status = 'processing', claimed_at = now(), worker_id = :worker_id
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'pending'
          AND attempts <= max_retries
        ORDER BY scheduled_at NULLS FIRST, created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id
"""


async def enqueue(
    db: AsyncSession,
    job_type: str,
    payload: dict[str, Any] | None = None,
    scheduled_at: datetime.datetime | None = None,
    target_entity_id: uuid.UUID | None = None,
) -> Job:
    """Create a new job in the queue.

    Args:
        db: Active async SQLAlchemy session. Caller must commit.
        job_type: The type of job (e.g., ``ingest_document``).
        payload: Arbitrary JSON-serializable payload for the job.
        scheduled_at: Optional UTC datetime to delay job execution.
        target_entity_id: Optional entity UUID for entity-targeted jobs
            (e.g., ``synthesize_entity``).

    Returns:
        The newly created Job instance.
    """
    job = Job(
        job_type=job_type,
        payload=payload,
        status="pending",
        attempts=0,
        scheduled_at=scheduled_at,
        target_entity_id=target_entity_id,
    )
    db.add(job)
    await db.flush()
    logger.info("job enqueued", job_id=str(job.id), job_type=job_type)
    return job


async def claim_next(
    db: AsyncSession,
    job_types: list[str] | None = None,
    worker_id: str | None = None,
) -> Job | None:
    """Claim the next available job atomically.

    Uses ``SELECT FOR UPDATE SKIP LOCKED`` to ensure only one worker claims
    each pending job. Sets status to ``processing`` and records the worker_id.

    Args:
        db: Active async SQLAlchemy session. Caller must commit.
        job_types: Optional filter by job type (not implemented in v1).
        worker_id: Optional identifier for the claiming worker.

    Returns:
        The claimed Job, or None if no jobs are available.
    """
    # job_types filtering is reserved for future multi-type worker dispatch.
    _ = job_types
    result = await db.execute(
        text(_CLAIM_JOB_SQL),
        {"worker_id": worker_id or "unknown"},
    )
    row = result.fetchone()
    if row is None:
        return None
    job_id = row[0]
    stmt = select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
    job_result = await db.execute(stmt)
    job = job_result.scalar_one_or_none()
    if job is not None:
        logger.info(
            "job claimed",
            job_id=str(job.id),
            job_type=job.job_type,
            worker_id=worker_id,
        )
    return job


async def complete_job(job: Job, db: AsyncSession) -> None:
    """Mark a job as completed.

    Args:
        job: The job to complete.
        db: Active async SQLAlchemy session. Caller must commit.
    """
    job.status = "completed"
    job.completed_at = datetime.datetime.now(datetime.UTC)
    logger.info("job completed", job_id=str(job.id), job_type=job.job_type)


async def release_claim_to_pending(job: Job, db: AsyncSession) -> None:
    """Release a claimed job back to pending status.

    Used when a job cannot proceed (e.g., advisory lock exhausted) but
    should be retried rather than failed.

    Args:
        job: The job to release.
        db: Active async SQLAlchemy session. Caller must commit.
    """
    job.status = "pending"
    job.claimed_at = None
    job.worker_id = None
    logger.info("job released to pending", job_id=str(job.id), job_type=job.job_type)


async def fail_job(job: Job, db: AsyncSession, error_message: str) -> None:
    """Record a job failure and either schedule a retry or mark it failed.

    Increments attempts. If ``attempts < max_retries``, resets status to
    ``pending`` so the worker can claim it again. Otherwise, marks it ``failed``.

    Args:
        job: The job that failed.
        db: Active async SQLAlchemy session. Caller must commit.
        error_message: Human-readable reason for the failure.
    """
    job.attempts += 1
    job.error_message = error_message
    if job.attempts <= job.max_retries:
        job.status = "pending"
        job.claimed_at = None
        job.worker_id = None
        logger.warning(
            "job failed, retrying",
            job_id=str(job.id),
            job_type=job.job_type,
            attempts=job.attempts,
            max_retries=job.max_retries,
            error_message=error_message,
        )
    else:
        job.status = "failed"
        logger.error(
            "job failed after max retries",
            job_id=str(job.id),
            job_type=job.job_type,
            attempts=job.attempts,
            max_retries=job.max_retries,
            error_message=error_message,
        )
