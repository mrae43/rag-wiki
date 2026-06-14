"""
ragwiki.db
----------
SQLAlchemy models, async session management, and Alembic environment.

All database schema definitions and ORM models live here. Schema changes must
be applied through Alembic migrations, never ad-hoc.
"""

from ragwiki.db.base import Base as Base
from ragwiki.db.base import TimestampMixin as TimestampMixin
from ragwiki.db.base import UUIDMixin as UUIDMixin

# Import all models so they register on Base.metadata for Alembic autogenerate.
from ragwiki.db.models import (
    Chunk,
    ChunkEntity,
    Entity,
    Job,
    Relation,
    Source,
    WikiPage,
    WikiPageEntity,
)

__all__ = [
    "Base",
    "Chunk",
    "ChunkEntity",
    "Entity",
    "Job",
    "Relation",
    "Source",
    "TimestampMixin",
    "UUIDMixin",
    "WikiPage",
    "WikiPageEntity",
]
