"""tests/jobs.test_queue
--------------------
Unit tests for the Postgres-native job queue interface.

Covers enqueue, claim_next, complete_job, and fail_job with real database
round-trips via the per-test rollback ``db`` fixture.
"""

from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.jobs import claim_next, complete_job, enqueue, fail_job


async def test_enqueue_creates_pending_job_with_correct_defaults(
    db: AsyncSession,
) -> None:
    job = await enqueue(
        db,
        "ingest_document",
        payload={"file_path": "/tmp/test.md"},
    )

    assert job.status == "pending"
    assert job.attempts == 0
    assert job.max_retries == 3
    assert job.job_type == "ingest_document"
    assert job.payload == {"file_path": "/tmp/test.md"}
    assert job.id is not None


async def test_claim_next_returns_none_when_queue_empty(db: AsyncSession) -> None:
    result = await claim_next(db, worker_id="worker-1")
    assert result is None


async def test_claim_next_claims_oldest_pending_job(db: AsyncSession) -> None:
    job1 = await enqueue(db, "ingest_document", payload={"file_path": "/tmp/1.md"})
    await enqueue(db, "ingest_document", payload={"file_path": "/tmp/2.md"})
    await db.commit()

    claimed = await claim_next(db, worker_id="worker-1")
    assert claimed is not None
    assert claimed.id == job1.id
    assert claimed.status == "processing"
    assert claimed.worker_id == "worker-1"
    assert claimed.claimed_at is not None


async def test_claim_next_respects_scheduled_at_ordering(db: AsyncSession) -> None:
    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    await enqueue(
        db,
        "ingest_document",
        payload={"file_path": "/tmp/scheduled.md"},
        scheduled_at=future,
    )
    job2 = await enqueue(
        db,
        "ingest_document",
        payload={"file_path": "/tmp/unscheduled.md"},
    )
    await db.commit()

    claimed = await claim_next(db, worker_id="worker-1")
    assert claimed is not None
    assert claimed.id == job2.id


async def test_complete_job_sets_status_and_completed_at(db: AsyncSession) -> None:
    job = await enqueue(db, "ingest_document")
    await db.commit()

    await complete_job(job, db)

    assert job.status == "completed"
    assert job.completed_at is not None
    assert job.completed_at <= datetime.datetime.now(datetime.UTC)


async def test_fail_job_increments_attempts_and_schedules_retry(
    db: AsyncSession,
) -> None:
    job = await enqueue(db, "ingest_document")
    job.max_retries = 3
    await db.commit()

    await fail_job(job, db, "transient error")

    assert job.attempts == 1
    assert job.status == "pending"
    assert job.error_message is None
    assert job.claimed_at is None
    assert job.worker_id is None


async def test_fail_job_marks_failed_after_max_retries(db: AsyncSession) -> None:
    job = await enqueue(db, "ingest_document")
    job.max_retries = 2
    job.attempts = 1
    await db.commit()

    await fail_job(job, db, "final error")

    assert job.attempts == 2
    assert job.status == "failed"
    assert job.error_message == "final error"


async def test_claim_next_skips_failed_and_completed_jobs(db: AsyncSession) -> None:
    failed_job = await enqueue(
        db, "ingest_document", payload={"file_path": "/tmp/failed.md"}
    )
    failed_job.status = "failed"
    failed_job.attempts = 3
    failed_job.max_retries = 3

    completed_job = await enqueue(
        db,
        "ingest_document",
        payload={"file_path": "/tmp/completed.md"},
    )
    completed_job.status = "completed"

    pending_job = await enqueue(
        db,
        "ingest_document",
        payload={"file_path": "/tmp/pending.md"},
    )
    await db.commit()

    claimed = await claim_next(db, worker_id="worker-1")
    assert claimed is not None
    assert claimed.id == pending_job.id
