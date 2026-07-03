# ADR-0017: Stage-1 deployment topology

## Status

Accepted

## Context

The system has been developed for months as a headless AI backend (FastAPI +
worker + MCP + Postgres). The author now wants to:

1. Deploy it as a standalone system as soon as possible, following best
   practices.
2. Move authentication into a future dedicated full-stack **Interface App**,
   keeping the Backend headless and auth-free.
3. Learn continuous delivery (CI/CD) by shipping a real deploy pipeline.
4. Position rag_wiki as a system that is "easy to connect within any existing
   systems" — starting with the author as the first user, with an SME
   monetization path later.

ADRs 0004 (single-tenant), 0013 (no auth in v1), and 0016 (dual-transport MCP)
already establish the *abstract* posture: trusted-network-only, no inbound auth,
MCP over stdio by default. But none records the *concrete* deployment contract
— which topology, which CI shape, which secrets posture, which ops floor, which
MCP transport ships in production. This ADR closes that gap.

The key constraint is that every Stage-1 decision must be **additive-safe**:
Stage-2 enhancements (Helm chart, shared API key, auto-deploy, SeaweedFS, MCP
HTTP service, managed Postgres, observability stack) must land alongside the
Stage-1 artifacts without requiring a rewrite.

## Decision

Adopt a **Compose-on-VM deployment** with network-isolation-only auth, a
manual-gated CI/CD pipeline, and a minimal ops floor. The Backend ships as a
container image in GHCR; a single VM runs the full stack behind Caddy with
Tailscale-internal TLS.

### 1. Trust model: trusted clients only

The Backend runs unauthenticated. The only things that connect to it are
systems the operator controls: the future Interface App, MCP hosts on the
operator's machine, automation scripts. Protection = network isolation (private
VPC or Tailscale), not an application-layer token.

This formalizes ADR-0013 §3 ("No authentication in v1... intended to run inside
a trusted network or behind an existing gateway") and ADR-0004 (single-tenant,
per-customer).

### 2. Topology: Compose-on-VM

A single VM runs `docker compose up` with: `db` (pgvector/pgvector:pg16), `api`
(uvicorn), `worker` (`python -m rag_wiki.worker`), and `caddy` (reverse proxy
with auto-HTTPS). Local filesystem storage (`STORAGE_PROVIDER=local`); SeaweedFS
deferred to Stage-2. The image is pulled from GHCR
(`ghcr.io/<owner>/rag-wiki:<tag>`), not built on the VM.

### 3. TLS: Tailscale-internal CA

