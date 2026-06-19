"""
rag_wiki.retrieval.context
-------------------------
Token-budget context assembly for the retrieval pipeline.

Fetches wiki pages, scores chunks, applies elastic budgets, and builds the
final ``RetrievalResult`` with per-slot token accounting.
"""

from __future__ import annotations

import datetime
import uuid

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.db.models.source import Chunk, ChunkEntity
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import DatabaseError
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.schemas import (
    RetrievalResult,
    ScoredChunk,
    SeedResult,
    SlotTokenCounts,
    SubgraphEdge,
    WikiPageSnapshot,
)
from rag_wiki.retrieval.scoring import (
    cosine_similarity,
    deduplicate_chunks,
    estimate_tokens,
    truncate_to_budget,
)
from rag_wiki.retrieval.traversal import TraversalResult
from rag_wiki.settings import get_settings

logger = structlog.get_logger(__name__)

_RETRIEVAL_INSTRUCTION = (
    "Use the provided context — including entity metadata, graph relations, "
    "wiki pages, and source chunks — to answer the user's question accurately."
)

ENTITY_PAGE_SECTION_PRIORITY = [
    "entity_prose",
    "relationships",
    "contradictions",
    "sources",
    "other",
]

SOURCE_PAGE_SECTION_PRIORITY = [
    "entity_prose",
    "pages_created",
    "key_relations",
    "contradictions",
    "ingest_history",
    "sources",
    "other",
]


def _heading_to_tag(heading: str) -> str:
    mapping = {
        "relations": "relationships",
        "relationships": "relationships",
        "contradictions": "contradictions",
        "sources": "sources",
        "chunks": "sources",
        "pages created or updated": "pages_created",
        "key relations introduced": "key_relations",
        "ingest history": "ingest_history",
    }
    return mapping.get(heading, "other")


def _parse_sections(content: str) -> list[tuple[str, str]]:
    """Parse markdown content into (tag, text) sections on ``##`` headings."""
    lines = content.splitlines()
    sections: list[tuple[str, str]] = []
    current_tag = "entity_prose"
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current_lines:
                sections.append((current_tag, "\n".join(current_lines)))
            heading = line[3:].strip().lower()
            current_tag = _heading_to_tag(heading)
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_tag, "\n".join(current_lines)))

    return sections


def _truncate_chunks(chunks: list[ScoredChunk], budget: int) -> list[ScoredChunk]:
    """Greedy-fill *chunks* into *budget* tokens, keeping whole chunks."""
    result: list[ScoredChunk] = []
    used = 0
    for chunk in chunks:
        cost = estimate_tokens(chunk.text)
        if used + cost <= budget:
            result.append(chunk)
            used += cost
        else:
            break
    return result


async def _fetch_wiki_page(
    entity_id: uuid.UUID,
    db: AsyncSession,
    budget: int,
) -> WikiPageSnapshot | None:
    """Fetch and section-priority-truncate a wiki page."""
    try:
        result = await db.execute(
            sa.select(WikiPage).where(WikiPage.entity_id == entity_id)
        )
        page = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "Failed to fetch wiki page for context assembly",
            entity_id=str(entity_id),
            error=str(exc),
        )
        raise DatabaseError(
            f"Failed to fetch wiki page for entity {entity_id}"
        ) from exc
    if page is None:
        return None

    priority = (
        ENTITY_PAGE_SECTION_PRIORITY
        if page.entity_id is not None
        else SOURCE_PAGE_SECTION_PRIORITY
    )
    sections = _parse_sections(page.content)

    def _prio(tag: str) -> int:
        try:
            return priority.index(tag)
        except ValueError:
            return 0

    sections.sort(key=lambda s: _prio(s[0]))
    section_strings = [text for _, text in sections]
    selected = truncate_to_budget(section_strings, budget)
    selected_set = set(selected)

    included = [tag for tag, text in sections if text in selected_set]
    dropped = [tag for tag, text in sections if text not in selected_set]

    contributing = page.synthesized_from_sources or []
    return WikiPageSnapshot(
        entity_id=entity_id,
        content="\n\n".join(selected),
        synthesized_at=page.synthesized_at,
        contributing_source_count=len(contributing),
        was_truncated=len(selected) < len(sections),
        original_token_count=estimate_tokens(page.content),
        sections_included=included,
        sections_dropped=dropped,
    )


