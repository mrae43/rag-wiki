"""
rag_wiki.api.schemas
-------------------
Shared Pydantic models for API requests and responses.

Includes the paginated list envelope used by every collection endpoint and
RFC 7807 Problem Detail responses for errors.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PaginatedListEnvelope[T](BaseModel):
    """Consistent wrapper for list responses.

    Attributes:
        items: The page of results.
        total: Total number of items matching the query.
        offset: Number of items skipped.
        limit: Maximum number of items returned.
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[T]
    total: int
    offset: int
    limit: int


class ProblemDetail(BaseModel):
    """RFC 7807 Problem Details response.

    Attributes:
        type: A URI reference identifying the problem type.
        title: A short, human-readable summary.
        status: The HTTP status code.
        detail: A human-readable explanation specific to this occurrence.
        instance: A URI reference identifying the specific occurrence.
    """

    type: str
    title: str
    status: int
    detail: str
    instance: str
