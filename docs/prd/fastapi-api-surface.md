# PRD: FastAPI API Surface

## Problem Statement

The rag-wiki system has a working CLI and worker pipeline, but no HTTP interface. Other clients — a future web UI, external integrations, automation scripts, and the CLI itself — need a stable, language-agnostic way to submit documents, track ingestion, browse the knowledge graph, read wiki pages, and ask questions over the wiki.

ADR-0013 established the API architecture: a FastAPI application mounted at `/api/v1`, read-only graph/wiki endpoints, async ingestion via the Postgres-native job queue, and optional answer generation on queries. This PRD defines the v1 implementation.

## Solution

A FastAPI application that exposes the automation and integration surface for the system. It is thin: routes handle HTTP concerns, validate input, and delegate domain work to the existing ingestion, retrieval, and job-queue modules. The API is unauthenticated in v1 and intended to run inside a trusted network or behind an existing gateway.

## User Stories

1. As a CLI user, I want to trigger ingestion via HTTP instead of direct DB access, so that the CLI and API share the same execution path.
2. As an operator, I want to upload a document and immediately receive a job ID, so that I can poll for completion without blocking the request.
3. As a user, I want to ask a natural-language question and receive a synthesized answer, so that I can query the wiki without writing code.
4. As a user, I want to retrieve only structured context without an LLM answer, so that I can save tokens or build my own answer pipeline.
5. As a developer, I want to browse entities and relations via HTTP, so that I can build visualizations and navigation UIs.
6. As a developer, I want to fetch wiki pages by slug, so that human-readable URLs work in a browser or Obsidian export.
7. As an operator, I want to inspect job status and failure messages, so that I can debug ingestion problems.
8. As an operator, I want a health endpoint that checks the database, so that load balancers and orchestrators know when the API is ready.
9. As a developer, I want OpenAPI documentation generated from the code, so that I can generate clients in other languages.
10. As a developer, I want consistent error responses, so that clients can handle failures predictably.
11. As an operator, I want request IDs in logs, so that I can trace a single request through the system.
12. As a developer, I want CORS configured via environment variables, so that a future web UI can call the API from a browser.
13. As an operator, I want to delete a source and have its file and chunks cleaned up, so that storage does not grow indefinitely.
14. As a developer, I want list endpoints to paginate and filter consistently, so that UI implementation is straightforward.

## Implementation Decisions

### 1. Module structure (`rag_wiki/api/`)

The API layer is split into a small package with clear responsibilities:

- **`router.py`** — Mounts all v1 routes under `/api/v1` and wires the application.
- **`routes/source.py`, `routes/job.py`, `routes/entity.py`, `routes/relation.py`, `routes/wiki_page.py`, `routes/query.py`, `routes/health.py`** — Route handlers, one per resource. Each module contains its own request/response Pydantic schemas unless they are shared.
- **`dependencies.py`** — FastAPI dependencies: `get_db()` (reuses the existing session factory), `get_chat_provider()`, `get_embedding_provider()`.
- **`schemas.py`** — Shared API schemas (e.g., paginated list envelopes, problem-detail responses).
- **`exceptions.py`** — Exception handlers that map `RagWikiError` subclasses to RFC 7807 Problem Details responses.
- **`middleware.py`** — CORS and request-ID middleware.

### 2. Endpoint groups

| Resource | Endpoints | Notes |
|----------|-----------|-------|
| Sources | `POST /api/v1/sources`, `GET /api/v1/sources`, `GET /api/v1/sources/{id}`, `DELETE /api/v1/sources/{id}`, `GET /api/v1/sources/{id}/chunks` | Upload is single-file multipart. Chunks are exposed only as a nested read-only sub-resource. |
| Jobs | `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}` | Read-only observability. Jobs are created implicitly by source upload. |
| Entities | `GET /api/v1/entities`, `GET /api/v1/entities/{id}`, `GET /api/v1/entities/{id}/relations`, `GET /api/v1/entities/{id}/wiki-page` | Read-only browsing. Filters: status, entity_type, name search. |
| Relations | `GET /api/v1/relations` | Read-only top-level list. Filters: relation_type, source_entity_id, target_entity_id. |
| Wiki pages | `GET /api/v1/wiki-pages`, `GET /api/v1/wiki-pages/{id}`, `GET /api/v1/wiki-pages/slug/{slug}`, `GET /api/v1/wiki-pages/{id}/mentions` | Read-only. Slug endpoint for human-readable URLs. |
| Queries | `POST /api/v1/queries` | Hybrid retrieval + optional LLM answer. |
| Health | `GET /health` | Liveness/readiness with a lightweight DB ping. |

### 3. Ingestion is always async

`POST /api/v1/sources` accepts a multipart file upload and an optional `metadata` JSON form field. It:

1. Generates a UUID for the new source.
2. Streams the file to `{upload_dir}/{source_id}` using async file I/O (`aiofiles`).
3. Creates a `Source` row in `pending` status.
4. Enqueues an `ingest_document` job with the server-side path.
5. Commits the DB transaction.
6. Returns source metadata plus `job_id`.

If the DB transaction fails after the file is saved, the saved file is deleted to avoid orphan uploads.

### 4. File upload constraints

- Configurable max file size (`upload_max_file_size_bytes`, default 100 MB).
- Empty files are rejected.
- No MIME type allow-list: the parser decides what it can handle, and the job fails cleanly if it cannot.
- Files are stored flat by UUID filename; original filename lives only in the database.
- Configurable storage directory (`upload_dir`).

