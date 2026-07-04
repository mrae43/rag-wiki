# FastAPI API Surface — Implementation Plan

Derived from:
- `docs/adr/0013-fastapi-api-surface.md`
- `docs/prd/fastapi-api-surface.md`

This plan breaks the implementation into **five staged PRs**. Each stage is designed to be atomic, reviewable, and mergeable on its own. Later stages depend only on merged earlier stages. The full API can also be delivered as one larger PR if team velocity prefers it; the staging here provides a safe incremental path.

---

## 0. Pre-Implementation Checklist

Before opening PRs, verify the repository state and conventions.

| # | Task | Done |
|---|------|------|
| 1 | Read `CONTEXT.md` and confirm terminology (`Source`, `Chunk`, `Entity`, `Relation`, `WikiPage`, etc.). | [ ] |
| 2 | Read `docs/coding-standards.md` (docstrings, error handling, typing, logging, DB conventions). | [ ] |
| 3 | Read `docs/adr/0013-fastapi-api-surface.md` and confirm no new ADR is needed. | [ ] |
| 4 | Read existing models in `rag_wiki/db/models/` (`source.py`, `graph.py`, `wiki.py`, `jobs.py`, `index.py`). | [ ] |
| 5 | Read existing `rag_wiki/settings.py`, `rag_wiki/main.py`, and `.env.example`. | [ ] |
| 6 | Read existing job-queue interface in `rag_wiki/jobs/`. | [ ] |
| 7 | Read existing provider protocols in `rag_wiki/providers/base.py` and registry in `rag_wiki/providers/__init__.py`. | [ ] |
| 8 | Confirm `rag_wiki/retrieval/` already exposes (or will expose) `RetrievalResult` and a `retrieve()` orchestrator per this plan. | [ ] |
| 9 | Confirm `pyproject.toml` allows adding `aiofiles` and any test-only deps. | [ ] |

---

## PR Stage 1: Foundation — Scaffolding, Settings, Middleware, Dependencies, Errors

**Goal:** Create the `rag_wiki/api/` package, wire it into `main.py`, add configuration, and establish cross-cutting concerns (CORS, request IDs, Problem Details, DB/provider dependencies). No business routes yet.

### Files Added

```
rag_wiki/api/
  __init__.py
  router.py          # Mounts /api/v1, includes health router
  dependencies.py    # get_db, get_chat_provider, get_embedding_provider
  exceptions.py      # Problem Details handlers
  middleware.py      # Request-ID + CORS middleware factories
  schemas.py         # PaginatedListEnvelope, ProblemDetail, shared bases
rag_wiki/main.py     # Updated to create FastAPI app, include api.router
rag_wiki/settings.py # New: upload_dir, upload_max_file_size_bytes, CORS_ORIGINS
.env.example         # New env vars
pyproject.toml       # Add aiofiles
```

### Atomic Steps

1.1. Add `aiofiles` to `pyproject.toml` dependencies.
1.2. Add new settings to `rag_wiki/settings.py`:
  - `upload_dir: Path` (default `./uploads`)
  - `upload_max_file_size_bytes: int` (default 104_857_600 = 100 MB)
  - `CORS_ORIGINS: str` (default `""`, comma-separated)
  - Ensure `api_host` and `api_port` exist or add them.
1.3. Create `rag_wiki/api/__init__.py`.
1.4. Create `rag_wiki/api/schemas.py`:
  - `PaginatedListEnvelope[T]` generic model (`items`, `total`, `offset`, `limit`).
  - `ProblemDetail` model (`type`, `title`, `status`, `detail`, `instance`).
1.5. Create `rag_wiki/api/exceptions.py`:
  - Handler for `RagWikiError` hierarchy → map to Problem Details status codes.
  - Handler for `RequestValidationError` → 422 Problem Detail.
  - Catch-all 500 handler → generic Problem Detail + structured log with traceback.
1.6. Create `rag_wiki/api/middleware.py`:
  - `add_request_id_middleware(app)` — reads/propagates `X-Request-ID`, binds to structlog context.
  - `add_cors_middleware(app, origins)` — conditionally adds `CORSMiddleware`.
