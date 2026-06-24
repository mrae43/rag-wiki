# ADR-0014: Ingest-time and retrieval-time planner

## Status

Accepted

## Context

The ingest pipeline and retrieval pipeline each had a blind spot: every source
got the same parser treatment (MIME-guess dispatch); every query got the same
retrieval depth (vector seed + graph traversal). Without a planning step before
tool invocation, the system had no explicit, inspectable, logged decision about
*which* parser or *how deep* to retrieve.

Proposals in `planning-step.md` identified two planning moments:

| Moment | Question | Current behavior |
|---|---|---|
| Ingest | What kind of source is this, which parser? | `mimetypes.guess_type()` → `MIME_DISPATCH` dict |
| Retrieval | What kind of query, how deep? | None — every query gets entity-only seeding + 2-hop traversal |

The following design dimensions had viable alternatives and were resolved during
a structured design review:

1. **Parser enumeration** — which parsers exist as canonical values
2. **Ingest planner classification mechanism** — rule-based, LLM, or both
3. **Ingest planner timing** — synchronous during upload or asynchronous in worker
4. **Query planner classification mechanism** — rule-based, LLM, or both
5. **Query type taxonomy** — which types exist for v1
6. **Multi-type query handling** — compound type vs depth escalation
7. **Plan persistence** — where plans live in Postgres
8. **Confidence threshold ownership** — env vars vs hardcoded vs DB
9. **Planner re-run triggers** — session awareness

## Decision

### 1. Canonical parser enumeration

The existing code had four ad-hoc engine strings (`pdf`, `ocr`, `simple`,
`unstructured`) but `settings.py` used a different set (`lightweight`,
`mineru`). These are unified into a single enum:

| Enum value | Module | Description |
|---|---|---|
| `pdf` | `rag_wiki.ingest.parsers.pdf` | PyMuPDF-based PDF parser with optional OCR flag |
| `simple` | `rag_wiki.ingest.parsers.simple` | Plain text / markdown line-by-line |
| `unstructured` | `rag_wiki.ingest.parsers.unstructured` | `unstructured` library for mixed-format docs |
| `mineru` | *not yet implemented* | MinerU multimodal (images, tables, equations) |

`ocr` is removed as a separate engine — it was only invoked via
`metadata["parser"]` override and is semantically a flag on PDF parsing, not a
parser in its own right.

The `settings.parser` field (`"lightweight"` / `"mineru"`) is replaced by a
builder or factory that reads from the enum.

### 2. Ingest planner — rule-based classification

The ingest planner classifies sources along four dimensions without an LLM call:

| Dimension | Derivation | Source |
|---|---|---|
| Media type | MIME guess + file extension | Filesystem |
| Density | File size thresholds in settings | `os.path.getsize()` |
| Structure | MIME → mapping in code | Hardcoded lookup |
| Content domain | Defaults to `"unknown"` | None in v1 |

Content-domain classification via a cheap LLM call is flagged as a v2
enhancement.

The planner produces a `SourcePlan` object before any parser is invoked:

```
SourcePlan:
  source_id
  detected_type            (media type string)
  detected_structure       (structured | semi-structured | unstructured)
  selected_parser          (enum value from set above)
  chunking_strategy        (derived from selected_parser)
  confidence               (float 0-1, always 1.0 for rule-based)
  fallback_parser          (enum value, always "simple" for rule-based)
  rationale                (one-sentence summary)
  planner_version          (semver string)
```

### 3. Ingest planner timing — synchronous during upload

The planner runs in `POST /sources` before the ingest job is enqueued. Rationale:

- Negligible latency (~ms of rule-based logic added to the upload path).
- The plan exists in Postgres before the worker sees the job.
- The worker reads the plan from `sources.source_plan` instead of guessing.
- No new job types or queue orchestration needed.

### 4. Query planner — hybrid classification (LLM primary, rule fallback)

The query planner uses two paths:

**Primary path (LLM):** A cheap model (`gpt-4o-mini`, temperature 0, tight token
budget of 50 max for output) with structured output (Pydantic) to classify the
query into one of the four v1 types. Timeout 500ms. Results are cached in a
small LRU for exact repeat queries within the same process lifetime.

**Fallback path (rules):** When the LLM call fails (timeout, rate-limit,
network error), simple keyword/regex rules produce a classification:

| Keyword signal | Classified type |
|---|---|
| `"what is"`, `"define"`, `"who is"` | factual_lookup |
| `"how does.*relat"`, `"connect"` | relationship_query |
| `"summar"`, `"overview"`, `"give me an overview"` | summarization |
| `"compare"`, `"difference"` | comparison |
| Default | factual_lookup |

**Explicit override:** The API accepts an optional `query_type` parameter to
bypass classification entirely when the caller already knows the type.

### 5. Query type taxonomy — four types for v1

"Navigational" (`"Show me all documents about X"`) is dropped for v1. It
requires a fundamentally different response path (list, not context) and users
can already achieve it via `GET /sources?filename=` and `GET /entities?name=`.

| Type | Retrieval strategy |
|---|---|
| factual_lookup | Shallow: top-k vector search (entity-only), 1 seed, prioritize wiki pages |
| relationship_query | Deep: 2+ seeds, graph traversal, bidirectional CTE |
| summarization | Broad: many seeds, no traversal, aggregate chunks |
| comparison | Parallel shallow: 1 seed per entity, merge results |

