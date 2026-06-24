# PRD: Ingest-time and Query-time Planner

## Problem Statement

The ingest pipeline and query pipeline each make decisions without an explicit planning step. `parse_document()` dispatches by MIME guess alone; `retrieve()` runs the same graph traversal depth for every query. There is no logged, inspectable, persisted record of *why* a particular parser was chosen or *how deep* retrieval went. Without a planner, the system defaults to uniform handling — the same parser for every source, the same retrieval depth for every query — and troubleshooting requires reading code, not logs.

## Solution

Add a lightweight planning layer that runs **before** any tool is invoked. Two planners, one for each pipeline:

- **Ingest planner** (rule-based, < 200ms): Classifies a Source by media type, density, and structure; selects a parser; produces a `SourcePlan` stored as JSONB on the `sources` table.
- **Query planner** (hybrid, LLM primary + rule fallback): Classifies a user query into one of four types (`factual_lookup`, `relationship_query`, `summarization`, `comparison`); produces a `QueryPlan` persisted to a new `query_plans` table.

Both plans are first-class artifacts: persisted, logged before execution, versioned, and independently testable.

## User Stories

1. As a system admin, I want the system to classify every ingested source before parsing, so that the correct parser is selected and the decision is logged for debugging.
2. As a system admin, I want the ingest planner to be rule-based and deterministic, so that the same file always selects the same parser without an LLM call.
3. As a system admin, I want the ingest planner to run synchronously during upload, so that the source plan exists in the database before the worker starts processing.
4. As a system admin, I want the ingest plan to include a fallback parser, so that parsing failures don't leave the system guessing at what to try next.
5. As a user, I want the system to classify my query before retrieval, so that shallow factual lookups don't trigger expensive graph traversal and deep relationship queries aren't starved of context.
6. As a user, I want the query planner to use an LLM for accurate classification, so that rephrased queries still pick the right retrieval depth.
7. As an operator, I want the query planner to fall back to keyword rules when the LLM is unavailable, so that query classification never blocks on a provider outage.
8. As a user, I want the API to accept an explicit `query_type` parameter, so that I can bypass classification when I already know what I need.
9. As an operator, I want both plans persisted in Postgres, so that I can audit why a particular parser or retrieval depth was chosen.
10. As a developer, I want the planner module to be independently testable with no I/O except the LLM call (mocked), so that tests are fast and reliable.
11. As a developer, I want the planner to produce structured objects, not free-text strings, so that the plan's fields are type-checked and documented.

## Implementation Decisions

### Module structure

| File | Responsibility |
|------|---------------|
| `rag_wiki/planner/__init__.py` | Public exports: `create_source_plan`, `classify_query`, `SourcePlan`, `QueryPlan` |
| `rag_wiki/planner/base.py` | `SourcePlan`, `QueryPlan` data models (Pydantic BaseModel) |
| `rag_wiki/planner/ingest.py` | `IngestPlanner` class: rule-based classification, produces `SourcePlan` |
| `rag_wiki/planner/query.py` | `QueryPlanner` class: hybrid LLM + rule classification, produces `QueryPlan` |
| `rag_wiki/db/models/planner.py` | SQLAlchemy model for `query_plans` table |
| `rag_wiki/settings.py` | Replace `parser` setting, add `planner_confidence_*` env vars |

### Parser enum

Replace the ad-hoc engine strings with a canonical enum:

```
class ParserType(str, Enum):
    PDF = "pdf"
    SIMPLE = "simple"
    UNSTRUCTURED = "unstructured"
    MINERU = "mineru"
```

`ocr` is removed as a separate value — it becomes a flag on `pdf` parsing:

```
class PDFParserMode(str, Enum):
    STANDARD = "standard"
    WITH_OCR = "with_ocr"
```

### Ingest planner contract

```
class SourcePlan(BaseModel):
    source_id: uuid.UUID
    detected_type: str                  # media type string
    detected_structure: str             # structured | semi-structured | unstructured
    selected_parser: ParserType         # enum value
    pdf_mode: PDFParserMode | None = None  # only for pdf parser
    chunking_strategy: str              # derived from parser
    confidence: float                   # always 1.0 for rule-based
    fallback_parser: ParserType         # always SIMPLE
    rationale: str                      # one-sentence reason
    planner_version: str                # semver
```

### Ingest planner classification

Rule-based, no LLM call. Four dimensions:

