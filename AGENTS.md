# AGENTS.md

Guidance for OpenCode (and any agent) working in this repo. Everything here
is something an agent would likely get wrong without being told.

---

## Before you write any code

Read these files first, every session, in this order:

1. `CONTEXT.md` — domain terminology. Use these terms exactly; do not invent synonyms.
2. `docs/coding-standards.md` — docstrings, error handling, typing, logging, DB conventions. Non-negotiable.
3. The ADR(s) relevant to the subsystem you are touching (see `docs/adr/`). If you are unsure which ADR applies, read `docs/adr/` index below.
4. `docs/harness-engineering.md` — harness design principles for agent workflows (16-step blueprint).
5. `.github/copilot-instruction.md` — git hygiene, commit message format, and PR conventions. Follow this file when creating any PR.

If a task is ambiguous and no ADR covers it, **stop and ask** rather than
inventing a solution that may conflict with a decision already made elsewhere.

---

## Project

Self-hosted system that builds an LLM-maintained knowledge wiki from documents
over a Postgres + knowledge graph backend.

- `CONTEXT.md` — terminology (Source, Chunk, Entity, Relation, Wiki, Source of Truth)
- `docs/llm-wiki.md` — broader context of the project (the LLM Wiki pattern)
- `docs/adr/` — all architectural decisions and their rationale
- `docs/tech-stack.md` — concrete library/tool choices
- `docs/coding-standards.md` — code conventions (read before writing anything)
- `docs/harness-engineering.md` — broader context for agentic system design principles
- `docs/agent-harness.md` — conceptual mapping of the [16-step agent harness blueprint] in the system

---

## Tech stack

- Python 3.12+
- FastAPI + Pydantic v2 (async API layer)
- SQLAlchemy 2.0 async + asyncpg driver
- Alembic (schema migrations)
- pgvector + pgvector-python (embedding column type)
- structlog (structured logging — never use `print()` or the stdlib `logging` module directly)
- pytest + pytest-asyncio · ruff (lint & format) · mypy (type checking)
- Docker / Docker Compose (local dev) · Helm chart (production) — planned
- Configuration via environment variables only — see `.env.example`

---

## Architecture constraints

These are locked-in decisions from ADRs. Violating them requires writing a new
ADR or explicitly revisiting an existing one — not a unilateral change in code.

**Postgres is the only backend** — no Redis, no Neo4j, no separate graph DB,
no separate vector store. Vectors (pgvector), knowledge graph, job queue, and
wiki pages all live in one PostgreSQL 16+ database. (ADR-0001/0003/0005/0006)

**Knowledge graph = plain relational tables** — `entities` and `relations`
tables with recursive CTEs for traversal. Not Apache AGE, not JSONB blobs.
(ADR-0001)

**All chunks embed as text** — images/tables/equations are captioned-to-text
first, then embedded with a single text embedding model into one `vector`
column. No multimodal embeddings, no separate embedding spaces. (ADR-0003)

**Wiki pages live in Postgres** — `wiki_pages` table is the source of truth.
File export to `.md` files (for Obsidian) is optional and derived, never
primary storage. CLI: `rag-wiki export`. Export emits the OKF (Open Knowledge
Format) bundle format — front-matter, flat `entities/`+`sources/` layout,
inline `[[slug]]`→Markdown link rewrite, `index.md`+`log.md`, manifest-based
diff. (ADR-0006, ADR-0019)

**No direct LLM API calls outside the provider abstraction** — all LLM calls
go through the `ChatProvider` and `EmbeddingProvider` protocols (defined in
`rag_wiki/providers/base.py`). `ChatProvider` provides `complete()` and
`caption_image()`; `EmbeddingProvider` provides `embed()`. Never import
`openai` or `anthropic` outside `rag_wiki/providers/`. Per-operation model
selection via env vars:
`LLM_MODEL_CAPTION`, `LLM_MODEL_EXTRACTION`, `LLM_MODEL_RESOLUTION`,
`LLM_MODEL_WIKI_SYNTHESIS`, `LLM_MODEL_QUERY`, `LLM_MODEL_QUERY_CLASSIFICATION`, `EMBEDDING_MODEL`.
Also set `LLM_PROVIDER` (chat) and `LLM_EMBEDDING_PROVIDER` (embeddings),
`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_API_VERSION`. (ADR-0007)

**Postgres-native job queue** — a `jobs` table with `SELECT FOR UPDATE SKIP
LOCKED` claiming, behind the interface `enqueue()` / `claim_next()` /
`complete_job()` / `fail_job()` / `release_claim_to_pending()`. Not Celery/RQ. Worker: `python -m rag_wiki.worker`.
The interface is designed so a future Celery/RQ migration is additive, not a
rewrite. (ADR-0005)