async def _score_chunk_pairs(
    pairs: list[tuple[Chunk, uuid.UUID]],
    query_embedding: list[float],
    embed_provider: EmbeddingProvider,
    model: str,
) -> list[tuple[float, Chunk, uuid.UUID]]:
    """Score (chunk, entity_id) pairs against a pre-computed query embedding.

    Chunks that lack an embedding are embedded on-the-fly using
    *embed_provider*, matching the behavior documented in ADR-0012.
    """
    if not pairs:
        return []

    chunks_missing = [
        chunk for chunk, _ in pairs if chunk.embedding is None and chunk.text_content
    ]
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

    scored: list[tuple[float, Chunk, uuid.UUID]] = []
    for chunk, entity_id in pairs:
        if chunk.embedding is None:
            continue
        if len(chunk.embedding) != len(query_embedding):
            logger.warning(
                "embedding_dimension_mismatch",
                chunk_id=str(chunk.id),
                chunk_dim=len(chunk.embedding),
                desc_dim=len(query_embedding),
            )
            continue
        sim = cosine_similarity(query_embedding, chunk.embedding)
        scored.append((sim, chunk, entity_id))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_scored_chunk(
    sim: float,
    chunk: Chunk,
    entity_id: uuid.UUID,
    hop_distance: int,
) -> ScoredChunk:
    source = chunk.source
    return ScoredChunk(
        chunk_id=chunk.id,
        entity_id=entity_id,
        source_file=source.file_name if source else "unknown",
        source_name=source.file_name if source else "unknown",
        ingested_at=chunk.created_at.isoformat() if chunk.created_at else "",
        text=chunk.text_content or "",
        similarity_score=sim,
        hop_distance=hop_distance,
    )


