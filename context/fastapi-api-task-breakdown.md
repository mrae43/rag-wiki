# FastAPI API Surface — Atomic Task Breakdown

Companion to `fastapi-api-implementation-plan.md`. This file lists every atomic task, organized by PR stage, with an optional tracker for status and owner.

---

## Legend

| Field | Meaning |
|-------|---------|
| ID | Unique task identifier (`STAGE-N`) |
| Task | Atomic, verifiable unit of work |
| Owner | Person or role responsible |
| Status | `todo` / `in_progress` / `done` / `blocked` |
| Notes | Links, blockers, dependencies |

---

## PR Stage 1: Foundation

| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| 1.1 | Add `aiofiles` to `pyproject.toml` dependencies. | | todo | |
| 1.2 | Add `upload_dir`, `upload_max_file_size_bytes`, `CORS_ORIGINS`, `api_host`, `api_port` to `rag_wiki/settings.py`. | | todo | Use `pydantic-settings`; `Path` defaults relative to project root. |
| 1.3 | Create `rag_wiki/api/__init__.py`. | | todo | |
| 1.4 | Create `rag_wiki/api/schemas.py` with `PaginatedListEnvelope[T]` and `ProblemDetail`. | | todo | Pydantic v2; `from_attributes=True` where applicable. |
| 1.5 | Create `rag_wiki/api/exceptions.py` mapping `RagWikiError` → Problem Details; 422 normalization; 500 catch-all. | | todo | Register handlers in app factory. |
| 1.6 | Create `rag_wiki/api/middleware.py` for request-ID propagation and CORS. | | todo | Bind request ID to structlog context. |
| 1.7 | Create `rag_wiki/api/dependencies.py` for `get_db`, `get_chat_provider`, `get_embedding_provider`. | | todo | `get_db` must commit/rollback. |
| 1.8 | Create `rag_wiki/api/router.py` to mount `/api/v1` and include health placeholder. | | todo | |
| 1.9 | Update `rag_wiki/main.py` to construct FastAPI app and wire router/middleware/handlers. | | todo | App factory preferred. |
| 1.10 | Update `.env.example` with new variables. | | todo | |
| 1.11 | Add `tests/api/conftest.py` with `httpx.AsyncClient` fixture. | | todo | Reuse DB fixtures from `tests/conftest.py`. |
| 1.12 | Add `tests/api/test_health.py` smoke test. | | todo | |
| 1.13 | Add `tests/api/test_middleware.py` for request ID and CORS. | | todo | |
| 1.14 | Add `tests/api/test_exceptions.py` for Problem Details mapping. | | todo | |
| 1.15 | Run `ruff check → ruff format → mypy → pytest` and fix issues. | | todo | |

---

## PR Stage 2: Sources & Jobs

| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| 2.1 | Define source request/response schemas in `rag_wiki/api/routes/source.py`. | | todo | Include metadata JSON form field handling. |
| 2.2 | Implement `POST /api/v1/sources` multipart upload + async ingest job enqueue. | | todo | Stream with `aiofiles`; handle orphan cleanup. |
| 2.3 | Implement empty-file rejection (400 Problem Detail). | | todo | |
| 2.4 | Implement oversize-file rejection (413 Problem Detail). | | todo | Configurable via settings. |
| 2.5 | Implement `GET /api/v1/sources` with pagination and filters. | | todo | Filters: `status`, `filename`. |
| 2.6 | Implement `GET /api/v1/sources/{id}` with 404 handling. | | todo | |
| 2.7 | Implement `DELETE /api/v1/sources/{id}` deleting DB row and disk file. | | todo | Return 204. |
| 2.8 | Implement `GET /api/v1/sources/{id}/chunks` paginated sub-resource. | | todo | Exclude embedding vector from response. |
| 2.9 | Define job schemas in `rag_wiki/api/routes/job.py`. | | todo | |
| 2.10 | Implement `GET /api/v1/jobs` with pagination and filters. | | todo | Filters: `status`, `job_type`. |
| 2.11 | Implement `GET /api/v1/jobs/{id}` with 404 handling. | | todo | |
| 2.12 | Update `rag_wiki/api/router.py` to include source and job routers. | | todo | |
| 2.13 | Ensure `upload_dir` is created on startup. | | todo | |
| 2.14 | Add `tests/api/routes/test_source.py`. | | todo | Cover happy path, empty, oversized, metadata, list, get, delete, chunks. |
| 2.15 | Add `tests/api/routes/test_job.py`. | | todo | Cover list, filters, get, 404. |
| 2.16 | Run `ruff → format → mypy → pytest`. | | todo | |

