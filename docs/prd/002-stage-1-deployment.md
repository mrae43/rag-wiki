# PRD-002: Stage-1 Deployment — Compose-on-VM, Trusted-Clients-Only

## Problem Statement

The RAG Wiki Backend has been developed for months as a headless AI system
(FastAPI + worker + MCP + Postgres), but it only runs locally via
`docker-compose.yml` with bind-mounts, `--reload`, and no production deployment
artifacts. The author wants to:

1. Deploy the Backend as a standalone system as soon as possible, following
   best practices.
2. Move authentication into a future dedicated full-stack Interface App,
   keeping the Backend headless and auth-free.
3. Learn continuous delivery (CI/CD) by shipping a real deploy pipeline.
4. Position rag_wiki as a Backend that is easy to connect from existing systems
   — starting with the author as the first user, with an SME monetization path
   later.

There is no concrete deployment contract today: no prod compose, no reverse
proxy, no image push, no deploy job, no backups, no MCP-in-prod guidance. The
Stage-1 deployment must be minimal (YAGNI) but additive-safe — every Stage-2
enhancement (Helm chart, shared API key, auto-deploy, SeaweedFS, MCP HTTP
service, observability stack) must land without rewriting Stage-1 artifacts.

## Solution

A Compose-on-VM deployment topology with network-isolation-only auth, a
manual-gated CI/CD pipeline, and a minimal ops floor. The Backend ships as a
container image in GHCR; a single VM runs the full stack (`db`, `api`,
`worker`, `caddy`) behind Caddy with Tailscale-internal TLS. MCP runs stdio-only
on the operator's local machine, pointing at the deployed API over Tailscale.
A daily `pg_dump` cron protects the source-of-truth database.

The operator (first user) deploys by filling in a `.env` file on the VM and
running `docker compose up -d`. CI builds, scans, and pushes the image; a
manual-gated `workflow_dispatch` job SSHes to the VM to pull and restart.
Rollback is editing one `IMAGE_TAG` value.

Every decision is recorded in ADR-0017 and additive-safe by construction.

## User Stories

1. As the Backend operator, I want a single `docker compose -f
   deploy/docker-compose.prod.yml up -d` command to bring up the entire
   production stack, so that I can deploy without hand-editing service
   definitions.

2. As the Backend operator, I want the VM to expose zero public ports, so that
   the unauthenticated Backend is reachable only over my Tailscale tailnet.

3. As the Backend operator, I want valid HTTPS on `rag-wiki.<tailnet>` without
   a public DNS domain, so that clients trust the TLS certificate without
   manual override.

4. As the Backend operator, I want the image pulled from a container registry
   (not built on the VM), so that the deployed artifact is the same one CI
   built and scanned.

5. As the Backend operator, I want CI to push the image to GHCR automatically
   on `main` after lint, tests, migration check, build, and Trivy scan all
   pass, so that a known-good artifact is always available to deploy.

6. As the Backend operator, I want a manual `workflow_dispatch` deploy job
   that SSHes to the VM and runs `docker compose pull && up -d`, so that I
   control when production changes and can watch the first few deploys.

7. As the Backend operator, I want a pinned `IMAGE_TAG` in `.env` on the VM,
   so that rollback is editing one line and re-running `up -d`.

8. As the Backend operator, I want the entrypoint to run `alembic upgrade head`
   before the app starts, so that migrations apply automatically on deploy
   without a separate manual step.

9. As the Backend operator, I want a single flat `.env` file on the VM holding
   all secrets, so that I have one file to maintain and back up.

10. As the Backend operator, I want `DATABASE_URL` assembled by compose from
    `POSTGRES_PASSWORD` and the known `db` service host, so that I set one DB
    secret instead of constructing a connection string by hand.

11. As the Backend operator, I want `CORS_ORIGINS` locked to empty by default,
    so that no cross-origin browser requests reach the unauthenticated API
    until the Interface App is ready and its origin is explicitly added.

12. As the Backend operator, I want to run `rag-wiki mcp serve` locally on my
    laptop with `RAG_WIKI_MCP_API_URL=https://rag-wiki.<tailnet>`, so that
    Obsidian / Claude Desktop / Copilot Chat can query the deployed Backend
    over stdio without a 24/7 MCP HTTP service on the VM.

13. As the Backend operator, I want MCP HTTP transport to refuse non-loopback
    binds, so that I cannot accidentally expose an unauthenticated MCP HTTP
    endpoint to the tailnet.

