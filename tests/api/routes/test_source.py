"""tests/api/routes/test_source
-----------------------------
Tests for the source upload and lifecycle endpoints.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_db
from rag_wiki.db.models import Chunk, ProcessingStatus, Source
from rag_wiki.main import create_app
from rag_wiki.settings import Settings, get_settings


async def test_upload_source_creates_source_and_job(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """POST /sources stores the file, creates a Source row, and returns a job_id."""
    content = b"This is a test document."
    metadata = '{"category": "test"}'

    response = await api_client.post(
        "/api/v1/sources",
        data={"metadata": metadata},
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["file_name"] == "test.txt"
    assert body["status"] == ProcessingStatus.PENDING
    assert body["metadata"] == {"category": "test"}
    assert body["job_id"] is not None

    source_id = uuid.UUID(body["id"])
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one()
    assert source.file_name == "test.txt"
    assert source.file_type == "text/plain"
    assert source.file_size == len(content)
    assert source.status == ProcessingStatus.PENDING
    assert source.metadata_ == {"category": "test"}
    assert source.source_plan is not None
    assert source.source_plan["selected_parser"] == "simple"

    upload_path = Path(source.storage_key)
    assert upload_path.exists()
    assert upload_path.read_bytes() == content


async def test_upload_empty_file_rejected(api_client: AsyncClient) -> None:
    """Empty file uploads return a 400 Problem Detail."""
    response = await api_client.post(
        "/api/v1/sources",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["status"] == 400
    assert "empty" in body["detail"].lower()


async def test_upload_oversized_file_rejected(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    """Files exceeding the configured max size return a 413 Problem Detail."""
    settings = Settings.model_validate(get_settings())
    settings.upload_dir = tmp_path / "uploads"
    settings.upload_max_file_size_bytes = 10
    await _mkdir(settings.upload_dir)

    app = create_app(settings)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/sources",
            files={"file": ("big.txt", io.BytesIO(b"x" * 20), "text/plain")},
        )

    assert response.status_code == 413
    body = response.json()
    assert body["status"] == 413
    assert "exceeds" in body["detail"].lower()


async def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


async def test_list_sources_paginated_and_filtered(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /sources supports offset/limit and status/filename filters."""
    for i in range(3):
        db.add(
            Source(
                file_path=f"/tmp/{i}.txt",
                file_name=f"report-{i}.txt",
                file_type="text/plain",
                file_size=10,
                status=ProcessingStatus.PENDING,
            )
        )
    await db.flush()

    response = await api_client.get("/api/v1/sources?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["offset"] == 0
    assert body["limit"] == 2
    assert len(body["items"]) == 2

    filtered = await api_client.get("/api/v1/sources?filename=report-1")
    assert filtered.status_code == 200
    data = filtered.json()
    assert data["total"] == 1
    assert data["items"][0]["file_name"] == "report-1.txt"


async def test_get_source_by_id(api_client: AsyncClient, db: AsyncSession) -> None:
    """GET /sources/{id} returns the source; unknown id returns 404."""
    source = Source(
        file_path="/tmp/foo.txt",
        file_name="foo.txt",
        file_type="text/plain",
        file_size=5,
        status=ProcessingStatus.PENDING,
    )
    db.add(source)
    await db.flush()

    response = await api_client.get(f"/api/v1/sources/{source.id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(source.id)

    missing = await api_client.get(f"/api/v1/sources/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_delete_source_removes_row_and_file(
    api_client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    """DELETE /sources/{id} removes the DB row and the uploaded file."""
    source_id = uuid.uuid4()
    upload_path = tmp_path / "uploads" / str(source_id)
    await _mkdir(upload_path.parent)
    upload_path.write_text("to be deleted")

    source = Source(
        id=source_id,
        file_path=str(upload_path),
        file_name="delete-me.txt",
        file_type="text/plain",
        file_size=13,
        status=ProcessingStatus.PENDING,
    )
    db.add(source)
    await db.flush()

    response = await api_client.delete(f"/api/v1/sources/{source_id}")
    assert response.status_code == 204
    assert not upload_path.exists()

    result = await db.execute(select(Source).where(Source.id == source_id))
    assert result.scalar_one_or_none() is None


async def test_delete_missing_source_returns_404(api_client: AsyncClient) -> None:
    """DELETE /sources/{id} returns 404 when the source does not exist."""
    response = await api_client.delete(f"/api/v1/sources/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_list_source_chunks(
    api_client: AsyncClient,
    db: AsyncSession,
) -> None:
    """GET /sources/{id}/chunks returns paginated chunks without embeddings."""
    source = Source(
        file_path="/tmp/chunks.txt",
        file_name="chunks.txt",
        file_type="text/plain",
        file_size=10,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(source)
    await db.flush()

    for i in range(3):
        db.add(
            Chunk(
                source_id=source.id,
                chunk_index=i,
                chunk_type="text",
                text_content=f"chunk {i}",
                status=ProcessingStatus.PROCESSED,
            )
        )
    await db.flush()

    response = await api_client.get(f"/api/v1/sources/{source.id}/chunks?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert "embedding" not in body["items"][0]
    assert body["items"][0]["text_content"] == "chunk 0"


async def test_list_chunks_missing_source_returns_404(
    api_client: AsyncClient,
) -> None:
    """GET /sources/{id}/chunks returns 404 when the source does not exist."""
    response = await api_client.get(f"/api/v1/sources/{uuid.uuid4()}/chunks")
    assert response.status_code == 404