---

## PR Stage 3: Knowledge Graph & Wiki Pages

| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| 3.1 | Define entity schemas in `rag_wiki/api/routes/entity.py`. | | todo | |
| 3.2 | Implement `GET /api/v1/entities` with pagination and filters. | | todo | Filters: `status`, `entity_type`, `name`. |
| 3.3 | Implement `GET /api/v1/entities/{id}`. | | todo | |
| 3.4 | Implement `GET /api/v1/entities/{id}/relations` with direction filter. | | todo | Avoid N+1. |
| 3.5 | Implement `GET /api/v1/entities/{id}/wiki-page`. | | todo | 404 if none. |
| 3.6 | Define relation schemas in `rag_wiki/api/routes/relation.py`. | | todo | |
| 3.7 | Implement `GET /api/v1/relations` with pagination and filters. | | todo | Filters: `relation_type`, `source_entity_id`, `target_entity_id`. |
| 3.8 | Define wiki-page schemas in `rag_wiki/api/routes/wiki_page.py`. | | todo | |
| 3.9 | Implement `GET /api/v1/wiki-pages` with pagination and filters. | | todo | Filters: `status`, `title`. |
| 3.10 | Implement `GET /api/v1/wiki-pages/{id}`. | | todo | |
| 3.11 | Implement `GET /api/v1/wiki-pages/slug/{slug}`. | | todo | Document case-sensitivity behavior. |
| 3.12 | Implement `GET /api/v1/wiki-pages/{id}/mentions`. | | todo | |
| 3.13 | Update `rag_wiki/api/router.py` to include entity, relation, wiki_page routers. | | todo | |
| 3.14 | Add `tests/api/routes/test_entity.py`. | | todo | Seed entities/relations. |
| 3.15 | Add `tests/api/routes/test_relation.py`. | | todo | |
| 3.16 | Add `tests/api/routes/test_wiki_page.py`. | | todo | Seed wiki pages. |
| 3.17 | Run `ruff → format → mypy → pytest`. | | todo | |

---

## PR Stage 4: Query & Retrieval Orchestrator

| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| 4.1 | Create `rag_wiki/retrieval/orchestrator.py` with public `retrieve()` function. | | todo | Reuse existing retrieval internals. |
| 4.2 | Ensure `RetrievalResult` schema is complete in `rag_wiki/retrieval/schemas.py`. | | todo | |
| 4.3 | Add `tests/retrieval/test_orchestrator.py` using mocked embedding provider and real DB. | | todo | Cover vector seed and seed-entity bypass. |
| 4.4 | Define `QueryRequest` and `QueryResponse` schemas in `rag_wiki/api/routes/query.py`. | | todo | |
| 4.5 | Implement `POST /api/v1/queries`. | | todo | Embed → retrieve → optional chat answer. |
| 4.6 | Wire chat provider to `LLM_MODEL_QUERY` for answer generation. | | todo | |
| 4.7 | Support `generate_answer=false` to return context only. | | todo | |
| 4.8 | Update `rag_wiki/api/router.py` to include query router. | | todo | |
| 4.9 | Add `tests/api/routes/test_query.py` with dependency overrides for providers. | | todo | |
| 4.10 | Run `ruff → format → mypy → pytest`. | | todo | |

