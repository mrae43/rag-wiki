# ADR-0021: Interface App readiness — Backend contract

## Status
Accepted

## Context

> **The Interface App is a separate project** — its own repository, likely
> TypeScript + Vue/React, deployed independently. It is **not built inside the
> `rag_wiki` repo**. This repo produces only the headless Backend (FastAPI API +
> MCP server + worker). The two systems share only PostgreSQL (readable and
> writable by both). No frontend code, no UI templates, no user-session
> middleware, no JavaScript/TypeScript ever enters this repo. The Interface App
> calls this Backend's HTTP API server-side over Tailscale.

ADR-0017 establishes Stage-1 deployment as a headless Backend behind Caddy on a
Tailscale tailnet, with the future Interface App calling the Backend's API
server-side and owning authentication. CONTEXT.md defines the Interface App role
("renders the wiki for end users and owns authentication; calls the Backend's API
server-side"). But several concrete contracts the Interface App depends on are
not yet recorded:

- Does the Backend track which user triggered an upload/job/export, or is it
  user-agnostic?
- Which transport does the Interface App use — HTTP API, MCP, or both?
- How does the app learn about updates (job progress, wiki re-synthesis,
  analysis completion)?
- The app needs a search box ("find the page about X") distinct from the query
  box ("ask the wiki"); is there a search endpoint?
- The app renders a Graph Canvas (interactive force-directed visualization);
  what graph-data endpoint does it consume?

These decisions are coupled: they collectively define what the Backend must
expose *before* the Interface App is built, and several touch the schema (job
progress) or add routes (search, graph). Resolving them piecemeal during app
development would force mid-build Backend changes.

## Decision

### 1. Backend is user-agnostic; the Interface App owns all user-specific state
No `created_by` columns on `sources`, `jobs`, or any table. No `X-User-Id`
header. No `users` table in the Backend. The Interface App maintains its own
data store for user→source_id mappings, query history, bookmarks, and UI state.
The Backend's `/sources` and `/jobs` list endpoints stay flat (no user filter);
the app fetches by the IDs it remembers per user. This preserves ADR-0004 (no
tenant/user columns in v1), ADR-0017 §1 (trusted-clients-only), and ADR-0013 §3
(no auth in v1).

Rejected: adding a nullable `created_by` text column populated from an
`X-User-Id` header (trusted-client). Tempting — it's additive and unblocks "my
uploads" without the app tracking IDs — but it stores unvalidated user IDs in
the Backend (the Backend can't validate them under the trusted-client model),
and it splits user state across two stores (Backend attribution + app
auth/history), which is the worst of both. If user attribution becomes a real
Backend need, a future ADR adds it additively.

### 2. Deployed surface is HTTP API only; MCP stays stdio-only
Prod compose runs `db`, `api`, `worker`, `caddy` — no MCP service (confirms
ADR-0017 §6). The Interface App is a server-side HTTP client over Tailscale.
MCP remains for agent hosts (Obsidian, Claude Desktop) spawned locally by the
operator via stdio; MCP HTTP stays loopback-locked. A web app cannot usefully
drive MCP stdio (a subprocess per request is absurd), and MCP HTTP is
loopback-only so a deployed app on another host can't reach it. Deploying an
MCP HTTP service in prod is a Stage-2 additive move (new compose service +
relaxing the loopback validator), not needed for the app and not justified for
a no-auth Stage-1 system.

### 3. Poll-based updates; no real-time push in v1
The app polls `GET /api/v1/jobs/{id}` (with the `progress` field from §4) at
~1–2s while a job is in flight, and re-fetches wiki pages / graph / analysis on
demand (navigation or refresh). No long-lived SSE/WebSocket events channel, no
Postgres LISTEN/NOTIFY, no webhooks. The query SSE stream (`/queries/stream`,
ADR-0022) is per-request and not reused as a general events channel. Aligns
with ADR-0017's minimal ops floor. Real-time push is Stage-2 (additive — a new
`/events` SSE endpoint, no rewrite).

### 4. Job progress via a `progress` JSONB column on `jobs`
Add a nullable `progress` JSONB column to the `jobs` table. The worker writes
`{step, steps_done, steps_total, percent, step_label}` between stages. Step
sets are job-type-specific and opaque to the queue (just JSONB): ingest =
parse/chunk/embed/extract/resolve/synthesize; export =
render/rewrite/manifest/indexes/log; analyze =
load/cluster/pagerank/cohesion/surprising/persist; generate_output =
retrieve/synthesize/render. `JobResponse` gains a `progress` field. This is the
ADR-0010 `status`-column pattern again (add now for future use). The app
renders a stepper from `progress.step`.

Rejected: a `job_steps` child table (one row per stage with timing/retries) —
over-engineering for v1; justified only if per-stage timing is a real app
feature. Rejected: status-only (no progress) — stores up a later
worker-instrumentation + migration + app-rewrite.

### 5. Search endpoint: `GET /api/v1/search?q=` (full-text, no LLM)
A unified full-text search over `wiki_pages` (title + content) and `entities`
(name + description), using Postgres `tsvector` + `websearch_to_tsquery` +
`ts_rank`. Returns a ranked mixed list (pages + entities, each tagged by
type). No embedding call, no LLM — cheap and fast. Distinct from
`POST /queries` (LLM retrieval + answer): search is for "find the page about
X"; query is for "ask the wiki a question." Additive to ADR-0013.

Rejected: reusing `POST /queries?generate_answer=false` for search — runs the
full retrieval pipeline + an embedding call per search, and the
RetrievalResult shape (seeds/subgraph/chunks) is wrong for a search-results
list. Rejected: vector search over entities — semantic but needs an embedding
call per search; slower than FTS for a search box, and semantic match is
already covered by `/queries`.

### 6. Graph Canvas data: `GET /api/v1/graph` (whole-graph dump)
Returns `{nodes: [{id, name, type, community_id?, pagerank?}], edges:
[{source, target, type, weight?}]}` over all `status='published'` entities and
relations. `community_id` and `pagerank` are enriched from the latest completed
Graph Analysis Run (ADR-0020); they are NULL if no run exists (the canvas
renders without colors/sizing, still functional). The Interface App does the
force-directed layout (sigma.js / react-force-graph / d3-force); the Backend
serves data only. Fine for Stage-1 single-tenant scale (hundreds–low thousands
of entities render acceptably in a WebGL canvas).

Rejected: neighborhood-only (`?seed=&depth=N`) — scales but loses the
"graphify overview" the app wants; better as a Stage-2 drill-down. Rejected:
community-scoped only — loses inter-community edges. Rejected: summary +
drill-down (supernodes) from day one — the right answer for large graphs but
over-built for Stage-1 scale; it's additive on top of this endpoint when graph
size forces it.

## Rationale
- **User-agnostic Backend** keeps the trust model clean (ADR-0017): the Backend
  authenticates nothing, the app owns auth. Splitting user state across two
  stores (Backend attribution + app history) is the worst of both; a future
  `created_by` ADR is additive if needed.
- **HTTP API only** — a web app can't drive MCP stdio, and MCP HTTP is
  loopback-locked. Deploying an MCP HTTP service widens the attack surface of a
  no-auth Stage-1 system for no app benefit.
- **Poll + on-demand re-fetch** matches ADR-0017's minimal ops floor; the
  Backend already exposes everything the app needs. Real-time push is additive
  Stage-2.
- **`progress` JSONB** is the cheapest thing that lets the app render a
  stepper; the worker knows its current stage, it just needs to write it down.
- **FTS search** is the cheap, fast, no-LLM path for a browse-search box;
  `/queries` already covers semantic Q&A.
- **Whole-graph dump** is sufficient at Stage-1 scale and makes ADR-0020
  enrichment additive rather than a prerequisite.

## Consequences
- `rag_wiki/db/models/jobs.py` gains a nullable `progress` JSONB column; one
  autogenerate migration. Worker stages write to it between steps.
- `rag_wiki/api/routes/job.py` `JobResponse` gains a `progress` field.
- New route `rag_wiki/api/routes/search.py` (`GET /api/v1/search`); one router
  addition to `rag_wiki/api/router.py`.
- New route `rag_wiki/api/routes/graph.py` (`GET /api/v1/graph`); one router
  addition. Reuses `entities`/`relations` models; left-joins the latest
  ADR-0020 run snapshots for enrichment.
- No schema change for user identity (no `created_by`); the Interface App
  carries its own user DB.
- Stage-2 enhancements are additive: `created_by` columns (if needed), an MCP
  HTTP compose service, an `/events` SSE channel, a `job_steps` table,
  neighborhood/drill-down graph endpoints.
- This ADR does not touch ADR-0004/0013/0017 — it confirms and concretizes
  them for the Interface App case.
