# ADR-0020: Graph analysis layer — transient networkx Graph View + per-run snapshot tables

## Status
Accepted

## Context
The knowledge graph (`entities` + `relations`, ADR-0001) supports BFS-style
retrieval via a recursive CTE (`rag_wiki/retrieval/traversal.py`, ADR-0009) but
has no equivalent of graphify's analytical layer: community detection, PageRank
("god nodes"), cohesion scoring, and cross-community "surprising connections."
Those analyses let the wiki surface structural artifacts the CTE alone cannot —
concept clusters, the most-referred-to entities, and notable bridges between
otherwise disconnected neighborhoods.

ADR-0001 mandates Postgres as the only backend — no separate graph DB, no
separate vector store. The natural tension this ADR resolves: community
detection and PageRank are graph algorithms, but the project's architectural
commitment is relational-only persistence. graphify itself uses networkx + JSON
files (a separate store) — adopting its tooling without violating ADR-0001
requires drawing the line between *transient computation* and *durable storage*
explicitly.

ADR-0005 (job queue), ADR-0013 (FastAPI surface), ADR-0016 (MCP wrapper), and
ADR-0017 (Stage-1 trusted-clients, no inbound auth) all inform the run/trigger
and consumer-surface decisions.

## Decision

### 1. Transient networkx Graph View, not a graph DB
A new module `rag_wiki/analysis/` provides a **Graph View** — a transient,
in-memory `networkx.Graph` constructed from `status='published'` entities and
relations, used for the duration of one analysis run, then discarded. `networkx`
becomes a direct dependency of `rag-wiki` (it is already present as a transitive
dep). ADR-0001 is not violated: networkx is a computation engine, not a store;
no knowledge leaves Postgres and no knowledge is durably kept in networkx.

### 2. Coexist with the existing graph-access code
The recursive CTE in `retrieval/traversal.py` (ADR-0009) and the graph queries
in `wiki/context.py` (ADR-0006) are **untouched**. The analysis layer adds
new capability; it does not refactor retrieval or synthesis. Communities and
PageRank scores are not fed back into the existing retrieval/wiki paths in v1 —
those consumers stay as they are. Future wiki/retrieval enrichment is additive.

### 3. Four new snapshot tables; no columns on `entities`
All analysis outputs are persisted in new append-only tables keyed by `run_id`:

| Table | Columns | Notes |
|---|---|---|
| `graph_analysis_runs` | `id, job_id, started_at, completed_at, status, algorithm, params_json, entities_count, relations_count, communities_count` | One row per run. `job_id` FK ondelete SET NULL (the run outlives the job row). |
| `community_summaries` | `run_id, community_id, size, cohesion_score` | Per-community scalars; community_id is an int local to the run. |
| `community_members` | `run_id, community_id, entity_id, pagerank_score` | Projections: latest communities for an entity = JOIN against the latest completed run. One row per (run, entity); isolated entities become singleton members. |
| `surprising_connections` | `run_id, source_entity_id, target_entity_id, score, reason` | Top-K per run; `K` from `analysis_surprising_k` setting. |

