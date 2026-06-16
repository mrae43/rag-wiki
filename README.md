# LLM RAG Wiki

> Turn your documents into a living, self-updating knowledge wiki — powered by an LLM, stored in PostgreSQL, deployed on your own infrastructure.

Most RAG systems rediscover knowledge from scratch on every query. **LLM RAG Wiki** is different: as you add documents, an LLM incrementally builds and maintains a persistent wiki — a structured, interlinked knowledge graph that gets richer with every source you add. Cross-references are already there. Contradictions are already flagged. The synthesis already reflects everything you've ingested.

**Before**: upload documents → LLM retrieves and re-derives answers every time.
**After**: upload documents → LLM compiles knowledge into a wiki once, keeps it current, answers from it.

---

## Features

- **Multimodal ingestion** — text, tables, and images parsed into typed chunks (lightweight by default; optional MinerU-backed full multimodal path)
- **Knowledge graph** — entities and relations extracted from every chunk, stored as plain relational tables in Postgres with real-time entity resolution
- **Hybrid retrieval** — *planned* — vector similarity (pgvector) will seed a graph traversal (recursive CTE) for richer, context-aware answers
- **LLM-maintained wiki** — *planned* — markdown pages synthesized and kept current in Postgres; optional export to a directory of `.md` files for Obsidian browsing
- **Pluggable LLM providers** — OpenAI (fully implemented); Anthropic, Azure OpenAI, vLLM, and Ollama are config variants on the OpenAI-compatible path — swap by config, no code changes
- **Single Postgres backend** — vectors, knowledge graph, job queue, and wiki pages all in one database; no Redis, no Neo4j, no separate vector store
- **Background job queue** — Postgres-native (`SELECT FOR UPDATE SKIP LOCKED`), durable and restart-safe, with a clear migration path to Celery/RQ
- **Self-hosted, enterprise-ready** — Docker Compose for small teams, Helm chart for production; single-tenant by design for data sovereignty
- **Obsidian export** — *planned* — `rag-wiki export` will render wiki pages to a directory of `.md` files for graph-view browsing

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │           PostgreSQL 16+             │
  Raw documents         │                                      │
  (PDF, MD, DOCX, ...)  │  ┌──────────┐   ┌───────────────┐  │
        │               │  │  chunks  │   │  entities     │  │
        ▼               │  │ +embedding│  │  +relations   │  │
  ┌──────────┐  ingest  │  └──────────┘   └───────────────┘  │
  │  Parser  │─────────▶│                                      │
  │ (hybrid) │          │  ┌──────────┐   ┌───────────────┐  │
  └──────────┘          │  │   jobs   │   │  wiki_pages   │  │
                        │  │  (queue) │   │  (markdown)   │  │
  Query                 │  └──────────┘   └───────────────┘  │
     │                  └──────────────────────┬──────────────┘
     ▼                                         │
   ┌──────────────────┐ (🔲 planned)           │
   │ Hybrid Retrieval │◀────────────────────────┘
   │ vector seed +    │
   │ graph traversal  │
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐        ┌──────────────────┐ (🔲 planned)
   │   LLMProvider    │        │   Wiki Synthesis  │
   │ (OpenAI / Anthr  │        │  (LLM-maintained  │
   │  / vLLM / Ollama)│        │   wiki_pages)     │
   └──────────────────┘        └──────────────────┘
