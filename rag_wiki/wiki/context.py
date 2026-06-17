"""
rag_wiki.wiki.context
--------------------
Assembles structured context dictionaries for wiki page synthesis prompts.

Implements the 5-tier token budget from the PRD (§5) and context-assembly.md.
Does NOT call the LLM — only gathers and formats data from the database.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from rag_wiki.db.models.graph import Entity, Relation
from rag_wiki.db.models.source import Chunk, ChunkEntity, Source
from rag_wiki.providers.base import ChatProvider, EmbeddingProvider
from rag_wiki.settings import get_settings
from rag_wiki.wiki.slug import generate_slug

logger = structlog.get_logger(__name__)

_TOTAL_BUDGET = 6000
_TIER_1_BUDGET = 500  # instructions + entity metadata
_TIER_2_BUDGET = 800  # existing page (conditional)
_TIER_3_BUDGET = 200  # relations
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
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
        cost = _estimate_tokens(item)
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
    entity_description: str,
    chunks: list[Chunk],
    embed_provider: EmbeddingProvider,
    model: str,
) -> list[tuple[float, Chunk]]:
    """Score chunks by embedding similarity to *entity_description*.

    Chunks that lack an embedding are embedded on-the-fly (mutating the
    input ``Chunk`` objects).  The returned list is sorted descending by
    similarity score.

    Args:
        entity_description: Text to compare chunks against.
        chunks: Chunks to score.  Existing embeddings are reused when present.
        embed_provider: Provider used to embed missing chunks.
        model: Embedding model identifier.

    Returns:
        List of ``(similarity_score, chunk)`` tuples, sorted high-to-low.
    """
    if not chunks:
        return []

    desc_embeddings = await embed_provider.embed([entity_description], model=model)
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


async def build_entity_context(
    entity: Entity,
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
    source_ids: list[uuid.UUID],
    existing_page: str | None = None,
) -> dict[str, Any]:
    """Assemble the tiered context dict for entity wiki synthesis.

    Implements the 5-tier budget from the PRD:
      1. Entity metadata (~500 tok)
      2. Existing page head-truncated to ≤800 tok (conditional)
      3. All relations from DB, flat list (~200 tok)
      4. Source chunks scored by embedding similarity, deduplicated,
         greedy-filled to the remaining budget.
      5. 1-hop neighbor slugs/names for wiki link binding (if room).

    Args:
        entity: The entity to synthesize a page for.
        db: Async SQLAlchemy session.
        chat_provider: Unused in V1; reserved for future expansion.
        embed_provider: Provider used to embed the entity description
            and any chunks lacking embeddings.
        source_ids: Sources that triggered this synthesis; only chunks
            from these sources are considered.
        existing_page: Current wiki page content when updating, or
            ``None`` when creating a new page.

    Returns:
        A dictionary ready to be passed to the entity Jinja2 template.
    """
    # Tier 1 & 2: compute remaining budget after fixed slots.
    remaining_budget = _TOTAL_BUDGET - _TIER_1_BUDGET - _TIER_3_BUDGET
    existing_page_truncated = None
    if existing_page:
        max_chars = _TIER_2_BUDGET * _CHARS_PER_TOKEN
        existing_page_truncated = existing_page[:max_chars]
        remaining_budget -= _TIER_2_BUDGET

    # Tier 3: relations (outgoing + incoming).
    outgoing = await db.execute(
        sa.select(Relation)
        .options(joinedload(Relation.target_entity))
        .where(Relation.source_entity_id == entity.id)
    )
    incoming = await db.execute(
        sa.select(Relation)
        .options(joinedload(Relation.source_entity))
        .where(Relation.target_entity_id == entity.id)
    )
    outgoing_rels = outgoing.scalars().all()
    incoming_rels = incoming.scalars().all()

    edges: list[dict[str, str]] = []
    neighbor_entities: dict[uuid.UUID, Entity] = {}
    for rel in outgoing_rels:
        if rel.target_entity is None:
            continue
        edges.append(
            {
                "label": rel.relation_type,
                "target_slug": generate_slug(
                    rel.target_entity.name, rel.target_entity.id
                ),
            }
        )
        neighbor_entities[rel.target_entity.id] = rel.target_entity
    for rel in incoming_rels:
        if rel.source_entity is None:
            continue
        edges.append(
            {
                "label": f"← {rel.relation_type}",
                "target_slug": generate_slug(
                    rel.source_entity.name, rel.source_entity.id
                ),
            }
        )
        neighbor_entities[rel.source_entity.id] = rel.source_entity

    # Tier 4: source chunks scoped to the given sources and this entity.
    chunks: list[Chunk] = []
    if source_ids:
        chunk_result = await db.execute(
            sa.select(Chunk)
            .join(ChunkEntity, Chunk.id == ChunkEntity.chunk_id)
            .options(selectinload(Chunk.source))
            .where(
                Chunk.source_id.in_(source_ids),
                ChunkEntity.entity_id == entity.id,
            )
        )
        chunks = list(chunk_result.scalars().all())

    scored_chunks: list[tuple[float, Chunk]] = []
    description_text = entity.description or entity.name
    if chunks and description_text:
        settings = get_settings()
        scored_chunks = await score_chunks(
            description_text,
            chunks,
            embed_provider,
            model=settings.embedding_model,
        )
    elif chunks:
        # No description — fall back to chronological order.
        scored_chunks = [(0.0, c) for c in chunks]

    deduped = deduplicate_chunks(scored_chunks)

    # Build chunk strings for budget estimation and template data.
    chunk_entries: list[tuple[str, dict[str, Any]]] = []
    for _score, chunk in deduped:
        source_name = chunk.source.file_name if chunk.source else "unknown"
        ingested_at = chunk.created_at.isoformat() if chunk.created_at else ""
        text = (
            f"---\n[{source_name}:{chunk.chunk_index}] "
            f"(source: {source_name}, ingested: {ingested_at})\n"
            f"{chunk.text_content or ''}"
        )
        chunk_entries.append(
            (
                text,
                {
                    "source_file": source_name,
                    "index": chunk.chunk_index,
                    "source_name": source_name,
                    "ingested_at": ingested_at,
                    "text": chunk.text_content or "",
                },
            )
        )

    chunk_texts = [entry[0] for entry in chunk_entries]
    selected_texts = truncate_to_budget(chunk_texts, remaining_budget)
    selected_text_set = set(selected_texts)
    source_chunks = [
        entry[1] for entry in chunk_entries if entry[0] in selected_text_set
    ]

    # Tier 5: 1-hop neighbors (wiki link binding) — only if room.
    used_by_chunks = sum(_estimate_tokens(t) for t in selected_texts)
    neighbor_budget = remaining_budget - used_by_chunks

    neighbor_entries: list[tuple[str, dict[str, str]]] = []
    for ne in neighbor_entities.values():
        slug = generate_slug(ne.name, ne.id)
        text = f"- [[{slug}]] — {ne.name}"
        neighbor_entries.append((text, {"slug": slug, "name": ne.name}))

    neighbor_texts = [entry[0] for entry in neighbor_entries]
    selected_neighbor_texts = truncate_to_budget(neighbor_texts, neighbor_budget)
    selected_neighbor_set = set(selected_neighbor_texts)
    known_entities = [
        entry[1] for entry in neighbor_entries if entry[0] in selected_neighbor_set
    ]

    logger.info(
        "entity_context_built",
        entity_id=str(entity.id),
        edge_count=len(edges),
        chunk_count=len(source_chunks),
        neighbor_count=len(known_entities),
        budget_remaining=neighbor_budget
        - sum(_estimate_tokens(t) for t in selected_neighbor_texts),
    )

    return {
        "entity_name": entity.name,
        "entity_type": entity.entity_type,
        "entity_description": entity.description or "",
        "existing_page": existing_page_truncated,
        "edges": edges,
        "source_chunks": source_chunks,
        "known_entities": known_entities,
    }


async def build_source_summary_context(
    source: Source,
    db: AsyncSession,
    chat_provider: ChatProvider,
) -> dict[str, Any]:
    """Assemble the context dict for a source-summary wiki page.

    Args:
        source: The source document to summarize.
        db: Async SQLAlchemy session.
        chat_provider: Unused in V1; reserved for future expansion.

    Returns:
        A dictionary ready to be passed to the source-summary Jinja2
        template.
    """
    # All chunks for this source.
    chunk_result = await db.execute(
        sa.select(Chunk).where(Chunk.source_id == source.id)
    )
    chunks = list(chunk_result.scalars().all())

    chunk_contexts: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk.text_content or ""
        first_line = text.splitlines()[0] if text else ""
        summary = f"{first_line[:77]}..." if len(first_line) > 80 else first_line
        chunk_contexts.append(
            {
                "source_file": source.file_name,
                "index": chunk.chunk_index,
                "text": text,
                "summary_or_first_line": summary,
            }
        )

    # Entities touched by this source.
    entity_result = await db.execute(
        sa.select(Entity)
        .join(ChunkEntity, Entity.id == ChunkEntity.entity_id)
        .join(Chunk, Chunk.id == ChunkEntity.chunk_id)
        .where(Chunk.source_id == source.id)
        .distinct()
    )
    touched_entities = [
        {
            "slug": generate_slug(ent.name, ent.id),
            "name": ent.name,
        }
        for ent in entity_result.scalars().all()
    ]

    # Relations introduced by this source.
    relation_result = await db.execute(
        sa.select(Relation)
        .join(Chunk, Relation.chunk_id == Chunk.id)
        .options(
            joinedload(Relation.source_entity),
            joinedload(Relation.target_entity),
        )
        .where(Chunk.source_id == source.id)
    )
    source_relations: list[dict[str, str]] = []
    for rel in relation_result.scalars().all():
        if rel.source_entity is None or rel.target_entity is None:
            continue
        source_relations.append(
            {
                "source_slug": generate_slug(
                    rel.source_entity.name, rel.source_entity.id
                ),
                "label": rel.relation_type,
                "target_slug": generate_slug(
                    rel.target_entity.name, rel.target_entity.id
                ),
            }
        )

    # Re-ingest metadata (V1 default).
    reingest_count = 0
    previous_ingested_at = None
    if source.metadata_:
        reingest_count = source.metadata_.get("reingest_count", 0)
        previous_ingested_at = source.metadata_.get("previous_ingested_at")

    logger.info(
        "source_summary_context_built",
        source_id=str(source.id),
        chunk_count=len(chunk_contexts),
        entity_count=len(touched_entities),
        relation_count=len(source_relations),
    )

    return {
        "source_file_name": source.file_name,
        "ingested_at": source.created_at.isoformat() if source.created_at else "",
        "chunk_count": len(chunk_contexts),
        "chunks": chunk_contexts,
        "touched_entities": touched_entities,
        "source_relations": source_relations,
        "reingest_count": reingest_count,
        "previous_ingested_at": previous_ingested_at,
    }
