# Tech Stack

This document records the concrete tools/libraries chosen to implement the
decisions in `docs/adr/`. Unlike the ADRs, these are mostly standard choices
without major architectural trade-offs — recorded here for clarity, not as
decisions requiring justification.

## Language & runtime
- **Python 3.12+**

## Web/API layer
- **FastAPI** — the API layer over the library core (see project shape decision,
  pre-ADR-0001 discussion). Async-native, fits the async Postgres access pattern
  below.
- **Pydantic v2** — request/response models, and (via `pydantic-settings`)
  environment-driven configuration — supports ADR-0007's per-deployment LLM
  provider config and ADR-0004's config-driven secrets.

## Database (Postgres, ADR-0001/0003/0005/0006)
- **PostgreSQL 16+** with extensions:
  - **pgvector** — embedding storage/similarity search (ADR-0003)
  - (pgcrypto if UUID generation at the DB level is preferred)
- **SQLAlchemy 2.0 (async)** + **asyncpg** driver — ORM/query layer for
  `entities`, `relations`, `chunks`, `wiki_pages`, `jobs`, etc.
- **Alembic** — schema migrations.
- **pgvector-python** — SQLAlchemy/Python integration for the `vector` column
  type (ADR-0003).

## Job queue (ADR-0005)
- Custom Postgres-native queue: a `jobs` table + `SELECT ... FOR UPDATE SKIP
  LOCKED` claiming, implemented as a small internal module behind an
  `enqueue()`/`claim_next()`/`complete()`/`fail()` interface (per ADR-0005's
  consequences) — written to be swappable for Celery/RQ + Redis later without
  changing pipeline code.
- A simple worker entrypoint (`python -m rag_wiki.worker`) that polls and
  executes jobs; can be run as a separate container/process from the API.

## Document parsing (ADR-0002)
- **Lightweight default path**:
  - `pymupdf` (PDF text/table extraction)
  - `unstructured` (general document parsing — docx, html, etc.)
  - Markdown read directly (for Obsidian-clipped sources, per the original LLM
    Wiki doc's tooling tips)
- **Optional MinerU path**: feature-flagged, separate optional dependency group
  (e.g. `uv pip install rag-wiki[mineru]`), isolated behind the same chunk-producing
  interface as the lightweight path so downstream code doesn't differ by path.

## LLM provider abstraction (ADR-0007)
- A small internal `LLMProvider` protocol (`complete()`, `embed()`,
  `caption_image()`).
- **`openai` Python client** — used for the OpenAI-compatible implementation
  (works against OpenAI, Azure OpenAI, vLLM, Ollama via `base_url` override).
- **`anthropic` Python client** — used for the Anthropic implementation.
- Per-operation model selection via config (e.g. `LLM_MODEL_CAPTION`,
  `LLM_MODEL_EXTRACTION`, `LLM_MODEL_WIKI_SYNTHESIS`, `LLM_MODEL_QUERY`,
  `EMBEDDING_MODEL`).
- **`aioboto3`** — optional dependency (`pip install rag-wiki[s3]`) for S3/SeaweedFS
  storage provider implementation (ADR-0015).

## Testing & quality
- **pytest** + **pytest-asyncio** — unit/integration tests.
- **ruff** — linting and formatting (single tool, fast).
- **mypy** — type checking (the codebase leans on protocols/interfaces per
  ADR-0007, which benefits from static checking).

## Deployment (ADR-0004)
- **Docker** images for API and worker.
- **Docker Compose** — local dev / small single-instance deployments (API +
  worker + Postgres + SeaweedFS (S3-compatible storage), ADR-0015).
- **Helm chart** — production deployment target for enterprise customers
  (multi-replica API/worker against a managed or self-hosted Postgres).
- Configuration entirely via environment variables (12-factor style), documented
  in a `.env.example`.

## Storage (ADR-0015)
- **SeaweedFS** — self-hosted S3-compatible store (optional dependency,
  `pip install rag-wiki[s3]`). Provides shared storage across containers in
  Docker Compose without per-host binding mounts.
- **Storage provider** abstraction with `LocalStorageProvider` (filesystem,
  default) and `S3StorageProvider` (aioboto3, optional). Configuration via
  `STORAGE_PROVIDER` and `S3_*` environment variables.
- **upload_dir** persists only in `LocalStorageProvider`; all other storage
  providers use opaque `storage_key` column and external store.

## Wiki file export (ADR-0006)
- A `rag-wiki export` CLI command renders `wiki_pages` rows to a directory of
  `.md` files (optionally with a git commit), for users who want the
  Obsidian/graph-view workflow from the original LLM Wiki pattern.

## Not yet decided
- Auth/RBAC implementation (flagged in ADR-0004, not yet designed).
- Observability stack (structured logging format, metrics — flagged in
  ADR-0004/0008/0010, not yet designed).
