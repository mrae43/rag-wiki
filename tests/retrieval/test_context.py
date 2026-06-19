"""tests/retrieval/test_context
----------------------------
Integration tests for token-budget context assembly.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus
from rag_wiki.db.models.source import Chunk, ChunkEntity, ProcessingStatus, Source
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.context import assemble_context
from rag_wiki.retrieval.schemas import SeedResult, StructuralAnchor
from rag_wiki.retrieval.traversal import TraversalResult
from rag_wiki.settings import get_settings


def _embedding(dim: int, value: float = 1.0) -> list[float]:
    return [value] + [0.0] * (dim - 1)


async def _make_source(db: AsyncSession) -> Source:
    src = Source(
        file_path="/tmp/test.pdf",
        file_name="test.pdf",
        file_type="application/pdf",
        file_size=1234,
        status=ProcessingStatus.PENDING,
    )
    db.add(src)
    await db.commit()
    return src


async def _make_entity(db: AsyncSession, name: str) -> Entity:
    ent = Entity(
        name=name,
        entity_type="concept",
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
async def test_wiki_page_section_priority_truncation(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    dims = get_settings().embedding_dimensions
    seed = await _make_entity(db, "WikiSeed")
    page = WikiPage(
        entity=seed,
        title="WikiSeed",
        slug="wikiseed",
        content=(
            "# WikiSeed\n\nProse here.\n\n"
            "## Sources\n\nSome sources.\n\n"
            "## Relations\n\nSome relations.\n\n"
            "## Contradictions\n\nSome contradictions.\n\n"
        ),
        status=PublishedStatus.PUBLISHED,
    )
    db.add(page)
    await db.commit()

    seed_result = SeedResult(
        entity_id=seed.id,
        similarity_score=0.0,
        seed_quality="high",
        anchor=StructuralAnchor(
            name=seed.name,
            type=seed.entity_type,
            description="",
            degree=0,
            relative_centrality="low",
            hop_distance=0,
            relation_summary="No relations",
        ),
    )

    result = await assemble_context(
        query="test",
        query_embedding=_embedding(dims, 1.0),
        seeds=[seed_result],
        traversal=TraversalResult(entities=[], relations=[], hop_map={}),
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
    )

    assert result.wiki_page is not None
    assert result.wiki_page.entity_id == seed.id
    # entity_prose should be included (highest priority), then relations.
    assert "entity_prose" in result.wiki_page.sections_included
    assert "relationships" in result.wiki_page.sections_included
    # sources is lower priority and may be dropped if budget exhausted.
    # With the default 1000 token budget for wiki page, all sections fit,
    # but let's verify truncation metadata is sane.
    assert result.wiki_page.was_truncated is False


@pytest.mark.asyncio
async def test_seed_vs_hop_chunk_assignment(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    dims = get_settings().embedding_dimensions
    src = await _make_source(db)
    seed = await _make_entity(db, "Seed")
    hop = await _make_entity(db, "Hop")

    seed_chunk = await _make_chunk(
        db, src, "seed chunk text", [1.0] + [0.0] * (dims - 1)
    )
    hop_chunk = await _make_chunk(
        db, src, "hop chunk text", [0.0, 1.0] + [0.0] * (dims - 2)
    )

    db.add(ChunkEntity(chunk_id=seed_chunk.id, entity_id=seed.id))
    db.add(ChunkEntity(chunk_id=hop_chunk.id, entity_id=hop.id))
    await db.commit()

    seed_result = SeedResult(
        entity_id=seed.id,
        similarity_score=0.0,
        seed_quality="high",
        anchor=StructuralAnchor(
            name=seed.name,
            type=seed.entity_type,
            description="",
            degree=0,
            relative_centrality="low",
            hop_distance=0,
            relation_summary="No relations",
        ),
    )

    traversal = TraversalResult(
        entities=[hop],
        relations=[],
        hop_map={hop.id: 1},
    )

    result = await assemble_context(
        query="test",
        query_embedding=_embedding(dims, 1.0),
        seeds=[seed_result],
        traversal=traversal,
        db=db,
        embed_provider=mock_embedding_provider,
        max_context_tokens=3600,
    )

    assert any(c.chunk_id == seed_chunk.id for c in result.seed_chunks)
    assert any(c.chunk_id == hop_chunk.id for c in result.hop1_chunks)
    assert result.token_counts.budget == 3600
    assert result.total_tokens_used <= 3600


@pytest.mark.asyncio
async def test_context_no_seeds_empty_result(
    db: AsyncSession,
    mock_embedding_provider: EmbeddingProvider,
) -> None:
    """Verify assemble_context returns an empty result when no seeds are provided."""
    dims = get_settings().embedding_dimensions
    result = await assemble_context(
        query="test",
        query_embedding=_embedding(dims, 1.0),
        seeds=[],
        traversal=TraversalResult(entities=[], relations=[], hop_map={}),
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
