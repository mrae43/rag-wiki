"""rag_wiki.cli
-----------
CLI commands for the RagWiki system.

Commands:
    rag-wiki ingest <file_path>
        — Enqueue a document for ingestion.
    rag-wiki export
        — Export wiki pages to local markdown files (e.g., Obsidian).

Usage:
    rag-wiki <command>
    python -m rag_wiki.cli <command>
"""

from __future__ import annotations

import asyncio
import mimetypes
import uuid
from pathlib import Path

import structlog
import typer

from rag_wiki.db.models import ProcessingStatus, Source
from rag_wiki.db.session import AsyncSessionFactory
from rag_wiki.exceptions import IngestError
from rag_wiki.jobs import enqueue
from rag_wiki.settings import get_settings
from rag_wiki.storage import get_storage_provider

logger = structlog.get_logger(__name__)

app = typer.Typer(help="RagWiki CLI")


async def _ingest_command(file_path: str) -> None:
    """Enqueue a single document for ingestion.

    Args:
        file_path: Absolute or relative path to the document to ingest.
    """
    path = Path(file_path)
    if not path.is_file():
        raise IngestError(f"File not found: {file_path!r}")

    settings = get_settings()
    storage = get_storage_provider(settings)
    source_id = uuid.uuid4()

    with open(path, "rb") as f:
        storage_key = await storage.upload(str(source_id), f, path.name)

    file_type, _ = mimetypes.guess_type(str(path))
    file_type = file_type or "application/octet-stream"
    file_size = path.stat().st_size

    async with AsyncSessionFactory() as db:
        source = Source(
            id=source_id,
            storage_key=storage_key,
            file_name=path.name,
            file_type=file_type,
            file_size=file_size,
            status=ProcessingStatus.PENDING,
        )
        db.add(source)
        await db.flush()

        job = await enqueue(
            db,
            "ingest_document",
            payload={"storage_key": storage_key, "source_id": str(source_id)},
        )
        await db.commit()
        logger.info(
            "job enqueued",
            job_id=str(job.id),
            storage_key=storage_key,
        )
        typer.echo(f"Job {job.id} enqueued")


@app.command()
def ingest(
    file_path: str = typer.Argument(..., help="Path to the document to ingest"),
) -> None:
    """Enqueue a document for ingestion."""
    asyncio.run(_ingest_command(file_path))


@app.command()
def export() -> None:
    """Export wiki pages to local markdown files."""
    typer.echo("Export not yet implemented.")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
