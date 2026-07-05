# PRD-004: Graph Analysis Layer — Community Detection, PageRank, Cohesion, and Surprising Connections

## Problem Statement

The knowledge graph (`entities` + `relations` tables) supports BFS-style retrieval via a recursive CTE (`rag_wiki/retrieval/traversal.py`) but has no analytical layer. There is no way to discover Communities of related Entities, identify God Nodes (high-PageRank entities that serve as structural hubs), measure per-community Cohesion, or detect Surprising Connections that bridge otherwise disconnected neighborhoods of the graph.

The wiki synthesis pipeline and the Interface App currently have no structural awareness of graph topology beyond the per-entity traversal that retrieval provides. Concepts like "what are the major thematic clusters in this knowledge base?", "which entity is the most-connected hub?", and "which connections cross important boundaries?" require a separate analysis pass.

## Solution

A new `rag_wiki/analysis/` package that provides a **Graph View** — a transient in-memory `networkx.Graph` constructed from published entities and relations — runs community detection (Louvain), PageRank, cohesion scoring, and surprising-connection detection over it, then persists results to four new append-only snapshot tables keyed by `run_id`. The analysis is triggered manually via `rag-wiki analyze-graph`, runs on the existing worker, and is exposed through read-only API endpoints.

The analysis layer is additive: it does not modify `retrieval/traversal.py`, `wiki/context.py`, `graph/extraction.py`, or `graph/resolution.py`. Communities and PageRank scores are not fed back into the retrieval or synthesis paths in v1.

## User Stories

1. As a system operator, I want to run a single `rag-wiki analyze-graph` command that enqueues an analysis job, so that I can trigger community detection and PageRank without manual per-algorithm steps.

2. As a system operator, I want the analysis to run on the existing worker so that I do not need a separate runtime or process for graph computation.

3. As a system operator, I want every analysis run to produce an immutable snapshot of communities, memberships, cohesion scores, PageRank scores, and surprising connections, so that I can compare runs over time and see how the knowledge graph's structure evolved.

4. As a system operator, I want old analysis runs to be retained (never overwritten), so that I can diff structural changes between any two points in time.

5. As a client developer (Interface App), I want a `GET /api/v1/analysis/runs` endpoint that lists all analysis runs with their status, counts, and timestamps, so that the Interface App can display a run history.

6. As a client developer (Interface App), I want a `GET /api/v1/analysis/runs/{run_id}` endpoint that returns a single run's summary, so that the Interface App can show run details.

7. As a client developer (Interface App), I want a `GET /api/v1/analysis/runs/{run_id}/communities` endpoint that returns per-community summaries with member entities and their PageRank scores, so that the Interface App can render a community browser.

8. As a client developer (Interface App), I want a `GET /api/v1/analysis/runs/{run_id}/god-nodes` endpoint that returns the top-K entities by PageRank score for that run, so that the Interface App can display a "most-connected entities" widget.

9. As a client developer (Interface App), I want a `GET /api/v1/analysis/runs/{run_id}/surprising-connections` endpoint that returns the persisted top-K surprising inter-community connections, so that the Interface App can highlight unexpected bridges.

10. As a client developer (Interface App), I want `/api/v1/analysis/runs/latest/*` convenience aliases for all endpoints, so that I can always query the most recent completed run without looking up its ID.

11. As a developer, I want only `status='published'` entities and relations to be included in the analysis, so that `pending_review` rows (when they exist in future) do not affect the analytical output.

12. As a developer, I want self-loop relations (`source_entity_id = target_entity_id`) to be dropped with a logged warning, so that extraction anomalies do not distort PageRank or community detection.

13. As a developer, I want isolated entities (degree 0 after self-loop filtering) to be included so that they appear in community membership as singleton communities, so that the analysis always covers every published entity.

14. As a developer, I want the community detection algorithm to be Louvain (from `networkx.algorithms.community`) on an undirected, unweighted, parallel-edge-collapsed graph, so that communities reflect structural proximity without edge-type weighting complexity.

15. As a developer, I want PageRank to be calculated on a directed, weighted graph where parallel edges between the same source-target pair are collapsed with `weight = count`, so that frequently-co-occurring entity pairs influence centrality.

16. As a developer, I want per-community cohesion to be computed as internal edge density, so that communities with dense internal connections can be distinguished from loose aggregations.

17. As a developer, I want surprising-connection scoring to use the formula `pagerank(u) * pagerank(v) * (1 - mean(cohesion(u), cohesion(v)))` for each inter-community edge, ranked descending with configurable top-K, so that the result is deterministic, single-pass, and explainable.

18. As a developer, I want CPU-bound networkx computation to run inside `asyncio.to_thread()`, so that the event loop remains responsive for other jobs.

19. As a developer, I want `analysis_surprising_k` and `analysis_louvain_resolution` to be configurable via environment variables, so that I can tune output volume and community granularity without code changes.

20. As a developer, I want the new analysis module to be mirrored by a `tests/analysis/` directory that tests algorithms in isolation (pure networkx, no DB) and the runner end-to-end (with real DB), following the existing project test conventions.

