# LLM RAG Wiki

> Turn your documents into a living, self-updating knowledge wiki — powered by an LLM, stored in PostgreSQL, deployed on your own infrastructure.

Most RAG systems rediscover knowledge from scratch on every query. **LLM RAG Wiki** is different: as you add documents, an LLM incrementally builds and maintains a persistent wiki — a structured, interlinked knowledge graph that gets richer with every source you add. Cross-references are already there. Contradictions are already flagged. The synthesis already reflects everything you've ingested.

**Before**: upload documents → LLM retrieves and re-derives answers every time.
**After**: upload documents → LLM compiles knowledge into a wiki once, keeps it current, answers from it.

---

## Features

- **Multimodal ingestion** — text, tables, and images parsed into typed chunks (lightweight by default; optional MinerU-backed full multimodal path)
- **Knowledge graph** — entities and relations extracted from every chunk, stored as plain relational tables in Postgres with real-time entity resolution
- **Hybrid retrieval** — vector similarity (pgvector) seeds a graph traversal (recursive CTE) for richer, context-aware answers
- **Intelligent planner** — classifies queries and documents by confidence and density, routes each operation to the optimal processing strategy and model
- **LLM-maintained wiki** — markdown pages synthesized and kept current in Postgres during ingestion; optional export to a directory of `.md` files for Obsidian browsing is planned
- **Pluggable LLM providers** — OpenAI (fully implemented); Anthropic (stub); Azure OpenAI, vLLM, and Ollama via the OpenAI provider with `base_url` config — swap by config, no code changes
- **Single Postgres backend** — vectors, knowledge graph, job queue, and wiki pages all in one database; no Redis, no Neo4j, no separate vector store
- **Background job queue** — Postgres-native (`SELECT FOR UPDATE SKIP LOCKED`), durable and restart-safe, with a clear migration path to Celery/RQ
- **Pluggable storage** — source files stored locally or on S3-compatible backends (SeaweedFS, MinIO); swap by config, no application code changes
- **Self-hosted, enterprise-ready** — Docker Compose for small teams, Helm chart for production; single-tenant by design for data sovereignty
- **Obsidian export** — *planned* — `rag-wiki export` will render wiki pages to a directory of `.md` files for graph-view browsing

---

## Architecture

```
  Raw documents                                        Query
  (PDF, MD, DOCX, ...)                                   │
        │                                                ▼
        ▼                                     ┌──────────────────┐
  ┌──────────┐                                │   LLMProvider    │
  │  Parser  │                                │  (OpenAI /       │
  │ (hybrid) │                                │   Anthropic)     │
  └────┬─────┘                                └────────┬─────────┘
       │                                               │
       ▼                                               │
  ┌──────────────┐                                      │
  │   Planner    │                                      │
  │  (classify,  │                                      │
  │   route)     │                                      │
  └──────┬───────┘                                      │
         │                                              │
         ▼                                              │
  ┌─────────────────────────────────────────────────────────────┐
  │                    PostgreSQL 16+                            │
  │                                                              │
  │  ┌──────────┐   ┌───────────────┐   ┌────────────────────┐  │
  │  │  chunks  │   │  entities     │   │  Hybrid Retrieval  │  │
  │  │ +embedding│  │  +relations   │   │  vector seed  →    │  │
  │  └──────────┘   └───────────────┘   │  graph traversal   │  │
  │                                      │  → context assembly│  │
  │  ┌──────────┐   ┌───────────────┐   └────────────────────┘  │
  │  │   jobs   │──▶│  worker       │───▶  ┌───────────────┐    │
  │  │  (queue) │   │  (claim +     │    │  wiki_pages   │    │
  │  └──────────┘   │   synthesize) │    │  (markdown)   │    │
  │                  └───────────────┘    └───────┬───────┘    │
  └──────────────────────────────────────────────┼─────────────┘
                                                   │
                                                   ▼
                                           ┌──────────────┐
                                           │   Storage    │
                                           │ (local / S3) │
                                           └──────────────┘
```

