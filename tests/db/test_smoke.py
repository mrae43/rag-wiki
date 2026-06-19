"""Smoke tests for database connectivity and base models."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from rag_wiki.db.base import Base, TimestampMixin, UUIDMixin


def test_base_imports() -> None:
    """Verify Base, TimestampMixin, and UUIDMixin import without error. No params. No return value."""
    assert Base is not None
    assert TimestampMixin is not None
    assert UUIDMixin is not None


def test_base_is_declarative() -> None:
    """Verify Base is a subclass of SQLAlchemy's DeclarativeBase. No params. No return value."""
    assert issubclass(Base, DeclarativeBase)


async def test_db_connection(engine: AsyncEngine) -> None:
    """Verify the database engine can execute a trivial query. engine: AsyncEngine. No return value."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


async def test_pgvector_extension(engine: AsyncEngine) -> None:
    """Verify the pgvector extension is installed on the database. engine: AsyncEngine. No return value."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "vector"
