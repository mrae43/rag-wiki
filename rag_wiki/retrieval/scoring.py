"""
rag_wiki.retrieval.scoring
-------------------------
Shared scoring and budget utilities used by retrieval and wiki synthesis.

Defines cosine similarity, chunk scoring, deduplication, token estimation,
and budget truncation. Imported by ``rag_wiki.wiki.context`` with no
behavioral change.
"""

from __future__ import annotations

import math

import structlog

from rag_wiki.db.models.source import Chunk
from rag_wiki.providers.base import EmbeddingProvider

logger = structlog.get_logger(__name__)

_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Return a rough token count using ~4 characters per token."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def truncate_to_budget(items: list[str], budget: int) -> list[str]:
    """Greedy-fill *items* into *budget* tokens (estimated).

    Items are kept whole; if the next item would exceed the budget the
    loop stops.  The caller is responsible for pre-sorting items by
    priority.
    """
    result: list[str] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item)
        if used + cost <= budget:
            result.append(item)
            used += cost
        else:
            break
    return result


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def score_chunks(
    query_text: str,
    chunks: list[Chunk],
    embed_provider: EmbeddingProvider,
    model: str,
) -> list[tuple[float, Chunk]]:
    """Score chunks by embedding similarity to *query_text*.

    Chunks that lack an embedding are embedded on-the-fly (mutating the
    input ``Chunk`` objects).  The returned list is sorted descending by
    similarity score.

    Args:
        query_text: Text to compare chunks against.
        chunks: Chunks to score.  Existing embeddings are reused when present.
        embed_provider: Provider used to embed missing chunks.
        model: Embedding model identifier.

    Returns:
        List of ``(similarity_score, chunk)`` tuples, sorted high-to-low.
    """
    if not chunks:
        return []

    desc_embeddings = await embed_provider.embed([query_text], model=model)
    desc_vec = desc_embeddings[0]

    # Embed any chunks that are missing an embedding.
    chunks_missing = [c for c in chunks if c.embedding is None and c.text_content]
    if chunks_missing:
        texts = [c.text_content or "" for c in chunks_missing]
        logger.debug(
            "embedding_chunks_for_scoring",
            count=len(chunks_missing),
            model=model,
        )
        new_embeddings = await embed_provider.embed(texts, model=model)
        for chunk, emb in zip(chunks_missing, new_embeddings, strict=True):
            chunk.embedding = emb

    scored: list[tuple[float, Chunk]] = []
    for chunk in chunks:
        if chunk.embedding is None:
            continue
        if len(chunk.embedding) != len(desc_vec):
            logger.warning(
                "embedding_dimension_mismatch",
                chunk_id=str(chunk.id),
                chunk_dim=len(chunk.embedding),
                desc_dim=len(desc_vec),
            )
            continue
        sim = cosine_similarity(desc_vec, chunk.embedding)
        scored.append((sim, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def deduplicate_chunks(
    scored_chunks: list[tuple[float, Chunk]],
    threshold: float = 0.92,
) -> list[tuple[float, Chunk]]:
    """Remove chunks whose embedding is too similar to an already-kept chunk.

    Iterates in the given order (expected to be descending by score) and
    keeps a chunk only if its cosine similarity to every previously kept
    chunk is below *threshold*.

    Args:
        scored_chunks: Chunks ordered by priority (e.g. similarity score).
        threshold: Cosine similarity above which a chunk is considered a
            duplicate.  Default 0.92.

    Returns:
        Filtered list of chunks with duplicates removed.
    """
    kept: list[tuple[float, Chunk]] = []
    for score, chunk in scored_chunks:
        emb = chunk.embedding
        if emb is None:
            continue

        is_duplicate = False
        for _, kept_chunk in kept:
            kept_emb = kept_chunk.embedding
            if kept_emb is None:
                continue
            if len(emb) != len(kept_emb):
                continue
            sim = cosine_similarity(emb, kept_emb)
            if sim >= threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            kept.append((score, chunk))

    return kept
