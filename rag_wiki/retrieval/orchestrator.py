"""
rag_wiki.retrieval.orchestrator
------------------------------
Public orchestrator for the hybrid retrieval pipeline.

Combines the four internal steps — seed finding, graph traversal, and context
assembly — into a single ``retrieve()`` callable used by the API and other
callers. The implementation lives here so ``rag_wiki.retrieval.__init__`` can
expose a clean public namespace without circular imports.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.exceptions import RetrievalError
from rag_wiki.planner.base import QueryPlan, QueryType
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.context import assemble_context, merge_retrieval_results
from rag_wiki.retrieval.schemas import RetrievalResult
from rag_wiki.retrieval.seeds import find_seeds
from rag_wiki.retrieval.traversal import traverse
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)


async def retrieve(
    query: str,
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
    max_context_tokens: int | None = None,
    seed_entity_ids: list[uuid.UUID] | None = None,
    query_plan: QueryPlan | None = None,
) -> RetrievalResult:
    """Retrieve structured context for a user query.

    Pipeline steps:
      1. Embed the query once.
      2. Find seed entities (vector search or direct lookup).
      3. Traverse the knowledge graph from seeds.
      4. Assemble context with token-budget management.

    When *query_plan* indicates a ``COMPARISON`` query, runs per-entity
    independent retrievals and merges the results.

    Args:
        query: Raw user question text. Always required — used for chunk
               scoring even when seed_entity_ids are provided.
        db: AsyncSession for all database queries.
        embed_provider: Used to embed the query; embedding reused for
                        both vector search and chunk scoring.
        max_context_tokens: Remaining token budget after the caller has
                            subtracted conversation history, system prompt,
                            and expected response overhead. Defaults to
                            ``settings.retrieval_total_budget_tokens``.
        seed_entity_ids: If provided, skips vector search and uses these
                         entities as seeds directly. Useful for direct
                         entity navigation and wiki-link traversal.
        query_plan: Optional plan produced by the query planner. When
                    present and ``classified_type == COMPARISON``, the
                    pipeline runs per-entity shallow retrieval and merges
                    the results.

    Returns:
        RetrievalResult with all context slots populated and token accounting.
    """
    settings = get_settings()
    if max_context_tokens is None:
        max_context_tokens = settings.retrieval_total_budget_tokens

    try:
        query_embeddings = await embed_provider.embed(
            [query], model=settings.embedding_model
        )
        query_embedding = query_embeddings[0]

        if query_plan and query_plan.classified_type == QueryType.COMPARISON:
            result = await _retrieve_comparison(
                query=query,
                query_embedding=query_embedding,
                db=db,
                embed_provider=embed_provider,
                max_context_tokens=max_context_tokens,
                seed_entity_ids=seed_entity_ids,
            )
        else:
            result = await _retrieve_single_pass(
                query=query,
                query_embedding=query_embedding,
                db=db,
                embed_provider=embed_provider,
                max_context_tokens=max_context_tokens,
                seed_entity_ids=seed_entity_ids,
            )
    except Exception as exc:
        logger.error(
            "retrieval_failed",
            query=query,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise RetrievalError(f"Retrieval pipeline failed for query {query!r}") from exc

    logger.info(
        "retrieval_complete",
        query=query,
        seed_count=len(result.seeds),
        entity_count=result.entities_after_truncation,
        chunk_count=len(result.seed_chunks) + len(result.hop1_chunks),
        total_tokens=result.total_tokens_used,
    )
    return result


async def _retrieve_single_pass(
    query: str,
    query_embedding: list[float],
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
    max_context_tokens: int,
    seed_entity_ids: list[uuid.UUID] | None = None,
) -> RetrievalResult:
    """Single-pass retrieval pipeline (the existing default behavior)."""
    seeds = await find_seeds(
        query_embedding=query_embedding,
        db=db,
        embed_provider=embed_provider,
        seed_entity_ids=seed_entity_ids,
    )
    seed_ids = [s.entity_id for s in seeds]
    traversal = await traverse(seed_ids, db)
    return await assemble_context(
        query=query,
        query_embedding=query_embedding,
        seeds=seeds,
        traversal=traversal,
        db=db,
        embed_provider=embed_provider,
        max_context_tokens=max_context_tokens,
    )


async def _retrieve_comparison(
    query: str,
    query_embedding: list[float],
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
    max_context_tokens: int,
    seed_entity_ids: list[uuid.UUID] | None = None,
) -> RetrievalResult:
    """Per-entity shallow retrieval for comparison queries.

    For each entity in *seed_entity_ids* (or each entity found by seed
    finding if not provided), runs an independent shallow retrieval and
    merges the results.
    """
    compare_ids = list(seed_entity_ids) if seed_entity_ids else []
    if not compare_ids:
        seeds = await find_seeds(
            query_embedding=query_embedding,
            db=db,
            embed_provider=embed_provider,
        )
        compare_ids = [s.entity_id for s in seeds]

    if not compare_ids:
        return await _retrieve_single_pass(
            query=query,
            query_embedding=query_embedding,
            db=db,
            embed_provider=embed_provider,
            max_context_tokens=max_context_tokens,
        )

    per_entity_budget = max_context_tokens // len(compare_ids)
    partial_results: list[RetrievalResult] = []

    for eid in compare_ids:
        seeds = await find_seeds(
            query_embedding=query_embedding,
            db=db,
            embed_provider=embed_provider,
            seed_entity_ids=[eid],
        )
        if not seeds:
            continue
        traversal = await traverse([eid], db)
        ctx = await assemble_context(
            query=query,
            query_embedding=query_embedding,
            seeds=seeds,
            traversal=traversal,
            db=db,
            embed_provider=embed_provider,
            max_context_tokens=per_entity_budget,
        )
        partial_results.append(ctx)

    if not partial_results:
        return await _retrieve_single_pass(
            query=query,
            query_embedding=query_embedding,
            db=db,
            embed_provider=embed_provider,
            max_context_tokens=max_context_tokens,
            seed_entity_ids=seed_entity_ids,
        )

    merged = merge_retrieval_results(partial_results)
    logger.info(
        "retrieval_comparison_complete",
        query=query,
        entity_count=len(compare_ids),
        merged_chunk_count=len(merged.seed_chunks) + len(merged.hop1_chunks),
    )
    return merged