| Dimension | Derivation |
|-----------|-----------|
| Media type | `mimetypes.guess_type()` + file extension |
| Density | File size thresholds from settings |
| Structure | Hardcoded MIME → structure map |
| Content domain | Always `"unknown"` (v2: LLM-augmented) |

When `source_metadata` contains an explicit `parser` key, the planner uses it directly with confidence 1.0 and rationale `"explicit override in metadata"`.

### Ingest planner timing

Synchronous in `POST /sources`, before `Source` row creation and enqueue. The planner call replaces the inlined MIME guess in `create_source`. The worker reads `sources.source_plan` instead of guessing inside `parse_document`.

### `parse_document()` refactor

Current signature: `parse_document(file_path, source_metadata)` — dispatches by MIME internally.

New signature: `parse_document(file_path, source_plan: SourcePlan)` — the caller passes the plan. `source_plan.selected_parser` determines which parser to invoke. `source_plan.pdf_mode` determines standard vs OCR PDF parsing.

### Query planner contract

```
class QueryPlan(BaseModel):
    query_id: uuid.UUID
    raw_query: str
    classified_type: QueryType          # enum: factual_lookup | relationship_query | summarization | comparison
    retrieval_depth: str                # shallow | medium | deep
    seed_count: int                     # expected number of seeds
    termination_condition: str          # e.g. "top-3 seeds found, graph depth 2 reached"
    confidence: float                   # 0-1
    classification_source: str          # "llm" | "rule" | "explicit"
    model_used: str | None             # which LLM classified it
    rationale: str                      # one-sentence reason
    planner_version: str                # semver
```

### Query classification — hybrid approach

**Primary path:** Call a cheap chat model (`gpt-4o-mini`, temperature 0, max 50 output tokens) with a structured output schema. Timeout 500ms. The prompt asks the model to classify the query into one of the four types with a confidence score and rationale. Results are cached in a process-local LRU for exact duplicate queries.

**Fallback path:** Keyword/regex rules when LLM call fails:

| Signal | Type |
|--------|------|
| `"what is"`, `"define"`, `"who is"` | factual_lookup |
| `"how does.*relat"`, `"connect"`, `"relationship"` | relationship_query |
| `"summar"`, `"overview"`, `"give me an overview"` | summarization |
| `"compare"`, `"difference"`, `"versus"`, `"vs"` | comparison |
| default | factual_lookup |

**Explicit override:** The API accepts `query_type` parameter to bypass classification. The planner assigns `classification_source = "explicit"` and copies the user-provided type directly.

### Multi-type query handling

Single-type assignment with depth escalation. When confidence is ambiguous (< `planner_confidence_low` threshold), the planner escalates to the deeper retrieval strategy. Depth order: `relationship_query` > `summarization` > `comparison` > `factual_lookup`.

### Plan persistence

**Ingest plan:** `SourcePlan` stored as a `source_plan` JSONB column on the `sources` table. Written during `POST /sources`, read by the worker during `run_ingest_pipeline`.

**Query plan:** A new `query_plans` table with columns mirroring the `QueryPlan` Pydantic model. Inserted during `POST /queries` before `retrieve()`. Never updated. Auto-expiry via `created_at` + TTL.

```
query_plans:
  id: UUID PK
  raw_query: TEXT
  classified_type: TEXT
  retrieval_depth: TEXT
  seed_count: INTEGER
  termination_condition: TEXT
  confidence: FLOAT
  classification_source: TEXT
  model_used: TEXT
  rationale: TEXT
  planner_version: TEXT
  created_at: TIMESTAMPTZ
  ttl_days: INTEGER (default 30)
```

### Confidence thresholds (env vars)

| Env var | Default | Behavior |
|---------|---------|----------|
| `PLANNER_CONFIDENCE_HIGH` | 0.8 | Confidence ≥ threshold → proceed as classified |
| `PLANNER_CONFIDENCE_LOW` | 0.5 | Confidence in [low, high) → log flag, escalate depth |
| `PLANNER_CONFIDENCE_MINIMUM` | 0.5 | Confidence < threshold → halt, require explicit override |

### Settings changes

Remove `parser: Literal["lightweight", "mineru"]`. Add:

```python
class ParserType(str, enum.Enum):
    PDF = "pdf"
    SIMPLE = "simple"
    UNSTRUCTURED = "unstructured"

PLANNER_CONFIDENCE_HIGH: float = 0.8
PLANNER_CONFIDENCE_LOW: float = 0.5
PLANNER_CONFIDENCE_MINIMUM: float = 0.5
PLANNER_VERSION: str = "1.0.0"
LLM_MODEL_QUERY_CLASSIFICATION: str = "gpt-4o-mini"
PLANNER_QUERY_CLASSIFICATION_TIMEOUT_MS: int = 500
PLANNER_DENSITY_LARGE_THRESHOLD_BYTES: int = 10_485_760  # 10 MB
```