async def assemble_context(
    query: str,
    query_embedding: list[float],
    seeds: list[SeedResult],
    traversal: TraversalResult,
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
    max_context_tokens: int,
) -> RetrievalResult:
    """Assemble structured context and return a ``RetrievalResult``.

    Args:
        query: Raw user query text.
        query_embedding: Pre-computed embedding of *query*.
        seeds: Seed entities from the seed-finding step.
        traversal: Traversed subgraph.
        db: Async SQLAlchemy session.
        embed_provider: Provider for on-the-fly chunk embedding; used when
            retrieved chunks lack pre-computed embeddings.
        max_context_tokens: Total token budget provided by the caller.

    Returns:
        A fully populated ``RetrievalResult`` with per-slot token counts.
    """
    settings = get_settings()
    retrieved_at = datetime.datetime.now(datetime.UTC)

    # --- Slot 1: Structural anchor ---
    anchor_budget = settings.retrieval_anchor_budget_tokens
    anchor_lines: list[str] = []
    for seed in seeds:
        a = seed.anchor
        anchor_lines.append(
            f"- {a.name} ({a.type}): {a.description} | degree={a.degree} "
            f"centrality={a.relative_centrality} | {a.relation_summary}"
        )
    anchor_text = "\n".join(truncate_to_budget(anchor_lines, anchor_budget))
    anchor_tokens = estimate_tokens(anchor_text)

    # --- Slot 2: Subgraph edges ---
    subgraph_budget = settings.retrieval_subgraph_budget_tokens
    edge_lines: list[str] = []
    for rel in traversal.relations:
        source_name = rel.source_entity.name if rel.source_entity else "?"
        target_name = rel.target_entity.name if rel.target_entity else "?"
        source_type = rel.source_entity.entity_type if rel.source_entity else "?"
        target_type = rel.target_entity.entity_type if rel.target_entity else "?"
        hop = (
            min(
                traversal.hop_map.get(rel.source_entity_id, 0),
                traversal.hop_map.get(rel.target_entity_id, 0),
            )
            + 1
        )
        line = f"[{source_name}] --{rel.relation_type}--> [{target_name}] (hop {hop})"
        edge_lines.append(line)
    subgraph_text = "\n".join(truncate_to_budget(edge_lines, subgraph_budget))
    subgraph_tokens = estimate_tokens(subgraph_text)

    # --- Slot 3: Wiki page ---
    wiki_budget = settings.retrieval_wiki_page_budget_tokens
    wiki_snapshot: WikiPageSnapshot | None = None
    if seeds:
        wiki_snapshot = await _fetch_wiki_page(seeds[0].entity_id, db, wiki_budget)
    wiki_tokens = estimate_tokens(wiki_snapshot.content) if wiki_snapshot else 0

    # --- Slot 6: Instruction ---
    instruction_text = _RETRIEVAL_INSTRUCTION
    instruction_tokens = estimate_tokens(instruction_text)

    # --- Elastic pool ---
    fixed_consumed = anchor_tokens + subgraph_tokens + instruction_tokens + wiki_tokens
    elastic_budget = max_context_tokens - fixed_consumed
    if elastic_budget < 0:
        elastic_budget = 0

    seed_chunk_budget = int(elastic_budget * 0.6)
    hop_chunk_budget = elastic_budget - seed_chunk_budget

    # --- Fetch chunks ---
    seed_ids = [s.entity_id for s in seeds]
    seed_ids_set = set(seed_ids)
    hop_ids = [e.id for e in traversal.entities]
    all_entity_ids = seed_ids + hop_ids

    seed_chunks_list: list[ScoredChunk] = []
    hop_chunks_list: list[ScoredChunk] = []
    chunks_fetched = 0
    chunks_after_dedup = 0

    if all_entity_ids and elastic_budget > 0:
        try:
            ce_result = await db.execute(
                sa.select(ChunkEntity.chunk_id, ChunkEntity.entity_id).where(
                    ChunkEntity.entity_id.in_(all_entity_ids)
                )
            )
            chunk_to_entities: dict[uuid.UUID, list[uuid.UUID]] = {}
            for chunk_id, entity_id in ce_result.all():
                chunk_to_entities.setdefault(chunk_id, []).append(entity_id)

            chunk_ids = list(chunk_to_entities.keys())
            if chunk_ids:
                chunk_result = await db.execute(
                    sa.select(Chunk)
                    .options(joinedload(Chunk.source))
                    .where(Chunk.id.in_(chunk_ids))
                )
                chunks_by_id = {c.id: c for c in chunk_result.unique().scalars().all()}
        except Exception as exc:
            logger.error(
                "Failed to fetch chunks for context assembly",
                entity_count=len(all_entity_ids),
                error=str(exc),
            )
            raise DatabaseError(
                f"Failed to fetch chunks for {len(all_entity_ids)} entities"
            ) from exc

            seed_pairs: list[tuple[Chunk, uuid.UUID]] = []
            hop_pairs: list[tuple[Chunk, uuid.UUID]] = []
            for cid, ent_ids in chunk_to_entities.items():
                chunk = chunks_by_id.get(cid)
                if chunk is None:
                    continue
                linked_seeds = [eid for eid in ent_ids if eid in seed_ids_set]
                if linked_seeds:
                    seed_pairs.append((chunk, linked_seeds[0]))
                else:
                    hop_pairs.append((chunk, ent_ids[0]))

            chunks_fetched = len(seed_pairs) + len(hop_pairs)

            # Score with the pre-computed query embedding.
            seed_scored = await _score_chunk_pairs(
                seed_pairs, query_embedding, embed_provider, settings.embedding_model
            )
            hop_scored = await _score_chunk_pairs(
                hop_pairs, query_embedding, embed_provider, settings.embedding_model
            )

            # Deduplicate globally across seed + hop.
            all_for_dedup = [(sim, chunk) for sim, chunk, _ in seed_scored + hop_scored]
            deduped = deduplicate_chunks(
                all_for_dedup,
                threshold=settings.retrieval_dedup_threshold,
            )
            deduped_ids = {c.id for _, c in deduped}
            chunks_after_dedup = len(deduped_ids)

            seed_scored = [
                (sim, chunk, eid)
                for sim, chunk, eid in seed_scored
                if chunk.id in deduped_ids
            ]
            hop_scored = [
                (sim, chunk, eid)
                for sim, chunk, eid in hop_scored
                if chunk.id in deduped_ids
            ]

            # Build ScoredChunk objects.
            seed_chunks_raw = [
                _build_scored_chunk(sim, chunk, eid, hop_distance=0)
                for sim, chunk, eid in seed_scored
            ]
            hop_chunks_raw = [
                _build_scored_chunk(
                    sim, chunk, eid, hop_distance=traversal.hop_map.get(eid, 1)
                )
                for sim, chunk, eid in hop_scored
            ]

            seed_chunks_list = _truncate_chunks(seed_chunks_raw, seed_chunk_budget)
            hop_chunks_list = _truncate_chunks(hop_chunks_raw, hop_chunk_budget)

    # --- Token counts ---
    seed_chunk_tokens = sum(estimate_tokens(c.text) for c in seed_chunks_list)
    hop_chunk_tokens = sum(estimate_tokens(c.text) for c in hop_chunks_list)
    total_tokens = (
        anchor_tokens
        + subgraph_tokens
        + wiki_tokens
        + seed_chunk_tokens
        + hop_chunk_tokens
        + instruction_tokens
    )
    utilization = total_tokens / max_context_tokens if max_context_tokens else 0.0

    token_counts = SlotTokenCounts(
        anchor=anchor_tokens,
        subgraph=subgraph_tokens,
        wiki_page=wiki_tokens,
        seed_chunks=seed_chunk_tokens,
        hop_chunks=hop_chunk_tokens,
        instruction=instruction_tokens,
        total=total_tokens,
        budget=max_context_tokens,
        utilization=round(utilization, 4),
    )

    # Build SubgraphEdge list for the result.
    subgraph_edges: list[SubgraphEdge] = []
    for rel in traversal.relations:
        source_name = rel.source_entity.name if rel.source_entity else "?"
        target_name = rel.target_entity.name if rel.target_entity else "?"
        source_type = rel.source_entity.entity_type if rel.source_entity else "?"
        target_type = rel.target_entity.entity_type if rel.target_entity else "?"
        hop = (
            min(
                traversal.hop_map.get(rel.source_entity_id, 0),
                traversal.hop_map.get(rel.target_entity_id, 0),
            )
            + 1
        )
        subgraph_edges.append(
            SubgraphEdge(
                source_name=source_name,
                source_type=source_type,
                relation=rel.relation_type,
                target_name=target_name,
                target_type=target_type,
                hop=hop,
                confidence_tag=rel.confidence_tag,
                confidence_score=rel.confidence_score,
            )
        )

    return RetrievalResult(
        query=query,
        retrieved_at=retrieved_at,
        seeds=seeds,
        subgraph=subgraph_edges,
        wiki_page=wiki_snapshot,
        seed_chunks=seed_chunks_list,
        hop1_chunks=hop_chunks_list,
        token_counts=token_counts,
        total_tokens_used=total_tokens,
        entities_traversed=len(traversal.hop_map),
        entities_after_truncation=len(traversal.entities),
        chunks_fetched=chunks_fetched,
        chunks_after_dedup=chunks_after_dedup,
    )
