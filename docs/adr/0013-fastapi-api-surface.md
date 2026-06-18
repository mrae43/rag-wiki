# ADR-0013: FastAPI API surface for automation and integration

## Status

Accepted

## Context

The RagWiki system has three primary user-facing interfaces: the `rag-wiki` CLI,
a Postgres-native job worker, and the underlying database. As the system grows,
other clients (a web UI, external integrations, Obsidian plugins, automation)
will need a stable, language-agnostic way to:

- Submit documents for ingestion.
- Track async ingestion jobs.
- Browse the knowledge graph and read wiki pages.
- Ask questions over the wiki using hybrid retrieval.

This ADR records the shape of the HTTP API introduced for these purposes.

## Decision

Expose a **FastAPI** application mounted at `/api/v1` with the following
first-class resources:

- **`/sources`** — document upload and lifecycle.
- **`/jobs`** — read-only job queue observability.
- **`/entities`** and **`/relations`** — read-only knowledge graph browsing.
- **`/wiki-pages`** — read-only access to synthesized wiki pages.
- **`/queries`** — hybrid retrieval with optional LLM-generated answer.
- **`/health`** — liveness/readiness check with a lightweight DB query.

### Key design choices

1. **Async ingestion only.** `POST /sources` stores the uploaded file, creates a
   `Source` row in `pending`, enqueues an `ingest_document` job, and returns the
   source metadata plus `job_id`. Heavy parsing/embedding/extraction work is
   delegated to the worker (ADR-0005).

2. **Read-only extracted artifacts in v1.** Entities, relations, and wiki pages
   are produced by the pipeline (ADR-0001, ADR-0008, ADR-0010). The API does not
   expose mutations for them in v1; human-in-the-loop editing is deferred.

3. **No authentication in v1.** Auth/RBAC is explicitly not decided
   (ADR-0004). The v1 API is unauthenticated and is intended to run inside a
   trusted network or behind an existing gateway.

4. **Problem Details for errors.** All API errors use RFC 7807 Problem Details
   (`application/problem+json`) with consistent `type`, `title`, `status`,
   `detail`, and `instance` fields. Domain exceptions (`RagWikiError` hierarchy)
   map to specific HTTP status codes.

5. **Offset/limit pagination and simple filters.** List endpoints use
   `?offset=&limit=` (default 20, max 100) and support status/type/name filters
   appropriate to each resource.

6. **File storage.** Uploaded files are saved to a configurable `upload_dir`
   using the `Source.id` as a flat filename. Client-provided filenames are
   stored only as metadata.

7. **Optional answer generation.** `POST /queries` always returns the structured
   `RetrievalResult` and generates a natural-language answer by default
   (`generate_answer=true`). Callers can set `generate_answer=false` to retrieve
   context without spending query-model tokens.

## Rationale

- **Small, stable surface.** Limiting v1 to sources, jobs, graph browsing,
  wiki reading, and queries gives clients everything they need without
  premature abstraction. Chunks, merge logs, and other pipeline internals are
  exposed only as nested sub-resources where useful.
- **Consistency with existing architecture.** Async ingestion reuses the
  Postgres-native job queue (ADR-0005) instead of adding a second sync path.
  Read-only graph/wiki endpoints reflect the automated, pipeline-driven nature
  of v1 ingestion (ADR-0010).
- **Future-proof versioning and documentation.** The `/api/v1` prefix and
  built-in `/docs` OpenAPI UI make later breaking changes additive rather than
  destructive.
- **Operational clarity.** Problem Details and a `/health` endpoint give
  operators and generated clients predictable failure and probe semantics.

## Consequences

- Adding auth later will require a global FastAPI dependency change but will
  not force route rewrites.
- Editable wiki pages, streaming query answers, batch uploads, and URL-based
  ingestion are deliberately deferred to later ADRs.
- API tests run against a real Postgres test database with mocked LLM providers,
  because SQLite cannot replicate pgvector behavior.
