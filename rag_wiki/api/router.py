"""
rag_wiki.api.router
------------------
Top-level router wiring for the FastAPI app.

Mounts the v1 API under ``/api/v1`` and exposes ``/health`` at the root.
Additional resource routers are included here in later PR stages.
"""

from __future__ import annotations

from fastapi import APIRouter

from rag_wiki.api.routes import health, job, source

api_router = APIRouter()
api_router.include_router(health.router)

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(source.router)
v1_router.include_router(job.router)
api_router.include_router(v1_router)