Caddy terminates TLS using an internal CA (Tailscale's built-in HTTPS or
Caddy's internal CA). No public DNS domain, no open ports. The VM has zero
public-facing ports; all access is over the Tailscale tailnet. This is the
cleanest realization of "if you can reach the port, you're trusted."

### 4. CI/CD: manual-gated

The existing GitHub Actions pipeline (lint → typecheck → test → migrations →
build → scan) is extended with two jobs:

- **push-images**: on `main` push (after scan passes) or `workflow_dispatch`,
  pushes the image to GHCR with `:latest` + `:sha-<short>` tags.
- **deploy**: `workflow_dispatch` only (manual-gate), SSHes to the VM, runs
  `docker compose pull && docker compose up -d --remove-orphans`. The
  entrypoint runs `alembic upgrade head` as today.

Rollback = edit `IMAGE_TAG` in `.env` on the VM + `docker compose up -d`. No
blue-green, no staging env in Stage-1.

### 5. Secrets: flat `.env` on the VM

A single `.env` file on the VM (gitignored, manually maintained) holds all
secrets: `POSTGRES_PASSWORD`, `LLM_API_KEY`, `GEMINI_API_KEY`,
`S3_SECRET_ACCESS_KEY` (Stage-2). `DATABASE_URL` is assembled by compose from
`POSTGRES_PASSWORD` + the known `db` service host, so the operator sets one DB
secret, not a connection string. `CORS_ORIGINS=""` (locked down; the Interface
App will call server-side, so CORS is irrelevant). CI secrets hold only what CI
needs: `GHCR_TOKEN` (the auto-provisioned `GITHUB_TOKEN`),
`DEPLOY_SSH_KEY`, `DEPLOY_HOST`.

### 6. MCP: stdio-only, no service in prod compose

The prod compose runs `db`, `api`, `worker`, `caddy` — no MCP service. The
operator runs `rag-wiki mcp serve` locally on their own machine with
`RAG_WIKI_MCP_API_URL=https://rag-wiki.<tailnet>` pointing at the deployed API
over Tailscale. Obsidian/Claude Desktop/Copilot Chat spawn it via stdio. The
MCP HTTP transport is hardened to loopback-only via a settings validator (a
`model_validator` raises if `mcp_transport == "http"` and `mcp_host` is not a
loopback address), matching Q2's network-isolation constraint.

### 7. Ops floor: structlog → stdout + `/health` + `pg_dump` cron

- **Logging**: structlog to stdout (already the codebase convention), captured
  per-container by Docker. `docker compose logs -f` is the only observability
  surface. No metrics, no dashboard, no Loki/Grafana in Stage-1.
- **Health**: the existing `GET /health` (lightweight DB query) is the only
  probe. Compose `healthcheck` uses it; Caddy can use it for upstream gating.
  No liveness/readiness split (a K8s-era distinction Compose doesn't need).
- **Backups**: a daily `pg_dump` cron job on the VM
  (`scripts/backup.sh`), 7-day retention. Postgres is the source of truth
  (CONTEXT.md, ADR-0001); a daily logical backup covers "VM died" without
  standing up WAL archiving infra.

## Rationale

- **"Deploy ASAP" + "best practices"**: Compose-on-VM with Caddy auto-HTTPS,
  GHCR image pull, and a Trivy-scanned manual-gated pipeline is the smallest
  honest "best-practice" deployment for a headless backend. It teaches the full
  CD cycle (build → scan → push → deploy → migrate) without the overhead of
  authoring a Helm chart correctly — which is genuinely hard and would blow the
  "ASAP" budget.
- **"Auth moves out"**: network isolation is the only option with *zero* auth
  code in rag_wiki, which is exactly what the author wants. The "best practice"
  here is network isolation + TLS termination at the reverse proxy, not an
  app-layer token. This is a legitimate, documented pattern for headless
  backends behind a gateway (ADR-0013 §3).
- **YAGNI + additive-safe**: every Stage-2 enhancement lands alongside the
  Stage-1 artifacts without a rewrite. The Helm chart translates
  `docker-compose.prod.yml` → `values.yaml` line-by-line. A shared API key
  bolts onto the `api` service as an env var. A staging env is a second compose
  overlay on the same base. SeaweedFS is an additional service block. MCP HTTP
  is an additional compose service. Auto-deploy is a trigger change on the
  `deploy` job. Managed Postgres is a `db` service swap. An observability stack
  is a `logging` service block. No Stage-2 move requires touching Stage-1 code.
- **SME graduation is not blocked**: an SME running their own single-tenant
  instance per ADR-0004 is also on a private network / behind their own gateway.
  The Stage-1 artifacts (`docker-compose.prod.yml` + `Caddyfile` +
  `.env.example`) are exactly what an SME would lift verbatim. When an SME wants
  a public endpoint, that's when a real auth ADR lands — additive, not a
  rewrite. The Helm chart (Stage-2) is a polish of the *same* topology, not a
  different system.
- **MCP stdio-only**: the only MCP client today is the operator's Obsidian /
  Copilot Chat on their own machine, over stdio. Running a 24/7 HTTP MCP
  service on the VM that nothing calls is pure operational overhead. A remote
  MCP HTTP client reaching the VM would cross the network, which the trust model
  (§1) forbids without auth — so stdio (a local-trust transport) is the
  Stage-1 answer.
- **`pg_dump` cron is non-negotiable**: the DB is the source of truth and
  Sources are re-ingestable but the knowledge graph (entities, relations, wiki
  pages, merge logs) is not trivially reconstructable. A daily logical backup is
  the cheapest thing that counts as "best practice" and covers the most likely
  failure mode (host death). WAL archiving / point-in-time recovery is Stage-2+.
- **Manual-gate protects a data-writing backend**: an LLM-pipeline backend that
  ingests documents and writes to a knowledge graph is exactly the kind of system
  you don't want to auto-deploy-then-discover-a-migration-broke-the-graph
  (ADR-0005: a failed deploy mid-migration could strand jobs; ADR-0010: a bad
  deploy could publish junk wiki pages). Manual-gate-on-`main` is the
  best-practice posture for a data-writing backend in early life.

## Consequences

- New files in `deploy/`: `docker-compose.prod.yml`, `Caddyfile`,
  `.env.example`, `README.md`. The dev `docker-compose.yml` at repo root is
  untouched.
- New script `scripts/backup.sh` + a cron entry on the VM.
- `.github/workflows/ci.yml` gains `push-images` and `deploy` jobs; the
  deferred `push-images` block (currently a commented-out placeholder) is
  replaced.
- `rag_wiki/settings.py` gains a `model_validator` that hardens MCP HTTP to
  loopback-only. This is a breaking change for anyone who has set
  `MCP_HOST=0.0.0.0` — but no such deployment exists today (the default is
  `127.0.0.1`), and the validator emits a clear error message.
- No changes to: `Dockerfile` (already production-shaped), `main.py`, any route
  code, ADRs 0004/0013/0016 (this ADR concretizes them, not supersedes them).
- Stage-2 moves (Helm chart, shared API key, auto-deploy, SeaweedFS, MCP HTTP
  service, managed Postgres, observability stack, WAL backups) are all
  additive — each lands as a new file or a new service block without modifying
  Stage-1 artifacts.
- If multi-tenant SaaS becomes a goal, it would require a new ADR revisiting
  the schema (per ADR-0004) — deliberately deferred and not enabled by this
  topology.
