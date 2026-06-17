"""
rag_wiki.wiki.synthesis
----------------------
Synthesis orchestrator -- claims jobs, deduplicates, acquires advisory locks,
builds context, calls LLM, writes wiki pages.

Implements the PRD section-11 worker flow for entity wiki page synthesis.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import uuid
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.db.models.jobs import Job
from rag_wiki.db.models.source import Source
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import AdvisoryLockExhausted, LLMProviderError
from rag_wiki.providers.base import ChatProvider, CompletionRequest, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.wiki.context import build_entity_context, build_source_summary_context
from rag_wiki.wiki.slug import generate_slug

logger = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

JOB_TYPE_SYNTHESIZE_ENTITY = "synthesize_entity"
JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY = "synthesize_source_summary"

_ADVISORY_LOCK_DELAYS = [0.1, 0.3, 0.9]


def _advisory_lock_key(entity_id_str: str) -> int:
    return (
        int.from_bytes(hashlib.md5(entity_id_str.encode()).digest()[:8], "big")
        & 0x7FFFFFFFFFFFFFFF
    )


async def _acquire_advisory_lock_with_retry(db: AsyncSession, lock_key: int) -> bool:
    for delay in _ADVISORY_LOCK_DELAYS:
        result = await db.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
        if result.scalar():
            return True
        await asyncio.sleep(delay)
    return False


async def _release_advisory_lock(db: AsyncSession, lock_key: int) -> None:
    await db.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": lock_key},
    )


async def _cancel_duplicate_jobs(
    db: AsyncSession,
    job: Job,
) -> list[uuid.UUID]:
    """Cancel pending duplicate synthesis jobs for the same source.

    Marks them as completed so the worker skips them. Unlike entity
    synthesis coalescing, source summaries are independent per source,
    so duplicates are simply cancelled rather than merged.

    Args:
        db: Active async SQLAlchemy session.
        job: The current job. Other pending jobs with the same job_type
            and source_id are cancelled.

    Returns:
        List of UUIDs of cancelled jobs.
    """
    payload = job.payload or {}
    source_id_str = payload.get("source_id")
    if not source_id_str or not isinstance(source_id_str, str):
        return []

    result = await db.execute(
        select(Job).where(
            Job.job_type == job.job_type,
            Job.status == "pending",
            Job.id != job.id,
            Job.payload["source_id"].as_string() == source_id_str,
        )
    )
    duplicates = list(result.scalars().all())
    cancelled_ids: list[uuid.UUID] = []
    for dup in duplicates:
        cancelled_ids.append(dup.id)
        dup.status = "completed"
        dup.completed_at = datetime.datetime.now(datetime.UTC)

    if duplicates:
        logger.info(
            "cancelled_duplicate_synthesis_jobs",
            job_type=job.job_type,
            source_id=source_id_str,
            count=len(duplicates),
        )

    return cancelled_ids


def _source_slug(source: Source) -> str:
    """Generate a deterministic slug for a source summary wiki page.

    Uses the source file name and source UUID, following the same pattern
    as entity slug generation but without requiring an entity_id.
    """
    return generate_slug(source.file_name, source.id)


async def _merge_duplicate_jobs(
    job: Job,
    db: AsyncSession,
) -> list[str]:
    payload = job.payload or {}
    own_source_ids: list[str] = payload.get("source_ids", [])

    result = await db.execute(
        select(Job).where(
            Job.job_type == JOB_TYPE_SYNTHESIZE_ENTITY,
            Job.target_entity_id == job.target_entity_id,
            Job.status == "pending",
            Job.id != job.id,
        )
    )
    duplicates = list(result.scalars().all())

    all_source_ids = set(own_source_ids)
    for dup in duplicates:
        dup_payload = dup.payload or {}
        dup_sources = dup_payload.get("source_ids", [])
        all_source_ids.update(dup_sources)
        dup.status = "completed"
        dup.completed_at = datetime.datetime.now(datetime.UTC)

    if duplicates:
        logger.info(
            "merged_duplicate_synthesis_jobs",
            entity_id=str(job.target_entity_id),
            duplicate_count=len(duplicates),
            merged_source_count=len(all_source_ids),
        )

    return list(all_source_ids)


async def synthesize_entity_page(
    job: Job,
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
) -> None:
    """Orchestrate entity wiki page synthesis.

    Does NOT manage job lifecycle — the caller (worker) handles
    complete_job / fail_job.  Exceptions signal outcomes to the caller.

    Implements the PRD section-11 worker flow:
      1. (Job is already claimed by the worker loop.)
      2. Query for pending duplicates.
      3. Merge their source_ids into claimed payload; mark duplicates completed.
      4. COMMIT (steps 2-3 as one transaction).
      5. Acquire PG advisory lock on entity_id hash.
      6. Retry with exponential backoff.  If exhausted: raise AdvisoryLockExhausted.
      7. Re-read current WikiPage inside advisory lock.
      8. Fetch the entity.
      9. Build context via ``context.build_entity_context()``.
     10. Render Jinja2 template with context.
     11. Call ``chat_provider.complete()`` with rendered prompt.
     12. Parse LLM response content.
     13. Write / update WikiPage row.
     14. Populate ``wiki_page_entities`` join table (graph-based).
     15. Release advisory lock.

    Args:
        job: The claimed synthesis job.  Must have ``target_entity_id`` set
            and a payload containing ``source_ids``.
        db: Active async SQLAlchemy session.
        chat_provider: LLM provider for generation.
        embed_provider: Embedding provider for chunk scoring.

    Raises:
        AdvisoryLockExhausted: When advisory lock retries are exhausted.
        ValueError: When target_entity_id is null or entity is not found.
    """
    entity_id = job.target_entity_id
    if entity_id is None:
        raise ValueError("target_entity_id is null")

    # Steps 2-4: Merge duplicate jobs and commit as one transaction.
    merged_source_ids = await _merge_duplicate_jobs(job, db)
    await db.commit()

    # Steps 5-6: Acquire advisory lock with exponential backoff.
    lock_key = _advisory_lock_key(str(entity_id))
    acquired = await _acquire_advisory_lock_with_retry(db, lock_key)
    if not acquired:
        logger.warning(
            "advisory_lock_exhausted",
            entity_id=str(entity_id),
            job_id=str(job.id),
        )
        raise AdvisoryLockExhausted(f"Advisory lock exhausted for entity {entity_id}")

    try:
        # Step 7: Re-read current WikiPage inside advisory lock.
        page_result = await db.execute(
            select(WikiPage).where(WikiPage.entity_id == entity_id)
        )
        existing_row = page_result.scalar_one_or_none()
        existing_content = existing_row.content if existing_row else None

        # Step 8: Fetch the entity.
        entity_result = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = entity_result.scalar_one_or_none()
        if entity is None:
            logger.error(
                "entity_not_found",
                entity_id=str(entity_id),
                job_id=str(job.id),
            )
            raise ValueError(f"Entity not found: {entity_id}")

        source_uuids = [uuid.UUID(sid) for sid in merged_source_ids]

        # Step 9: Build context.
        context = await build_entity_context(
            entity=entity,
            db=db,
            chat_provider=chat_provider,
            embed_provider=embed_provider,
            source_ids=source_uuids,
            existing_page=existing_content,
        )

        # Step 10: Render Jinja2 template.
        template = _jinja_env.get_template("synthesize_entity.j2")
        prompt = template.render(**context)

        # Step 11: Call LLM.
        settings = get_settings()
        request = CompletionRequest(
            system=prompt,
            messages=[],
            model=settings.llm_model_wiki_synthesis,
            temperature=0.3,
        )

        # Inner try/except for LLM skip behavior per PRD §9.
        try:
            response = await chat_provider.complete(request)
        except LLMProviderError:
            logger.error(
                "llm_error_skipping_synthesis",
                entity_id=str(entity_id),
                job_id=str(job.id),
                exc_info=True,
            )
            # Skip — return normally so the worker completes the job
            # without writing a page. The entity's data is already in
            # the graph; the page can be regenerated later.
            return

        # Step 12: Extract content from LLM response.
        generated_content = response.content or ""
        if not generated_content:
            logger.warning(
                "empty_synthesis_response",
                entity_id=str(entity_id),
                job_id=str(job.id),
            )
            generated_content = f"# {entity.name}\n\n*Synthesis produced no content.*"

        # Step 13: Write / update WikiPage row.
        slug = generate_slug(entity.name, entity.id)
        now = datetime.datetime.now(datetime.UTC)

        if existing_row:
            existing_row.content = generated_content
            existing_row.synthesized_from_sources = merged_source_ids
            existing_row.synthesized_at = now
        else:
            page = WikiPage(
                entity_id=entity.id,
                title=entity.name,
                slug=slug,
                content=generated_content,
                synthesized_from_sources=merged_source_ids,
                synthesized_at=now,
            )
            db.add(page)
            await db.flush()
            existing_row = page

        # Step 14: Populate wiki_page_entities join table.
        connected_entity_ids: set[uuid.UUID] = set()
        connected_entity_ids.add(entity.id)

        outgoing = await db.execute(
            select(Relation.target_entity_id).where(
                Relation.source_entity_id == entity.id
            )
        )
        for row_obj in outgoing.all():
            connected_entity_ids.add(row_obj[0])

        incoming = await db.execute(
            select(Relation.source_entity_id).where(
                Relation.target_entity_id == entity.id
            )
        )
        for row_obj in incoming.all():
            connected_entity_ids.add(row_obj[0])

        for eid in connected_entity_ids:
            await db.execute(
                text(
                    "INSERT INTO wiki_page_entities (wiki_page_id, entity_id) "
                    "VALUES (:page_id, :entity_id) ON CONFLICT DO NOTHING"
                ),
                {"page_id": existing_row.id, "entity_id": eid},
            )

        logger.info(
            "entity_page_synthesized",
            entity_id=str(entity_id),
            entity_name=entity.name,
            job_id=str(job.id),
            is_update=existing_content is not None,
        )

    except Exception:
        logger.error(
            "synthesis_failed",
            entity_id=str(entity_id),
            job_id=str(job.id),
            exc_info=True,
        )
        raise
    finally:
        # Step 15: Release advisory lock.
        await _release_advisory_lock(db, lock_key)


async def synthesize_source_summary(
    job: Job,
    db: AsyncSession,
    chat_provider: ChatProvider,
) -> None:
    """Orchestrate source summary wiki page synthesis.

    Does NOT manage job lifecycle — the caller (worker) handles
    complete_job / fail_job.  Exceptions signal outcomes to the caller.

    Simpler than entity synthesis — no advisory locks, no coalescing,
    no chunk scoring.

    Flow:
      1. Cancel any duplicate pending jobs for the same source.
      2. Fetch source + chunks + entities + relations.
      3. Build context via ``build_source_summary_context()``.
      4. Render Jinja2 template.
      5. Call LLM (retried once by RetryingProvider).
      6. Write/update WikiPage (entity_id=NULL, title=source file_name).

    On transient LLM error after RetryingProvider retries: skip (log and
    return normally) rather than re-queueing (PRD §9).

    Args:
        job: The claimed synthesis job. Payload must contain ``source_id``.
        db: Active async SQLAlchemy session.
        chat_provider: LLM provider for generation.

    Raises:
        ValueError: When payload has no source_id or source is not found.
    """
    payload = job.payload or {}
    source_id_str = payload.get("source_id")
    if not source_id_str or not isinstance(source_id_str, str):
        raise ValueError("payload missing or invalid source_id")

    # Step 1: Cancel duplicate pending jobs for the same source.
    await _cancel_duplicate_jobs(db, job)
    await db.commit()

    try:
        # Step 2: Fetch source.
        source_result = await db.execute(
            select(Source).where(Source.id == uuid.UUID(source_id_str))
        )
        source = source_result.scalar_one_or_none()
        if source is None:
            logger.error(
                "source_not_found",
                source_id=source_id_str,
                job_id=str(job.id),
            )
            raise ValueError(f"Source not found: {source_id_str}")

        # Step 3: Build context.
        context = await build_source_summary_context(
            source=source,
            db=db,
            chat_provider=chat_provider,
        )

        # Step 4: Render Jinja2 template.
        template = _jinja_env.get_template("synthesize_source_summary.j2")
        prompt = template.render(**context)

        # Step 5: Call LLM (RetryingProvider handles transient retries).
        settings = get_settings()
        request = CompletionRequest(
            system=prompt,
            messages=[],
            model=settings.llm_model_wiki_synthesis,
            temperature=0.3,
        )

        try:
            response = await chat_provider.complete(request)
        except LLMProviderError:
            logger.error(
                "source_summary_llm_error_skipping",
                source_id=source_id_str,
                job_id=str(job.id),
            )
            # Skip per PRD §9 — return normally so the worker completes
            # the job without writing a page.
            return

        generated_content = response.content or ""
        if not generated_content:
            logger.warning(
                "source_summary_empty_response",
                source_id=source_id_str,
                job_id=str(job.id),
            )
            generated_content = (
                f"# {source.file_name}\n\n*Synthesis produced no content.*"
            )

        # Step 6: Write/update WikiPage (entity_id=NULL for source pages).
        slug = _source_slug(source)
        now = datetime.datetime.now(datetime.UTC)

        page_result = await db.execute(select(WikiPage).where(WikiPage.slug == slug))
        existing_page = page_result.scalar_one_or_none()

        if existing_page:
            existing_page.content = generated_content
            existing_page.synthesized_from_sources = [source_id_str]
            existing_page.synthesized_at = now
        else:
            page = WikiPage(
                entity_id=None,
                title=source.file_name,
                slug=slug,
                content=generated_content,
                synthesized_from_sources=[source_id_str],
                synthesized_at=now,
            )
            db.add(page)

        logger.info(
            "source_summary_synthesized",
            source_id=source_id_str,
            file_name=source.file_name,
            job_id=str(job.id),
        )

    except Exception:
        logger.error(
            "source_summary_synthesis_failed",
            source_id=source_id_str,
            job_id=str(job.id),
            exc_info=True,
        )
        raise