**Three operations drive everything:**

- **Ingest** ✅ — parse source → extract chunks → caption non-text → embed → extract entities/relations → resolve against existing graph → synthesize/update wiki pages for every entity and source. Operations are routed through the planner which selects the processing strategy per document.
- **Query** ✅ — query classified by the planner, then hybrid retrieval (vector seed → graph traversal → context assembly) with optional LLM-generated answer via `POST /api/v1/queries`
- **Lint** 🔲 — periodic health check: find duplicate entities, contradictions, orphan pages, stale claims, missing cross-references

See `docs/adr/` for all architectural decisions and their rationale.

---

## Why self-hosted?

Enterprise knowledge bases contain sensitive documents — internal research, customer data, strategic plans. You should not send those to a third-party SaaS. LLM RAG Wiki is designed to run entirely on your own infrastructure:

- **Data never leaves your environment** — your documents, your Postgres, your LLM endpoint
- **Bring your own LLM** — point it at Azure OpenAI, a self-hosted vLLM instance, or Ollama; swap by changing an env var
- **No vendor lock-in** — everything is standard Python, standard Postgres, standard Docker

---

## Supported LLM providers

| Provider | Notes |
|---|---|---|
| OpenAI | Full implementation (chat + embeddings); GPT-4o, GPT-4o-mini, text-embedding-3-* |
| Azure OpenAI | Used via the OpenAI provider with `base_url` + `api_version` |
| Anthropic | Stub (`rag_wiki/providers/anthropic.py`); not yet implemented or registered |
| vLLM | Used via the OpenAI provider with custom `base_url` |
| Ollama | Used via the OpenAI provider with custom `base_url` |

Different operations can use different models — e.g. a cheap/fast model for captioning, a stronger model for wiki synthesis. Configured via env vars per operation.

---

## Setup

Choose the setup path that fits your environment:

