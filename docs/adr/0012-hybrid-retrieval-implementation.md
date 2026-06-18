# ADR-0012: Hybrid retrieval implementation — seed strategy, traversal semantics, context assembly

## Status
Accepted

## Context
ADR-0009 established a hybrid retrieval architecture (vector seed → recursive CTE graph traversal → combined context) and mandated separable internal steps for future mode-switching. This ADR makes the concrete implementation decisions for v1's single hybrid mode.

The following design dimensions each had viable alternatives and were resolved during a structured design review:

1. **Seed-finding strategy** — which embedding space to search (entity vs chunk vs both)
2. **Traversal bounds** — depth limits, breadth limits, directionality, total-node ceiling
3. **Chunk selection and scoring** — which traversed entities' chunks to include, how to rank them
4. **Token budget structure** — fixed vs elastic slots, conversation-awareness boundary
5. **Wiki page handling** — whether the retrieval module or caller fetches the wiki page, how to truncate
6. **Confidence metadata** — where extraction certainty lives and how it flows through the pipeline
7. **Return type** — structured model vs rendered string

## Decision

### 1. Entity-only seeding
The initial vector search queries only `entities.embedding`. Chunks are not searched for seeds. The knowledge graph is the value proposition of the architecture; entity embeddings aggregate meaning across chunks and provide stronger semantic anchors. Chunk-based seeding is a future mode, not v1's default.

Top-k cosine distance via pgvector (`embedding <=> :query`), with a configurable `retrieval_seed_count` (default 3). Each returned seed receives a `seed_quality` tag — "high" (cosine distance < 0.2), "low" (0.2–0.4), or "poor" (> 0.4) — with hardcoded thresholds. The caller inspects quality for UX decisions rather than the retrieval module raising or silently falling back. An optional `seed_entity_ids` parameter bypasses vector search entirely for direct entity navigation (wiki link clicks, "go to entity" requests).

### 2. Bidirectional CTE traversal with fixed bounds
The recursive CTE follows relations in both directions (undirected walk). All relation types are included — filtering by type is deferred as a future optimization.

Bounded by configurable settings:
- `retrieval_max_hops` (default 2)
- `retrieval_max_neighbors_per_hop` (default 10)

After the CTE runs, a total-node ceiling `retrieval_max_total_nodes` (default 50) is enforced in Python by ranking all traversed entities by (hop_distance ascending, degree descending) and truncating. The CTE itself is raw SQL (per coding standards §7.3); the entity and relation objects are loaded with a separate ORM query for type safety.

### 3. Two-tier chunk selection
Seed-entity chunks have priority claim on the elastic token budget. Hop-1 entity chunks fill the remainder. All chunks are scored against the query embedding (not the seed entity's embedding) to maximize relevance to the user's actual question. Deduplication is by cosine similarity (`retrieval_dedup_threshold`, default 0.92).

### 4. Caller-provided token budget
The `retrieve()` function receives `max_context_tokens` from its caller. It does not know about conversation history, system prompts, or model context windows. The API layer is responsible for subtracting conversation overhead and answer reserve before calling retrieval. This keeps the retrieval module conversation-agnostic and independently testable.

Budget structure (all configurable, defaults in parentheses):

| Slot | Content | Budget type | Default |
|------|---------|-------------|---------|
| 1 | Structural anchor (seed metadata + centrality) | Fixed | 200 |
| 2 | Traversed subgraph (edge list) | Fixed | 400 |
| 3 | Existing wiki page for seed entity | Fixed | 1000 |
| 4 | Seed-entity chunks (scored, deduped) | Elastic — first claim | remainder ~60% |
| 5 | Hop-1 entity chunks (scored, deduped) | Elastic — remainder | remainder ~40% |
| 6 | System instruction | Fixed | 200 |

Default total: 3600 tokens. Elastic pool = total - fixed slots (when wiki page exists, ~1800 tokens).

### 5. Retrieval-owns wiki page lookup
The retrieval module fetches the existing wiki page (`wiki_pages` table by `entity_id`) internally. Wiki page truncation uses section-priority for query-time retrieval: parse on `##` markdown headings, fill budget in priority order (`entity_prose` first, then relations, contradictions, sources, ingest_history). Unrecognized sections default to `entity_prose`. Source summary pages (no `entity_id`) use a different priority list reflecting their section structure. Head-truncation (simple character cut) is retained for wiki synthesis use, which operates via `wiki/context.py` and is not affected by this change.

### 6. Confidence tag on relations
All relations get a `confidence_tag` column with `server_default="INFERRED"`. LLM-extracted edges are INFERRED by definition; future AST or manual edges would be EXTRACTED. A nullable `confidence_score` column (0.0–1.0) is reserved for future ranking but defaults to `None` in v1. The tag flows through the `SubgraphEdge` model in retrieval results. The schema was added as a separate migration (ADR-0012's prerequisite) so existing rows have correct defaults without a backfill.

### 7. Structured return type
`retrieve()` returns a `RetrievalResult` dataclass with typed sub-models (`SeedResult`, `SubgraphEdge`, `WikiPageSnapshot`, `ScoredChunk`, `SlotTokenCounts`). The caller renders this into a prompt template; retrieval owns only the data. Per-slot token counts and observability fields (`entities_traversed`, `chunks_after_dedup`) make budget debugging straightforward.

### 8. Shared scoring utilities
`cosine_similarity`, `score_chunks`, `deduplicate_chunks`, `truncate_to_budget`, and `estimate_tokens` are defined in `rag_wiki/retrieval/scoring.py` and imported by `wiki/context.py`. This avoids logic duplication and positions the retrieval module as the canonical home for scoring/budget primitives. No behavioral change to wiki synthesis — the refactor is purely mechanical.

## Rationale
- **Entity-only seeding** avoids the indirection of chunk → entity reverse lookup and uses the graph as intended. Entity embeddings accumulate signal across multiple chunks, making them more robust seeds than individual chunk embeddings.
- **Bidirectional traversal** captures all semantically relevant paths. "Alice manages Bob" is informative whether starting from Alice or Bob. Direction filtering can be added as a query-time parameter later.
- **Fixed bounds** make the pipeline predictable and debuggable. Dynamic bounds (degree-based hop count) add complexity without evidence of need.
- **Two-tier chunk selection** prioritizes high-confidence seed evidence while the secondary budget catches bridging evidence that is topically adjacent but not directly about the seed entity.
- **Caller-provided budget** isolates retrieval from conversation state. The retrieval module does one thing — gather relevant context — and leaves context-window accounting to the layer that owns the prompt.
- **Section-priority truncation** preserves coherent wiki page sections rather than cutting mid-paragraph. The LLM receives complete sections in priority order.
- **INFERRED as default** is semantically correct for LLM-extracted relations. Adding the column with a server default makes the migration zero-touch for existing data.

## Consequences
- Retrieval's `score_chunks` function embeds chunks that lack embeddings on-the-fly (same pattern as `wiki/context.py`). This may add latency for the first query against fresh data.
- The CTE's bounding parameters are static per-deployment. Tuning them for specific graph topologies requires editing settings.
- `wiki/context.py` needs a mechanical import refactor but no behavioral change. The two context-assembly paths (synthesis vs query) share scoring primitives but remain separate pipelines.
- The `confidence_tag` column on `relations` is unused in v1 retrieval (all INFERRED, all equal), but it correctly represents the data and enables future confidence-gated retrieval or review workflows without a schema migration.
- Entity-only seeding means queries that match no entity (e.g., highly abstract questions without a graph presence) return empty results. The API layer must handle this UX gracefully.
