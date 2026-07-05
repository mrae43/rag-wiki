# ADR-0022: Query streaming via SSE

## Status
Accepted

## Context
`POST /api/v1/queries` (ADR-0013 §7) is synchronous: classify (LLM) → retrieve
(vector+CTE, fast) → generate answer (LLM, 10–30s) → return `QueryResponse`.
ADR-0013's consequences explicitly deferred "streaming query answers." The
`ChatProvider` protocol (ADR-0007) has only `complete()` returning a single
`CompletionResponse` — no streaming method; the OpenAI impl does not use
`stream=True`.

For the Interface App (ADR-0021), the query flow is the centerpiece. A
30-second synchronous request risks proxy/browser timeouts (Caddy, fetch),
gives no progress feedback, and loses work on navigation. The slow part is
isolated to the final LLM answer call; retrieval (vector search + CTE +
context assembly) is seconds. Refactoring the app from sync to streaming later
means rewriting its query-handling layer, so the decision belongs before app
development.

## Decision

### 1. Add `complete_stream()` to the `ChatProvider` protocol
A new async-generator method on `ChatProvider` (ADR-0007) that yields token
chunks (strings) as the model produces them. The OpenAI impl uses
`stream=True`. The caller accumulates chunks into the final text. The existing
`complete()` stays unchanged for non-streaming callers (MCP tools, simple
scripts). Additive to the protocol — implementations that don't support
streaming raise `NotImplementedError` or fall back to buffering `complete()`.

### 2. New `POST /api/v1/queries/stream` returning SSE
The streaming endpoint emits Server-Sent Events in order:
- `event: plan` — the `QueryPlan` (after classification).
- `event: retrieval` — the `RetrievalResult` (after vector+CTE; sources appear
  in seconds).
- `event: answer_chunk` — LLM token chunks (repeated, streamed as the model
  produces them).
- `event: done` — the full answer + final metadata.

The app renders sources immediately on `retrieval`, then streams the answer
token-by-token on `answer_chunk`. The sync `POST /queries` stays for MCP
(ADR-0016 tools return a single result) and curl.

### 3. Shared `run_query()` pipeline
Refactor the query pipeline (`api/routes/query.py`) into a shared `run_query()`
that yields events (plan, retrieval, answer_chunk, done). The sync endpoint
collects all events and returns `QueryResponse`; the streaming endpoint yields
them as SSE. No logic duplication between the two endpoints.

Rejected: negotiating via `Accept` header on one endpoint (less explicit,
harder to test, OpenAPI ambiguity). Rejected: a separate
`POST /queries/{id}/answer` after a sync retrieval (needs a server-side
query-result store to pass retrieval between calls — extra statefulness for no
v1 gain).

### 4. No async query job
Queries are frequent and interactive; enqueuing one job row per query misuses
ADR-0005's queue (designed for rare, heavy ingestion) and adds worker
round-trip latency + polling overhead. SSE streaming keeps the request on the
API worker and streams back — the right shape for interactive Q&A.

Rejected: async `query` job + poll `GET /queries/{job_id}` — wrong fit for the
queue's design intent; queries need low latency, not a queue.

## Rationale
- **SSE over WebSocket** — SSE is one-way (server→client), fits the query
  response shape, works through Caddy without websocket upgrade config, and is
  simpler for the app to consume (`EventSource`).
- **`complete_stream()` as a protocol method** — the streaming concern lives in
  the provider abstraction (ADR-0007), not in the route. The OpenAI impl
  already has `stream=True` available; exposing it through the protocol keeps
  callers decoupled from the SDK.
- **Shared `run_query()`** — the sync and streaming endpoints must not diverge
  in pipeline logic; a single event-yielding core fed by both prevents drift.
- **Sync endpoint stays** — MCP tools (ADR-0016) return a single result;
  curl/automation wants JSON. Streaming is an additive path, not a replacement.

## Consequences
- `rag_wiki/providers/base.py` gains `complete_stream()` on `ChatProvider`;
  `rag_wiki/providers/openai.py` implements it with `stream=True`. The retry
  wrapper (`providers/__init__.py`) gains a streaming-aware path.
- `rag_wiki/api/routes/query.py` gains a `POST /queries/stream` handler (SSE
  via FastAPI `StreamingResponse`); the existing sync handler is refactored to
  call the shared `run_query()`.
- `rag_wiki/retrieval/` and `rag_wiki/planner/` are unchanged; `run_query()`
  composes them.
- ADR-0016 MCP tools continue to use the sync `POST /queries` — no MCP change.
- ADR-0013's "streaming deferred" consequence is resolved; this ADR extends it.
- Stage-2: token-level cancellation (AbortController → backend cancellation
  token), answer regeneration with feedback — additive.
