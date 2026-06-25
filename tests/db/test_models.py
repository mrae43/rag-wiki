"""tests/db/test_models
--------------------
Model-level integration tests for the 9-table domain schema.

Each test verifies one table, its columns, defaults, and relationships using
a real Postgres database (created via ``Base.metadata.create_all`` in the
session-scoped engine fixture).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from rag_wiki.db.models import (
    Chunk,
    ChunkEntity,
    Entity,
    Job,
    JobStatus,
    ProcessingStatus,
    PublishedStatus,
    Relation,
    Source,
    WikiPage,
    WikiPageEntity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _table_exists(conn: AsyncConnection, table_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :name)"
        ),
        {"name": table_name},
    )
    row = result.fetchone()
    return bool(row[0]) if row else False


async def _index_exists(conn: AsyncConnection, index_name: str) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = :name)"
        ),
        {"name": index_name},
    )
    row = result.fetchone()
    return bool(row[0]) if row else False


async def _column_default(conn: AsyncConnection, table: str, column: str) -> str | None:
    result = await conn.execute(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    row = result.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------


async def test_sources_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``sources`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "sources")


async def test_chunks_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``chunks`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "chunks")


async def test_entities_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``entities`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "entities")


async def test_relations_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``relations`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "relations")


async def test_chunk_entities_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``chunk_entities`` join table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "chunk_entities")


async def test_wiki_pages_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``wiki_pages`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "wiki_pages")


async def test_wiki_page_entities_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``wiki_page_entities`` join table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "wiki_page_entities")


async def test_jobs_table_exists(engine: AsyncEngine) -> None:
    """Verify the ``jobs`` table exists in the public schema."""
    async with engine.connect() as conn:
        assert await _table_exists(conn, "jobs")


# ---------------------------------------------------------------------------
# Column defaults
# ---------------------------------------------------------------------------


async def test_sources_status_default_is_pending(engine: AsyncEngine) -> None:
    """Verify ``sources.status`` column defaults to ``'pending'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "sources", "status")
        assert default is not None
        assert "pending" in default


async def test_chunks_status_default_is_pending(engine: AsyncEngine) -> None:
    """Verify ``chunks.status`` column defaults to ``'pending'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "chunks", "status")
        assert default is not None
        assert "pending" in default


async def test_entities_status_default_is_published(engine: AsyncEngine) -> None:
    """Verify ``entities.status`` column defaults to ``'published'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "entities", "status")
        assert default is not None
        assert "published" in default


async def test_relations_status_default_is_published(engine: AsyncEngine) -> None:
    """Verify ``relations.status`` column defaults to ``'published'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "relations", "status")
        assert default is not None
        assert "published" in default


async def test_wiki_pages_status_default_is_published(engine: AsyncEngine) -> None:
    """Verify ``wiki_pages.status`` column defaults to ``'published'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "wiki_pages", "status")
        assert default is not None
        assert "published" in default


async def test_jobs_status_default_is_pending(engine: AsyncEngine) -> None:
    """Verify ``jobs.status`` column defaults to ``'pending'``."""
    async with engine.connect() as conn:
        default = await _column_default(conn, "jobs", "status")
        assert default is not None
        assert "pending" in default


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


async def test_chunks_composite_index_exists(engine: AsyncEngine) -> None:
    """Verify the composite index on ``chunks (source_id, chunk_index)`` exists."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_chunks_source_id_chunk_index")


async def test_relations_composite_index_exists(engine: AsyncEngine) -> None:
    """Verify ``relations`` composite index exists."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_relations_source_target_type")


async def test_jobs_composite_index_exists(engine: AsyncEngine) -> None:
    """Verify the composite index on ``jobs (status, scheduled_at)`` exists."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_jobs_status_scheduled_at")


async def test_chunk_entities_indexes_exist(engine: AsyncEngine) -> None:
    """Verify both FK indexes exist on ``chunk_entities``."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_chunk_entities_chunk_id")
        assert await _index_exists(conn, "idx_chunk_entities_entity_id")


async def test_wiki_page_entities_indexes_exist(engine: AsyncEngine) -> None:
    """Verify both FK indexes exist on ``wiki_page_entities``."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_wiki_page_entities_wiki_page_id")
        assert await _index_exists(conn, "idx_wiki_page_entities_entity_id")


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------


async def test_wiki_pages_slug_unique(engine: AsyncEngine) -> None:
    """Verify a unique index exists on ``wiki_pages.slug``."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'wiki_pages' "
                "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%slug%'"
            )
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] >= 1