1.7. Create `rag_wiki/api/dependencies.py`:
  - `get_db()` async generator yielding SQLAlchemy async session; commit on success, rollback on exception.
  - `get_chat_provider()` returns configured `ChatProvider`.
  - `get_embedding_provider()` returns configured `EmbeddingProvider`.
1.8. Create `rag_wiki/api/router.py`:
  - Instantiate `APIRouter(prefix="/api/v1")`.
  - Include `health` router (created in this stage as a minimal placeholder).
  - Export a `create_app()` factory or a module-level `app` wiring function used by `main.py`.
1.9. Update `rag_wiki/main.py` to construct the FastAPI app, register middleware, exception handlers, and include `api.router`.
1.10. Update `.env.example` with new variables and sensible defaults.
1.11. Add placeholder `tests/api/test_health.py` that asserts `GET /health` returns 200 (basic smoke test of wiring).

### Tests Added

- `tests/api/conftest.py` — shared `client` fixture using `httpx.AsyncClient` over the real FastAPI app.
- `tests/api/test_health.py` — smoke test for `/health`.
- `tests/api/test_middleware.py` — request ID propagation; CORS headers when configured.
- `tests/api/test_exceptions.py` — validate that a thrown `RagWikiError` returns Problem Details.

### Acceptance Criteria

- `pytest tests/api/test_health.py` passes.
- `ruff check`, `ruff format`, `mypy` pass.
- `GET /health` returns a JSON payload.
- `GET /api/v1/nonexistent` returns a 404 Problem Detail.
- `X-Request-ID` is echoed or generated on every response.

---

## PR Stage 2: Sources & Jobs — Async Ingestion and File Lifecycle

**Goal:** Implement document upload, source lifecycle, chunk sub-resource, and read-only job observability.

### Files Added

```
rag_wiki/api/routes/
  source.py          # POST /sources, GET /sources, GET /sources/{id}, DELETE /sources/{id}, GET /sources/{id}/chunks
  job.py             # GET /jobs, GET /jobs/{id}
rag_wiki/api/router.py  # Include source and job routers
```

### Atomic Steps

2.1. Define request/response schemas in `rag_wiki/api/routes/source.py`:
  - `SourceCreateMetadata` (optional JSON form field).
  - `SourceResponse` (id, filename, status, created_at, updated_at, metadata, job_id).
  - `SourceListResponse` (use `PaginatedListEnvelope[SourceResponse]`).
  - `ChunkResponse` for sub-resource.
2.2. Implement `POST /api/v1/sources`:
  - Accept `multipart/form-data` with `file` and optional `metadata` JSON string.
  - Reject empty files with 400 Problem Detail.
  - Reject files exceeding `upload_max_file_size_bytes` (check `Content-Length` if present, and/or `file.size` after read).
  - Generate `source_id` UUID.
  - Stream file asynchronously to `{upload_dir}/{source_id}` using `aiofiles`.
  - Create `Source` row in `pending` status, store original filename and metadata.
  - Enqueue `ingest_document` job with server-side path via existing job-queue interface.
  - Commit transaction.
  - On DB failure after file write, delete the saved file to avoid orphans.
  - Return `SourceResponse` with `job_id`.
2.3. Implement `GET /api/v1/sources`:
  - Offset/limit pagination (default 20, max 100).
  - Filters: `status`, `filename` substring (optional).
  - Return `PaginatedListEnvelope[SourceResponse]`.
2.4. Implement `GET /api/v1/sources/{id}`:
  - Return 404 Problem Detail if not found.
2.5. Implement `DELETE /api/v1/sources/{id}`:
  - Delete source row (cascade should delete chunks; verify in tests).
  - Delete associated file from `upload_dir`.
  - Return 204 No Content.
  - Return 404 if source does not exist.
2.6. Implement `GET /api/v1/sources/{id}/chunks`:
  - Return paginated list of chunks for a source (id, index, text, embedding not included).
2.7. Define job schemas in `rag_wiki/api/routes/job.py`:
  - `JobResponse` (id, job_type, status, payload, result, error_message, created_at, updated_at, claimed_at, completed_at).