---

## PR Stage 5: Integration, Quality, and Documentation

| ID | Task | Owner | Status | Notes |
|----|------|-------|--------|-------|
| 5.1 | Finalize `rag_wiki/main.py` metadata and confirm `/docs`, `/redoc`, `/health`. | | todo | |
| 5.2 | Add explicit `operation_id` and `tags` to all routes for clean OpenAPI clients. | | todo | |
| 5.3 | Add `tests/api/test_smoke.py` verifying all routes and OpenAPI schema. | | todo | |
| 5.4 | Add seed fixtures in `tests/api/conftest.py` for graph/wiki tests. | | todo | |
| 5.5 | Optionally add `tests/api/test_openapi.py` snapshot test. | | todo | |
| 5.6 | Run full `ruff check .`, `ruff format .`, `mypy .`, `pytest`. | | todo | |
| 5.7 | Create `docs/api.md` with endpoint summary and curl examples. | | todo | |
| 5.8 | Update `README.md` with API startup and configuration section. | | todo | |
| 5.9 | Verify `.env.example` completeness. | | todo | |
| 5.10 | Final review: no new migrations required; no direct LLM SDK imports outside providers. | | todo | |

---

## Quick Reference: Endpoint Matrix

| Resource | Method | Path | PR Stage |
|----------|--------|------|----------|
| Health | GET | `/health` | 1 |
| Sources | POST | `/api/v1/sources` | 2 |
| Sources | GET | `/api/v1/sources` | 2 |
| Sources | GET | `/api/v1/sources/{id}` | 2 |
| Sources | DELETE | `/api/v1/sources/{id}` | 2 |
| Sources | GET | `/api/v1/sources/{id}/chunks` | 2 |
| Jobs | GET | `/api/v1/jobs` | 2 |
| Jobs | GET | `/api/v1/jobs/{id}` | 2 |
| Entities | GET | `/api/v1/entities` | 3 |
| Entities | GET | `/api/v1/entities/{id}` | 3 |
| Entities | GET | `/api/v1/entities/{id}/relations` | 3 |
| Entities | GET | `/api/v1/entities/{id}/wiki-page` | 3 |
| Relations | GET | `/api/v1/relations` | 3 |
| Wiki Pages | GET | `/api/v1/wiki-pages` | 3 |
| Wiki Pages | GET | `/api/v1/wiki-pages/{id}` | 3 |
| Wiki Pages | GET | `/api/v1/wiki-pages/slug/{slug}` | 3 |
| Wiki Pages | GET | `/api/v1/wiki-pages/{id}/mentions` | 3 |
| Queries | POST | `/api/v1/queries` | 4 |

---

## File Map

```
rag_wiki/
  main.py
  settings.py
  api/
    __init__.py
    router.py
    dependencies.py
    exceptions.py
    middleware.py
    schemas.py
    routes/
      source.py
      job.py
      entity.py
      relation.py
      wiki_page.py
      query.py
      health.py
  retrieval/
    orchestrator.py   # new
    seeds.py          # existing / updated
    traversal.py      # existing / updated
    context.py        # existing / updated
    scoring.py        # existing / updated
    schemas.py        # existing / updated
tests/
  api/
    conftest.py
    test_smoke.py
    test_middleware.py
    test_exceptions.py
    test_health.py
    test_openapi.py   # optional
    routes/
      test_source.py
      test_job.py
      test_entity.py
      test_relation.py
      test_wiki_page.py
      test_query.py
  retrieval/
    test_orchestrator.py
docs/
  api.md              # new
.env.example
pyproject.toml
```

---

## Notes

- All PRs should target the same base branch and be rebased as earlier stages merge.
- If merging as one PR, use this breakdown as the commit/PR description sections.
- Keep changes minimal and consistent with existing code style per `docs/coding-standards.md`.