async def test_wiki_pages_entity_id_unique(engine: AsyncEngine) -> None:
    """Verify a unique index exists on ``wiki_pages.entity_id``."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'wiki_pages' "
                "AND indexdef LIKE '%UNIQUE%' AND indexdef LIKE '%entity_id%'"
            )
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] >= 1


# ---------------------------------------------------------------------------
# CRUD round-trip via ORM
# ---------------------------------------------------------------------------


async def test_create_source_and_chunk(db: AsyncSession) -> None:
    """Create a Source and Chunk via ORM and verify they persist correctly."""
    source = Source(
        storage_key="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    chunk = Chunk(
        source=source,
        chunk_index=0,
        text_content="hello world",
        status=ProcessingStatus.PENDING,
    )
    db.add(source)
    db.add(chunk)
    await db.commit()

    result = await db.execute(
        text("SELECT file_name, status FROM sources WHERE id = :id"),
        {"id": source.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "test.pdf"
    assert row[1] == "pending"


async def test_create_entity_and_relation(db: AsyncSession) -> None:
    """Create two Entities and a Relation between them via ORM."""
    source = Source(
        storage_key="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    chunk = Chunk(
        source=source,
        chunk_index=0,
        text_content="hello world",
        status=ProcessingStatus.PENDING,
    )
    entity_a = Entity(
        name="Alice",
        entity_type="person",
        description="A person",
        status=PublishedStatus.PUBLISHED,
    )
    entity_b = Entity(
        name="Bob",
        entity_type="person",
        description="Another person",
        status=PublishedStatus.PUBLISHED,
    )
    relation = Relation(
        source_entity=entity_a,
        target_entity=entity_b,
        relation_type="knows",
        chunk=chunk,
        status=PublishedStatus.PUBLISHED,
    )
    db.add(source)
    db.add(chunk)
    db.add(entity_a)
    db.add(entity_b)
    db.add(relation)
    await db.commit()

    result = await db.execute(
        text("SELECT relation_type FROM relations WHERE source_entity_id = :a"),
        {"a": entity_a.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "knows"


async def test_create_wiki_page(db: AsyncSession) -> None:
    """Create a WikiPage linked to an Entity via ORM and verify persistence."""
    entity = Entity(
        name="Alice",
        entity_type="person",
        status=PublishedStatus.PUBLISHED,
    )
    page = WikiPage(
        entity=entity,
        title="Alice",
        slug="alice",
        content="# Alice\n\nA person.",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(entity)
    db.add(page)
    await db.commit()

    result = await db.execute(
        text("SELECT title, slug FROM wiki_pages WHERE id = :id"),
        {"id": page.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "Alice"
    assert row[1] == "alice"


async def test_create_job(db: AsyncSession) -> None:
    """Create a Job via ORM and verify default ``attempts`` and ``max_retries``."""
    job = Job(
        job_type="ingest",
        payload={"source_id": str(uuid.uuid4())},
        status=JobStatus.PENDING,
        max_retries=5,
    )
    db.add(job)
    await db.commit()

    result = await db.execute(
        text("SELECT job_type, status, attempts, max_retries FROM jobs WHERE id = :id"),
        {"id": job.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == "ingest"
    assert row[1] == "pending"
    assert row[2] == 0
    assert row[3] == 5


# ---------------------------------------------------------------------------
# Join tables
# ---------------------------------------------------------------------------


async def test_chunk_entity_link(db: AsyncSession) -> None:
    """Create a ChunkEntity join row and verify it round-trips."""
    source = Source(
        storage_key="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    chunk = Chunk(
        source=source,
        chunk_index=0,
        text_content="hello world",
        status=ProcessingStatus.PENDING,
    )
    entity = Entity(
        name="Alice",
        entity_type="person",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(source)
    db.add(chunk)
    db.add(entity)
    await db.commit()

    link = ChunkEntity(chunk_id=chunk.id, entity_id=entity.id)
    db.add(link)
    await db.commit()

    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM chunk_entities WHERE chunk_id = :c AND entity_id = :e"
        ),
        {"c": chunk.id, "e": entity.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_wiki_page_entity_link(db: AsyncSession) -> None:
    """Create a WikiPageEntity join row and verify it round-trips."""
    entity = Entity(
        name="Alice",
        entity_type="person",
        status=PublishedStatus.PUBLISHED,
    )
    page = WikiPage(
        entity=entity,
        title="Alice",
        slug="alice",
        content="# Alice",
        status=PublishedStatus.PUBLISHED,
    )
    mentioned = Entity(
        name="Bob",
        entity_type="person",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(entity)
    db.add(page)
    db.add(mentioned)
    await db.commit()

    link = WikiPageEntity(wiki_page_id=page.id, entity_id=mentioned.id)
    db.add(link)
    await db.commit()

    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM wiki_page_entities "
            "WHERE wiki_page_id = :w AND entity_id = :e"
        ),
        {"w": page.id, "e": mentioned.id},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == 1


# ---------------------------------------------------------------------------
# New columns (wiki synthesis ADR)
# ---------------------------------------------------------------------------


async def test_wiki_pages_has_synthesized_at_column(engine: AsyncEngine) -> None:
    """wiki_pages.synthesized_at column exists and is nullable."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name, is_nullable, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'wiki_pages' "
                "AND column_name = 'synthesized_at'"
            )
        )
        row = result.fetchone()
        assert row is not None, "synthesized_at column not found"
        assert row[1] == "YES"  # nullable


async def test_wiki_pages_has_synthesized_from_sources_column(
    engine: AsyncEngine,
) -> None:
    """wiki_pages.synthesized_from_sources column exists and is JSONB."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'wiki_pages' "
                "AND column_name = 'synthesized_from_sources'"
            )
        )
        row = result.fetchone()
        assert row is not None, "synthesized_from_sources column not found"
        # PostgreSQL reports JSONB as 'jsonb'
        assert row[1] in ("jsonb",)


async def test_jobs_has_target_entity_id_index(engine: AsyncEngine) -> None:
    """idx_jobs_target_entity_id index exists on jobs.target_entity_id."""
    async with engine.connect() as conn:
        assert await _index_exists(conn, "idx_jobs_target_entity_id")