14. As the Backend operator, I want my local MCP client to trust Caddy's
    internal CA, so that the httpx client validates the TLS certificate rather
    than falling back to plaintext.

15. As the Backend operator, I want a daily `pg_dump` of the Postgres database
    with 7-day retention, so that a VM failure does not destroy the
    knowledge graph.

16. As the Backend operator, I want `GET /health` to be the compose
    `healthcheck`, so that a wedged API container is restarted automatically
    and Caddy can gate upstream routing.

17. As the Backend operator, I want structlog output captured by Docker
    per-container, so that `docker compose logs -f api` (or `worker`) is my
    observability surface without standing up a logging stack.

18. As a future SME customer, I want to lift `deploy/docker-compose.prod.yml`,
    `Caddyfile`, and `.env.example` verbatim into my own VM, so that I can run
    my own single-tenant instance without re-engineering the deployment.

19. As a developer, I want every Stage-1 artifact to be additive-safe, so
    that the Stage-2 Helm chart, shared API key, auto-deploy, SeaweedFS, MCP
    HTTP service, observability stack, and managed Postgres each land as new
    files or service blocks without rewriting Stage-1.

20. As a developer, I want a `deploy/README.md` documenting the Tailscale
    setup, `.env` fill-in, deploy flow, and rollback procedure, so that I can
    re-deploy after a break without remembering the steps.

21. As the Backend operator, I want the dev `docker-compose.yml` at the repo
    root left untouched, so that local development with `--reload` and
    bind-mounts continues to work as before.

22. As a developer, I want CI's `push-images` and `deploy` jobs to be the only
    additions to the existing `ci.yml`, so that the lint / typecheck / test /
    migrations / build / scan pipeline I already trust is preserved.

## Implementation Decisions

### Trust model: trusted-clients-only (no inbound auth)

The Backend runs unauthenticated. The only things that connect to it are
systems the operator controls: the future Interface App, MCP hosts on the
operator's machine, automation scripts. Protection = network isolation
(Tailscale), not an application-layer token. This formalizes ADR-0013 §3 and
ADR-0004. A shared API key (`RAG_WIKI_API_KEY`) is the Stage-2 additive
upgrade when a client leaves the trusted network — not built now.

### Topology: Compose-on-VM, four services

The prod compose runs exactly: `db` (pgvector/pgvector:pg16), `api` (uvicorn
from the GHCR image), `worker` (same image, `command: python -m
rag_wiki.worker`), `caddy` (reverse proxy). No `pgadmin`, no SeaweedFS, no
`--reload`, no bind-mounts. `restart: unless-stopped` on all services. The
`api` and `worker` use `image: ghcr.io/${GHCR_OWNER}/rag-wiki:${IMAGE_TAG}`
with `${VAR}` interpolation from `.env`.

### Storage: local filesystem

`STORAGE_PROVIDER=local` with `UPLOAD_DIR=/var/lib/rag-wiki/uploads` mounted
as a named volume. SeaweedFS is deferred to Stage-2 (additive: a new service
block + `STORAGE_PROVIDER=s3`).

### TLS: Tailscale-internal CA via Caddy