2.8. Implement `GET /api/v1/jobs`:
  - Offset/limit pagination.
  - Filters: `status`, `job_type`.
2.9. Implement `GET /api/v1/jobs/{id}`:
  - Return 404 if not found.
2.10. Update `rag_wiki/api/router.py` to include source and job routers.
2.11. Ensure `upload_dir` is created on startup if it does not exist.

### Tests Added

- `tests/api/routes/test_source.py`:
  - Upload succeeds, creates `Source` + `ingest_document` job, returns `job_id`.
  - Empty file rejected.
  - Oversized file rejected (set a small max size for the test).
  - Metadata stored and returned.
  - List with offset/limit/filters.
  - Get by id returns 200; invalid id returns 404.
  - Delete removes DB row and file.
  - Chunks sub-resource returns chunks for a source.
- `tests/api/routes/test_job.py`:
  - List jobs with filters.
  - Get job by id returns correct payload/status.
  - Get nonexistent job returns 404.

### Acceptance Criteria

- `pytest tests/api/routes/test_source.py tests/api/routes/test_job.py` passes.
- File uploads appear in `upload_dir` named by UUID.
- `POST /api/v1/sources` returns a `job_id`.
- Deleting a source removes the file from disk.
- Pagination envelope is consistent across list endpoints.

---

## PR Stage 3: Knowledge Graph & Wiki Pages — Read-Only Browsing

**Goal:** Expose entities, relations, and wiki pages for browsing and navigation.

### Files Added

```
rag_wiki/api/routes/
  entity.py          # GET /entities, GET /entities/{id}, GET /entities/{id}/relations, GET /entities/{id}/wiki-page
  relation.py        # GET /relations
  wiki_page.py       # GET /wiki-pages, GET /wiki-pages/{id}, GET /wiki-pages/slug/{slug}, GET /wiki-pages/{id}/mentions
rag_wiki/api/router.py  # Include entity, relation, wiki_page routers
```

### Atomic Steps

3.1. Define entity schemas in `rag_wiki/api/routes/entity.py`:
  - `EntityResponse` (id, name, entity_type, status, metadata, created_at, updated_at).
  - `EntityRelationResponse` for nested relation list.
  - `EntityWikiPageResponse` for nested wiki page.
3.2. Implement `GET /api/v1/entities`:
  - Offset/limit pagination.
  - Filters: `status`, `entity_type`, `name` substring/ilike.
3.3. Implement `GET /api/v1/entities/{id}`:
  - Return 404 if not found.
3.4. Implement `GET /api/v1/entities/{id}/relations`:
  - Return relations where entity is source or target.
  - Optional filter `direction` (`outgoing`, `incoming`, `both`).
3.5. Implement `GET /api/v1/entities/{id}/wiki-page`:
  - Return the wiki page whose primary entity id matches (or via mention lookup if that schema exists).
  - 404 if none.
3.6. Define relation schemas in `rag_wiki/api/routes/relation.py`:
  - `RelationResponse` (id, relation_type, source_entity_id, target_entity_id, metadata, created_at).
3.7. Implement `GET /api/v1/relations`:
  - Offset/limit pagination.
  - Filters: `relation_type`, `source_entity_id`, `target_entity_id`.
3.8. Define wiki-page schemas in `rag_wiki/api/routes/wiki_page.py`:
  - `WikiPageResponse` (id, slug, title, content, status, entity_id?, created_at, updated_at).
  - `WikiPageMentionResponse`.
3.9. Implement `GET /api/v1/wiki-pages`:
  - Offset/limit pagination.
  - Filters: `status`, `title` substring/ilike.
3.10. Implement `GET /api/v1/wiki-pages/{id}`.
3.11. Implement `GET /api/v1/wiki-pages/slug/{slug}`:
  - Case-sensitive or case-insensitive slug lookup; document choice.
  - 404 if not found.
3.12. Implement `GET /api/v1/wiki-pages/{id}/mentions`:
  - Return entities that mention this page (or chunks referencing it), depending on existing schema.
