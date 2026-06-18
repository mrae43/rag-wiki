"""
rag_wiki.main
------------
FastAPI application entrypoint.

Creates the ASGI app and mounts all routers. The app can be started via:
    uvicorn rag_wiki.main:app

Does NOT include the worker loop — that lives in rag_wiki.worker.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag_wiki.api.exceptions import (
    catch_all_exception_handler,
    http_exception_handler,
    ragwiki_error_handler,
    validation_error_handler,
)
from rag_wiki.api.middleware import add_cors_middleware, add_request_id_middleware
from rag_wiki.api.router import api_router
from rag_wiki.exceptions import RagWikiError
from rag_wiki.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional settings instance. Defaults to ``get_settings()``.

    Returns:
        The configured FastAPI app.
    """
    if settings is None:
        settings = get_settings()

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Create the upload directory on startup."""
        await asyncio.to_thread(
            lambda: settings.upload_dir.mkdir(parents=True, exist_ok=True)
        )
        yield

    app = FastAPI(
        title="RagWiki",
        description="LLM-maintained knowledge wiki API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.settings = settings

    add_request_id_middleware(app)
    add_cors_middleware(app, settings.cors_origins)

    app.add_exception_handler(RagWikiError, ragwiki_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, catch_all_exception_handler)

    app.include_router(api_router)
    return app


app = create_app()
