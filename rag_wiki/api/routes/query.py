"""
rag_wiki.api.routes.query
------------------------
Question-answering endpoint over the knowledge wiki.

Embeds the user query, runs hybrid retrieval (vector seeds + graph traversal +
context assembly), and optionally asks the chat provider to synthesize a
natural-language answer from the retrieved context.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.api.dependencies import get_chat_provider, get_db, get_embedding_provider
from rag_wiki.db.models import QueryPlanRecord
from rag_wiki.exceptions import RetrievalError
from rag_wiki.planner import QueryPlanner
from rag_wiki.planner.base import QueryType
from rag_wiki.planner.exceptions import PlannerClassificationError
from rag_wiki.prompts.constants import QUERY_SYSTEM_PROMPT
from rag_wiki.providers.base import (
    ChatProvider,
    CompletionRequest,
    EmbeddingProvider,
    Message,
)
from rag_wiki.retrieval import retrieve
from rag_wiki.retrieval.schemas import RetrievalResult
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/queries", tags=["queries"])


class QueryRequest(BaseModel):
    """Request body for ``POST /queries``."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(..., min_length=1, description="User question text.")
    generate_answer: bool = Field(
        True,
        description="If true, ask the LLM to synthesize an answer from context.",
    )
    query_type: QueryType | None = Field(
        None,
        description="Explicit query type override. Skips LLM/keyword classification.",
    )
    seed_entity_ids: list[uuid.UUID] | None = Field(
        None,
        description="Optional entity IDs to use as seeds, bypassing vector search.",
    )
    max_context_tokens: int | None = Field(
        None,
        ge=1,
        description="Token budget for retrieved context.",
    )


class QueryResponse(BaseModel):
    """Response body for ``POST /queries``."""

    query: str
    answer: str | None
    retrieval: RetrievalResult
    plan: dict[str, Any] | None = Field(
        None,
        description="Query plan produced by the query planner.",
    )


def _format_context(retrieval: RetrievalResult) -> str:
    """Render a RetrievalResult as prompt-ready context text."""
    lines: list[str] = []

    if retrieval.seeds:
        lines.append("## Seed entities")
        for seed in retrieval.seeds:
            a = seed.anchor
            lines.append(
                f"- {a.name} ({a.type}): {a.description} "
                f"[degree={a.degree}, centrality={a.relative_centrality}]"
            )

    if retrieval.subgraph:
        lines.append("\n## Related entities and relations")
        for edge in retrieval.subgraph:
            lines.append(
                f"- [{edge.source_name}] --{edge.relation}--> [{edge.target_name}] "
                f"(hop {edge.hop})"
            )

    if retrieval.wiki_page:
        lines.append("\n## Wiki page")
        lines.append(retrieval.wiki_page.content)

    for label, chunks in (
        ("\n## Seed chunks", retrieval.seed_chunks),
        ("\n## Hop chunks", retrieval.hop1_chunks),
    ):
        if chunks:
            lines.append(label)
            for chunk in chunks:
                lines.append(f"- {chunk.text}")

    return "\n".join(lines)


async def _generate_answer(
    query: str,
    retrieval: RetrievalResult,
    chat_provider: ChatProvider,
) -> str:
    """Ask the configured query model to answer from retrieved context."""
    settings = get_settings()
    context = _format_context(retrieval)
    system = QUERY_SYSTEM_PROMPT
    user = f"Question: {query}\n\nContext:\n{context}"

    response = await chat_provider.complete(
        CompletionRequest(
            system=system,
            messages=[Message(role="user", content=user)],
            model=settings.llm_model_query,
        )
    )
    answer = response.content or ""
    logger.info(
        "query_answer_generated",
        query=query,
        model=settings.llm_model_query,
        answer_length=len(answer),
    )
    return answer


@router.post(
    "",
    response_model=QueryResponse,
    operation_id="create_query",
)
async def create_query(
    request: QueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    embed_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    chat_provider: Annotated[ChatProvider, Depends(get_chat_provider)],
) -> QueryResponse:
    """Answer a question using hybrid retrieval over the wiki and graph.

    Args:
        request: Query parameters, including optional seed entity IDs.
        db: Async SQLAlchemy session.
        embed_provider: Provider used to embed the query.
        chat_provider: Provider used to synthesize the answer.

    Returns:
        QueryResponse containing the original query, structured retrieval
        context, and an optional generated answer.
    """
    logger.info("query_received", query=request.query)

    query_id = uuid.uuid4()

    planner = QueryPlanner(
        settings=get_settings(),
        chat_provider=chat_provider,
    )

    try:
        query_plan = await planner.classify_query(
            query=request.query,
            query_id=query_id,
            explicit_type=request.query_type,
        )
    except PlannerClassificationError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    db_plan = QueryPlanRecord(
        id=query_plan.query_id,
        raw_query=query_plan.raw_query,
        classified_type=query_plan.classified_type.value,
        retrieval_depth=query_plan.retrieval_depth,
        seed_count=query_plan.seed_count,
        termination_condition=query_plan.termination_condition,
        confidence=query_plan.confidence,
        classification_source=query_plan.classification_source,
        model_used=query_plan.model_used,
        rationale=query_plan.rationale,
        planner_version=query_plan.planner_version,
    )
    db.add(db_plan)
    await db.flush()

    try:
        retrieval = await retrieve(
            query=request.query,
            db=db,
            embed_provider=embed_provider,
            max_context_tokens=request.max_context_tokens,
            seed_entity_ids=request.seed_entity_ids,
        )
    except RetrievalError:
        raise
    except Exception as exc:
        raise RetrievalError(
            f"Failed to retrieve context for query {request.query!r}"
        ) from exc

    try:
        answer: str | None = None
        if request.generate_answer:
            answer = await _generate_answer(
                query=request.query,
                retrieval=retrieval,
                chat_provider=chat_provider,
            )
    except Exception as exc:
        logger.error(
            "query_answer_failed",
            query=request.query,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise RetrievalError(
            f"Failed to generate answer for query {request.query!r}"
        ) from exc

    try:
        response = QueryResponse(
            query=request.query,
            answer=answer,
            retrieval=retrieval,
            plan=query_plan.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.error(
            "query_response_construction_failed",
            query=request.query,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise RetrievalError(
            f"Failed to build response for query {request.query!r}"
        ) from exc

    return response
