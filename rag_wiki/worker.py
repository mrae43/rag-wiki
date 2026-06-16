"""rag_wiki.worker
--------------
Job worker entrypoint.

Run with:
    python -m rag_wiki.worker

Claims jobs from the Postgres-native queue and executes them. Designed so a
future migration to Celery/RQ is additive, not a rewrite.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket

import structlog

from rag_wiki.db.session import AsyncSessionFactory
from rag_wiki.ingest.pipeline import run_ingest_pipeline
from rag_wiki.jobs import claim_next, complete_job, fail_job
from rag_wiki.providers import get_chat_provider, get_embedding_provider
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)


def _signal_handler(stop_event: asyncio.Event) -> None:
    """Set the stop event on SIGINT/SIGTERM."""
    stop_event.set()


async def worker_loop() -> None:
    """Poll the job queue and execute claimed jobs."""
    settings = get_settings()
    chat_provider = get_chat_provider(settings)
    embed_provider = get_embedding_provider(settings)
    stop_event = asyncio.Event()
    worker_id = f"{socket.gethostname()}-{os.getpid()}"

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, stop_event)

    logger.info(
        "worker started",
        worker_id=worker_id,
        poll_interval=settings.worker_poll_interval_seconds,
    )

    while not stop_event.is_set():
        job = None
        async with AsyncSessionFactory() as db:
            job = await claim_next(db, worker_id=worker_id)
            if job is not None:
                logger.info(
                    "job started",
                    job_id=str(job.id),
                    job_type=job.job_type,
                    worker_id=worker_id,
                )

                try:
                    if job.job_type == "ingest_document":
                        await run_ingest_pipeline(
                            job, db, chat_provider, embed_provider
                        )
                    # elif job.job_type == "...":
                    #     ...
                    else:
                        raise ValueError(f"Unknown job type: {job.job_type}")

                    await complete_job(job, db)
                    await db.commit()
                    logger.info(
                        "job completed",
                        job_id=str(job.id),
                        job_type=job.job_type,
                    )
                except Exception as exc:
                    # Job boundary — catch everything so a single bad job does
                    # not crash the worker loop.
                    logger.error(
                        "job failed",
                        job_id=str(job.id),
                        job_type=job.job_type,
                        worker_id=worker_id,
                        error=str(exc),
                        exc_info=True,
                    )
                    await fail_job(job, db, str(exc))
                    await db.commit()

        if job is None:
            await asyncio.sleep(settings.worker_poll_interval_seconds)

    # Remove signal handlers so they do not leak if the loop is reused.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.remove_signal_handler(sig)

    logger.info("worker stopped", worker_id=worker_id)


def main() -> None:
    """Synchronous entrypoint for the worker."""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
