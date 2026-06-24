"""
tests/retrieval/test_orchestrator
--------------------------------
Tests for the public ``retrieve()`` orchestrator.

Uses a real Postgres test database seeded with entities, relations, and
chunks, plus a deterministic embedding provider so vector search behavior
is reproducible without calling a real embedding API.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models import Chunk, ChunkEntity, Entity, Relation, Source
from rag_wiki.planner.base import QueryPlan, QueryType
from rag_wiki.providers.base import EmbeddingProvider
from rag_wiki.retrieval.orchestrator import retrieve
from rag_wiki.settings import get_settings


class _DeterministicEmbeddingProvider:
    """Test double returning a one-hot vector keyed by the input text."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Return a deterministic unit-vector embedding for each text."""
        return [_vector_for(t, self._dimensions) for t in texts]


def _vector_for(text: str, dimensions: int) -> list[float]:
    """Return a unit vector with a single 1.0 at a hash-derived index."""
    vec = [0.0] * dimensions
    index = hash(text) % dimensions
    vec[index] = 1.0
    return vec


@pytest.fixture
def embed_provider() -> EmbeddingProvider:
    """Return a deterministic embedding provider sized from settings."""
    return _DeterministicEmbeddingProvider(get_settings().embedding_dimensions)


async def _seed_graph(db: AsyncSession) -> tuple[Entity, Entity, Entity]:
    """Create a small knowledge graph for retrieval tests."""
    source = Source(
        file_path="/tmp/retrieval.txt",
        file_name="retrieval.txt",
        file_type="text/plain",
        file_size=100,
    )
    db.add(source)
    await db.flush()

    entity_a = Entity(
        name="Retrieval Orchestrator",
        entity_type="Concept",
        description="Coordinates seed finding, traversal, and context assembly.",
        embedding=_vector_for(
            "Retrieval Orchestrator",
            get_settings().embedding_dimensions,
        ),
    )
    entity_b = Entity(
        name="Graph Traversal",
        entity_type="Concept",
        description="Walks relations recursively from seed entities.",
        embedding=_vector_for("Graph Traversal", get_settings().embedding_dimensions),
    )
    entity_c = Entity(
        name="Seed Finding",
        entity_type="Concept",
        description="Finds starting entities via vector search.",
        embedding=_vector_for("Seed Finding", get_settings().embedding_dimensions),
    )
    db.add_all([entity_a, entity_b, entity_c])
    await db.flush()

    relation_chunk = Chunk(
        source_id=source.id,
        chunk_index=0,
        text_content="Relation provenance chunk.",
        embedding=_vector_for("relation chunk", get_settings().embedding_dimensions),
    )
    db.add(relation_chunk)
    await db.flush()

    relation_ab = Relation(
        source_entity_id=entity_a.id,
        target_entity_id=entity_b.id,
        relation_type="uses",
        chunk_id=relation_chunk.id,
    )
    relation_ac = Relation(
        source_entity_id=entity_a.id,
        target_entity_id=entity_c.id,
        relation_type="includes",
        chunk_id=relation_chunk.id,
    )
    db.add_all([relation_ab, relation_ac])
    await db.flush()

    chunk_a = Chunk(
        source_id=source.id,
        chunk_index=1,
        text_content="The retrieval orchestrator combines multiple steps.",
        embedding=_vector_for("chunk a", get_settings().embedding_dimensions),
    )
    chunk_b = Chunk(
        source_id=source.id,
        chunk_index=2,
        text_content="Graph traversal walks relations from seeds.",
        embedding=_vector_for("chunk b", get_settings().embedding_dimensions),
    )
    db.add_all([chunk_a, chunk_b])
    await db.flush()

    db.add_all(
        [
            ChunkEntity(chunk_id=chunk_a.id, entity_id=entity_a.id),
            ChunkEntity(chunk_id=chunk_b.id, entity_id=entity_b.id),
        ]
    )
    await db.flush()

    return entity_a, entity_b, entity_c