## Implementation Decisions

### 1. New package: `rag_wiki/analysis/`

Four modules mirroring the `retrieval/` pattern:

| Module | Responsibility | Deep module? |
|---|---|---|
| `schemas.py` | Pydantic/dataclass models for analysis runs, community summaries, community members, surprising connections | Shallow — type definitions only |
| `view.py` | `GraphView` class that loads published entities+relations from the DB and constructs a `networkx.Graph` with two views (undirected for Louvain, directed weighted for PageRank) | Deep — simple `build(db) -> GraphView` interface, encapsulates self-loop drop, status filtering, parallel-edge collapse |
| `algorithms.py` | Pure functions: `detect_communities(graph) -> list[set]`, `compute_pagerank(graph) -> dict`, `compute_cohesion(graph, communities) -> dict`, `compute_surprising(graph, communities, pagerank, cohesion, k) -> list`. All take and return plain Python types | Deep — input `networkx.Graph`, output scalars/lists. Testable without any DB, async, or I/O |
| `runner.py` | `run_analysis(db, settings) -> int` orchestrator: calls `view.build()`, offloads algorithms via `asyncio.to_thread()`, inserts snapshot rows, returns `run_id` | Medium — ties view + algorithms + DB persistence |

A `rag_wiki/analysis/exceptions.py` module defines `AnalysisError(RagWikiError)` and `AnalysisViewError`, `AnalysisAlgorithmError` for domain-specific failures.

### 2. Graph View construction rules

- **Filter:** only `entities.status='published'` and `relations.status='published'` are loaded.
- **Two views on same data:**
  - **Undirected, unweighted, parallel-edges-collapsed** for Louvain community detection. Edge weight defaults to 1.0.
  - **Directed, weighted** for PageRank. Parallel relations between the same `(source → target)` pair collapse to one edge with `weight = count` of parallel relations.
- **Self-loops:** any `Relation` where `source_entity_id == target_entity_id` is dropped from both views. A per-run count is logged at WARN.
- **Isolated entities:** entities with degree 0 after self-loop removal are included as nodes. They will appear as singleton communities with cohesion 0.0 and PageRank ≈ 1/n.

### 3. Snapshot tables (new `rag_wiki/db/models/analysis.py`)

| Table | Key columns | Notes |
|---|---|---|
| `graph_analysis_runs` | `id` (UUID, PK), `job_id` (UUID FK→jobs SET NULL), `started_at`, `completed_at`, `status`, `algorithm` (default "louvain"), `params_json` (JSONB), `entities_count`, `relations_count`, `communities_count` | `TimestampMixin` included for created_at/updated_at. |
| `community_summaries` | `run_id` (UUID FK→graph_analysis_runs CASCADE), `community_id` (int), `size` (int), `cohesion_score` (float) | Per-community scalars; community_id is local to the run. |
| `community_members` | `run_id` (UUID FK→graph_analysis_runs CASCADE), `community_id` (int), `entity_id` (UUID FK→entities CASCADE), `pagerank_score` (float) | One row per (run, entity). Isolated entities become singleton members. |
| `surprising_connections` | `run_id` (UUID FK→graph_analysis_runs CASCADE), `source_entity_id` (UUID), `target_entity_id` (UUID), `score` (float), `reason` (text) | Top-K per run; K from `analysis_surprising_k` setting. |

`run_id` FK CASCADE: deleting a run deletes its snapshots. Latest view is queried as `WHERE run_id = (SELECT MAX(id) FROM graph_analysis_runs WHERE status='completed')`.

All four models inherit from `Base` and use `UUIDMixin`/`TimestampMixin` where appropriate.

### 4. CLI: `rag-wiki analyze-graph`

New Typer subcommand on the existing `app` CLI. Enqueues one `analyze_graph` job via the existing `enqueue()` interface. No payload needed.

### 5. Worker dispatch

The `worker.py` dispatch table gains one entry:
```python
elif job.job_type == "analyze_graph":
    from rag_wiki.analysis.runner import run_analysis
    await run_analysis(db, settings)
```

The worker does not need a chat provider or embedding provider for this job type. The `analyze_graph` handler only needs a DB session and settings.

### 6. API routes (new `rag_wiki/api/routes/analysis.py`)

| Endpoint | Response | Notes |
|---|---|---|
| `GET /api/v1/analysis/runs` | `PaginatedListEnvelope[RunSummary]` | All runs, newest first |
| `GET /api/v1/analysis/runs/{run_id}` | `RunDetail` | Single run with aggregate counts |
| `GET /api/v1/analysis/runs/{run_id}/communities` | `PaginatedListEnvelope[CommunityDetail]` | Communities with member entities + pagerank scores |
| `GET /api/v1/analysis/runs/{run_id}/god-nodes` | `PaginatedListEnvelope[GodNode]` | Top-K entities by pagerank |
| `GET /api/v1/analysis/runs/{run_id}/surprising-connections` | `PaginatedListEnvelope[SurprisingConnection]` | Persisted top-K connections |
| `GET /api/v1/analysis/runs/latest/communities` | Same as above | Aliased to max(id) completed run |
| `GET /api/v1/analysis/runs/latest/god-nodes` | Same as above | Aliased |
| `GET /api/v1/analysis/runs/latest/surprising-connections` | Same as above | Aliased |

