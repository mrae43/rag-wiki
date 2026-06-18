"""
rag_wiki.retrieval
-----------------
Public API for the hybrid retrieval pipeline.

Orchestrates the four internal steps — seed finding, graph traversal,
context assembly — into a single callable: ``retrieve()``.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.context import assemble_context
from rag_wiki.retrieval.schemas import RetrievalResult
from rag_wiki.retrieval.seeds import find_seeds
from rag_wiki.retrieval.traversal import traverse
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)


async def retrieve(
    query: str,
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
    max_context_tokens: int,
    seed_entity_ids: list[uuid.UUID] | None = None,
) -> RetrievalResult:
    """Retrieve structured context for a user query.

    Pipeline steps:
      1. Embed the query once.
      2. Find seed entities (vector search or direct lookup).
      3. Traverse the knowledge graph from seeds.
      4. Assemble context with token-budget management.

    Args:
        query: Raw user question text. Always required — used for chunk
               scoring even when seed_entity_ids are provided.
        db: AsyncSession for all database queries.
        embed_provider: Used to embed the query; embedding reused for
                        both vector search and chunk scoring.
        max_context_tokens: Remaining token budget after the caller has
                            subtracted conversation history, system prompt,
                            and expected response overhead.
        seed_entity_ids: If provided, skips vector search and uses these
                         entities as seeds directly. Useful for direct
                         entity navigation and wiki-link traversal.

    Returns:
        RetrievalResult with all context slots populated and token accounting.
    """
    settings = get_settings()

    # 1. Embed query once.
    query_embeddings = await embed_provider.embed(
        [query], model=settings.embedding_model
    )
    query_embedding = query_embeddings[0]

    # 2. Find seeds.
    seeds = await find_seeds(
        query_embedding=query_embedding,
        db=db,
        embed_provider=embed_provider,
        seed_entity_ids=seed_entity_ids,
    )

    # 3. Traverse graph.
    seed_ids = [s.entity_id for s in seeds]
    traversal = await traverse(seed_ids, db)

    # 4. Assemble context.
    result = await assemble_context(
        query=query,
        query_embedding=query_embedding,
        seeds=seeds,
        traversal=traversal,
        db=db,
        embed_provider=embed_provider,
        max_context_tokens=max_context_tokens,
    )

    logger.info(
        "retrieval_complete",
        query=query,
        seed_count=len(seeds),
        entity_count=result.entities_after_truncation,
        chunk_count=len(result.seed_chunks) + len(result.hop1_chunks),
        total_tokens=result.total_tokens_used,
    )
    return result