3.13. Update `rag_wiki/api/router.py` to include entity, relation, and wiki_page routers.

### Tests Added

- `tests/api/routes/test_entity.py`:
  - List and filter entities by status/type/name.
  - Get entity by id.
  - Nested relations endpoint returns relevant relations.
  - Nested wiki-page endpoint returns page.
- `tests/api/routes/test_relation.py`:
  - List and filter by type/source/target.
- `tests/api/routes/test_wiki_page.py`:
  - List and filter pages.
  - Get by id.
  - Get by slug.
  - Mentions endpoint returns mentions.

### Acceptance Criteria

- All graph/wiki route tests pass.
- List endpoints return `PaginatedListEnvelope`.
- Nested sub-resources avoid N+1 (use joined/eager loading where appropriate).
- Slug endpoint enables human-readable URLs.

---

## PR Stage 4: Query & Retrieval Orchestrator — Hybrid Search over the Wiki

**Goal:** Add the public `retrieve()` orchestrator and the `POST /api/v1/queries` endpoint with optional answer generation.

### Files Added

```
rag_wiki/retrieval/orchestrator.py   # NEW: public retrieve() function
rag_wiki/api/routes/query.py         # POST /queries
rag_wiki/api/router.py               # Include query router
```

### Atomic Steps

4.1. Create `rag_wiki/retrieval/orchestrator.py`:
  - Define `retrieve(query: str, embedding_provider, db_session, seed_entity_ids: list[UUID] | None = None, **kwargs) -> RetrievalResult`.
  - Internally: embed query → find seed entities/chunks via vector search or seed IDs → traverse graph with recursive CTE → assemble context.
  - Reuse existing `rag_wiki/retrieval/` internals (`seeds.py`, `traversal.py`, `context.py`, `scoring.py`) where they exist; refactor minimally.
  - Expose a clear `RetrievalResult` schema already defined in `rag_wiki/retrieval/schemas.py`.
4.2. Add unit/integration tests for `retrieve()` in `tests/retrieval/test_orchestrator.py`:
  - Mock embedding provider.
  - Use real Postgres test DB seeded with chunks/entities/relations.
  - Verify vector seed path and seed-entity bypass path.
4.3. Define query schemas in `rag_wiki/api/routes/query.py`:
  - `QueryRequest` (`query: str`, `generate_answer: bool = True`, `seed_entity_ids: list[UUID] | None = None`, `max_context_tokens: int | None = None`).
  - `QueryResponse` (`query: str`, `answer: str | None`, `retrieval: RetrievalResult`).
4.4. Implement `POST /api/v1/queries`:
  - Embed query via `get_embedding_provider()`.
  - Call `retrieve()` to produce `RetrievalResult`.
  - If `generate_answer` is true, call chat provider with `LLM_MODEL_QUERY` and a prompt built from retrieval context.
  - Return `QueryResponse`.
4.5. Add dependency override tests for chat/embedding providers.
4.6. Update `rag_wiki/api/router.py` to include query router.

### Tests Added

- `tests/retrieval/test_orchestrator.py` — orchestrator behavior.
- `tests/api/routes/test_query.py`:
  - Query returns answer and retrieval context when `generate_answer=true`.
  - `generate_answer=false` omits answer.
  - Invalid body returns 422 Problem Detail.
  - Seed entity ids bypass vector search.

### Acceptance Criteria

- `retrieve()` function is callable and returns a typed `RetrievalResult`.
- Query endpoint returns answer + context by default.
- Query endpoint returns structured context only when requested.
- All new and existing retrieval tests pass.

---

## PR Stage 5: Integration, Quality, and Documentation

**Goal:** Wire everything together, add remaining API tests, ensure docs/OpenAPI, and pass quality gates.

### Files Touched

```
rag_wiki/main.py
rag_wiki/api/router.py
docs/api.md                         # NEW: endpoint summary and examples
README.md                           # Add API section
pyproject.toml                      # Final dependency check
```

### Atomic Steps