### `POST /queries` endpoint changes

1. Call `classify_query(query, chat_provider)` → `QueryPlan`
2. Persist `QueryPlan` to `query_plans` table
3. If confidence < `PLANNER_CONFIDENCE_MINIMUM` and no explicit `query_type` override, return 400 with the plan's rationale
4. Pass `retrieval_depth` (or the classified type) to `retrieve()` so the retrieval layer can tune seed count, hop depth, etc.
5. Return the `QueryPlan` (or its id) in the response for traceability

### `POST /sources` endpoint changes

1. Call `create_source_plan(file_path, metadata)` → `SourcePlan`
2. Store `source_plan` on the `Source` row
3. Enqueue `ingest_document` job as before

### Retrieval module — comparison support

For `comparison` type queries, the retrieval layer needs to find multiple independent seed groups and merge results. This requires the `orchestrator.retrieve()` to accept a `seed_groups` concept. In v1, this is handled by:

1. Finding top-k entities for each entity named in the query (e.g., "Compare X and Y" → find X's entity, find Y's entity)
2. Running shallow retrieval per entity independently
3. Merging the `RetrievalResult` objects at the context-assembly level

The `retrieve()` function gains an optional `seed_entity_ids: list[uuid.UUID]` parameter (already exists) plus the entity names discovered during planning. This is a small refactor, not a deep change.

## Testing Decisions

### What makes a good test

- Test external behavior, not implementation details. A passing test should stay passing after internal refactoring.
- The ingest planner is deterministic — the same file metadata always produces the same `SourcePlan`. Test the mapping exhaustively.
- The query planner's LLM path is the riskiest part. Test with a `FakeChatProvider` that returns controlled classification responses, and test that the structured output parsing handles edge cases (unexpected types, missing fields, empty rationale).
- The fallback rule path must be independently tested — the same query text always produces the same fallback classification.
- The confidence threshold policy must be tested at boundary values: exactly at threshold, one below, one above.
- Persistence of plans must be tested with a real database session.

### Modules to test

| Module | Tests |
|--------|-------|
| `planner/ingest.py` | PDF → `pdf` parser; .md → `simple`; .html → `unstructured`; explicit parser override in metadata; unknown MIME → fallback; pdf_mode selection; density thresholds |
| `planner/query.py` | LLM path with mock: each query type returned correctly; LLM timeout → fallback rule path; explicit `query_type` override; confidence below minimum → halts; ambiguous confidence → depth escalation; LRU cache hit vs miss |
| `db/models/planner.py` | QueryPlan row creation, TTL default, round-trip serialization |
| `api/routes/source.py` | Planner is called; source_plan stored on Source; existing upload behavior preserved |
| `api/routes/query.py` | Planner is called; QueryPlan persisted; plan returned in response; low confidence returns 400 |

### Prior art

- `tests/ingest/` — pipeline integration tests with mocked providers
- `tests/providers/test_openai.py` — provider mocking pattern for LLM calls
- `tests/retrieval/` — DB fixture patterns for testing with real Postgres
- `tests/db/test_models.py` — model creation and round-trip testing
- `tests/graph/` — entity/relation test setup with `tmp_path` for file fixtures

## Out of Scope

- **LLM-augmented content domain** — deferred to v2
- **Navigational query type** — dropped for v1; users use `GET /sources` and `GET /entities` directly
- **Compound query types** — single-type with depth escalation covers v1
- **Session-aware planner** — planner runs per-call, no conversation state
- **Hot-reloadable confidence thresholds** — env vars require restart
- **Distributed LRU cache** — process-local cache is sufficient for v1
- **Background cleanup job** for expired query plans — manual or scheduled TTL enforcement deferred
- **Authentication / authorization** — no planner-specific auth changes

## Further Notes

- Implementation order: settings → planner data models → ingest planner → query planner → schema migrations → wire into API routes → tests
- ADR-0014 records the design decisions from the structured review that produced this PRD
- The `parser` setting removal (`PARSER=mineru` → `PARSER=mineru` no longer valid) is a breaking `.env` change. Document in migration notes.
- All quality commands must pass: `ruff check → ruff format → mypy → pytest`
