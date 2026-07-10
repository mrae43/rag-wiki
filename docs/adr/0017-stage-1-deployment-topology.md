# ADR-0017: Stage-1 deployment topology

## Status

Accepted

## Context

The system has been developed for months as a headless AI backend (FastAPI +
worker + MCP + Postgres), operated so far only via CLI and MCP by a single
trusted operator. The author wants to deploy it as a standalone system, following
best practices, and to use the process to learn a real CI/CD pipeline.

ADRs 0004 (single-tenant), 0013 (no auth in v1), and 0016 (dual-transport MCP)
already establish the *abstract* posture: trusted-network-only, no inbound auth,
MCP over stdio by default. None of them records the *concrete* deployment
contract — which topology, which CI shape, which secrets posture, which ops
floor, which MCP transport ships in production. This ADR closes that gap.

The trust model below is a standing decision, not a placeholder waiting on a
future consumer. Whatever eventually calls this backend — CLI, MCP, a future
UI, an automation script — it does so as a trusted client on the operator's
own network. The auth posture does not change based on what shape that client
takes; it changes only if a client outside the operator's trust boundary needs
access, which is explicitly out of scope for Stage-1.

Every Stage-1 decision must be **additive-safe**: Stage-2 enhancements (Helm
chart, edge-level API key, auto-deploy, SeaweedFS, MCP HTTP service, managed
Postgres, observability stack) must land alongside the Stage-1 artifacts
without requiring a rewrite.

## Decision

Adopt a **Compose-on-VM deployment** with network-isolation-only auth, a
manual-gated CI/CD pipeline with forward-only migrations, and a minimal but
non-trivial ops floor. The Backend ships as a container image in GHCR; a
single VM runs the full stack behind Caddy with Tailscale-internal TLS.

### 1. Trust model: trusted clients only

The Backend runs unauthenticated. The only things that connect to it are
systems the operator controls. Protection = network isolation (Tailscale
tailnet), not an application-layer token. This formalizes ADR-0013 §3 and
ADR-0004 (single-tenant). It holds regardless of how many or which clients
exist, now or later.

### 2. Topology: Compose-on-VM

A single VM runs `docker compose up` with four services: `db`
(pgvector/pgvector:pg16), `api` (uvicorn), `worker`
(`python -m rag_wiki.worker`), and `caddy` (reverse proxy, auto-HTTPS). Local
filesystem storage (`STORAGE_PROVIDER=local`); SeaweedFS deferred to Stage-2.
The image is pulled from GHCR (`ghcr.io/<owner>/rag-wiki:<tag>`), never built
on the VM.

All four services set `restart: unless-stopped`. Each sets an explicit
`mem_limit`/`cpus` bound, sized so a runaway ingestion job in `worker` cannot
starve `api` or `db` on the same VM. Docker's `json-file` log driver is capped
per-service (`max-size: "10m", max-file: "3"`) to prevent unbounded log growth
on a host with no log shipping.

### 3. TLS: Tailscale-internal CA

