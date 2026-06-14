# ADR-0005: Postgres-native job queue for ingestion processing

## Status
Accepted

## Context
Ingestion (parsing, captioning, entity/relation extraction, embedding, wiki
synthesis) involves multiple LLM calls and can take significant time for large
documents. This cannot run synchronously inside an HTTP request handler if the
system is to be "enterprise-grade" (ADR-0004): it needs retries, durability across
restarts, and observability of job status.

Options considered:

1. **Synchronous processing** (FastAPI `BackgroundTasks`, in-process) — simplest,
   zero extra infrastructure, but not restart-safe, no retries, doesn't scale
   beyond one process.
2. **Celery/RQ + Redis** — mature, widely-used job queue tooling, but introduces
   Redis as a second datastore alongside Postgres.
3. **Postgres-native job queue** — a `jobs` table, workers claim rows via
   `SELECT ... FOR UPDATE SKIP LOCKED` (or the `pgmq` extension). Durable,
   retryable, restart-safe, no additional infrastructure beyond Postgres.

## Decision
Use a **Postgres-native job queue** (option 3) for now: a `jobs` table with
`SELECT FOR UPDATE SKIP LOCKED`-based claiming, status/attempt tracking, and a
worker process (or pool) that polls and executes jobs.

This is an **explicitly provisional** decision. The intent is to migrate to
Celery/RQ + Redis (option 2) in the future once throughput/concurrency needs
exceed what a polling Postgres queue comfortably handles.

## Rationale
- **Consistent with the project's "single Postgres backend" narrative** —
  vectors (pgvector), knowledge graph (ADR-0001), and now jobs all live in one
  datastore. Customers self-hosting (ADR-0004) only need to operate Postgres.
- **Durable and retry-capable from day one**, meeting the enterprise reliability
  bar without adding Redis as a hard dependency for an initial deployment.
- **Lower operational footprint for early customers/demos** — one fewer service
  to run, monitor, and secure.
- **Migration path is explicit and isolated**: by designing the job
  producer/consumer interface as a small abstraction (`enqueue(job)`,
  `claim_next()`, `complete(job_id)`, `fail(job_id, error)`), swapping the
  Postgres-backed implementation for a Celery/RQ-backed one later should not
  require changes to the ingestion pipeline logic itself.

## Consequences
- Worker concurrency is limited by polling frequency and `SKIP LOCKED` contention
  — fine for low/moderate throughput, but will need tuning or replacement under
  high concurrent ingestion load.
- The job interface (enqueue/claim/complete/fail) must be defined as an
  abstraction layer now, even though only the Postgres implementation exists, so
  the future Celery/RQ migration is additive rather than a rewrite.
- No Redis dependency for v1 — keep this explicit in deployment docs so it isn't
  silently introduced later without revisiting this ADR.