**Single-tenant deployment** — no `tenant_id` columns, no RLS policies.
Auth/RBAC is scoped to users within one organization's deployment. (ADR-0004)

**Hybrid parsing pipeline** — lightweight default (pymupdf + unstructured);
MinerU path defined as optional dep (`uv pip install rag-wiki[mineru]`) and
accepted in `settings.py`, but the parser dispatch does not yet handle
`"mineru"`. Both paths target the same chunk interface. (ADR-0002)

**Hybrid retrieval, single mode for v1** ✅ — vector search seeds → recursive CTE
graph traversal → combined context. No multiple selectable modes in v1, but
seed-finding / traversal / context assembly are separate internal steps.
(ADR-0009)

**Entity resolution at ingest time** — embedding similarity + LLM merge
decision during ingestion; periodic lint pass as backup. Not exact-string-match
only, not defer-all-to-batch. (ADR-0008)

**Automated ingestion for v1** — ingest commits directly. `entities`,
`relations`, and `wiki_pages` must include a `status` column defaulting to
`published` so a future `pending_review` workflow is additive. (ADR-0010)

**Transient Graph View for analysis** — community detection, PageRank, cohesion
scoring, and surprising-connection detection run as a batch job inside the
worker. The Graph View is a transient `networkx` graph loaded from Postgres,
run inside `asyncio.to_thread`, discarded at the end of the run; never a
backend, never persisted. All outputs (Communities, Cohesion, God Nodes,
Surprising Connections) live in four append-only `run_id`-keyed snapshot tables
(`graph_analysis_runs`, `community_summaries`, `community_members`,
`surprising_connections`). Triggered manually via `rag-wiki analyze-graph` — no
auto-enqueue after ingest. The existing recursive CTE traversal (`retrieval/`)
and wiki synthesis (`wiki/`) are untouched; the analysis layer is additive. Read
endpoints under `rag_wiki/api/routes/analysis.py` expose the persisted snapshots;
MCP tools are deferred to Phase B. (ADR-0020)

**Planner-driven processing** — a planner module classifies documents (density,
content type) and queries (intent, complexity) and routes each operation to the
optimal strategy and model. Planner decisions are logged in every plan for
provenance. (ADR-0014)

**Pluggable storage provider** — source files are stored on the local filesystem
by default (`STORAGE_PROVIDER=local`). An S3-compatible backend
(`STORAGE_PROVIDER=s3`) is available via the `StorageProvider` protocol; swap by
config, no application code changes. (ADR-0015)

**Stage-1 deployment: trusted-clients-only** — the Backend ships unauthenticated,
behind a reverse proxy on a private Tailscale network. No inbound auth code in
rag_wiki; auth is owned by the future Interface App. Compose-on-VM topology
(`deploy/docker-compose.prod.yml`), Caddy internal-CA TLS, GHCR image, manual-gated
CI/CD, `pg_dump` backups. MCP is stdio-only in prod; MCP HTTP is hardened to
loopback-only via a settings validator. Every Stage-2 enhancement (Helm chart,
shared API key, auto-deploy, SeaweedFS, MCP HTTP service, observability stack)
is additive — no Stage-1 artifact is rewritten. (ADR-0017)

**User-agnostic Backend** — no `created_by` columns on any table, no `X-User-Id`
header, no `users` table. The Interface App owns all user-specific state (auth,
query history, bookmarks, user→source_id mappings) in its own data store. The
Backend's `/sources` and `/jobs` list endpoints stay flat (no user filter); the
app fetches by the IDs it remembers per user. If user attribution becomes a real
Backend need, a future ADR adds it additively — do not add `created_by` columns
unilaterally. (ADR-0021)

**Interface App is a separate project** — its own repo, different tech stack
(likely TypeScript + Vue/React), deployed independently. No frontend code, UI
templates, or JS/TS ever enters this repo. The two systems share only
PostgreSQL. This repo builds the headless Backend only. (ADR-0021)

---

## ADR index

