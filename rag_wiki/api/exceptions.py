"""
rag_wiki.api.exceptions
----------------------
Exception handlers that map domain errors to RFC 7807 Problem Details.

Handlers are registered in ``rag_wiki.main.create_app`` for:
  - ``RagWikiError`` and subclasses
  - ``RequestValidationError``
  - ``HTTPException``
  - bare ``Exception`` (catch-all 500)
"""

from __future__ import annotations

from http import HTTPStatus

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag_wiki.api.schemas import ProblemDetail
from rag_wiki.exceptions import (
    AdvisoryLockExhausted,
    IngestError,
    ParseError,
    RagWikiError,
    RetrievalError,
)

logger = structlog.get_logger(__name__)


class NotFoundError(RagWikiError):
    """Raised when a requested API resource does not exist."""


class BadRequestError(RagWikiError):
    """Raised when the client sends an invalid request."""


class PayloadTooLargeError(RagWikiError):
    """Raised when a request payload exceeds the configured size limit."""


class ConflictError(RagWikiError):
    """Raised when the request conflicts with the current state."""


def _status_for_ragwiki_error(exc: RagWikiError) -> int:
    """Map a domain error to an HTTP status code."""
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, BadRequestError):
        return 400
    if isinstance(exc, PayloadTooLargeError):
        return 413
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, (IngestError, ParseError)):
        return 400
    if isinstance(exc, AdvisoryLockExhausted):
        return 503
    if isinstance(exc, RetrievalError):
        return 500
    return 500


def _type_slug(title: str) -> str:
    """Derive a problem-type URI slug from a title."""
    return title.lower().replace(" ", "-")


def _build_problem_detail(
    request: Request,
    status: int,
    title: str,
    detail: str,
) -> ProblemDetail:
    """Build a ProblemDetail model for the given error."""
    return ProblemDetail(
        type=f"https://rag-wiki.io/errors/{_type_slug(title)}",
        title=title,
        status=status,
        detail=detail,
        instance=str(request.url.path),
    )


class ProblemDetailResponse(JSONResponse):
    """JSONResponse with the RFC 7807 problem+json media type."""

    media_type = "application/problem+json"


async def ragwiki_error_handler(
    request: Request, exc: RagWikiError
) -> ProblemDetailResponse:
    """Handle ``RagWikiError`` subclasses as Problem Details."""
    status = _status_for_ragwiki_error(exc)
    title = HTTPStatus(status).phrase
    detail = str(exc)
    problem = _build_problem_detail(request, status, title, detail)
    logger.error(
        "api_ragwiki_error",
        request_path=request.url.path,
        status=status,
        detail=detail,
    )
    return ProblemDetailResponse(status_code=status, content=problem.model_dump())


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> ProblemDetailResponse:
    """Normalize FastAPI validation failures into Problem Details."""
    errors = exc.errors()
    detail = errors[0]["msg"] if errors else "Validation error"
    problem = _build_problem_detail(request, 422, "Unprocessable Entity", detail)
    logger.warning(
        "api_validation_error",
        request_path=request.url.path,
        detail=detail,
    )
    return ProblemDetailResponse(status_code=422, content=problem.model_dump())


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> ProblemDetailResponse:
    """Normalize Starlette HTTPExceptions (e.g. 404) into Problem Details."""
    title = HTTPStatus(exc.status_code).phrase
    problem = _build_problem_detail(request, exc.status_code, title, str(exc.detail))
    logger.warning(
        "api_http_exception",
        request_path=request.url.path,
        status=exc.status_code,
        detail=str(exc.detail),
    )
    return ProblemDetailResponse(
        status_code=exc.status_code,
        content=problem.model_dump(),
    )


async def catch_all_exception_handler(
    request: Request, exc: Exception
) -> ProblemDetailResponse:
    """Return a generic 500 Problem Detail and log the traceback."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    logger.exception(
        "api_unhandled_exception",
        request_path=request.url.path,
        request_id=request_id,
        exc_info=exc,
    )
    problem = _build_problem_detail(
        request,
        500,
        "Internal Server Error",
        "An unexpected error occurred.",
    )
    return ProblemDetailResponse(status_code=500, content=problem.model_dump())
