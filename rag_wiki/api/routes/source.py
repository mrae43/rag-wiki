"""rag_wiki.api.routes.source
---------------------------
Document upload and source lifecycle routes.

Provides multipart upload, list/get/delete sources, and a paginated chunk
sub-resource. Uploads are stored flat by UUID filename; the worker pipeline
picks up the job from the Postgres-native queue.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import aiofiles
import structlog
from fastapi import APIRouter, Depends, Form, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_db
from rag_wiki.api.exceptions import BadRequestError, NotFoundError, PayloadTooLargeError
from rag_wiki.api.schemas import PaginatedListEnvelope
from rag_wiki.db.models import Chunk, ProcessingStatus, Source
from rag_wiki.exceptions import DatabaseError
from rag_wiki.jobs import enqueue
from rag_wiki.planner.ingest import IngestPlanner
from rag_wiki.settings import Settings, get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
_CHUNK_SIZE = 8192


class SourceResponse(BaseModel):
    """Public representation of a source document."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None = None
    job_id: uuid.UUID | None = None


class ChunkResponse(BaseModel):
    """Public representation of a chunk (embedding excluded)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    chunk_type: str
    text_content: str | None = None
    image_url: str | None = None
    image_mime_type: str | None = None
    status: str
    metadata: dict[str, Any] | None = None


def _parse_metadata(metadata: str | None) -> dict[str, Any] | None:
    """Parse and validate the optional JSON metadata form field."""
    if metadata is None:
        return None
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise BadRequestError(f"Invalid metadata JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BadRequestError("Metadata must be a JSON object")
    return parsed


async def _get_source_or_404(db: AsyncSession, source_id: uuid.UUID) -> Source:
    """Fetch a source by id, raising a 404 Problem Detail if missing."""
    try:
        result = await db.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch source",
            source_id=str(source_id),
            error=str(exc),
        )
        raise DatabaseError(f"Failed to fetch source {source_id}") from exc
    if source is None:
        raise NotFoundError(f"Source not found: {source_id}")
    return source


@router.post(
    "",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_source",
)
async def create_source(
    request: Request,
    file: UploadFile,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    metadata: Annotated[str | None, Form()] = None,
) -> SourceResponse:
    """Upload a document and enqueue an async ingestion job.

    Args:
        request: The incoming HTTP request, used to inspect ``Content-Length``.
        file: The multipart file upload.
        db: Async SQLAlchemy session (committed by dependency on success).
        settings: Application settings.
        metadata: Optional JSON object supplied as a form field.

    Returns:
        The created source metadata, including the enqueued ``job_id``.

    Raises:
        BadRequestError: If the file is empty or metadata is invalid JSON.
        PayloadTooLargeError: If the file exceeds the configured size limit.
    """
    if not file.filename:
        raise BadRequestError("Upload filename is required")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            content_length_int = int(content_length)
        except ValueError:
            logger.warning(
                "invalid_content_length_header",
                content_length=content_length,
            )
            raise BadRequestError(
                f"Invalid Content-Length header: {content_length}"
            ) from None
        else:
            if content_length_int > settings.upload_max_file_size_bytes:
                raise PayloadTooLargeError(
                    f"File exceeds maximum size of "
                    f"{settings.upload_max_file_size_bytes} bytes"
                )

    source_id = uuid.uuid4()
    file_path = settings.upload_dir / str(source_id)
    metadata_dict = _parse_metadata(metadata)

    total_size = 0
    try:
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > settings.upload_max_file_size_bytes:
                    raise PayloadTooLargeError(
                        f"File exceeds maximum size of "
                        f"{settings.upload_max_file_size_bytes} bytes"
                    )
                await f.write(chunk)
    except PayloadTooLargeError:
        with contextlib.suppress(OSError):
            await _delete_upload(file_path)
        raise

    if total_size == 0:
        with contextlib.suppress(OSError):
            await _delete_upload(file_path)
        raise BadRequestError("Empty files are not allowed")

    file_type = file.content_type or "application/octet-stream"

    planner = IngestPlanner(settings)
    source_plan = planner.create_source_plan(
        source_id=source_id,
        file_path=str(file_path),
        source_metadata=metadata_dict,
        original_filename=file.filename,
    )

    try:
        source = Source(
            id=source_id,
            storage_key=str(file_path),
            file_name=file.filename,
            file_type=file_type,
            file_size=total_size,
            status=ProcessingStatus.PENDING,
            metadata_=metadata_dict,
            source_plan=source_plan.model_dump(mode="json"),
        )
        db.add(source)
        await db.flush()

        job = await enqueue(
            db,
            "ingest_document",
            payload={
                "source_id": str(source_id),
                "file_path": str(file_path),
                "source_metadata": metadata_dict,
            },
        )
    except Exception as exc:
        logger.exception(
            "source_enqueue_failed",
            source_id=str(source_id),
            file_path=str(file_path),
            error=str(exc),
        )
        with contextlib.suppress(OSError):
            await _delete_upload(file_path)
        raise

    logger.info(
        "source_uploaded",
        source_id=str(source_id),
        job_id=str(job.id),
        file_name=file.filename,
        file_size=total_size,
    )

    return SourceResponse(
        id=source.id,
        file_name=source.file_name,
        status=source.status,
        created_at=source.created_at,
        updated_at=source.updated_at,
        metadata=source.metadata_,
        job_id=job.id,
    )


async def _delete_upload(file_path: Path) -> None:
    """Best-effort deletion of a partially or fully written upload file."""
    try:
        file_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "failed_to_delete_upload_file",
            file_path=str(file_path),
            error=str(exc),
        )
        raise


@router.get(
    "",
    response_model=PaginatedListEnvelope[SourceResponse],
    operation_id="list_sources",
)
async def list_sources(
    db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
    status: str | None = None,
    filename: str | None = None,
) -> PaginatedListEnvelope[SourceResponse]:
    """List sources with offset/limit pagination and optional filters."""
    limit = min(limit, MAX_LIMIT)

    stmt = select(Source)
    count_stmt = select(func.count(Source.id))

    if status is not None:
        stmt = stmt.where(Source.status == status)
        count_stmt = count_stmt.where(Source.status == status)
    if filename is not None:
        like = f"%{filename}%"
        stmt = stmt.where(Source.file_name.ilike(like))
        count_stmt = count_stmt.where(Source.file_name.ilike(like))

    stmt = stmt.order_by(Source.created_at.desc()).offset(offset).limit(limit)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        sources = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list sources",
            offset=offset,
            limit=limit,
            status=status,
            error=str(exc),
        )
        raise DatabaseError("Failed to list sources") from exc
    items = [
        SourceResponse(
            id=s.id,
            file_name=s.file_name,
            status=s.status,
            created_at=s.created_at,
            updated_at=s.updated_at,
            metadata=s.metadata_,
            job_id=None,
        )
        for s in sources
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)


@router.get(
    "/{source_id}",
    response_model=SourceResponse,
    operation_id="get_source",
)
async def get_source(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> SourceResponse:
    """Return a single source by id."""
    try:
        source = await _get_source_or_404(db, source_id)
        return SourceResponse(
            id=source.id,
            file_name=source.file_name,
            status=source.status,
            created_at=source.created_at,
            updated_at=source.updated_at,
            metadata=source.metadata_,
            job_id=None,
        )
    except (NotFoundError, DatabaseError):
        raise
    except Exception as exc:
        logger.error(
            "failed_to_get_source",
            source_id=str(source_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise DatabaseError(f"Failed to fetch source {source_id}") from exc


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_source",
)
async def delete_source(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> None:
    """Delete a source, its chunks (cascade), and the uploaded file."""
    source = await _get_source_or_404(db, source_id)
    await _delete_upload(Path(source.storage_key))
    try:
        await db.delete(source)
    except Exception as exc:
        logger.error(
            "Failed to delete source",
            source_id=str(source_id),
            error=str(exc),
        )
        raise DatabaseError(f"Failed to delete source {source_id}") from exc


@router.get(
    "/{source_id}/chunks",
    response_model=PaginatedListEnvelope[ChunkResponse],
    operation_id="list_source_chunks",
)
async def list_source_chunks(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)] = ...,  # type: ignore[assignment]
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> PaginatedListEnvelope[ChunkResponse]:
    """Return paginated chunks for a source (embedding excluded)."""
    limit = min(limit, MAX_LIMIT)
    await _get_source_or_404(db, source_id)

    stmt = (
        select(Chunk)
        .where(Chunk.source_id == source_id)
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    count_stmt = select(func.count(Chunk.id)).where(Chunk.source_id == source_id)

    try:
        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        result = await db.execute(stmt)
        chunks = result.scalars().all()
    except Exception as exc:
        logger.error(
            "Failed to list source chunks",
            source_id=str(source_id),
            offset=offset,
            limit=limit,
            error=str(exc),
        )
        raise DatabaseError(f"Failed to list chunks for source {source_id}") from exc
    items = [
        ChunkResponse(
            id=c.id,
            chunk_index=c.chunk_index,
            chunk_type=c.chunk_type,
            text_content=c.text_content,
            image_url=c.image_url,
            image_mime_type=c.image_mime_type,
            status=c.status,
            metadata=c.metadata_,
        )
        for c in chunks
    ]

    return PaginatedListEnvelope(items=items, total=total, offset=offset, limit=limit)