| ADR  | Subsystem          | Decision                                                                                                             |
| ---- | ------------------ | -------------------------------------------------------------------------------------------------------------------- |
| 0001 | Knowledge graph    | Relational tables (`entities`, `relations`)                                                                          |
| 0002 | Parsing            | Hybrid pipeline (lightweight default + optional MinerU)                                                              |
| 0003 | Embeddings         | Caption-to-text, single vector space                                                                                 |
| 0004 | Deployment         | Single-tenant, per-customer                                                                                          |
| 0005 | Job queue          | Postgres-native (`jobs` table, SKIP LOCKED)                                                                          |
| 0006 | Wiki storage       | Postgres `wiki_pages` table, file export optional                                                                    |
| 0007 | LLM calls          | Thin `ChatProvider` + `EmbeddingProvider` protocols, no direct SDK imports outside providers/                        |
| 0008 | Entity resolution  | Real-time (embedding + LLM) + periodic lint                                                                          |
| 0009 | Retrieval          | Hybrid single-mode (vector seed + graph traversal)                                                                   |
| 0010 | Ingestion workflow | Fully automated; `status` column for future review queue                                                             |
| 0011 | Parsing            | MinerU primary parser (deferred); lightweight is default                                                             |
| 0012 | Retrieval          | Hybrid retrieval implementation — vector seed + CTE traversal + context assembly                                     |
| 0013 | API                | FastAPI API surface for automation and integration                                                                   |
| 0014 | Planner            | Ingest and query planner — classify documents/queries by confidence and density, route to optimal strategy and model |
| 0015 | Storage            | S3-compatible storage provider (SeaweedFS, MinIO); local default, s3 optional via config                             |
| 0016 | MCP server         | Python FastMCP wrapper, dual-transport (stdio+Streamable HTTP), HTTP proxy to existing FastAPI                       |
| 0017 | Deployment         | Stage-1 Compose-on-VM topology, trusted-clients-only, manual-gated CI/CD, Tailscale-internal TLS, stdio-only MCP   |
| 0018 | CI/Security        | Branch protection, release & security policy for the public portfolio repo (Rulesets, no-bypass, tag releases, Dependabot/CodeQL) |
| 0019 | Wiki export        | Adopt OKF (Open Knowledge Format) as the `rag-wiki export` format — export-only, Postgres stays SoT; front-matter set, flat directory, inline `[[slug]]` rewrite, manifest-based `log.md`, per-page atomic writes |
| 0020 | Graph analysis     | Transient networkx Graph View + per-run snapshot tables (`graph_analysis_runs`, `community_summaries`, `community_members`, `surprising_connections`); manual CLI trigger, full re-cluster, coexists with retrieval CTE; FastAPI read endpoints in v1, MCP deferred to Phase B |
| 0021 | Interface App      | Backend↔Interface App contract — user-agnostic Backend (no `created_by`, no `users` table; app owns auth + user state), HTTP-API-only in prod (confirms ADR-0017 §6), poll-based updates, `progress` JSONB on `jobs`, `/search` FTS endpoint, `/graph` whole-graph dump endpoint |
| 0022 | Query              | SSE streaming — `complete_stream()` async generator on `ChatProvider` (extends ADR-0007) + `POST /api/v1/queries/stream`; sync endpoint stays for MCP; shared `run_query()` pipeline. Resolves ADR-0013's deferred streaming |
| 0023 | Export             | Export API + generalized `GET /api/v1/jobs/{job_id}/artifact` download — revisits ADR-0019 §9 (CLI-only → add `export_bundle` job + `POST/GET /api/v1/export`); on-the-fly tar.gz stream; one download path for all artifact-producing jobs |
| 0024 | Output generation  | Generated Output pipeline — `generate_output` job (LLM slide synthesis, separate from `export_bundle`); input mirrors `/queries` reusing `retrieve()`; one slide spec → PPTX (`python-pptx`) + HTML carousel (Jinja2); shared artifact download (ADR-0023) |

---

## Package layout

