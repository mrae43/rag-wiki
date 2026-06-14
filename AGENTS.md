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

---

## Tech stack

- Python 3.12+
- FastAPI + Pydantic v2 (async API layer)
- SQLAlchemy 2.0 async + asyncpg driver
- Alembic (schema migrations)
- pgvector + pgvector-python (embedding column type)
- structlog (structured logging — never use `print()` or the stdlib `logging` module directly)
- pytest + pytest-asyncio · ruff (lint & format) · mypy (type checking)
- Docker / Docker Compose (local dev) · Helm chart (production)
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
primary storage. CLI: `rag-wiki export`. (ADR-0006)

**No direct LLM API calls outside the provider abstraction** — all LLM calls
go through the `LLMProvider` protocol (`complete()`, `embed()`,
`caption_image()`). Never import `openai` or `anthropic` outside
`rag_wiki/providers/`. Per-operation model selection via env vars:
`LLM_MODEL_CAPTION`, `LLM_MODEL_EXTRACTION`, `LLM_MODEL_WIKI_SYNTHESIS`,
`LLM_MODEL_QUERY`, `EMBEDDING_MODEL`. (ADR-0007)

**Postgres-native job queue** — a `jobs` table with `SELECT FOR UPDATE SKIP
LOCKED` claiming, behind the interface `enqueue()` / `claim_next()` /
`complete()` / `fail()`. Not Celery/RQ. Worker: `python -m rag_wiki.worker`.
The interface is designed so a future Celery/RQ migration is additive, not a
rewrite. (ADR-0005)

**Single-tenant deployment** — no `tenant_id` columns, no RLS policies.
Auth/RBAC is scoped to users within one organization's deployment. (ADR-0004)

**Hybrid parsing pipeline** — lightweight default (pymupdf + unstructured);
optional MinerU path via `uv pip install rag-wiki[mineru]`, feature-flagged. Both
paths produce the same chunk interface. (ADR-0002)

**Hybrid retrieval, single mode for v1** — vector search seeds → recursive CTE
graph traversal → combined context. No multiple selectable modes in v1, but
seed-finding / traversal / context assembly are separate internal steps.
(ADR-0009)

**Entity resolution at ingest time** — embedding similarity + LLM merge
decision during ingestion; periodic lint pass as backup. Not exact-string-match
only, not defer-all-to-batch. (ADR-0008)

**Automated ingestion for v1** — ingest commits directly. `entities`,
`relations`, and `wiki_pages` must include a `status` column defaulting to
`published` so a future `pending_review` workflow is additive. (ADR-0010)

---

## ADR index

| ADR | Subsystem | Decision |
|-----|-----------|----------|
| 0001 | Knowledge graph | Relational tables (`entities`, `relations`) |
| 0002 | Parsing | Hybrid pipeline (lightweight default + optional MinerU) |
| 0003 | Embeddings | Caption-to-text, single vector space |
| 0004 | Deployment | Single-tenant, per-customer |
| 0005 | Job queue | Postgres-native (`jobs` table, SKIP LOCKED) |
| 0006 | Wiki storage | Postgres `wiki_pages` table, file export optional |
| 0007 | LLM calls | Thin `LLMProvider` protocol, no direct SDK imports outside providers/ |
| 0008 | Entity resolution | Real-time (embedding + LLM) + periodic lint |
| 0009 | Retrieval | Hybrid single-mode (vector seed + graph traversal) |
| 0010 | Ingestion workflow | Fully automated; `status` column for future review queue |

---

## Package layout

```
rag_wiki/
  main.py              # FastAPI app (entrypoint: rag_wiki.main:app)
  worker.py            # Job worker entrypoint (python -m rag_wiki.worker)
  cli.py               # CLI commands (rag-wiki export, ...)
  settings.py          # pydantic-settings config (all env vars)
  exceptions.py        # Domain exception hierarchy rooted in RagWikiError
  providers/           # LLMProvider implementations (openai.py, anthropic.py, base.py)
  ingest/              # Parse → extract → resolve → embed pipeline
  graph/               # Entity/relation extraction, resolution, graph queries
  retrieval/           # Hybrid retrieval (seed-finding, traversal, context assembly)
  wiki/                # Wiki page synthesis and export
  jobs/                # Job queue (enqueue, claim, complete, fail)
  db/                  # SQLAlchemy models, session, Alembic env
tests/
  providers/
  ingest/
  graph/
  retrieval/
  wiki/
  jobs/
```

Test file mirrors source file: `rag_wiki/graph/extraction.py` →
`tests/graph/test_extraction.py`.

---

## Quality commands

```bash
ruff check .          # lint (fix with --fix)
ruff format .         # format
mypy .                # type checking
pytest                # all tests
pytest tests/graph/   # specific module
```

Run in order: `ruff check` → `ruff format` → `mypy` → `pytest`.
All four must pass before considering a task done.

---

## Migrations

Always auto-generate migrations with `alembic revision --autogenerate` — never
hand-write migration files. Run from inside Docker so the tool can inspect the
live schema. See [README.md](README.md) for the full workflow.

---

## Local development

See [README.md#local-development-without-docker](README.md#local-development-without-docker) for host venv setup and quality commands.

---

## Not yet decided

These areas are flagged in ADRs but have no implementation decision yet.
Do not invent a solution for either — raise it as a question first.

- Auth/RBAC implementation (flagged in ADR-0004)
- Observability stack — structured logging config, metrics (flagged in ADR-0004/0008/0010)