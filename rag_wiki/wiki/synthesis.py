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
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import LLMProviderError
from rag_wiki.jobs import complete_job, fail_job
from rag_wiki.providers.base import ChatProvider, CompletionRequest, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.wiki.context import build_entity_context
from rag_wiki.wiki.slug import generate_slug

logger = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

_JOB_TYPE_SYNTHESIZE_ENTITY = "synthesize_entity"

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


async def _release_claim_to_pending(job: Job, db: AsyncSession) -> None:
    job.status = "pending"
    job.claimed_at = None
    job.worker_id = None


async def _merge_duplicate_jobs(
    job: Job,
    db: AsyncSession,
) -> list[str]:
    payload = job.payload or {}
    own_source_ids: list[str] = payload.get("source_ids", [])

    result = await db.execute(
        select(Job).where(
            Job.job_type == _JOB_TYPE_SYNTHESIZE_ENTITY,
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

    Implements the PRD section-11 worker flow:
      1. (Job is already claimed by the worker loop.)
      2. Query for pending duplicates.
      3. Merge their source_ids into claimed payload; mark duplicates completed.
      4. COMMIT (steps 2-3 as one transaction).
      5. Acquire PG advisory lock on entity_id hash.
      6. Retry with exponential backoff.  If exhausted: release claim -> pending.
      7. Re-read current WikiPage inside advisory lock.
      8. Fetch the entity.
      9. Build context via ``context.build_entity_context()``.
     10. Render Jinja2 template with context.
     11. Call ``chat_provider.complete()`` with rendered prompt.
     12. Parse LLM response content.
     13. Write / update WikiPage row.
     14. Populate ``wiki_page_entities`` join table (graph-based).
     15. Release advisory lock.
     16. Mark job completed.

    Args:
        job: The claimed synthesis job.  Must have ``target_entity_id`` set
            and a payload containing ``source_ids``.
        db: Active async SQLAlchemy session.
        chat_provider: LLM provider for generation.
        embed_provider: Embedding provider for chunk scoring.
    """
    entity_id = job.target_entity_id
    if entity_id is None:
        await fail_job(job, db, "target_entity_id is null")
        await db.commit()
        return

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
        await _release_claim_to_pending(job, db)
        await db.commit()
        return

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
            await fail_job(job, db, f"Entity not found: {entity_id}")
            await db.commit()
            return

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
        response = await chat_provider.complete(request)

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

        # Step 16: Mark job completed.
        await complete_job(job, db)
        await db.commit()

        logger.info(
            "entity_page_synthesized",
            entity_id=str(entity_id),
            entity_name=entity.name,
            job_id=str(job.id),
            is_update=existing_content is not None,
        )

    except LLMProviderError:
        logger.error(
            "llm_provider_error",
            entity_id=str(entity_id),
            job_id=str(job.id),
            exc_info=True,
        )
        await fail_job(job, db, "LLM provider error during synthesis")
        await db.commit()
    except Exception:
        logger.error(
            "synthesis_failed",
            entity_id=str(entity_id),
            job_id=str(job.id),
            exc_info=True,
        )
        await fail_job(job, db, "Unexpected error during synthesis")
        await db.commit()
    finally:
        # Step 15: Release advisory lock.
        await _release_advisory_lock(db, lock_key)
