# Handoff: `stage-1-deployment-impl-plan`

**Date:** `2026-07-03` (plan) · `2026-07-03` (closeout)
**This session did:** Executed the remaining closeout tasks from the Stage-1 deployment plan: verified all 5 PRs (#92–#96) are merged on `main`, ran the full quality gate (ruff check ✓ · ruff format ✓ · mypy ✓ · pytest 364 passed / 7 skipped), and re-ran the `handoff` skill to mark the plan complete.
**Next session goal:** None — Stage-1 deployment implementation is complete. Stage-2 enhancements (Helm chart, shared API key, auto-deploy, SeaweedFS, MCP HTTP service, observability stack) are additive and not yet scoped; raise as new PRDs/ADRs when started.

---

## Current State

### ADR Status
| ADR | Title | Status | Path |
|-----|-------|--------|------|
| ADR-0017 | Stage-1 deployment topology | Accepted | `docs/adr/0017-stage-1-deployment-topology.md` |

All other ADRs (0001–0016) are Accepted and untouched by this work. ADR-0017 concretizes 0004 (single-tenant), 0013 (no auth in v1), 0016 (dual-transport MCP) — it does not supersede them.

### PRD Status
| PRD | Title | Status | Path |
|-----|-------|--------|------|
| PRD-002 | Stage-1 Deployment — Compose-on-VM, Trusted-Clients-Only | Final | `docs/prd/002-stage-1-deployment.md` |

### Implementation Plan Status (Task Breakdown)
> Source of truth for monitoring implementation. Update statuses as PRs open/merge.

| PR  | Step | Description | Status | Blocks / Blocked-by |
|-----|------|-------------|--------|---------------------|
| PR-1 | 1.1 | Add `model_validator(mode="after")` to `Settings` rejecting non-loopback `mcp_host` when `mcp_transport=="http"` | `done` — merged #92 | — |
| PR-1 | 1.2 | Create `tests/settings/__init__.py` + `tests/settings/test_mcp_loopback_validator.py` (rejects `0.0.0.0`/`192.168.x.x`; accepts `127.0.0.1`/`::1`/`localhost`; accepts any host when stdio) | `done` — merged #92 | — |
| PR-1 | 1.3 | Update `.env.example` `MCP_HOST` comment to note loopback constraint | `done` — merged #92 | — |
| PR-1 | 1.4 | Quality gate: `ruff check` → `ruff format` → `mypy` → `pytest tests/settings/` | `done` — merged #92 | — |
| PR-2 | 2.1 | Create `deploy/` dir + `deploy/docker-compose.prod.yml` (db, api, worker, caddy; `DATABASE_URL` assembled inline; named volumes `postgres_data`+`uploads`; no pgadmin/SeaweedFS/--reload/bind-mount) | `done` — merged #93 | — |
| PR-2 | 2.2 | `deploy/Caddyfile` — `rag-wiki.{$TAILNET_HOST}` site, `reverse_proxy api:8000`, `health_uri /health`, `tls internal` | `done` — merged #93 | — |
| PR-2 | 2.3 | `deploy/.env.example` — every var from `settings.py` grouped (DB/LLM/embeddings/storage/MCP/worker/planner/retrieval/API/deploy); `CORS_ORIGINS=""` locked; prod uses `POSTGRES_USER=ragwiki`/`POSTGRES_DB=ragwiki` | `done` — merged #93 | — |
| PR-2 | 2.4 | Validate: `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.example config` exits 0 | `done` — merged #93 | — |
| PR-3 | 3.1 | `scripts/backup.sh` — `pg_dump | gzip > /backups/ragwiki-$(date +%F).sql.gz` + `find -mtime +7 -delete`; `set -euo pipefail`; env overrides `BACKUP_DIR`/`COMPOSE_FILE`/`DB_USER`/`DB_NAME`/`RETENTION_DAYS` | `done` — merged #94 | PR-2 |
| PR-3 | 3.2 | `chmod +x scripts/backup.sh`; manual smoke (non-empty gzip) | `done` — merged #94 | PR-2 |
| PR-4 | 4.1 | `.github/workflows/ci.yml`: replace deferred `push-images` placeholder (lines 284-295) with real job — `needs: [scan-images]`, `if: main push \|\| workflow_dispatch`, `packages: write`, login to GHCR via `GITHUB_TOKEN`, push `:latest`+`:sha-<short>` | `done` — PR #95 | PR-2 |
| PR-4 | 4.2 | Add `deploy` job — `needs: push-images`, `if: workflow_dispatch` only (manual-gate), `appleboy/ssh-action` (pinned) runs `compose pull && up -d --remove-orphans` using `DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_PATH` secrets | `done` — PR #95 | PR-2 |
| PR-4 | 4.3 | Update `docs/ci-context.md` §10 — remove `push-images` from deferred list; note `deploy` is manual-gated | `done` — PR #95 | — |
| PR-5 | 5.1 | `deploy/README.md` — Prerequisites (Tailscale, Docker, Caddy CA trust); `.env` fill-in; first/subsequent deploy; rollback (edit `IMAGE_TAG`); backups cron; MCP-from-laptop; observability (`logs -f`); Stage-2 additive path (link ADR-0017) | `done` — PR #96 | PR-2, PR-3, PR-4 |

**Dependency graph:**
```
PR-1 (settings validator) ──┐
PR-2 (compose/Caddy/env) ──┬── PR-3 (backup.sh) ──┐
                            ├── PR-4 (CI jobs) ───┤
                            └──────────────────────┴── PR-5 (README)
```
PR-1 ∥ PR-2 (open in parallel) → {PR-3, PR-4} → PR-5.

---

## Files

### Modified
```
rag_wiki/settings.py            # PR-1: added _validate_mcp_http_loopback model_validator + MCP_LOOPBACK_HOSTS
.env.example                    # PR-1: MCP_HOST comment noting loopback constraint (ADR-0017 §6)
HANDOFF-stage-1-deployment.md   # PR-1 statuses updated
```

### Created
```
HANDOFF-stage-1-deployment.md   # this file — implementation source of truth
tests/settings/__init__.py      # PR-1: new test package mirroring rag_wiki/settings.py
tests/settings/test_mcp_loopback_validator.py  # PR-1: 14 cases covering ADR-0017 §6
```

### Deleted
```
(none)
```

### Files to be touched (per PR, for the next agent)
```
PR-1: rag_wiki/settings.py, tests/settings/__init__.py, tests/settings/test_mcp_loopback_validator.py, .env.example (comment only)
PR-2: deploy/docker-compose.prod.yml, deploy/Caddyfile, deploy/.env.example
PR-3: scripts/backup.sh
PR-4: .github/workflows/ci.yml, docs/ci-context.md
PR-5: deploy/README.md
```

### Explicitly NOT touched (ADR-0017 §Consequences, PRD-002 §No changes to)
```
Dockerfile                 # already production-shaped (python:3.12-slim, uv sync --frozen, entrypoint runs alembic, CMD uvicorn)
docker-entrypoint.sh       # runs `alembic upgrade head` then `exec "$@"` — reused as-is by prod compose
docker-compose.yml         # dev: bind-mounts, --reload, pgadmin, SeaweedFS — left untouched
rag_wiki/main.py           # no route changes
rag_wiki/api/routes/*      # no route changes (GET /health already exists, ready for compose healthcheck)
rag_wiki/mcp/transport.py  # validator lives in Settings; transport already reads settings.mcp_host
docs/adr/0004*, 0013*, 0016*  # concretized by ADR-0017, not superseded
```

---

## Key Decisions

> Decisions NOT already in an ADR. ADR-0017 / PRD-002 own the rationale.

- **5 independent PRs, atomic per concern** — chosen over one large PR for blame/revert clarity and parallel review. Each PR is additive-safe on its own.
- **PR-1 first (only code change)** — the `Settings.model_validator` is the riskiest-to-leave-done artifact (silent security regression risk) and is fully isolated from deploy config. Merge it before any deploy artifact so the hardening is in place when the first image is built.
- **PR-4 separate from PR-2 (user-confirmed)** — smaller diffs; the `deploy` job is `workflow_dispatch`-only so CI can merge before any VM exists. Combining would force one large review of unrelated concerns (compose YAML vs GitHub Actions YAML).
- **Prod `.env.example` uses `POSTGRES_USER=ragwiki`/`POSTGRES_DB=ragwiki`** (distinct from dev's `rag_wiki`) to match the PRD-002 §Secrets assembled `DATABASE_URL=postgresql+asyncpg://ragwiki:${POSTGRES_PASSWORD}@db:5432/ragwiki`. The dev `.env.example` at repo root keeps `rag_wiki`.
- **`tls internal` in Caddyfile, not Tailscale HTTPS** — Caddy's internal CA is the simpler default; flipping to Tailscale's built-in HTTPS is a one-line change. Both satisfy "no public DNS, no open ports."
- **Plan not persisted to `docs/plans/`** (user-confirmed) — this root handoff file is the source of truth instead. No new docs dir created.
- **`tests/settings/` is a new test dir** — follows the `tests/<module>/` mirror pattern (settings.py → tests/settings/). No existing settings tests to extend.

---

## Gotchas & Constraints

- **No `deploy/` dir exists today** — must be created in PR-2. No `docs/plans/` dir either (not created).
- **`scripts/` currently has only `demo-setup.sh` + `fix-venv.sh`** — `backup.sh` is additive.
- **`.github/workflows/ci.yml` lines 284-295 are a commented deferred-jobs block** — PR-4 replaces that exact block; do not delete the surrounding `# ====` banner, only the `push-images`/`helm-lint`/etc. deferred bullets that PR-4 implements. Leave `helm-lint`, `nightly [mineru]/[s3] smoke`, `live-provider`, `pip-audit` bullets alone (still deferred).
- **`build-images` job currently tags `ghcr.io/${{ github.repository }}:${{ github.sha }}` with `push: false`** — PR-4's `push-images` should reuse the GHA cache (`cache-from: type=gha`) and push `:latest` + `:sha-<short>` under `${{ github.repository_owner }}/rag-wiki` (note: `repository_owner`/`rag-wiki`, not full `repository` — the image name is `rag-wiki`, matching the compose `image:`).
- **`rag_wiki/settings.py` has no validators today** — `mcp_host: str = "127.0.0.1"`, `mcp_port: int | None = None`, `mcp_transport: Literal["stdio","http"] = "stdio"`. Add a `@model_validator(mode="after")` method; import `model_validator` from `pydantic`.
- **`docker-entrypoint.sh` runs `alembic upgrade head` then `exec "$@"`** — prod compose reuses it unchanged for both `api` and `worker` (worker also needs migrations up-to-date). No change needed.
- **`GET /health` does a `SELECT 1`** (`rag_wiki/api/routes/health.py:33`) — lightweight DB ping, ready as compose healthcheck. Use `curl -f http://localhost:8000/health` (or `wget`) in the `api` service healthcheck.
- **`.venv/` was root-owned this session** — `uv run` failed with `Permission denied` on `.venv/CACHEDIR.TAG`; `./scripts/fix-venv.sh` needs `sudo` (password, no terminal for askpass). Workaround used: `export UV_PROJECT_ENVIRONMENT=/tmp/opencode/ragwiki-venv && uv venv --python /usr/bin/python3 && uv sync --extra dev`, then prefix every `uv run` with the same `UV_PROJECT_ENVIRONMENT` export. To permanently fix, run `sudo ./scripts/fix-venv.sh` from a real terminal.
- **GHCR namespace is a placeholder** — `${GHCR_OWNER}` in compose, `${{ github.repository_owner }}` in CI. No code change when the GitHub account is created; only a `.env` value on the VM. Do not hardcode an owner.

---

## Critical Snippets

### `rag_wiki/settings.py` — MCP fields to validate (lines 90-94)
```python
# Context: PR-1 adds a model_validator(mode="after") that raises ValueError
# when mcp_transport == "http" and mcp_host not in {127.0.0.1, ::1, localhost}.
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_api_url: AnyHttpUrl = "http://127.0.0.1:8000"  # type: ignore[assignment]
    mcp_host: str = "127.0.0.1"
    mcp_port: int | None = None
```

### `docker-entrypoint.sh` — reused unchanged by prod compose
```bash
#!/usr/bin/env bash
set -e
echo "Running Alembic migrations..."
uv run alembic upgrade head
echo "Migrations complete. Starting command..."
exec "$@"
```

### `.github/workflows/ci.yml` — deferred block to replace in PR-4 (lines 284-295)
```yaml
# =========================================================================== #
# Deferred (flagged in ci-context.md §10 / "not yet decided"):                #
#   - push-images        : needs GHCR/registry creds + OIDC; add once registry  #
#                         exists. ...                                          #
#   - helm-lint/publish  : no chart in repo yet ...                            #
#   - nightly [mineru]/[s3] smoke + SeaweedFS service container ...            #
#   - live-provider job   : gated on a secret ...                              #
#   - pip-audit           : third-party dependency audit (nightly).            #
# =========================================================================== #
```
PR-4 implements only `push-images` + `deploy`; leave `helm-lint`, `nightly smoke`, `live-provider`, `pip-audit` bullets in the deferred block.

---

## Artifacts

| Artifact | Path | Notes |
|----------|------|-------|
| ADR-0017 | `docs/adr/0017-stage-1-deployment-topology.md` | Accepted — owns topology/trust/CI/secrets/MCP/ops rationale |
| PRD-002 | `docs/prd/002-stage-1-deployment.md` | Final — owns user stories, implementation decisions, testing decisions, out-of-scope |
| This handoff | `HANDOFF-stage-1-deployment.md` | Implementation source of truth — PR task breakdown with statuses |
| Existing CI | `.github/workflows/ci.yml` | lint→typecheck→test→migrations→build-images→scan-images; PR-4 extends, doesn't rewrite |
| Existing dev compose | `docker-compose.yml` | Untouched reference — bind-mount/--reload/pgadmin/SeaweedFS |
| Existing Dockerfile | `Dockerfile` | Untouched — already prod-shaped |
| Existing health route | `rag_wiki/api/routes/health.py` | Untouched — reused as compose healthcheck |

---

## Next Session Checklist

> Tasks the next agent should do, in order. Update statuses in the Implementation Plan Status table above as each completes.

- [x] **PR-1**: Add `model_validator` to `rag_wiki/settings.py` (reject non-loopback MCP HTTP bind)
- [x] **PR-1**: Create `tests/settings/test_mcp_loopback_validator.py` (6 cases: rejects `0.0.0.0`/`192.168.x.x`, accepts `127.0.0.1`/`::1`/`localhost`, accepts any host when stdio)
- [x] **PR-1**: Update `.env.example` `MCP_HOST` comment (loopback note)
- [x] **PR-1**: Run `uv run ruff check && ruff format && mypy && pytest tests/settings/` — all green
- [x] **PR-1**: Open PR — merged as #92
- [x] **PR-2** (parallel with PR-1): Create `deploy/docker-compose.prod.yml` (db/api/worker/caddy, `DATABASE_URL` inline, named volumes, no dev services)
- [x] **PR-2**: Create `deploy/Caddyfile` (`tls internal`, `reverse_proxy api:8000`, `health_uri /health`)
- [x] **PR-2**: Create `deploy/.env.example` (all `settings.py` vars grouped, `CORS_ORIGINS=""`, prod `ragwiki` user/db)
- [x] **PR-2**: Validate `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.example config` exits 0
- [x] **PR-2**: Open PR — merged as #93
- [x] **PR-3** (after PR-2): Create `scripts/backup.sh` (`pg_dump | gzip` + 7-day `find -delete`), `chmod +x`, smoke
- [x] **PR-3**: Open PR — merged as #94
- [x] **PR-4** (after PR-2): Add `push-images` job (replace deferred block lines 284-295; `:latest`+`:sha-<short>`; `packages: write`)
- [x] **PR-4**: Add `deploy` job (`workflow_dispatch` only; `appleboy/ssh-action` pinned; `compose pull && up -d`)
- [x] **PR-4**: Update `docs/ci-context.md` §10 (remove `push-images` from deferred; note `deploy` manual-gated)
- [x] **PR-4**: Open PR — merged as #95
- [x] **PR-5** (after PR-2/3/4): Write `deploy/README.md` runbook (Tailscale setup, .env fill-in, deploy/rollback, backups cron, MCP-from-laptop, observability, Stage-2 link)
- [x] **PR-5**: Open PR — merged as #96
- [x] After all merge: run full quality gate `uv run ruff check && ruff format && mypy && pytest` — all green (364 passed, 7 skipped)
- [x] Run `handoff` skill again at end of next session — done (this update)

---

## Suggested Skills

- `handoff` — run again at the end of the next implementation session to update PR statuses and capture gotchas discovered while writing code.
- (No `grilling` or `adr` skills needed — ADR-0017 and PRD-002 are final. Implementation is a straight execution of the plan.)

---
_Handoff generated by `handoff` skill, customized for repo-root persistence per user request. Do not edit the ADR/PRD rationale sections — update statuses in the Implementation Plan Status table and the Next Session Checklist only._