Response models use Pydantic's `ConfigDict(from_attributes=True)`. The latest-alias endpoints are implemented as a sub-route that internally looks up `WHERE status='completed' ORDER BY id DESC LIMIT 1` and then delegates to the `{run_id}` handler. They return a 404 if no completed run exists.

### 7. Settings

Two new settings on `Settings`:

```python
analysis_surprising_k: int = 50
analysis_louvain_resolution: float = 1.0
```

### 8. Dependency

`networkx>=3.4` added to `[project.dependencies]` in `pyproject.toml`. Already present in `uv.lock` as a transitive dep; making it explicit.

### 9. Existing modules untouched

- `retrieval/traversal.py` — unchanged
- `wiki/context.py` — unchanged
- `graph/extraction.py` — unchanged
- `graph/resolution.py` — unchanged

### 10. MCP tools deferred

Per ADR-0020, MCP analysis tools are Phase B — not built in this PRD. The API routes exist; MCP wrapping is additive later.

## Testing Decisions

### Test philosophy

A good test for the analysis layer:
- Exercises external behavior (input → output), not internal implementation
- For algorithms: feeds a hand-crafted `networkx.Graph` and asserts deterministic output (Louvain on a tiny graph, PageRank on a known structure, cohesion formula)
- For the view: seeds a DB with known entities/relations, builds the `GraphView`, asserts correct nodes, edges, weights, directionality, self-loop filtering, isolated entity inclusion
- For the runner: seeds a DB, calls `run_analysis()`, asserts the four snapshot tables contain the expected rows with correct counts and scores
- For API routes: seeds a run in the DB, calls each endpoint with `httpx.AsyncClient`, asserts response shape, pagination, and field correctness

### Modules to test

| Test file | What it tests | Prior art |
|---|---|---|
| `tests/analysis/test_algorithms.py` | `detect_communities`, `compute_pagerank`, `compute_cohesion`, `compute_surprising` in isolation | `tests/graph/test_extraction.py` (pure function tests), `tests/retrieval/test_scoring.py` (scoring function tests) |
| `tests/analysis/test_view.py` | `GraphView.build()` — entity/relation filtering, self-loop drop, isolated entity, directed vs undirected construction | `tests/retrieval/test_traversal.py` (graph construction tests) |
| `tests/analysis/test_runner.py` | `run_analysis()` end-to-end with real DB — verifies snapshot table contents | `tests/retrieval/test_orchestrator.py` (full pipeline test), `tests/ingest/test_pipeline.py` (pipeline tests) |
| `tests/api/routes/test_analysis.py` | All 6+ API endpoints, 404 for missing run, empty latest | `tests/api/routes/test_entity.py` (API route tests with httpx) |

### What makes algorithms tests deep and isolated

The algorithm functions take `networkx.Graph` objects and return plain Python types. No DB, no async, no I/O. Tests can construct hand-crafted graphs with known properties:

- A triangle graph → one community of size 3 with cohesion 1.0
- A 4-node barbell → two communities of size 2
- A disconnected graph → two communities (possibly plus isolates)
- PageRank on a star graph → center node has highest score
- Surprising on two dense communities bridged by one low-pagerank edge → low surprise score vs two loose communities bridged by two high-pagerank entities → high surprise score

### Not tested in this PRD

- MCP tools (Phase B)
- Automatic periodic scheduling of analysis runs (future enhancement)
- Incremental cluster updates (future enhancement)
- Community label synthesis via LLM (future enhancement)

## Out of Scope

- MCP tools for analysis data (Phase B — deferred until Interface App confirms API response shapes are stable)
- Automatic analysis trigger after every ingest (manual CLI + worker only)
- Incremental cluster updates (full re-cluster per run)
- Community label or summary synthesis via LLM (names like "Community 0" only)
- Feeding communities or PageRank scores back into retrieval or wiki synthesis (v1 is query-surface only)
- Metric detail like dispersion scoring for surprising connections (the chosen formula is intentionally simpler)
- Graph visualization or rendering (the Interface App owns that)
- Any mutation API on analysis data (read-only endpoints only)
- Graph analysis for non-published entities/relations

## Further Notes

- The ADR-0020 is the authoritative decision record for this feature. This PRD translates those decisions into implementation and testing plans. If there is any conflict, ADR-0020 takes precedence.
- The `asyncio.to_thread()` approach for networkx computation was chosen over a subprocess worker to keep one event loop, one logging context, and one async session pool. This is appropriate at v1 scale; a subprocess pool can be revisited if graph sizes grow.
- The latest-run convenience endpoints avoid a `{"run_id": "<latest>"}` sentinel value pattern — they do an explicit query for the most recent completed run, which keeps the route logic simple and avoids special-case handling in the run-specific routes.
- The `analysis_louvain_resolution` setting is a pass-through to `networkx.algorithms.community.louvain_communities` resolution parameter. Default 1.0 matches networkx's default.
