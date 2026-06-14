"""tests/db/test_migration
-----------------------
Alembic migration round-trip tests.

Verifies that the generated migration can be applied (upgrade) and reverted
(downgrade) without errors, and that all expected tables are created /
removed accordingly.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from alembic import command
from ragwiki.db import Base
from ragwiki.settings import get_settings

# Tables expected after the migration is applied.
_EXPECTED_TABLES = [
    "sources",
    "chunks",
    "entities",
    "relations",
    "chunk_entities",
    "wiki_pages",
    "wiki_page_entities",
    "jobs",
]

# HNSW indexes expected after the migration is applied.
_EXPECTED_HNSW_INDEXES = [
    "idx_chunks_embedding_hnsw",
    "idx_entities_embedding_hnsw",
]


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


def _alembic_cfg() -> Config:
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


async def _run_upgrade(engine: AsyncEngine) -> None:
    """Run Alembic upgrade inside an async context."""
    # Alembic command is synchronous; run it via run_sync on a connection.
    async with engine.connect() as conn:

        def _upgrade(c: Connection) -> None:
            cfg = _alembic_cfg()
            cfg.attributes["connection"] = c
            command.upgrade(cfg, "head")

        await conn.run_sync(_upgrade)


async def _run_downgrade(engine: AsyncEngine) -> None:
    """Run Alembic downgrade inside an async context."""
    async with engine.connect() as conn:

        def _downgrade(c: Connection) -> None:
            cfg = _alembic_cfg()
            cfg.attributes["connection"] = c
            command.downgrade(cfg, "base")

        await conn.run_sync(_downgrade)


@pytest.fixture
async def migration_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Fresh engine scoped to this module for migration tests.

    We create a separate engine so the session-scoped engine fixture does
    not interfere with our drop/create cycle.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    url = get_settings().database_url
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        await conn.run_sync(Base.metadata.drop_all)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_upgrade_creates_all_tables(migration_engine: AsyncEngine) -> None:
    await _run_upgrade(migration_engine)
    async with migration_engine.connect() as conn:
        for table in _EXPECTED_TABLES:
            msg = f"table {table} missing after upgrade"
            assert await _table_exists(conn, table), msg


async def test_upgrade_creates_hnsw_indexes(
    migration_engine: AsyncEngine,
) -> None:
    await _run_upgrade(migration_engine)
    async with migration_engine.connect() as conn:
        for idx in _EXPECTED_HNSW_INDEXES:
            msg = f"index {idx} missing after upgrade"
            assert await _index_exists(conn, idx), msg


async def test_downgrade_removes_all_tables(
    migration_engine: AsyncEngine,
) -> None:
    await _run_upgrade(migration_engine)
    await _run_downgrade(migration_engine)
    async with migration_engine.connect() as conn:
        for table in _EXPECTED_TABLES:
            msg = f"table {table} still exists after downgrade"
            assert not await _table_exists(conn, table), msg


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


async def test_downgrade_removes_hnsw_indexes(
    migration_engine: AsyncEngine,
) -> None:
    await _run_upgrade(migration_engine)
    await _run_downgrade(migration_engine)
    async with migration_engine.connect() as conn:
        for idx in _EXPECTED_HNSW_INDEXES:
            msg = f"index {idx} still exists after downgrade"
            assert not await _index_exists(conn, idx), msg


async def test_upgrade_sets_status_defaults(migration_engine: AsyncEngine) -> None:
    """After Alembic upgrade, every status column must have a server-side default."""
    await _run_upgrade(migration_engine)
    async with migration_engine.connect() as conn:
        defaults = [
            ("sources", "status", "pending"),
            ("chunks", "status", "pending"),
            ("entities", "status", "published"),
            ("relations", "status", "published"),
            ("wiki_pages", "status", "published"),
            ("jobs", "status", "pending"),
        ]
        for table, column, expected in defaults:
            default = await _column_default(conn, table, column)
            msg = f"{table}.{column} missing default after upgrade"
            assert default is not None, msg
            assert expected in default, (
                f"{table}.{column} default={default!r} missing {expected!r}"
            )
