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
from typing import Literal

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
def export(
    output: Path | None = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        help="OKF bundle output dir (overrides EXPORT_OUTPUT_PATH)",
    ),
) -> None:
    """Export wiki pages to an OKF markdown bundle.

    Renders all published wiki_pages as an OKF-compliant directory of
    .md files with YAML front-matter and rewritten [[slug]] markdown
    links.
    """
    asyncio.run(_export_command(output=output))


async def _export_command(output: Path | None = None) -> None:
    """Async helper for the export CLI command.

    Args:
        output: Override output path. Falls back to
            ``settings.export_output_path``.
    """
    settings = get_settings()
    root_dir = output or settings.export_output_path
    storage = get_storage_provider(settings)

    from rag_wiki.wiki.export import export_bundle

    async with AsyncSessionFactory() as db:
        count = await export_bundle(
            db=db,
            storage=storage,
            root_dir=root_dir,
            api_base_url=str(settings.mcp_api_url),
        )

    typer.echo(f"Export complete: {count} page(s) written or updated.")
    logger.info("export finished", root_dir=str(root_dir), changed_count=count)


mcp_app = typer.Typer(help="MCP server commands")
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    transport: Literal["stdio", "http"] = typer.Option(
        "stdio", "--transport", "-t", help="Transport: stdio or http"
    ),
    host: str = typer.Option(
        "127.0.0.1", "--host", "-H", help="Bind host for HTTP transport"
    ),
    port: int | None = typer.Option(
        None, "--port", "-p", help="Port for HTTP transport"
    ),
    api_url: str = typer.Option(
        "http://127.0.0.1:8000", "--api-url", "-u", help="Backend API URL"
    ),
) -> None:
    """Start the MCP server."""
    from rag_wiki.mcp import run

    run(transport=transport, host=host, port=port, api_url=api_url)


if __name__ == "__main__":
    app()
