"""
rag_wiki.db.session
------------------
Async SQLAlchemy session management.

Provides the FastAPI ``get_db`` dependency and the ``AsyncSessionFactory``
used by the worker and CLI. Factory creation is deferred until first use so
that importing this module does not require a valid ``DATABASE_URL``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rag_wiki.settings import get_settings


class _LazySessionFactory:
    """Lazy async session factory to avoid requiring settings at import time."""

    _factory: async_sessionmaker[AsyncSession] | None = None

    def _ensure(self) -> async_sessionmaker[AsyncSession]:
        if self._factory is None:
            settings = get_settings()
            engine = create_async_engine(
                settings.database_url,
                pool_size=10,
                max_overflow=20,
                echo=False,  # set True temporarily for SQL debugging
            )
            self._factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,  # important for async — avoids lazy-load errors
            )
        return self._factory

    def __call__(self) -> AsyncSession:
        return self._ensure()()


AsyncSessionFactory: async_sessionmaker[AsyncSession] = _LazySessionFactory()  # type: ignore[assignment]


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, rolls back on error."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
