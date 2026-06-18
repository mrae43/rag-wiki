"""tests/api/routes/test_job
--------------------------
Tests for the read-only job observability endpoints.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import Job, JobStatus


async def test_list_jobs_paginated_and_filtered(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /jobs supports offset/limit and status/job_type filters."""
    for i in range(3):
        db.add(
            Job(
                job_type="ingest_document",
                payload={"file_path": f"/tmp/{i}.txt"},
                status=JobStatus.PENDING,
            )
        )
    db.add(
        Job(
            job_type="synthesize_entity",
            payload={"source_ids": [str(uuid.uuid4())]},
            status=JobStatus.COMPLETED,
        )
    )
    await db.flush()

    response = await api_client.get("/api/v1/jobs?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2

    by_status = await api_client.get("/api/v1/jobs?status=completed")
    assert by_status.status_code == 200
    data = by_status.json()
    assert data["total"] == 1
    assert data["items"][0]["job_type"] == "synthesize_entity"

    by_type = await api_client.get("/api/v1/jobs?job_type=ingest_document")
    assert by_type.status_code == 200
    data = by_type.json()
    assert data["total"] == 3


async def test_get_job_by_id(api_client: AsyncClient, db: AsyncSession) -> None:
    """GET /jobs/{id} returns the job; unknown id returns 404."""
    job = Job(
        job_type="ingest_document",
        payload={"file_path": "/tmp/bar.txt"},
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.flush()

    response = await api_client.get(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["job_type"] == "ingest_document"
    assert body["status"] == JobStatus.PENDING
    assert body["payload"] == {"file_path": "/tmp/bar.txt"}

    missing = await api_client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert missing.status_code == 404