### 6. Multi-type queries — single-type with depth escalation

When a query spans multiple types (e.g. `"Summarize how X relates to Y"` spans
summarization and relationship_query), the planner classifies to exactly one
type. When confidence is ambiguous (< 0.7), it escalates to the deeper
retrieval strategy (relationship_query > summarization > comparison >
factual_lookup). No compound types exist in v1.

### 7. Plan persistence — dual approach

**Ingest plan:** A `source_plan` JSONB column on the `sources` table. The plan
is co-located with the source, no extra joins, and JSONB allows the schema to
evolve without migrations.

**Query plan:** A new `query_plans` table with the following columns:

| Column | Type | Purpose |
|---|---|---|
| `id` | UUID PK | Plan identifier |
| `raw_query` | TEXT | Original user input |
| `classified_type` | TEXT | One of the four v1 types |
| `retrieval_depth` | TEXT | shallow / medium / deep |
| `seed_count` | INTEGER | Expected number of seeds |
| `termination_condition` | TEXT | What signals "done" |
| `confidence` | FLOAT | 0-1 |
| `classification_source` | TEXT | `"llm"` / `"rule"` / `"explicit"` |
| `model_used` | TEXT | Nullable, which model classified it |
| `rationale` | TEXT | One-sentence reason |
| `planner_version` | TEXT | Semver string |
| `created_at` | TIMESTAMPTZ | Auto-set |
| `ttl_days` | INTEGER | Auto-expiry (default 30, cleanup by background job) |

Query plans are inserted synchronously during `POST /queries` before retrieval
begins. They are never updated; each query call produces its own plan.

### 8. Confidence thresholds — environment variables

Three env vars in `settings.py`:

| Setting | Default | Behavior |
|---|---|---|
| `planner_confidence_high` | 0.8 | Confidence ≥ threshold → proceed with selected plan |
| `planner_confidence_low` | 0.5 | Confidence in [low, high) → proceed, log flag, escalate depth |
| `planner_confidence_minimum` | 0.5 | Confidence < threshold → halt, require explicit override |

For the rule-based ingest planner, confidence is always 1.0. For the query
planner, confidence comes from the LLM's structured output. The fallback rule
path assigns fixed confidence values (0.9 for clear keyword matches, 0.3 for
default catch-all).

### 9. Planner re-run — per-call, no session awareness

The planner runs once per API call. Identical queries in separate requests get
new plans. The LRU cache handles exact duplicates within the same process, but
there is no session-level plan reuse or conversation awareness. This is the
caller's responsibility (the query API route / session layer).

## Rationale

- **Rule-based ingest planner** is sufficient because MIME type + file size
  already select the right parser for the vast majority of cases. Content domain
  classification adds little value when the parser dispatch is orthogonal to
  content type (PDF parser handles all PDFs regardless of domain).
- **Sync ingest planner** avoids introducing a new job type for planning alone.
  The planner is on the critical path anyway; adding a queue hop would increase
  latency for no benefit.
- **Hybrid query classifier** provides the accuracy of an LLM for the common
  path (the LLM correctly classifies rephrased and novel queries) with the
  robustness of rules for degraded conditions. Rules alone would require
  constant maintenance; LLM alone would be a single point of failure.
- **Single-type with depth escalation** avoids compound types while still
  producing good results — deeper retrieval subsumes shallower, so erring on
  the side of depth rarely hurts answer quality.
- **JSONB on sources** keeps the ingest plan tightly coupled to its source.
  Normalizing into a separate table would require a join for every source read,
  and JSONB gives us schema flexibility during the v1 refinement period.
- **New query_plans table** makes plans inspectable, evaluable, and auditable.
  A TTL prevents unbounded growth. Log-only would lose the ability to
  correlate plans with retrieval quality in post-hoc analysis.
- **Env-var thresholds** follow the project's configuration pattern (ADR-0007).
  They change infrequently and don't need a DB-backed config table, but they
  are more flexible than hardcoded constants.
- **Per-call planning** is the simplest correct behavior. Session-level plan
  reuse would require conversation state that doesn't exist yet and risks
  stale plans for evolving queries.

## Consequences

- The `settings.parser` env var (`"lightweight"` / `"mineru"`) must be removed
  or aliased to the new parser enum. This is a breaking change for anyone who
  has `PARSER=mineru` in their `.env`.
- `rag_wiki/ingest/parser.py`'s `MIME_DISPATCH` dict is replaced by the planner
  module. The `parse_document()` function becomes a dispatch target that accepts
  a `SourcePlan` instead of guessing inside.
- The `POST /queries` handler gains a planner call before `retrieve()`. The
  `QueryPlan` is logged and persisted before any retrieval tool is invoked.
- Query plans with `classified_type=comparison` require the retrieval module to
  support parallel seed lookups (currently single-pass in `orchestrator.py`).
  This may require a small refactor to make `retrieve()` accept multiple
  independent seed groups.
- The LRU cache for LLM query classification is process-local. In multi-worker
  deployments, each worker builds its own cache. This is acceptable for v1.
- A background job (`cleanup_expired_query_plans`) must be added if
  auto-expiry of the `query_plans` table is desired.
- The planner is independently testable — `SourcePlan` and `QueryPlan`
  production use no I/O except the LLM call (which is mocked in tests).