Caddy terminates TLS using its internal CA (or Tailscale's built-in HTTPS) on
the tailnet hostname. No public DNS, no open ports, no Let's Encrypt account.
The `Caddyfile` site block reverse-proxies to `api:8000` and uses the
`/health` endpoint for upstream gating. Flipping to a public domain later is
editing the site block + adding a DNS record — additive.

### CI/CD: manual-gated, two new jobs

The existing `ci.yml` (lint → typecheck → test → migrations → build-images →
scan-images) is extended with:

- **push-images**: triggers on `main` push (after `scan-images` passes) and
  `workflow_dispatch`. Pushes to `ghcr.io/${{ github.repository_owner
  }}/rag-wiki` with tags `:latest` and `:sha-<short>`. Uses the auto-provisioned
  `GITHUB_TOKEN` with `packages: write`.
- **deploy**: `workflow_dispatch` only (manual-gate), needs `push-images`. SSHes
  to `DEPLOY_HOST` using `DEPLOY_SSH_KEY` secret, runs `docker compose --env-file
  .env -f deploy/docker-compose.prod.yml pull && docker compose --env-file .env
  -f deploy/docker-compose.prod.yml up -d --remove-orphans`.

Rollback: edit `IMAGE_TAG` in `.env` on the VM, re-run `up -d`. No blue-green,
no staging env in Stage-1.

### Secrets: flat `.env` on the VM

A single `.env` file (gitignored, manually maintained) holds: `POSTGRES_PASSWORD`,
`LLM_API_KEY`, `GEMINI_API_KEY`, `GHCR_OWNER`, `IMAGE_TAG`, `MCP_API_URL`,
`CORS_ORIGINS=""`, plus all non-secret config from `settings.py`.
`DATABASE_URL` is assembled in the compose file as
`postgresql+asyncpg://ragwiki:${POSTGRES_PASSWORD}@db:5432/ragwiki`. CI
secrets hold only `DEPLOY_SSH_KEY` and `DEPLOY_HOST` (and the auto-provisioned
`GITHUB_TOKEN`). Docker secrets / external secret manager is Stage-2+.

### MCP: stdio-only, loopback-hardened

No MCP service in the prod compose. The operator runs `rag-wiki mcp serve`
locally with `RAG_WIKI_MCP_API_URL=https://rag-wiki.<tailnet>`. A
`model_validator` in `Settings` raises `ValueError` if
`mcp_transport == "http"` and `mcp_host` is not a loopback address
(`127.0.0.1`, `::1`, `localhost`). The local client trusts Caddy's internal
CA root (installed once into the OS trust store).

### Ops floor: stdout logs + `/health` + `pg_dump` cron

- **Logging**: structlog to stdout, captured per-container by Docker. `docker
  compose logs -f` is the only surface. No Loki/Grafana.
- **Health**: the existing `GET /health` (lightweight DB query) is the compose
  `healthcheck` for the `api` service. Caddy uses it for upstream gating.
- **Backups**: `scripts/backup.sh` runs `docker compose exec -T db pg_dump -U
  ragwiki ragwiki | gzip > /backups/ragwiki-$(date +%F).sql.gz`, with
  `find /backups -name 'ragwiki-*.sql.gz' -mtime +7 -delete` for 7-day
  retention. Installed as a daily cron on the VM host (not inside a container).

### Module structure (new files)

```
deploy/
  docker-compose.prod.yml   # db, api, worker, caddy services
  Caddyfile                 # rag-wiki.<tailnet> site, reverse_proxy api:8000
  .env.example              # every var from settings.py, commented, grouped
  README.md                 # Tailscale setup, .env fill-in, deploy/rollback flow

scripts/
  backup.sh                 # pg_dump + 7-day retention
```

### Modified modules

- `.github/workflows/ci.yml` — add `push-images` and `deploy` jobs (the
  deferred `push-images` placeholder is replaced). No changes to existing
  lint/typecheck/test/migrations/build/scan jobs.
- `rag_wiki/settings.py` — add a `model_validator` that hardens MCP HTTP to
  loopback-only. Small, defensive, additive. Breaking only for a hypothetical
  `MCP_HOST=0.0.0.0` deployment that does not exist today (default is
  `127.0.0.1`); the validator emits a clear error message.

### No changes to

- `Dockerfile` (already production-shaped: `python:3.12-slim`, `uv sync
  --frozen`, entrypoint runs migrations, default CMD uvicorn).
- `docker-compose.yml` (dev stays as-is: bind-mount, `--reload`, pgadmin,
  SeaweedFS).
- `rag_wiki/main.py`, any route code, any ADRs (0004/0013/0016 are
  concretized by ADR-0017, not superseded).

### GHCR namespace placeholder

The author does not yet have a GitHub Container Registry account/owner set up.
The compose file and CI use `${GHCR_OWNER}` / `${{ github.repository_owner }}`
interpolation throughout. When the GH account is created, the operator sets
`GHCR_OWNER` in `.env` on the VM (CI uses `github.repository_owner`
automatically). No code change is needed — only a `.env` value.

## Testing Decisions

### What makes a good test

- Tests should exercise the **external contract** (the settings validator
  rejects non-loopback MCP HTTP binds; the prod compose file is valid and
  interpolates correctly; the backup script produces a non-empty gzip), not
  implementation details.
- The prod compose file is a configuration artifact, not code — it is
  validated by `docker compose config` (interpolation + schema check), not by
  unit tests.
- The CI pipeline changes are validated by running the workflow on a branch
  and confirming `push-images` succeeds (image lands in GHCR) and `deploy`
  (dry-run) SSHes correctly. The existing `scan-images` gate already covers
  image integrity.

### Modules tested

| Test file | What it tests | Prior art |
|-----------|---------------|-----------|
| `tests/settings/test_mcp_loopback_validator.py` | `model_validator` rejects `mcp_transport="http"` + non-loopback `mcp_host`; accepts loopback | Existing `tests/` settings tests, pydantic `model_validator` pattern |
| (no unit test) | `deploy/docker-compose.prod.yml` validity | Validated via `docker compose -f deploy/docker-compose.prod.yml config` in CI or locally |
| (no unit test) | `scripts/backup.sh` | Manual smoke: run once, confirm a non-empty `.sql.gz` lands in `/backups/` |

### Manual validation (deploy dry-run)

Before the first real deploy:

1. `docker compose -f deploy/docker-compose.prod.yml --env-file
   deploy/.env.example config` — confirms interpolation and schema.
2. `docker run --rm ghcr.io/<owner>/rag-wiki:<tag> uv run alembic check` —
   confirms no migration drift in the shipped image.
3. `curl -sk https://rag-wiki.<tailnet>/health` — confirms Caddy TLS + upstream
   gating + DB ping.
4. `docker compose -f deploy/docker-compose.prod.yml exec -T db pg_dump -U
   ragwiki ragwiki | gzip | gunzip | head` — confirms backup path works.

## Out of Scope

- **Authentication / RBAC** — auth is owned by the future Interface App.
  A shared API key (`RAG_WIKI_API_KEY`) is the Stage-2 additive upgrade when
  a client leaves the trusted network.
- **Helm chart** — Stage-2. Translates `docker-compose.prod.yml` → `values.yaml`
  line-by-line.
- **Auto-deploy on `main`** — Stage-2. Flipping the `deploy` job trigger from
  `workflow_dispatch` to `push: branches: [main]` is a one-line change.
- **Staging environment** — Stage-2. A second compose overlay
  (`docker-compose.staging.yml`) on the same base.
- **SeaweedFS / S3 storage** — Stage-2. A new service block +
  `STORAGE_PROVIDER=s3`.
- **MCP HTTP service in prod compose** — Stage-2, contingent on a real remote
  MCP client existing and a revisit of the auth boundary (loopback-only →
  needs a token).
- **Observability stack (Loki/Grafana/Prometheus)** — Stage-2. A `logging`
  service block appended to the prod compose.
- **WAL archiving / point-in-time recovery** — Stage-2+, arrives naturally
  with managed Postgres or the Helm chart.
- **Multi-tenant SaaS** — would require a new ADR revisiting the schema (per
  ADR-0004). Deliberately deferred.
- **CI secret rotation automation** — manual rotation of `DEPLOY_SSH_KEY` is
  fine for a single-operator Stage-1.

## Further Notes

- The VM must be on the operator's Tailscale tailnet before first deploy.
  Caddy's internal CA is trusted by Tailscale's HTTPS feature or by installing
  the Caddy root into the OS trust store once.
- The `.env.example` is the single source of truth for what the operator must
  configure. Every var from `rag_wiki/settings.py` is listed, grouped by
  concern (DB, LLM, embeddings, storage, MCP, worker, planner, retrieval,
  API), with the default value commented out and a note where a value is
  required.
- `GHCR_OWNER` is a placeholder until the GitHub account is created. The CI
  `push-images` job uses `${{ github.repository_owner }}` automatically; the
  VM `.env` sets `GHCR_OWNER` to match.
- The `pg_dump` cron runs on the VM host (not in a container) so that a crashed
  `db` container does not prevent backups. The `/backups/` directory is a
  host path, optionally backed by a separate volume.
- ADR-0017 records the full rationale for each decision, including the
  rejected alternatives (K3s+Helm now, PaaS, shared service token, JWT
  validation, auto-deploy, GitOps, MCP HTTP in prod, Docker secrets, WAL
  archiving, Loki/Grafana). Refer to it when a Stage-2 enhancement is
  proposed — the rejection rationale there is the starting point.
- Every Stage-2 enhancement is additive-safe by construction: the Helm chart
  lands in `deploy/helm/`; the shared API key is a new env var on the `api`
  service; auto-deploy is a trigger change on the `deploy` job; SeaweedFS is
  a new service block; MCP HTTP is a new `mcp` service; observability is a
  new `logging` block; managed Postgres is a `db` service swap. No Stage-1
  artifact is rewritten.