```

**Three operations drive everything:**

- **Ingest** ✅ — parse source → extract chunks → caption non-text → embed → extract entities/relations → resolve against existing graph → (synthesize/update wiki pages is planned)
- **Query** 🔲 — vector search for seed chunks/entities → graph traversal for context → LLM answer synthesis → optionally file answer back into wiki
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
|---|---|
| OpenAI | GPT-4o, GPT-4o-mini, text-embedding-3-* |
| Azure OpenAI | Same client, `base_url` + `api_version` config |
| Anthropic | Claude 3.5 Sonnet, Claude 3 Haiku |
| vLLM | Any OpenAI-compatible self-hosted model |
| Ollama | Local models (Llama 3, Mistral, etc.) |

Different operations can use different models — e.g. a cheap/fast model for captioning, a stronger model for wiki synthesis. Configured via env vars per operation.

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
LLM_PROVIDER=openai                          # openai | anthropic | openai_compatible
LLM_API_KEY=sk-...
LLM_MODEL_EXTRACTION=gpt-4o-mini
LLM_MODEL_WIKI_SYNTHESIS=gpt-4o
LLM_MODEL_QUERY=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=3072
```

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

> 🔲 **Querying is not yet implemented.** The FastAPI endpoints are still under development. You can query the database directly via SQL or the CLI (coming soon).

### 6. Export to Obsidian (optional)

> 🔲 **Export is not yet implemented.** The CLI stub exists but exits immediately. Once wiki synthesis is built, `rag-wiki export --output ./wiki` will render `.md` files for Obsidian.

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

By default, the lightweight parser (PyMuPDF + unstructured) handles PDFs, DOCX,
and markdown. For GPU-accelerated full multimodal parsing (tables, images,
equations as distinct typed chunks):

```bash
uv pip install rag-wiki[mineru]
```

Then set `PARSER=mineru` in `.env`. MinerU is optional — the system runs fully
without it.

---

## Deployment

### Small team / single instance
Docker Compose is the recommended path. Everything runs in one `compose.yml`:
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

```
rag_wiki/
  main.py          # FastAPI app
  worker.py        # Background job worker
  cli.py           # CLI (rag-wiki export, ...)
  settings.py      # Pydantic-settings config
  exceptions.py    # Domain exception hierarchy
  providers/       # LLMProvider implementations
  ingest/
    pipeline.py    # Full ingestion orchestrator
    parser.py      # MIME-based routing into parsing backends
    chunking.py    # Chunk splitting with configurable overlap
    schemas.py     # ParsedChunk discriminated union
    parsers/       # pdf.py, simple.py, unstructured.py
  graph/
    extraction.py  # Entity/relation extraction from chunks
    schemas.py     # Graph models (Entity, Relation, etc.)
    resolution.py  # Real-time entity resolution (embedding + LLM merge)
    merge.py       # Hard merge of duplicate entities with FK repointing
  retrieval/       # Hybrid retrieval (seed, traverse, assemble) — 🔲 planned
  wiki/            # Wiki page synthesis and export — 🔲 planned
  jobs/            # Job queue (enqueue, claim, complete, fail)
  db/
    models/        # graph.py, wiki.py, jobs.py, source.py (Chunk lives here)
    session.py     # Async session factory
    base.py        # Declarative base
tests/             # Mirrors rag_wiki/ structure
docs/
  adr/             # Architecture Decision Records (ADR-0001 to ADR-0010)
  coding-standards.md
  tech-stack.md
AGENTS.md          # Guidance for LLM coding agents (Claude Code, OpenCode, etc.)
CONTEXT.md         # Domain terminology glossary
```

---

## Roadmap

| Status | Item |
|---|---|
| ✅ Done | Architecture decisions (10 ADRs) |
| ✅ Done | Coding standards, tech stack, agent guidance |
| ✅ Done | Database schema + Alembic migrations |
| ✅ Done | Lightweight parsing pipeline |
| ✅ Done | Docker Compose stack |
| ✅ Done | LLM provider abstraction + OpenAI implementation (Anthropic is a stub) |
| ✅ Done | Entity/relation extraction + real-time resolution |
| ✅ Done | Background job worker |
| ✅ Done | Ingest pipeline orchestration (parse → chunk → embed → extract → resolve) |
| 🔲 Next | Wiki page synthesis |
| 🔲 Next | Hybrid retrieval |
| 🔲 Next | FastAPI endpoints |
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