5.1. Finalize `rag_wiki/main.py`:
  - Ensure app title, version, description, OpenAPI metadata are set.
  - Confirm `/docs` and `/redoc` are reachable.
  - Confirm `/health` is mounted at root (not under `/api/v1`).
5.2. Finalize `rag_wiki/api/router.py`:
  - Include all v1 routers with explicit `tags` and `operation_id` patterns (e.g. `list_sources`, `create_source`).
5.3. Add `tests/api/test_smoke.py`:
  - Verify every top-level route returns expected status codes.
  - Verify OpenAPI schema is generated and contains all routes.
5.4. Add load/seed fixtures in `tests/api/conftest.py`:
  - Seed sources, jobs, entities, relations, wiki pages for graph/wiki tests.
5.5. Run full quality pipeline:
  - `ruff check .`
  - `ruff format .`
  - `mypy .`
  - `pytest`
  - Fix all issues.
5.6. Create `docs/api.md` (or extend README):
  - List all endpoints with examples.
  - Document error format (Problem Details).
  - Document environment variables.
5.7. Update `README.md` API section to describe how to run the API (`uvicorn rag_wiki.main:app --reload` or Docker).
5.8. Verify `.env.example` is complete.
5.9. Optional: generate an `openapi.json` snapshot test under `tests/api/test_openapi.py` to catch unintended contract changes.

### Acceptance Criteria

- Full test suite passes (`pytest`).
- `ruff check`, `ruff format`, `mypy` pass.
- OpenAPI docs at `/docs` render all endpoints.
- `README.md` describes how to start the API and configure CORS/uploads.
- No schema migrations are required; all tables already exist.

---

## Cross-Cutting Concerns

### Error Response Format

All error responses follow RFC 7807 Problem Details:

```json
{
  "type": "https://rag-wiki.io/errors/source-not-found",
  "title": "Source not found",
  "status": 404,
  "detail": "No source with id ...",
  "instance": "/api/v1/sources/..."
}
```

### Pagination Contract

List responses use this envelope:

```json
{
  "items": [...],
  "total": 123,
  "offset": 0,
  "limit": 20
}
```

Default `limit` is 20; maximum is 100.

### Logging

Use `structlog` exclusively. Never use `print()` or stdlib `logging`. Request IDs are bound to the log context in middleware.

### File Upload Safety

- Flat UUID filenames in `upload_dir`.
- Original filename stored only in the database.
- Empty files rejected.
- Oversized files rejected before streaming.
- Orphan files deleted on transaction failure.

### LLM Provider Abstraction

All LLM/embedding calls go through `ChatProvider` and `EmbeddingProvider`. No direct `openai`/`anthropic` imports outside `rag_wiki/providers/`.

---

## PR Dependency Graph

```
PR Stage 1 (Foundation)
       │
       ▼
PR Stage 2 (Sources & Jobs)
       │
       ▼
PR Stage 3 (Graph & Wiki)
       │
       ▼
PR Stage 4 (Query)
       │
       ▼
PR Stage 5 (Integration & Docs)
```

Stages 2, 3, and 4 each depend only on Stage 1 and can be developed in parallel if needed. Stage 5 must follow all others.

---

## Definition of Done

- [ ] All five PRs merged (or equivalent single consolidated PR).
- [ ] `ruff check .`, `ruff format .`, `mypy .`, and `pytest` pass on `main`.
- [ ] Every endpoint in the PRD table is implemented and tested.
- [ ] OpenAPI docs are generated and complete at `/docs`.
- [ ] README/docs describe how to configure and run the API.
- [ ] No direct LLM SDK imports outside `rag_wiki/providers/`.
- [ ] No new database schema migrations were required.
- [ ] All list endpoints use the paginated envelope.
- [ ] All errors return RFC 7807 Problem Details.

---

## Out of Scope (per PRD)

- Authentication / authorization
- Batch upload
- URL-based ingestion (`POST /sources/from-url`)
- Streaming query answers (`POST /queries/stream`)
- Editable wiki pages
- Mutations on entities/relations/jobs
- Rate limiting
- Generated SDK clients
- File export API
- Real-time job notifications (WebSocket/SSE)
