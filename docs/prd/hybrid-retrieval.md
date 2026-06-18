# PRD: Hybrid Retrieval Pipeline

## Problem Statement

The rag-wiki system ingests documents, extracts entities and relations into a knowledge graph, and synthesizes wiki pages. But there is no mechanism for **querying** the system — a user cannot ask a question and get an answer that draws on both the vector embeddings and the knowledge graph. The ingest pipeline fills the database; the retrieval pipeline needs to read it back.

ADR-0009 established a hybrid retrieval architecture (vector seed → recursive CTE graph traversal → context assembly) but left all implementation details undecided. This PRD defines the v1 implementation.

## Solution

A retrieval pipeline that accepts a user query and returns structured context from both the vector store and knowledge graph, ready for an LLM to answer. The pipeline is callable from code — the FastAPI query endpoint, wiki synthesis, or future consumers — and is designed as separable internal steps so alternative modes can be added later without rewriting.

## User Stories

1. As a user, I want to ask a natural language question about ingested content, so that I can get an answer synthesized from the knowledge graph.
2. As a user, I want the answer to draw on both chunk content and entity/relationship structure, so that I get context-aware responses rather than raw chunk search.
3. As a developer, I want to navigate directly to an entity's wiki page and see related context, so that wiki link clicks trigger context assembly without a vector search.
4. As an operator, I want retrieval latency and budget utilization surfaced in the return value, so that I can monitor and debug context assembly.
5. As a developer, I want the scoring and deduplication utilities shared between retrieval and wiki synthesis, so that there is one implementation of cosine similarity, budget truncation, and chunk dedup.
6. As a developer, I want the retrieval module to accept a caller-provided token budget, so that the API layer can subtract conversation history before calling retrieval, keeping the module conversation-agnostic.
7. As a developer, I want the retrieval result to include per-slot token counts and a utilization ratio, so that I can debug budget exhaustion and tune settings.

## Implementation Decisions

### 1. Module structure (`rag_wiki/retrieval/`)

The retrieval pipeline is split into four internal modules plus a top-level orchestrator, mapping to ADR-0009's "separable steps":

- **`seeds.py`** — Finds seed entities. Either by pgvector cosine top-k on `entities.embedding` (given a user query), or by loading entities directly by ID (given `seed_entity_ids`). Computes `seed_quality` tags ("high"/"low"/"poor") and `StructuralAnchor` metadata (degree, relative centrality percentile bucket, natural-language relation summary) for each seed.
- **`traversal.py`** — Recursive CTE graph traversal. Uses raw SQL for the CTE (per coding standards §7.3) returning `(entity_id, hop_distance)`, then loads full entity and relation objects with SQLAlchemy ORM. Bidirectional, all relation types, bounded by `retrieval_max_hops`, `retrieval_max_neighbors_per_hop`, and a Python-side `retrieval_max_total_nodes` ceiling.
- **`scoring.py`** — Shared primitives used by both retrieval and wiki synthesis: `cosine_similarity`, `score_chunks`, `deduplicate_chunks`, `truncate_to_budget`, `estimate_tokens`. Moved out of `wiki/context.py` into this module; `wiki/context.py` imports from here with no behavioral change.
- **`context.py`** — Token-budget context assembly. Fetches existing wiki page for seed entity. Scores seed chunks and hop-1 chunks against the query embedding. Applies section-priority truncation to wiki pages (two priority lists: entity pages and source summary pages). Builds the final `RetrievalResult` with per-slot token accounting.
- **`schemas.py`** — All data models as `@dataclass`: `StructuralAnchor`, `SeedResult`, `SubgraphEdge`, `WikiPageSnapshot`, `ScoredChunk`, `SlotTokenCounts`, `RetrievalResult`.
- **`__init__.py`** — Public `retrieve()` function that orchestrates the pipeline in order: embed query → find seeds → traverse graph → assemble context → return `RetrievalResult`.

### 2. Seed-finding strategy

Entity embeddings only — not chunks. The knowledge graph is the architecture's central value; entity embeddings aggregate signal across multiple chunks and provide stronger semantic anchors. An optional `seed_entity_ids` parameter bypasses vector search entirely for direct navigation.

Top-k cosine distance via pgvector (`embedding <=> :query`), `retrieval_seed_count` = 3 (configurable). Each seed gets a `seed_quality` tag with hardcoded thresholds:
- `"high"`: cosine distance < 0.2
- `"low"`: 0.2–0.4
- `"poor"`: > 0.4

### 3. Graph traversal

Recursive CTE, bidirectional (undirected walk), all relation types. Bounded by:
- `retrieval_max_hops` = 2
- `retrieval_max_neighbors_per_hop` = 10
- `retrieval_max_total_nodes` = 50 (enforced in Python post-CTE, ranking by hop distance ascending then degree descending)

The CTE returns `(entity_id, hop_distance)`. A second ORM query loads full `Entity` + `Relation` objects with `joinedload`.

### 4. Token budget and context assembly