async def test_retrieve_uses_vector_search_to_find_seeds(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """retrieve() embeds the query and returns seed entities from vector search."""
    entity_a, entity_b, _entity_c = await _seed_graph(db)

    result = await retrieve(
        query="Retrieval Orchestrator",
        db=db,
        embed_provider=embed_provider,
    )

    assert result.query == "Retrieval Orchestrator"
    seed_ids = {s.entity_id for s in result.seeds}
    assert entity_a.id in seed_ids
    # The closest seed by one-hot hash should be entity_a; other seeds may
    # also appear depending on settings.retrieval_seed_count.
    assert len(result.seeds) <= get_settings().retrieval_seed_count
    assert any(
        edge.source_name == entity_b.name or edge.target_name == entity_b.name
        for edge in result.subgraph
    ) or any(s.entity_id == entity_b.id for s in result.seeds)
    assert result.token_counts.budget == get_settings().retrieval_total_budget_tokens


async def test_retrieve_bypasses_vector_search_with_seed_entity_ids(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """retrieve() uses provided seed_entity_ids directly instead of vector search."""
    entity_a, _entity_b, _entity_c = await _seed_graph(db)

    result = await retrieve(
        query="anything",
        db=db,
        embed_provider=embed_provider,
        seed_entity_ids=[entity_a.id],
    )

    assert len(result.seeds) == 1
    assert result.seeds[0].entity_id == entity_a.id
    assert result.seeds[0].similarity_score == 1.0
    assert result.seeds[0].seed_quality == "high"


async def test_retrieve_returns_empty_result_when_no_seeds(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """retrieve() gracefully returns an empty result when no seeds are found."""
    result = await retrieve(
        query="completely unrelated query",
        db=db,
        embed_provider=embed_provider,
    )

    assert result.seeds == []
    assert result.subgraph == []
    assert result.seed_chunks == []
    assert result.hop1_chunks == []


async def test_retrieve_comparison_dispatch_uses_per_entity_seeds(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """retrieve() uses per-entity retrieval when query_plan indicates comparison."""
    entity_a, entity_b, _entity_c = await _seed_graph(db)

    query_plan = QueryPlan(
        query_id=uuid.uuid4(),
        raw_query="compare Retrieval Orchestrator and Graph Traversal",
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
        embed_provider=embed_provider,
        seed_entity_ids=[entity_a.id, entity_b.id],
        query_plan=query_plan,
    )

    assert len(result.seeds) == 2
    seed_ids = {s.entity_id for s in result.seeds}
    assert entity_a.id in seed_ids
    assert entity_b.id in seed_ids


async def test_retrieve_comparison_without_explicit_seeds(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """Comparison without explicit seeds uses vector search."""
    entity_a, _entity_b, _entity_c = await _seed_graph(db)

    query_plan = QueryPlan(
        query_id=uuid.uuid4(),
        raw_query="compare Retrieval Orchestrator and Graph Traversal",
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
        query="Retrieval Orchestrator",
        db=db,
        embed_provider=embed_provider,
        query_plan=query_plan,
    )

    assert len(result.seeds) >= 1
    assert entity_a.id in {s.entity_id for s in result.seeds}


async def test_retrieve_no_comparison_dispatch_without_query_plan(
    db: AsyncSession,
    embed_provider: EmbeddingProvider,
) -> None:
    """retrieve() uses single-pass pipeline when query_plan is None."""
    entity_a, entity_b, _entity_c = await _seed_graph(db)

    result = await retrieve(
        query="compare",
        db=db,
        embed_provider=embed_provider,
        seed_entity_ids=[entity_a.id, entity_b.id],
    )

    assert len(result.seeds) == 2
    seed_ids = {s.entity_id for s in result.seeds}
    assert entity_a.id in seed_ids
    assert entity_b.id in seed_ids