Caddy terminates TLS using an internal CA (Tailscale's built-in HTTPS or
Caddy's internal CA). No public DNS domain, no open ports. The VM has zero
public-facing ports; all access is over the tailnet.

### 4. CI/CD: manual-gated, pinned, forward-only

The existing GitHub Actions pipeline (lint → typecheck → test → migrations →
build → scan) gains two jobs:

- **push-images**: on `main` push (after scan passes) or `workflow_dispatch`,
  pushes the image to GHCR tagged `:latest` and `:sha-<short>`. `:latest` is a
  convenience tag for local/dev pulls only and is never referenced by the
  deployed VM.
- **deploy**: `workflow_dispatch` only, SSHes to the VM, sets `IMAGE_TAG` in
  `.env` to the specific `sha-<short>` being deployed, and runs
  `docker compose pull && docker compose up -d --remove-orphans`. The
  entrypoint runs `alembic upgrade head`.

**Migrations are forward-only in Stage-1.** Rollback is not "revert the image
and hope the schema still matches" — a bad migration is fixed by shipping a
new forward migration that corrects or reverts the change, then redeploying.
Reverting `IMAGE_TAG` without a matching down-migration is explicitly
unsupported and must not be used as a recovery path, since older code is not
guaranteed to run against a newer schema.

### 5. Secrets: flat `.env` on the VM, scoped deploy access

A single `.env` file on the VM (gitignored, manually maintained) holds
`POSTGRES_PASSWORD`, `LLM_API_KEY`, `GEMINI_API_KEY`,
`S3_SECRET_ACCESS_KEY` (Stage-2). `DATABASE_URL` is assembled by compose from
`POSTGRES_PASSWORD` and the known `db` service host. `CORS_ORIGINS=""`.

CI secrets hold only `GHCR_TOKEN` (auto-provisioned `GITHUB_TOKEN`),
`DEPLOY_SSH_KEY`, `DEPLOY_HOST`. `DEPLOY_SSH_KEY` authenticates to a
dedicated, non-root `deploy` user on the VM whose `authorized_keys` entry is
command-restricted (`command="docker compose ..."`) rather than granting a
general shell — a compromised CI secret should not yield unrestricted VM
access.

### 6. MCP: stdio-only, no service in prod compose

The prod compose runs `db`, `api`, `worker`, `caddy` — no MCP service. The
operator runs `rag-wiki mcp serve` locally with
`RAG_WIKI_MCP_API_URL=https://rag-wiki.<tailnet>` pointing at the deployed API
over Tailscale. MCP HTTP transport is hardened to loopback-only via a
`model_validator` that raises if `mcp_transport == "http"` and `mcp_host` is
not a loopback address.

### 7. Ops floor: structured logs, health check, verified backups

- **Logging**: structlog to stdout, captured per-container by Docker
  (log-rotated per §2). `docker compose logs -f` is the only observability
  surface. No metrics, no dashboard, no Loki/Grafana in Stage-1 — this is
  deliberately deferred, not overlooked, and is the first Stage-1.5 addition
  once the system carries real traffic.
- **Health**: `GET /health` (lightweight DB query) backs the Compose
  `healthcheck` and Caddy's upstream gating. No liveness/readiness split — a
  K8s-era distinction Compose doesn't need.
- **Backups**: a daily `pg_dump` cron (`scripts/backup.sh`), 7-day retention.
  Each run validates its own output (`pg_restore --list` against the dump,
  non-zero size check) and fails loudly (non-zero exit, logged) if the dump
  is unusable. A monthly manual restore-to-scratch-DB drill confirms the
  backup chain actually works end to end, not just that a file gets written.

## Rationale

- **Compose-on-VM is the smallest honest best-practice deployment** for a
  headless backend: it exercises the full CD cycle (build → scan → push →
  deploy → migrate) without the overhead of a Helm chart, which is genuinely
  hard to author correctly and isn't justified at this scale yet.
- **Network isolation, not app-layer auth, is the correct pattern for a
  headless backend behind a trust boundary** (ADR-0013 §3). This is a stable
  architectural position, not a stand-in for auth that "should" exist once a
  particular client materializes.
- **Additive-safe**: every Stage-2 move — Helm chart, edge API key, staging
  overlay, SeaweedFS, MCP HTTP service, managed Postgres, observability stack
  — lands as a new file or service block. No Stage-2 move requires touching
  Stage-1 code or reversing a Stage-1 decision.
- **Forward-only migrations + pinned SHA tags** close the gap between "we have
  a rollback plan" and "the rollback plan actually works." A data-writing
  backend that ingests documents into a knowledge graph (ADR-0005, ADR-0010)
  cannot safely assume old code against a new schema; the honest policy is
  fix-forward, stated explicitly rather than implied.
- **Verified backups over unverified backups**: a cron job that writes a file
  no one has ever restored is not a backup strategy, it's a hope. The
  restore-list check and monthly drill are cheap and are what make the daily
  `pg_dump` claim credible.
- **Restart policy, resource limits, and log rotation** are the difference
  between "a container runs" and "a system is operated." All three are
  near-zero cost and directly address the most likely Stage-1 failure modes:
  a crashed process nobody notices, one runaway job starving the whole VM, and
  an unbounded log file filling the disk.
- **Command-restricted deploy key** bounds the blast radius of a leaked CI
  secret to exactly the one operation CI needs to perform.
- **Manual-gate protects a data-writing backend**: auto-deploying a system
  that writes to a knowledge graph risks stranding jobs mid-migration
  (ADR-0005) or publishing junk wiki pages (ADR-0010) with no human in the
  loop. Manual-gate-on-`main` is the appropriate posture at this stage.

## Consequences

- New files in `deploy/`: `docker-compose.prod.yml` (with restart policies,
  resource limits, log rotation), `Caddyfile`, `.env.example`, `README.md`.
  The dev `docker-compose.yml` at repo root is untouched.
- New script `scripts/backup.sh` (with post-dump validation) + a cron entry on
  the VM, plus a documented monthly restore-drill procedure.
- `.github/workflows/ci.yml` gains `push-images` and `deploy` jobs; `deploy`
  always pins `IMAGE_TAG` to a specific `sha-<short>`, never `:latest`.
- `rag_wiki/settings.py` gains a `model_validator` hardening MCP HTTP to
  loopback-only. Breaking only for a `MCP_HOST=0.0.0.0` setup, which doesn't
  exist today (default is `127.0.0.1`); the validator emits a clear error.
- A documented, enforced policy: no schema-rollback recovery path. A bad
  migration is corrected with a new forward migration.
- No changes to: `Dockerfile`, `main.py`, route code, or ADRs 0004/0013/0016
  (this ADR concretizes them, not supersedes them).
- Stage-2 moves (Helm chart, edge-level API key, auto-deploy, SeaweedFS, MCP
  HTTP service, managed Postgres, observability stack, WAL backups) remain
  fully additive.
- If a client outside the operator's trust boundary becomes a requirement
  (public endpoint, third-party consumer, multi-tenant SaaS), that triggers a
  new ADR revisiting both the trust model and the schema (per ADR-0004) —
  deliberately out of scope here.