`run_id` FKs CASCADE: deleting a run deletes its snapshot. No `community_id` /
`pagerank_score` column is added to `entities` — that path is closed to keep
"Postgres is the relational source of truth, last-write-wins would lose run
history." Latest view is queried as `WHERE run_id = (SELECT MAX(id) FROM
graph_analysis_runs WHERE status='completed')`.

Migrations are generated via `alembic revision --autogenerate` (never
hand-written, per `AGENTS.md`).

### 4. Manual CLI trigger, full re-cluster per run
`rag-wiki analyze-graph` (Typer, enrolled next to existing `ingest`/`export`)
enqueues one `analyze_graph` job via the existing `enqueue()` (ADR-0005). The
worker runs the full pipeline: load published entities+relations → construct the
Graph View → cluster (Louvain) + PageRank + cohesion + surprising → insert one
run's rows into the four tables. No auto-enqueue after ingest; the analysis is
not on any critical path. Old runs are kept; re-running never overwrites — every
run is a new snapshot row.

### 5. Graph construction rules
- **Filter:** only `entities.status='published'` and
  `relations.status='published'` (aligns with ADR-0010; pending_review rows are
  not yet written in v1, but the filter is in place for when they are).
- **Two views of the same loaded data:**
  - **Undirected, unweighted, parallel-edges-collapsed** for Louvain
    (`nx.community.louvain_communities`). Edge `weight=1.0`. Louvain wants
    undirected graphs.
  - **Directed, weighted** for PageRank (`nx.pagerank`). Parallel relations
    between the same `source → target` pair collapse to one edge with
    `weight = count` of parallel relations. PageRank wants direction and weight.
- **Self-loops** (`source_entity_id = target_entity_id`) are dropped and logged
  at WARN with a per-run count — these are extraction anomalies.
- **Isolated entities** (degree 0 after self-loop drop) are included as singleton
  Communities with cohesion 0.0 and pagerank ≈ 1/n. graphify is similarly
  inclusive; the cost is one extra row per isolated entity.

### 6. Algorithms
- **Community detection:** `nx.community.louvain_communities` on the undirected
  collapsed graph. community_id is the index in the returned list, scoped to one
  run (different runs may assign the same id to different groupings).
- **Cohesion:** internal edge density
  `2 · E_internal / (n · (n − 1))`, with `cohesion = 0.0` for `n < 2`. Stored on
  `community_summaries`. Attaches to Louvain output, not a separate clustering
  algorithm.
- **God Nodes:** PageRank over the directed weighted graph; the per-entity score
  is persisted on `community_members.pagerank_score`. Top-K queries ("god nodes
  for a run") are `ORDER BY pagerank_score DESC LIMIT K`.
- **Surprising Connections:** per inter-community edge `(u, v)` on the directed
  graph where `community(u) != community(v)`:

  ```
  surprise(u, v) = pagerank(u) * pagerank(v) * (1 - mean(cohesion(u), cohesion(v)))
  ```

  Rank by `surprise` desc, persist top `analysis_surprising_k` (default 50)
  rows to `surprising_connections` with a one-line `reason` string ("bridge
  between community A and community B"). Deterministic, single-pass, explainable.

### 7. Worker concurrency: `asyncio.to_thread`
The worker is single-event-loop async. networkx clustering and PageRank are
CPU-bound and sync. The runner calls the sync algorithm functions inside
`await asyncio.to_thread(...)`, then resumes the async context to insert the
snapshot rows in bulk. This keeps the event loop responsive for other jobs and
preserves async session usage for writes.

### 8. FastAPI read endpoints (Phase A)
The analysis output is **not** internal-only; it is surfaced through the
existing API surface (ADR-0013) as new read-only routes under
`rag_wiki/api/routes/analysis.py`, mirroring the existing route-module patterns.

| Endpoint | Returns |
|---|---|
| `GET /api/analysis/runs` | All runs (id, status, counts, timestamps) |
| `GET /api/analysis/runs/{run_id}` | One run with summary counts |
| `GET /api/analysis/runs/{run_id}/communities` | Per-community summaries + members for that run |
| `GET /api/analysis/runs/{run_id}/god-nodes` | Top-K entities by pagerank for that run |
| `GET /api/analysis/runs/{run_id}/surprising-connections` | Persisted top-K for that run |
| `GET /api/analysis/runs/latest/...` aliases | Convenience — equivalent to the highest completed run_id |

Reversing the earlier "internal-only" stance: the analysis data is a
first-class user-visible artifact the Interface App (CONTEXT.md Roles) will
render. Locking "no API" in v1 would have shipped the four snapshot tables as
dead-code-for-now and made algorithm tuning during development impossible
without browser/curl visibility. Stage-1 (ADR-0017) ships the Backend
unauthenticated behind a reverse proxy, so adding read endpoints costs nothing
in auth code.

### 9. MCP tools deferred to Phase B
Per ADR-0016, MCP tools should mirror the FastAPI surface. They are **not**
built in this ADR — Phase B, after the Interface App confirms the response
shapes are stable and asks for agent-host access. Adding them later is
additive: the routes already exist, MCP tools just proxy them through the
existing `mcp/tools.py` pattern.

## Rationale
- **networkx is a library, not a backend** — graphify uses networkx + JSON
  files; we adopt the library while keeping the storage boundary at Postgres.
  A future reader seeing `networkx` in `pyproject.toml` would otherwise violate
  ADR-0001 in spirit if this ADR did not explain that it is transient only.
- **Snapshot tables over mutable entity columns** — write-per-run preserves
  run-to-run history (the `entity_merge_log` pattern from ADR-0008, generalised);
  full re-cluster per run is cheap at v1 scale and the snapshot tables make it
  diffable. Mutable columns would be simpler schema but lose history and
  tighten the reversibility surface (a column drop is a heavy migration).
- **`asyncio.to_thread` over a subprocess worker** — keeps one event loop,
  one structured-logging context, one async session pool. A subprocess pool is
  worth revisiting only when graph sizes make in-process clustering block
  unrelated jobs — not at v1 scale.
- **Manual CLI rather than auto-enqueue after ingest** — the analysis does not
  gate any consumer in v1's wiki synthesis pipeline; auto-enqueue would couple
  two systems and add ingestion-latency blast radius for speculative freshness.
  Matches graphify's manual-run model.
- **FastAPI endpoints in v1** — the Backend→Interface App relationship in
  CONTEXT.md means a backend feature with no API is unusable by its primary
  consumer. Building read endpoints in the same work as the engine also gives
  the developer the visible-curl loop needed to tune cohesion thresholds.
- **Surprising-connection scoring** — chosen formula is computable in a single
  pass over directed inter-community edges, deterministic, and trivially
  explainable in a tooltip; dispersion (graphify's actual metric) is more
  novel-sounding but is `O(deg²)` per edge and harder to defend at scale.

## Consequences
- New direct dep `networkx>=3.4` in `pyproject.toml` (already in `uv.lock` as
  transitive; explicit declaration documents intent).
- New `rag_wiki/analysis/` package: `view.py`, `algorithms.py`, `runner.py`,
  `schemas.py`, `__init__.py`. Mirrors `retrieval/` and `graph/` layouts.
  Test mirror: `tests/analysis/` (per AGENTS.md).
- New routes module `rag_wiki/api/routes/analysis.py` and its tests in
  `tests/api/`. Adds one router to `rag_wiki/api/router.py`.
- Four new tables generated via one autogenerate migration. Schemas live in
  `rag_wiki/db/models/analysis.py` (new).
- New CLI subcommand `rag-wiki analyze-graph`; new job kind `analyze_graph`;
  worker dispatch table gains one entry.
- New settings: `analysis_surprising_k` (int, default 50),
  `analysis_louvain_resolution` (float, default 1.0 — pass-through to networkx).
- Existing modules are untouched: `retrieval/traversal.py`, `wiki/context.py`,
  `graph/extraction.py`, `graph/resolution.py`. The contract is additive.
- Stage-2 enhancements are all additive: MCP tools (Phase B), periodic trigger
  via the existing jobs `scheduled_at` column, incremental re-cluster keeping
  edge-fixed communities for unchanged subgraphs, enrichment of wiki synthesis
  with community labels. None of these require revisiting the four snapshot
  tables or the Graph View abstraction.
- The decision is reversible in the way that matters: adding consumer surfaces
  does not require touching the engine; choosing a different surprising-connection
  scoring is a single-function code change. The schema shape and the
  Graph-View-as-transient commitment are the parts that genuinely lock in.