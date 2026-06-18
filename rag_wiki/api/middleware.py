"""
rag_wiki.api.middleware
----------------------
Cross-cutting HTTP middleware for the FastAPI app.

Provides request-ID propagation (with structlog context binding) and
conditional CORS setup from settings.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Generate or propagate a request ID and bind it to the log context."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request with a bound request ID."""
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


def add_request_id_middleware(app: FastAPI) -> None:
    """Register the request-ID middleware on *app*."""
    app.add_middleware(RequestIDMiddleware)


def add_cors_middleware(app: FastAPI, origins: str) -> None:
    """Register CORS middleware if *origins* is non-empty.

    Args:
        app: The FastAPI application.
        origins: Comma-separated list of allowed origins. Empty disables CORS.
    """
    origin_list = [origin.strip() for origin in origins.split(",") if origin.strip()]
    if not origin_list:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("cors_enabled", origins=origin_list)