- **Docker (recommended)** — start the full stack (API + worker + Postgres) with a single command. See [Quickstart](#quickstart).
- **Local (no Docker)** — run directly on your host for faster iteration or IDE debugging. See [Local development](#local-development-without-docker).
- **Production** — deploy with Helm on Kubernetes for multi-replica, managed Postgres, and enterprise configuration. See [Deployment](#deployment).

---

## Quickstart

### Prerequisites

- Docker and Docker Compose
- An LLM API key (OpenAI, Anthropic, or a self-hosted endpoint)
- Git

### 1. Clone and configure

```bash
git clone https://github.com/your-username/llm-rag-wiki.git
cd llm-rag-wiki
cp .env.example .env
```

Edit `.env` — at minimum set:

```env
DATABASE_URL=postgresql+asyncpg://rag_wiki:rag_wiki@db:5432/rag_wiki
LLM_PROVIDER=openai                          # openai | anthropic
LLM_API_KEY=sk-...
LLM_MODEL_EXTRACTION=gpt-4o-mini
LLM_MODEL_WIKI_SYNTHESIS=gpt-4o
LLM_MODEL_QUERY=gpt-4o
EMBEDDING_MODEL=gemini-embedding-2
EMBEDDING_DIMENSIONS=3072
```

See `.env.example` for the full list of 30+ config options (per-operation model
selection, entity resolution, retrieval parameters, logging, etc.).

### 2. Start the stack

```bash
docker compose up
```

This starts: PostgreSQL 16 with pgvector, the API server (`rag_wiki.main:app`), and the background worker (`rag_wiki.worker`).

### 3. Run database migrations

Migrations run automatically on container start via `docker-entrypoint.sh`. You
can also run them manually:

```bash
docker compose exec api alembic upgrade head
```

**Creating new migrations** — after changing SQLAlchemy models, auto-generate a
migration from inside Docker (so the tool can inspect the live schema):

```bash
docker compose run --rm api uv run alembic revision --autogenerate -m "describe your change"
```

Review the generated file in `alembic/versions/` before committing. Never hand-write migration files.

### 4. Ingest a document

```bash
docker compose exec api rag-wiki ingest /path/to/your/document.pdf
```

The job is queued and processed in the background. Check the worker logs for progress.

### 5. Query the wiki

Ask a question over the wiki using the FastAPI endpoint:

```bash
curl -X POST http://localhost:8000/api/v1/queries \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the key findings?", "generate_answer": true}'
```

Set `generate_answer: false` to retrieve structured context without spending
tokens on an LLM answer. See `docs/api.md` for the full endpoint reference.

### 6. Export to Obsidian (optional)

> 🔲 **Export is not yet implemented.** The CLI stub exists but exits immediately. `rag-wiki export --output ./wiki` will render `.md` files for Obsidian.

---

## API

The RagWiki HTTP API is built with FastAPI and mounted at `/api/v1`. It is
unauthenticated in v1 and intended to run inside a trusted network or behind
an existing gateway.

### Running the API

With Docker Compose:

```bash
docker compose up
```

Or directly with `uvicorn`:

```bash
uv run uvicorn rag_wiki.main:app --host 0.0.0.0 --port 8000 --reload
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Bind host |
| `API_PORT` | `8000` | Bind port |
| `UPLOAD_DIR` | `./uploads` | Directory for uploaded source files |
| `UPLOAD_MAX_FILE_SIZE_BYTES` | `104857600` | Maximum upload size (100 MB) |
| `CORS_ORIGINS` | `""` | Comma-separated allowed origins |
| `STORAGE_PROVIDER` | `local` | `local` or `s3` (S3-compatible backends) |
| `S3_BUCKET` | `rag-wiki` | S3 bucket name |
| `S3_ENDPOINT_URL` | `""` | S3 endpoint (e.g. SeaweedFS, MinIO) |
| `S3_ACCESS_KEY_ID` | `""` | S3 access key |
| `S3_SECRET_ACCESS_KEY` | `""` | S3 secret key |
| `S3_REGION` | `us-east-1` | S3 region |
| `PLANNER_VERSION` | `1.0.0` | Planner version identifier |
| `LLM_MODEL_QUERY_CLASSIFICATION` | `gpt-4o-mini` | Model for query intent classification |
| `PLANNER_CONFIDENCE_HIGH` | `0.8` | Confidence threshold for direct execution |
| `PLANNER_CONFIDENCE_LOW` | `0.5` | Confidence threshold for escalated depth |
| `PLANNER_CONFIDENCE_MINIMUM` | `0.5` | Minimum confidence before halt |
| `PLANNER_DENSITY_LARGE_THRESHOLD_BYTES` | `10485760` | File size threshold for "large" classification |

### Documentation

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Full reference: `docs/api.md`

---

## Local development (without Docker)

If you want to run the code directly on your host (e.g. for faster iteration or IDE debugging):

```bash
# 1. Create the venv with the host Python
uv venv --python /usr/bin/python3

# 2. Install all dependencies including dev tools
uv sync --extra dev

# 3. Run the quality gate
ruff check .
ruff format .
mypy .
pytest --cov=rag_wiki --cov-fail-under=60
```

> **Never mix host and container venvs.** The `Dockerfile` installs the venv at `/opt/venv` so the `docker-compose.yml` bind mount `.:/app` never overwrites it. If `.venv` is root-owned or points to `/usr/local/bin/python3`, it was contaminated by Docker. Delete it and recreate with `uv venv --python /usr/bin/python3`.

---

## Optional: full multimodal parsing (MinerU)

> 🔲 **MinerU integration is not yet implemented.** The `PARSER` env var and
> `rag-wiki[mineru]` extra are defined in `settings.py`/`pyproject.toml`, but
> the parser dispatch does not yet handle `"mineru"` — it raises a `ParseError`.
> Implementation is tracked as a planned roadmap item.

By default, the lightweight parser (PyMuPDF + unstructured) handles PDFs, DOCX,
and markdown. The system runs fully without MinerU.

---

## Deployment

### Small team / single instance
Docker Compose is the recommended path. Everything runs in one `docker-compose.yml`:
API, worker, and Postgres. Suitable for a team of up to ~20 with moderate
ingestion volume.

### Production / enterprise
A Helm chart is *planned* for Kubernetes deployment with:
- Multi-replica API and worker deployments
- External managed Postgres (RDS, Cloud SQL, Supabase, etc.)
- Config via Kubernetes Secrets / environment injection
- Horizontal scaling of workers for high ingestion throughput

```bash
helm install rag-wiki ./helm/rag-wiki -f values.yaml
```

---

## Project structure

[See `[#Project Structure]` in `AGENTS.md`](AGENTS.md#package-layout) — it is the
single source of truth for the package layout and test mirroring conventions.

---

## Roadmap

| Status | Item |
|---|---|
| ✅ Done | Architecture decisions (15 ADRs) |
| ✅ Done | Coding standards, tech stack, agent guidance |
| ✅ Done | Database schema + Alembic migrations |
| ✅ Done | Lightweight parsing pipeline |
| ✅ Done | Docker Compose stack |
| ✅ Done | LLM provider abstraction + OpenAI implementation (Anthropic is a stub) |
| ✅ Done | Entity/relation extraction + real-time resolution |
| ✅ Done | Background job worker |
| ✅ Done | Ingest pipeline orchestration (parse → chunk → embed → extract → resolve → enqueue wiki synthesis) |
| ✅ Done | Hybrid retrieval (vector seed → graph traversal → context assembly) |
| ✅ Done | Wiki page synthesis (entity pages + source summaries) |
| ✅ Done | FastAPI endpoints |
| 🔲 Planned | Auth / RBAC |
| 🔲 Planned | Observability (structured logging, metrics) |
| 🔲 Planned | Lint operation (periodic graph health check) |
| 🔲 Planned | Obsidian export CLI |
| 🔲 Planned | Optional MinerU multimodal path |
| 🔲 Planned | Helm chart |
| 🔲 Planned | Ingestion review queue (pending_review workflow) |
| 🔲 Planned | Celery/RQ + Redis job queue migration path |

---

## Design influences

- **[LLM Wiki](https://github.com/karpathy)** (Andrej Karpathy) — the core
  insight: an LLM-maintained wiki is a *compounding artifact*. Knowledge is
  compiled once and kept current, not re-derived on every query. The
  Ingest / Query / Lint operation model and the `index.md`/`log.md` pattern
  come from here.
- **[RAG-Anything](https://github.com/HKUDS/RAG-Anything)** (*RAG-Anything:
  All-in-One RAG Framework*) — multimodal document parsing, dual-graph
  construction (knowledge graph + chunk-level), and hybrid graph+vector
  retrieval. This project adapts these ideas onto a Postgres-only backend.
- **Building a Second Brain / CODE** (Tiago Forte) — Capture / Organize /
  Distill / Express maps onto Ingest / graph construction / wiki synthesis /
  Query+file-back. "Organize for actionability, not perfect taxonomy" directly
  informs the entity resolution design (ADR-0008).
- **Vannevar Bush's Memex (1945)** — a personal, curated knowledge store with
  associative trails between documents, where connections are as valuable as
  documents themselves. The unsolved part of Bush's vision — *who does the
  maintenance* — is what this project's LLM pipeline is designed to solve.

---

## Contributing

Read before writing any code:

1. `CONTEXT.md` — domain terminology
2. `docs/coding-standards.md` — docstrings, error handling, typing, logging
3. `AGENTS.md` — if using an LLM coding agent (Claude Code, OpenCode, etc.)

```bash
ruff check .    # lint
ruff format .   # format
mypy .          # type check
pytest          # tests
```

All four must pass before opening a PR.

---

## License

MIT