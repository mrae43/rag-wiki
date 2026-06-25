"""tests/retrieval/test_retrieve
-----------------------------
End-to-end integration tests for the retrieval pipeline.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus, Relation
from rag_wiki.db.models.source import Chunk, ChunkEntity, ProcessingStatus, Source
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.planner.base import QueryPlan, QueryType
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval import retrieve
from rag_wiki.settings import get_settings


def _embedding(dim: int, value: float = 1.0) -> list[float]:
    return [value] + [0.0] * (dim - 1)


async def _make_source(db: AsyncSession) -> Source:
    src = Source(
        storage_key="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    db.add(src)
    await db.commit()
    return src


async def _make_entity(
    db: AsyncSession,
    name: str,
    embedding: list[float] | None = None,
) -> Entity:
    ent = Entity(
        name=name,
        entity_type="concept",
        embedding=embedding,
        status=PublishedStatus.PUBLISHED,
    )
    db.add(ent)
    await db.commit()
    return ent


async def _make_chunk(
    db: AsyncSession,
    source: Source,
    text: str,
    embedding: list[float] | None = None,
) -> Chunk:
    chunk = Chunk(
        source=source,
        chunk_index=0,
        text_content=text,
        embedding=embedding,
        status=ProcessingStatus.PROCESSED,
    )
    db.add(chunk)
    await db.commit()
    return chunk


@pytest.mark.asyncio
async def test_retrieve_end_to_end(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    """Verify retrieval pipeline returns seeds, subgraph, wiki page, token info."""
    dims = get_settings().embedding_dimensions
    src = await _make_source(db)

    seed = await _make_entity(db, "Seed", _embedding(dims, 1.0))
    hop = await _make_entity(db, "Hop", None)

    rel = Relation(
        source_entity=seed,
        target_entity=hop,
        relation_type="knows",
        chunk=await _make_chunk(db, src, "rel chunk"),
        status=PublishedStatus.PUBLISHED,
        confidence_tag="INFERRED",
    )
    db.add(rel)
    await db.commit()

    seed_chunk = await _make_chunk(db, src, "seed text", _embedding(dims, 1.0))
    hop_chunk = await _make_chunk(db, src, "hop text", _embedding(dims, 0.5))
    db.add(ChunkEntity(chunk_id=seed_chunk.id, entity_id=seed.id))
    db.add(ChunkEntity(chunk_id=hop_chunk.id, entity_id=hop.id))
    await db.commit()

    page = WikiPage(
        entity=seed,
        title="Seed",
        slug="seed",
        content="# Seed\n\nA seed entity.",
        status=PublishedStatus.PUBLISHED,
    )
    db.add(page)
    await db.commit()

    result = await retrieve(
        query="seed",
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
    )

    assert result.query == "seed"
    assert len(result.seeds) >= 1
    assert any(s.entity_id == seed.id for s in result.seeds)
    assert result.wiki_page is not None
    assert result.wiki_page.entity_id == seed.id
    assert len(result.subgraph) >= 1
    assert any(c.chunk_id == seed_chunk.id for c in result.seed_chunks)
    assert result.total_tokens_used <= 3600
    assert result.token_counts.utilization <= 1.0


@pytest.mark.asyncio
async def test_retrieve_with_seed_entity_ids(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    """Verify retrieve respects seed_entity_ids and returns the entity as a seed."""
    dims = get_settings().embedding_dimensions
    src = await _make_source(db)
    seed = await _make_entity(db, "DirectSeed", _embedding(dims, 1.0))
    seed_chunk = await _make_chunk(db, src, "direct text", _embedding(dims, 1.0))
    db.add(ChunkEntity(chunk_id=seed_chunk.id, entity_id=seed.id))
    await db.commit()

    result = await retrieve(
        query="direct",
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
        seed_entity_ids=[seed.id],
    )

    assert len(result.seeds) == 1
    assert result.seeds[0].entity_id == seed.id
    assert result.seeds[0].seed_quality == "high"


@pytest.mark.asyncio
async def test_retrieve_no_matches_returns_empty_result(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    """Verify retrieve returns an empty result when no entities match the query."""
    result = await retrieve(
        query="nothing",
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
    )

    assert result.seeds == []
    assert result.subgraph == []
    assert result.wiki_page is None
    assert result.seed_chunks == []
    assert result.hop1_chunks == []
    assert result.total_tokens_used <= 3600


@pytest.mark.asyncio
async def test_retrieve_comparison_merges_results(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    """Comparison query path merges per-entity retrievals into a single result."""
    dims = get_settings().embedding_dimensions
    src = await _make_source(db)

    entity_a = await _make_entity(db, "Alpha", _embedding(dims, 1.0))
    entity_b = await _make_entity(db, "Beta", _embedding(dims, 0.5))

    rel_a = Relation(
        source_entity=entity_a,
        target_entity=entity_b,
        relation_type="competes_with",
        chunk=await _make_chunk(db, src, "rel ab"),
        status=PublishedStatus.PUBLISHED,
        confidence_tag="INFERRED",
    )
    db.add(rel_a)
    await db.commit()

    chunk_a = await _make_chunk(db, src, "alpha text", _embedding(dims, 1.0))
    chunk_b = await _make_chunk(db, src, "beta text", _embedding(dims, 0.5))
    db.add(ChunkEntity(chunk_id=chunk_a.id, entity_id=entity_a.id))
    db.add(ChunkEntity(chunk_id=chunk_b.id, entity_id=entity_b.id))
    await db.commit()

    query_plan = QueryPlan(
        query_id=uuid.uuid4(),
        raw_query="compare Alpha and Beta",
        classified_type=QueryType.COMPARISON,
        retrieval_depth="shallow",
        seed_count=2,
        termination_condition="all entities resolved",
        confidence=0.9,
        classification_source="llm",
        rationale="comparison query",
        planner_version="1.0.0",
    )

    result = await retrieve(
        query="compare",
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
        seed_entity_ids=[entity_a.id, entity_b.id],
        query_plan=query_plan,
    )

    assert len(result.seeds) == 2
    seed_ids = {s.entity_id for s in result.seeds}
    assert entity_a.id in seed_ids
    assert entity_b.id in seed_ids

    all_chunk_ids = {c.chunk_id for c in result.seed_chunks}
    assert chunk_a.id in all_chunk_ids
    assert chunk_b.id in all_chunk_ids