### 5. Response models

All responses use Pydantic v2 models with `from_attributes=True`. List responses use a consistent envelope:

```json
{
  "items": [...],
  "total": 123,
  "offset": 0,
  "limit": 20
}
```

Detail responses are flat and include only scalar metadata. Nested collections (chunks, relations, mentions) are served by dedicated sub-resource endpoints to avoid accidental N+1 queries and oversized payloads.

### 6. Query endpoint

`POST /api/v1/queries` accepts:

```json
{
  "query": "string",
  "generate_answer": true,
  "seed_entity_ids": ["uuid"],
  "max_context_tokens": 3600
}
```

It returns:

```json
{
  "query": "string",
  "answer": "string | null",
  "retrieval": { /* RetrievalResult */ }
}
```

The route:
1. Embeds the query via the embedding provider dependency.
2. Calls the retrieval orchestrator (`retrieve()`) to produce a `RetrievalResult`.
3. If `generate_answer` is true, calls the chat provider with `LLM_MODEL_QUERY` and a prompt built from the retrieval context.

`seed_entity_ids` bypasses vector search for direct entity navigation.

### 7. Error handling

All domain errors are mapped to RFC 7807 Problem Details:

```json
{
  "type": "https://rag-wiki.io/errors/source-not-found",
  "title": "Source not found",
  "status": 404,
  "detail": "...",
  "instance": "/api/v1/sources/..."
}
```

FastAPI validation errors (422) are normalized into the same Problem Details shape. Unexpected exceptions return a generic 500 Problem Detail and are logged with full tracebacks.

### 8. Dependencies and middleware

- `get_db()` yields an async SQLAlchemy session and commits on success, rolls back on exception.
- `get_chat_provider()` and `get_embedding_provider()` instantiate the configured providers from settings.
- CORS middleware reads allowed origins from `CORS_ORIGINS` (comma-separated; empty means disabled).
- Request-ID middleware generates or propagates `X-Request-ID` and binds it to the `structlog` context.

### 9. Configuration additions

New environment variables added to `settings.py` and `.env.example`:

- `upload_dir` — directory for uploaded files.
- `upload_max_file_size_bytes` — max upload size (default 100 MB).
- `CORS_ORIGINS` — comma-separated list of allowed origins.

Existing settings reused: `api_host`, `api_port`, LLM/embedding provider settings, retrieval settings.

### 10. OpenAPI documentation

FastAPI generates OpenAPI docs at `/docs` (Swagger UI) and `/redoc` (ReDoc), mounted at the app root and always enabled. Each route declares an explicit `operation_id` and a resource tag for clean generated clients.

### 11. New retrieval orchestrator

A public `retrieve()` function is added to `rag_wiki.retrieval` to keep the route thin. It orchestrates: embed query → find seeds → traverse graph → assemble context → return `RetrievalResult`.

## Testing Decisions

### What makes a good test

- Test external behavior, not internal routing details.
- Use the `httpx.AsyncClient` against the real FastAPI app.
- Run against a real Postgres test database; do not mock the DB.
- Mock LLM providers via FastAPI dependency overrides.
- Cover happy paths and representative error paths (404, validation failures, upload too large).

### Modules to test

| Module | Tests |
|--------|-------|
| `api/routes/source.py` | Upload succeeds and creates source + job; empty file rejected; oversized file rejected; metadata stored; list/filter/delete work; chunks sub-resource works |
| `api/routes/job.py` | List and get job; filters by status and job_type |
| `api/routes/entity.py` | List/filter entities; get entity; nested relations and wiki-page endpoints |
| `api/routes/relation.py` | List/filter relations |
| `api/routes/wiki_page.py` | List pages; get by id; get by slug; mentions endpoint |
| `api/routes/query.py` | Query returns answer and retrieval context; `generate_answer=false` omits answer; invalid body returns 422 Problem Detail |
| `api/routes/health.py` | Healthy DB returns 200; unreachable DB returns 503 |
| `api/exceptions.py` | Domain exceptions map to correct Problem Details |
| `api/middleware.py` | Request ID propagated; CORS headers present when configured |

### Prior art

- `tests/db/test_smoke.py` — DB fixture and session helpers.
- `tests/providers/test_openai.py` — provider mocking pattern.
- `tests/graph/test_resolution.py` — real DB test setup for entity/relation data.
- `tests/retrieval/` — retrieval pipeline testing.

## Out of Scope

- **Authentication / authorization** — Deferred per ADR-0004.
- **Batch upload** — Single-file upload in v1.
- **URL-based ingestion** — `POST /sources/from-url` deferred.
- **Streaming query answers** — `POST /queries/stream` deferred.
- **Editable wiki pages** — Wiki pages remain LLM-synthesized in v1.
- **Mutations on entities/relations/jobs** — Read-only in v1.
- **Rate limiting** — Deferred.
- **SDK generation** — The OpenAPI spec is the SDK contract; no generated client included.
- **File export API** — CLI export remains the only export path in v1.
- **Real-time job notifications** — No WebSocket/SSE; clients poll `GET /jobs/{id}`.

## Further Notes

- The API is intended to be implemented in one PR: new `rag_wiki/api/` package, updates to `rag_wiki/main.py`, `rag_wiki/settings.py`, `.env.example`, `pyproject.toml` (add `aiofiles`), the new `retrieve()` orchestrator, and tests under `tests/api/`.
- All code must pass `ruff check → ruff format → mypy → pytest` before merging.
- No database schema migrations are required; the API uses existing tables.
