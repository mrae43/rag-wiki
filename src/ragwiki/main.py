"""
ragwiki.main
------------
FastAPI application entrypoint.

Creates the ASGI app and mounts all routers. The app can be started via:
    uvicorn ragwiki.main:app

Does NOT include the worker loop — that lives in ragwiki.worker.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="RagWiki")