```
rag_wiki/
  main.py              # FastAPI app (entrypoint: rag_wiki.main:app)
  worker.py            # Job worker entrypoint (python -m rag_wiki.worker)
  cli.py               # CLI commands (rag-wiki ingest, rag-wiki export, ...)
  settings.py          # pydantic-settings config (all env vars)
  exceptions.py        # Domain exception hierarchy rooted in RagWikiError
  api/                 # FastAPI routes, schemas, dependencies, middleware
    router.py            # Top-level router mount
    routes/              # Per-resource route modules
    schemas.py           # Request/response schemas
    dependencies.py      # FastAPI dependency injection
    middleware.py        # Custom middleware (error handling, etc.)
  providers/           # ChatProvider + EmbeddingProvider implementations
    base.py              # Protocols (ChatProvider, EmbeddingProvider, data models)
    openai.py            # Full implementation (complete, embed, caption_image)
    googleai.py          # EmbeddingProvider implementation (Gemini embeddings, REST API)
    anthropic.py         # Stub — TODO (#TODO comment, no code)
    __init__.py          # Retry wrapper, provider registry, get_chat_provider()
  ingest/              # Parse → chunk → caption → embed → extract → resolve pipeline
  planner/             # Ingest and query planner — classify, route, strategy selection
    base.py              # Base planner classes
    ingest.py            # Ingest-specific planning
    query.py             # Query-specific planning
    exceptions.py        # Planner-specific exceptions
  graph/               # extraction.py, resolution.py, merge.py, schemas.py
  retrieval/           # Hybrid retrieval (seeds.py, traversal.py, context.py, scoring.py, schemas.py, orchestrator.py)
  analysis/            # Graph analysis (view.py, algorithms.py, runner.py, schemas.py) — transient networkx
  wiki/                # Wiki synthesis (synthesis.py, context.py, slug.py, templates/)
  jobs/                # Job queue (enqueue, claim_next, complete_job, fail_job)
  storage/             # Pluggable storage provider
    base.py              # StorageProvider protocol
    local.py             # Local filesystem backend
    s3.py                # S3-compatible backend
  mcp/                 # MCP server (FastMCP wrapper, stdio + Streamable HTTP)
    __init__.py          # Exports create_mcp_server()
    server.py            # FastMCP factory
    tools.py             # Tool registration, backend proxy
    transport.py         # run() entrypoint, transport dispatch
    errors.py            # MCP error message formatter
  prompts/             # LLM prompt templates
  db/
    models/              # graph.py, wiki.py, jobs.py, source.py, index.py (Chunk lives here), analysis.py (run snapshots)
    session.py           # Async session factory
    base.py              # Declarative base, UUIDMixin, TimestampMixin
tests/
  providers/
  ingest/
  graph/
  db/                  # test_models.py, test_migration.py, test_smoke.py
  retrieval/
  wiki/
  jobs/
  api/                 # API route tests
  planner/
  storage/
  mcp/                 # MCP server tests
  settings/            # Settings/configuration tests
  conftest.py            # Shared fixtures
  test_smoke.py          # Top-level smoke tests
```

Test file mirrors source file where possible: `rag_wiki/graph/extraction.py` →
`tests/graph/test_extraction.py`. Some modules (e.g., `schemas.py`, `main.py`,
`worker.py`, `cli.py`, `session.py`) lack dedicated tests. Top-level tests
(`tests/test_smoke.py`) cover shared concerns. New test files should follow the
mirror pattern when adding a new module.

---

## Quality commands

All commands must be run through `uv run` to use the correct venv:

```bash
uv run ruff check .          # lint (fix with --fix)
uv run ruff format .         # format
uv run mypy .                # type checking
uv run pytest                # all tests
uv run pytest tests/graph/   # specific module
```

Run in order: `ruff check` → `ruff format` → `mypy` → `pytest`.
All four must pass before considering a task done.

**Before running `pytest`,** ensure all dependencies including dev tools are installed:

```
uv sync --extra dev
```

Do not use `pip install` for a missing test dependency — that means `--extra dev` was omitted from `uv sync`. Repeated ad-hoc `pip install` calls drift from the lockfile.

---

## Migrations

Always auto-generate migrations with `alembic revision --autogenerate` — never
hand-write migration files. Run from inside Docker so the tool can inspect the
live schema. See [README.md](README.md) for the full workflow.

---

## Local development

See [README.md#local-development-without-docker](README.md#local-development-without-docker) for host venv setup and quality commands.

### `.venv` owned by root (permission denied with `uv run`)

If `uv run` fails with `Permission denied` on `.venv/` files, the venv was
accidentally created as root (e.g. via Docker container, `sudo uv` install, or
`sudo uv sync`). Fix:

```bash
./scripts/fix-venv.sh
```

If you need to repair it manually, the equivalent is:

```bash
sudo chown -R "$(id -un)":"$(id -gn)" .venv
rm -rf .venv && uv venv --python "$(command -v python3)" && uv sync --extra dev
```

To prevent recurrence: ensure `uv` is installed under your user
(`curl -LsSf https://astral.sh/uv/install.sh | sh`), not via `sudo` or pip
system-wide.

---

## Deferred (decided — do not implement)

These areas have explicit decisions recorded in ADRs, but the implementation is
**deferred** to a later phase or a separate project. Do not build any of them
unilaterally — the ADR is the constraint, not a gap.

- **Auth/RBAC** — explicitly deferred to the Interface App (ADR-0021). The
  Backend ships unauthenticated behind a network-isolation trust boundary
  (ADR-0017). No `created_by` columns, no `users` table, no `X-User-Id` header.
- **Interface App** — a separate project (TypeScript + Vue/React, different repo).
  This repo builds the headless Backend only. No frontend code, UI templates, or
  JS/TS ever enters this repo. (ADR-0021)
- **Observability stack** (metrics, dashboard, Loki/Grafana) — explicitly
  deferred to Stage-2 (ADR-0017 §7). Current ops floor is structlog to stdout
  + Docker log rotation + `GET /health`. Do not add Prometheus metrics,
  OpenTelemetry tracing, or a telemetry export path.