The `retrieve()` function receives `max_context_tokens` from its caller. Six slots:

| Slot | Content | Budget | Type |
|------|---------|--------|------|
| 1 | Structural anchor | 200 | Fixed |
| 2 | Traversed subgraph edges | 400 | Fixed |
| 3 | Existing wiki page | 1000 | Fixed |
| 4 | Seed-entity chunks | ~1080 | Elastic — first claim |
| 5 | Hop-1 entity chunks | ~720 | Elastic — remainder |
| 6 | System instruction | 200 | Fixed |

Default total: 3600 tokens. Elastic pool = total - consumed fixed slots. Seed chunks claim ~60%, hop-1 chunks fill the rest. All chunks scored against the query embedding and cosine-deduplicated.

Wiki page truncation: section-priority at query time. Parse markdown on `##` headings, fill budget in priority order. Two priority lists: one for entity pages, one for source summary pages. Unrecognized sections default to `entity_prose` (highest priority).

### 5. Schema addition: `confidence_tag` on `relations`

A prerequisite migration adds:
- `confidence_tag: str`, nullable=False, `server_default="INFERRED"`
- `confidence_score: float | None`, nullable=True, `server_default=None`

LLM-extracted relations are INFERRED by definition. The tag flows through `SubgraphEdge` in retrieval results. The `chunk_entities` join table is not modified — edge confidence lives on the `Relation` table.

### 6. Exceptions

New `RetrievalError(RagWikiError)` base exception added to `exceptions.py` for hierarchy completeness. No retrieval code raises it — the pipeline always returns a valid `RetrievalResult`, even with zero seeds.

### 7. Shared scoring utilities

`cosine_similarity`, `score_chunks`, `deduplicate_chunks`, `truncate_to_budget`, `estimate_tokens` are moved from `wiki/context.py` to `retrieval/scoring.py`. `wiki/context.py` imports from there. No behavioral change to synthesis.

### 8. New ADR

ADR-0012 documents the seeding strategy, traversal semantics, budget structure, section-priority truncation, `confidence_tag` addition, and caller-provided `max_context_tokens` design. Written and committed alongside the implementation.

## Testing Decisions

### What makes a good test
- Test external behavior, not implementation details. A passing test should stay passing after internal refactoring.
- Mock LLM and embedding providers with deterministic fakes (existing pattern: `FakeLLMProvider` from provider tests).
- Test budget truncation at boundary values: exact budget fit, one item over, empty input, single item.
- Test section-priority truncation with known wiki page content to verify section ordering.
- Test the CTE against a real database with known entities and relations. The CTE SQL is the riskiest part — it needs a real pgvector test fixture.

### Modules to test
| Module | Tests |
|--------|-------|
| `retrieval/seeds.py` | Vector search returns correct seeds; seed_quality thresholds; seed_entity_ids bypass; empty results |
| `retrieval/traversal.py` | CTE returns correct entities at each hop; bidirectional traversal; per-hop limit; total node ceiling truncation; single entity, no relations |
| `retrieval/scoring.py` | Happy-path scoring; dimension mismatch; embed on-the-fly; dedup removes near-duplicates; budget truncation at boundaries |
| `retrieval/context.py` | Wiki page section-priority truncation; token counts correct; elastic budget split; seed vs hop-1 chunk assignment |
| `retrieval/__init__.py` | Full pipeline integration: end-to-end with mocked providers and real DB |

### Prior art
- `tests/providers/test_openai.py` — provider mocking pattern
- `tests/graph/test_resolution.py` — DB fixture setup for entity/relation tests
- `tests/wiki/test_synthesis.py` — context assembly testing pattern
- `tests/db/` — DB session fixture and model creation helpers

## Out of Scope

- **FastAPI query endpoint** — The retrieval pipeline is code-callable. The HTTP API that invokes it is a separate PR.
- **Refactoring `wiki/context.py` to call `retrieve()`** — Sharing scoring utilities is in scope, but making synthesis call the retrieval pipeline is deferred.
- **Community detection / Leiden clustering** — No `community_id` on entities yet; not needed for v1 retrieval.
- **Multi-mode selection** — "local" / "global" / "naive" modes are deferred per ADR-0009. v1 has one hybrid mode.
- **Chunk-seed retrieval** — Entity-only seeding is the v1 default. Chunk-first seeding is a future mode.
- **Confidence-weighted ranking** — `confidence_score` is reserved but unused in v1. All relations are INFERRED with equal weight.
- **Authentication / authorization** — No retrieval-specific auth changes. Out of scope for this PRD.

## Further Notes

The implementation is split into two PRs:
1. Schema migration PR: `confidence_tag` columns on `relations` + Alembic migration + one-line extraction pipeline update.
2. Retrieval PR: Everything in `rag_wiki/retrieval/`, settings additions, wik/context.py refactor, exceptions, CONTEXT.md update, ADR-0012, tests.

Both PRs must pass `ruff check → ruff format → mypy → pytest` before merging.
