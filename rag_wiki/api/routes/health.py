"""
rag_wiki.api.routes.health
-------------------------
Liveness/readiness endpoint.

Performs a lightweight database query so that orchestrators can detect
when the API is unable to reach Postgres.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_db

router = APIRouter(tags=["health"])


@router.get("/health", response_model=dict[str, str])
async def health_check(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Return service health after confirming the database is reachable."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}
