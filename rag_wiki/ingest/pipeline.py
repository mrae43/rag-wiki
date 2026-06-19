"""rag_wiki.ingest.pipeline
-----------------------
Ingestion pipeline orchestrator.

Composes parse → source/chunk creation → per-chunk embedding → extraction
→ resolution into a single callable. Does NOT handle job queue claiming
or worker lifecycle — that lives in rag_wiki.worker.
"""

from __future__ import annotations

import os
import uuid
import asyncio
import mimetypes

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import Chunk, ChunkEntity, Job, ProcessingStatus, Source
from rag_wiki.exceptions import IngestError
from rag_wiki.graph import extract_entities, resolve_entities
from rag_wiki.ingest.parser import parse_document
from rag_wiki.ingest.schemas import ImageChunk, ParsedChunk
from rag_wiki.jobs import enqueue
from rag_wiki.providers.base import ChatProvider, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
)

logger = structlog.get_logger(__name__)


async def run_ingest_pipeline(
    job: Job,
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
) -> None:
    """Run the full ingestion pipeline for a single job.

    Args:
        job: The job to process. payload must contain ``file_path``.
        db: Active async SQLAlchemy session. Caller must commit.
        chat_provider: LLM provider for captioning and extraction.
        embed_provider: Embedding provider for chunk and entity embeddings.

    Raises:
        IngestError: If the file cannot be parsed or all chunks fail.
    """
    settings = get_settings()
    payload = job.payload or {}
    file_path = payload.get("file_path")
    if not file_path or not isinstance(file_path, str):
        raise IngestError(f"Job payload missing file_path: job_id={job.id}")

    source_meta = payload.get("source_metadata")
    source_id = payload.get("source_id")

    # Compute required Source fields from filesystem.
    if not os.path.isfile(file_path):
        raise IngestError(f"File not found: {file_path!r}")
    file_type, _ = mimetypes.guess_type(file_path)
    file_type = file_type or "application/octet-stream"
    file_size = os.path.getsize(file_path)

    source: Source | None = None
    if source_id is not None:
        try:
            source_uuid = uuid.UUID(str(source_id))
        except ValueError as exc:
            raise IngestError(f"Invalid source_id in payload: {source_id!r}") from exc
        source_result = await db.execute(select(Source).where(Source.id == source_uuid))
        source = source_result.scalar_one_or_none()
        if source is None:
            raise IngestError(f"Source not found for source_id={source_uuid!r}")

    # 1. Create or reuse Source.
    if source is not None:
        source.status = ProcessingStatus.PROCESSING
        source.file_path = file_path
        source.file_type = file_type
        source.file_size = file_size
        if source_meta is not None:
            source.metadata_ = source_meta
    else:
        source = Source(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            file_type=file_type,
            file_size=file_size,
            status=ProcessingStatus.PROCESSING,
            metadata_=source_meta,
        )
        db.add(source)
    await db.flush()

    logger.info(
        "ingest pipeline started",
        job_id=str(job.id),
        source_id=str(source.id),
        file_path=file_path,
    )

    # 2. Parse (CPU-bound, offload to thread).
    try:
        parsed_chunks: list[ParsedChunk] = await asyncio.to_thread(
            parse_document, file_path, source_meta
        )
    except Exception as exc:
        source.status = ProcessingStatus.FAILED
        raise IngestError(
            f"Failed to parse document: source_id={source.id!r} "
            f"path={file_path!r} parser=lightweight"
        ) from exc

    # 3. Create Chunk rows.
    db_chunks: list[Chunk] = []
    for i, pc in enumerate(parsed_chunks):
        chunk = Chunk(
            source_id=source.id,
            chunk_index=i,
            chunk_type=str(pc.chunk_type),
            text_content=pc.text_content if hasattr(pc, "text_content") else None,
            image_url=None,
            image_mime_type=(
                pc.image_mime_type if isinstance(pc, ImageChunk) else None
            ),
            metadata_=pc.metadata,
        )
        db.add(chunk)
        db_chunks.append(chunk)
    await db.flush()

    # 4. Per-chunk processing.
    succeeded = 0
    for chunk, parsed_chunk in zip(db_chunks, parsed_chunks, strict=True):
        try:
            # ImageChunk → caption.
            if (
                chunk.chunk_type == "image"
                and isinstance(parsed_chunk, ImageChunk)
                and parsed_chunk.image_data
            ):
                chunk.text_content = await chat_provider.caption_image(
                    parsed_chunk.image_data,
                    parsed_chunk.image_mime_type,
                    settings.llm_model_caption,
                )

            if chunk.text_content:
                # Embed.
                embedding = await embed_provider.embed(
                    [chunk.text_content],
                    settings.embedding_model,
                )
                chunk.embedding = embedding[0]

                # Extract.
                result = await extract_entities(
                    chunk, chat_provider, settings.llm_model_extraction
                )
                if not result.entities:
                    logger.debug("chunk %s: no entities extracted", chunk.id)

                # Resolve.
                await resolve_entities(
                    candidates=result.entities,
                    chunk=chunk,
                    db=db,
                    chat_provider=chat_provider,
                    embed_provider=embed_provider,
                    job_id=job.id,
                    relations=result.relations,
                )

            chunk.status = ProcessingStatus.PROCESSED
            succeeded += 1
        except Exception as exc:
            # Chunk-level graceful degradation — a single bad chunk must not
            # block the entire document.
            logger.error(
                "chunk failed",
                chunk_id=str(chunk.id),
                source_id=str(source.id),
                error=str(exc),
                exc_info=True,
            )
            chunk.status = ProcessingStatus.FAILED

    # 5. Source status.
    if succeeded == 0:
        source.status = ProcessingStatus.FAILED
        raise IngestError(f"All chunks failed for source {source.id!r}")
    else:
        source.status = ProcessingStatus.PROCESSED

        # Enqueue synthesis jobs.
        entity_ids_result = await db.execute(
            select(ChunkEntity.entity_id)
            .distinct()
            .where(
                ChunkEntity.chunk_id.in_(
                    select(Chunk.id).where(Chunk.source_id == source.id)
                )
            )
        )
        entity_ids = list(entity_ids_result.scalars().all())

        for entity_id in entity_ids:
            await enqueue(
                db,
                JOB_TYPE_SYNTHESIZE_ENTITY,
                payload={"source_ids": [str(source.id)]},
                target_entity_id=entity_id,
            )

        await enqueue(
            db,
            JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
            payload={"source_id": str(source.id)},
        )

    logger.info(
        "ingest pipeline completed",
        job_id=str(job.id),
        source_id=str(source.id),
        chunk_count=len(db_chunks),
        succeeded=succeeded,
        failed=len(db_chunks) - succeeded,
    )
