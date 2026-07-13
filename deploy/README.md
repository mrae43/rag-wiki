# Stage-1 Deployment Runbook

Compose-on-VM, trusted-clients-only. Single VM runs `db`, `api`, `worker`,
`caddy` behind Tailscale-internal TLS; the image is pulled from GHCR; a daily
`pg_dump` cron protects the database.

> **Source of truth:** [ADR-0017](../docs/adr/0017-stage-1-deployment-topology.md)
> (topology, trust, CI, secrets, MCP, ops rationale) and
> [PRD-002](../docs/prd/002-stage-1-deployment.md) (user stories, implementation
> decisions, out-of-scope). This runbook is the operational companion to those
> two docs — when in doubt, the ADR wins.

---

## 1. Prerequisites

### 1.1 Tailscale

The VM and every client that will reach the Backend must be on the same
[Tailscale](https://tailscale.com/) tailnet. The VM exposes **zero public
ports**; the only path in is over the tailnet.

1. Install Tailscale on the VM: `curl -fsSL https://tailscale.com/install.sh | sh`.
2. `sudo tailscale up` and authenticate.
3. Enable [MagicDNS](https://tailscale.com/kb/1081/magicdns) (tailnet admin
   console → DNS). Note your tailnet name, e.g. `example.ts.net` — this is
   `TAILNET_HOST` in `.env`.
4. (Optional but recommended) enable Tailscale's HTTPS feature so the tailnet
   hostname gets a valid cert automatically. If you do this, switch the
   Caddyfile from `tls internal` to Tailscale's DNS provider — see §8.

### 1.2 Docker

Install Docker Engine + the `docker compose` plugin on the VM (do **not**
build images on the VM — it pulls from GHCR):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out/in after
```

### 1.3 Caddy internal CA trust

By default the Caddyfile uses `tls internal` — Caddy minted an internal CA.
Every client that hits `https://rag-wiki.<tailnet>` must trust that CA root,
or it will refuse the TLS cert. Install the Caddy root once into each client
OS trust store (the easiest path is to let Caddy ship its root via the
`caddy trust` command run from a machine that can reach the VM, or copy
`~/.local/share/caddy/pki/authorities/root/` onto the client and import it).
Alternatively, flip to Tailscale's built-in HTTPS (§8) and skip this step.

### 1.4 GitHub Container Registry

CI pushes to `ghcr.io/<github.repository_owner>/rag-wiki`. The package is
created automatically on the first `main` push after PR-4 merges — go to
`https://github.com/users/<owner>/packages/container/rag-wiki` and ensure the
visibility/permissions are what you want (private by default; the VM pulls
with a [personal access token](https://github.com/settings/tokens) that has
`read:packages`, or `docker login ghcr.io` interactively once on the VM).

---

## 2. First deploy

### 2.1 Get the repo onto the VM

```bash
git clone https://github.com/<owner>/rag-wiki.git /opt/rag-wiki
cd /opt/rag-wiki
```

`/opt/rag-wiki` is the canonical `DEPLOY_PATH` (set as the GitHub Actions
secret of the same name in §3.2). Any path works; just be consistent.

### 2.2 Fill in `.env`

```bash
cp deploy/.env.example deploy/.env
editor deploy/.env
```

Set at minimum (search the file for the full list — every var from
`rag_wiki/settings.py` is present, grouped by concern):

| Variable | Why |
|---|---|
| `GHCR_OWNER` | Your GitHub owner/login (must match `github.repository_owner`). |
| `IMAGE_TAG` | Pin to a sha tag (`sha-a1b2c3d`) for reproducibility, or `latest`. |
| `TAILNET_HOST` | Your tailnet, e.g. `example.ts.net`. |
| `POSTGRES_PASSWORD` | `openssl rand -base64 32`. Do not reuse a dev secret. |
| `LLM_API_KEY` | Provider key (or leave empty only for a no-auth local endpoint). |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | Must match the model you actually use; both must agree. |

`POSTGRES_USER=ragwiki` and `POSTGRES_DB=ragwiki` are the prod defaults
(distinct from dev's `rag_wiki`) — leave them unless you have a reason.
`DATABASE_URL` is **not** in `.env` — compose assembles it from the
`POSTGRES_*` vars + the `db` service host. `CORS_ORIGINS=""` is locked; do
not wildcard it.

### 2.3 Log the VM into GHCR (private packages)

```bash
echo "<YOUR_PAT_WITH_read:packages>" | docker login ghcr.io -u "<owner>" --password-stdin
```

### 2.4 Validate, then bring it up

```bash
# Interpolation + schema check (no containers started). Must exit 0.
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env config >/dev/null

# Pull the image and start the stack.
docker compose -f deploy/docker-compose.prod.yml up -d
```

Compose auto-discovers `deploy/.env` for `${VAR}` interpolation (the project
directory is the compose file's directory), so running `-f deploy/...` from
the repo root Just Works.

The `api` and `worker` entrypoint runs `alembic upgrade head` before the
app/worker start (User Story 8) — migrations apply automatically on first
boot. `api`'s healthcheck pings `GET /health` (a `SELECT 1`); Caddy will not
forward traffic until `api` reports healthy.

### 2.5 Verify

From a machine on the same tailnet:

```bash
curl -sk https://rag-wiki.<tailnet>/health   # -k until the Caddy CA is trusted (§1.3)
```

Expect a `200` with a small JSON body. `docker compose -f deploy/docker-compose.prod.yml ps`
should show every service `healthy`/`running`.

---

## 3. Subsequent deploys (CI-driven)

After PR-4 merges, the `ci.yml` pipeline runs two new jobs:

- **`push-images`** — on `main` push or `workflow_dispatch`, pushes `:latest`
  and `:sha-<short>` to GHCR after lint → typecheck → test → migrations →
  build → Trivy scan all pass.
- **`deploy`** — **`workflow_dispatch` only** (manual-gate, ADR-0017 §4).
  SSHes to the VM and runs `docker compose -f deploy/docker-compose.prod.yml
  pull && up -d --remove-orphans`.

### 3.1 Configure the deploy secrets

In the repo's **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | VM's Tailscale IP or MagicDNS hostname. |
| `DEPLOY_USER` | SSH user on the VM (e.g. `deploy` or `ubuntu`). |
| `DEPLOY_SSH_KEY` | Private key whose public half is in the VM user's `~/.ssh/authorized_keys`. |
| `DEPLOY_PATH` | Repo path on the VM, e.g. `/opt/rag-wiki`. |

The GitHub runner needs outbound SSH to `DEPLOY_HOST` over the tailnet —
either run a self-hosted runner on the tailnet, or expose Tailscale SSH on the
VM from the public runner (Stage-2 will revisit; for a single operator the
simplest is a self-hosted runner on the tailnet, but the job is written to
work with any runner that can reach `DEPLOY_HOST:22`).

### 3.2 Run a deploy

GitHub → **Actions → CI → Run workflow** (on `main`). Pick the `deploy`
workflow. The `deploy` job runs after `push-images` succeeds and prints the
remote `compose up` output into the workflow log.

The `deploy` job **automatically pins `IMAGE_TAG`** to the exact `sha-<short>`
it just built and scanned, before `docker compose pull` (ADR-0017 §4 / PRD-005
Gap #5). One SSH step runs
`python3 scripts/pin_image_tag.py deploy/.env sha-<short>`, which atomically
rewrites the `IMAGE_TAG=` line in the VM's `.env` and leaves every other
line byte-identical. The pinner **refuses to run** if `IMAGE_TAG=` is absent
from `.env` — so an operator who copies `.env.example` to `.env` but forgets
to set `IMAGE_TAG` gets a clear error naming the missing key and the file
path, rather than a silently-invented line. The `workflow_dispatch` UI is
unchanged (still a one-click manual gate).

Rollback is unchanged: a one-line `.env` edit (PRD-005 User Story 16):

```bash
cd /opt/rag-wiki
editor deploy/.env        # set IMAGE_TAG=sha-<previous-good-sha>
docker compose -f deploy/docker-compose.prod.yml pull
docker compose -f deploy/docker-compose.prod.yml up -d --remove-orphans
```

To instead deploy by hand on the VM (skipping the pinner):

```bash
cd /opt/rag-wiki
git pull --ff-only
docker compose -f deploy/docker-compose.prod.yml pull
docker compose -f deploy/docker-compose.prod.yml up -d --remove-orphans
```

---

## 4. Rollback

Rollback is editing one line and re-running `up -d` (no blue-green, no
staging env in Stage-1).

```bash
cd /opt/rag-wiki
editor deploy/.env        # set IMAGE_TAG=sha-<previous-good-sha>
docker compose -f deploy/docker-compose.prod.yml pull
docker compose -f deploy/docker-compose.prod.yml up -d --remove-orphans
```

Every pushed sha tag is immutable in GHCR, so a rollback is always a known-good
artifact. `:latest` moves with every `main` push — prefer sha tags for
production.

---

## 5. Backups

A daily logical `pg_dump -Fc` is the Stage-1 backup (ADR-0017 §7 / PRD-005
Gap #3). It runs on the VM **host** (not in a container) so a crashed `db`
container can't prevent backups. Every dump is validated with
`pg_restore --list` before the script exits 0; a zero-byte or structurally
corrupt dump fails loudly and is left in place for inspection.

### 5.1 Install the cron

The backup validator is a small Python helper invoked with `uv run python`
inside the repo, so `uv` must be available on the VM host (see
[uv installation](https://docs.astral.sh/uv/getting-started/installation/)):

```bash
sudo mkdir -p /backups
sudo chown "$USER":"$USER" /backups

# daily at 02:30
( crontab -l 2>/dev/null; echo "30 2 * * *  /opt/rag-wiki/scripts/backup.sh >> /var/log/rag-wiki-backup.log 2>&1" ) | crontab -
```

### 5.2 Env overrides (optional)

The script reads these env vars (defaults shown):

```bash
COMPOSE_FILE=deploy/docker-compose.prod.yml
BACKUP_DIR=/backups
DB_USER=ragwiki
DB_NAME=ragwiki
RETENTION_DAYS=7
```

Override in the crontab line if needed, e.g.
`BACKUP_DIR=/mnt/backups /opt/rag-wiki/scripts/backup.sh`.

### 5.3 Manual smoke

```bash
COMPOSE_FILE=deploy/docker-compose.prod.yml BACKUP_DIR=/backups \
  /opt/rag-wiki/scripts/backup.sh
ls -lh /backups                  # non-empty .dump
pg_restore --list /backups/ragwiki-*.dump   # valid custom-format archive
```

Retention is 7 days via `find /backups -name 'ragwiki-*.dump' -mtime +7 -delete`.
Legacy `.sql.gz` files from before PRD-005 are still pruned under the same rule
and age out naturally; they remain restorable with `gunzip | psql` if needed.
WAL archiving / point-in-time recovery is Stage-2+.

### 5.4 Restore (test on a throwaway VM, not in prod)

```bash
docker cp /backups/ragwiki-<date>.dump "$(docker compose -f deploy/docker-compose.prod.yml ps -q db)":/tmp/
docker compose -f deploy/docker-compose.prod.yml exec -T db \
  pg_restore -U ragwiki -d ragwiki /tmp/ragwiki-<date>.dump
```

For a legacy `.sql.gz` backup, restore with `gunzip | psql`:

```bash
docker cp /backups/ragwiki-<date>.sql.gz "$(docker compose -f deploy/docker-compose.prod.yml ps -q db)":/tmp/
docker compose -f deploy/docker-compose.prod.yml exec -T db \
  gunzip -c /tmp/ragwiki-<date>.sql.gz | psql -U ragwiki -d ragwiki
```

---

## 6. Monthly restore drill

A monthly verification that a backup can actually be restored (PRD-005 Gap #4 /
ADR-0017 §7). The drill restores a chosen custom-format dump into a **scratch**
Postgres database (never the prod `db` service), confirms the six core tables
are present (`sources`, `chunks`, `entities`, `relations`, `wiki_pages`,
`jobs`), then drops the scratch DB. If any step fails the script exits non-zero
and the scratch DB is still dropped via `trap`.

### 6.1 One-command drill

```bash
COMPOSE_FILE=deploy/docker-compose.prod.yml \
  scripts/restore_drill.sh \
    /backups/ragwiki-2026-07-13.dump \
    postgresql://ragwiki:${POSTGRES_PASSWORD}@localhost:5432/ragwiki_drill_20260713
```

The script parses the connection URI to set `PGUSER` / `PGPASSWORD` / `PGHOST` /
`PGPORT` for `createdb`/`dropdb` (which do not accept URIs) and passes the full
URI to `pg_restore` and `psql`. The only requirement is that the operator has
network access to the VM's Postgres port — on a single-VM deploy this is always
`localhost:5432`.

### 6.2 Scratch-DB naming convention

Use the pattern `ragwiki_drill_<YYYYMMDD>`. The script **refuses** to restore
into a database named `ragwiki` (the prod default) — this is a defensive guard
against running the drill against the production database. Change the guard
name via the `PROD_DB_NAME` environment variable if your prod DB has a
different name.

### 6.3 Pass/fail criteria

| Outcome | Exit code | What happened |
|---|---|---|
| **PASS** | `0` | Dump validated; scratch DB created; `pg_restore` succeeded; all six core tables present; scratch DB dropped. |
| **FAIL** | `1` | Any failure (corrupt dump, restore error, missing table, etc.). Reason printed to stderr. Scratch DB dropped. |
| **Usage error** | `2` | Wrong arguments (missing `<dump_file>` or `<scratch_db_url>`). |

In all cases the scratch database is guaranteed to be removed — the `trap` on
`EXIT` calls `dropdb` regardless of the exit path (User Story 26).

### 6.4 Running from CI

The `backup-validation` CI job runs the drill automatically against a known-good
dump and a corrupt dump to lock the pass/fail semantics (User Story 21). See
`.github/workflows/ci.yml`.

### 6.5 Scheduling

The drill is **manual** in Stage-1 — add a monthly calendar reminder, pick the
latest dump from `/backups`, and run the one-command invocation above.
Scheduled automation (e.g. a cron or GitHub Scheduled Workflow) is deferred to
Stage-2 (ADR-0017 §7 notes that backup verification must be a verified
procedure before any automation; the manual repetition builds that confidence).

---

## 7. MCP from the operator's laptop

No MCP service runs in prod compose (ADR-0017 §6) — the operator runs the
MCP server locally over **stdio**, pointing at the deployed API over
Tailscale. Obsidian / Claude Desktop / Copilot Chat spawn it as a stdio
process.

### 7.1 Install the CLI on the laptop

```bash
uv tool install rag-wiki          # or: pipx install rag-wiki
```

### 7.2 Run it

Either set the API URL inline, or via env var (no prefix — see
`rag_wiki/settings.py`):

```bash
# inline flag
rag-wiki mcp serve --api-url https://rag-wiki.<tailnet>

# or env var
MCP_API_URL=https://rag-wiki.<tailnet> rag-wiki mcp serve
```

`MCP_TRANSPORT` defaults to `stdio` — leave it. If you ever switch to
`--transport http`, `MCP_HOST` **must** be a loopback address
(`127.0.0.1` / `::1` / `localhost`); the `Settings` `model_validator`
rejects any other value (ADR-0017 §6 / PR-1).

### 7.3 Wire it into your MCP client

For Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS), add:

```json
{
  "mcpServers": {
    "rag-wiki": {
      "command": "rag-wiki",
      "args": ["mcp", "serve", "--api-url", "https://rag-wiki.<tailnet>"]
    }
  }
}
```

For Obsidian, use the equivalent MCP-plugin config. The laptop must trust
Caddy's internal CA (§1.3) or the httpx client will fall back to plaintext /
refuse the cert.

---

## 8. Observability

Stage-1 ops floor is deliberately minimal (ADR-0017 §7): structured logs to
stdout, captured per-container by Docker. No Loki/Grafana/Prometheus.

```bash
cd /opt/rag-wiki
docker compose -f deploy/docker-compose.prod.yml logs -f api
docker compose -f deploy/docker-compose.prod.yml logs -f worker
docker compose -f deploy/docker-compose.prod.yml logs -f db
docker compose -f deploy/docker-compose.prod.yml ps          # health at a glance
curl -sk https://rag-wiki.<tailnet>/health                   # the only probe
```

`LOG_LEVEL` / `LOG_FORMAT` (json | console) are in `.env`. A wedged `api`
container is restarted by the compose healthcheck (`GET /health`, a `SELECT 1`).

---

## 9. Branch protection (repo-admin)

Branch protection is codified in-repo for transparency and auditability
(ADR-0018). The configuration is in two files:

| File | Purpose |
|------|---------|
| `.github/branch-ruleset.json` | Main branch ruleset definition (required PR, status checks, linear history, no force-push, no deletion, no admin bypass) |
| `.github/tag-ruleset.json` | Tag-protection ruleset (`v*.*.*` — no force-push, no deletion) |
| `scripts/apply-branch-protection.sh` | Idempotent script that applies both rulesets and repo toggles via `gh api` |

### Prerequisites

```bash
gh auth refresh -h github.com -s admin:repo_hook
```

### Apply

```bash
# From the repo root:
./scripts/apply-branch-protection.sh
```

The script auto-detects `owner`/`repo` from `git remote get-url origin`.
Override with `GITHUB_OWNER` / `GITHUB_REPO` env vars. To force-update a
specific ruleset by numeric ID, set `RULESET_ID`.

The script also applies a tag-protection ruleset (`v*.*.*` no force-push, no
deletion) and repo-level toggles (auto-delete head branches = on, workflow
permissions default = read, push-protection verify). Use `SKIP_TAG=1` or
`SKIP_TOGGLES=1` to skip either step.

---

## 10. Stage-2 (additive — no Stage-1 rewrite)

Every Stage-2 enhancement lands as a new file or service block; none of the
Stage-1 artifacts in this directory are rewritten. See ADR-0017 §Consequences
for the full list. Highlights:

| Stage-2 move | How it lands |
|---|---|
| Helm chart | New `deploy/helm/` — translates `docker-compose.prod.yml` → `values.yaml` line-by-line. |
| Shared API key | New `RAG_WIKI_API_KEY` env var on the `api` service. |
| Auto-deploy on `main` | Trigger flip on the `deploy` job (`workflow_dispatch` → `push: branches: [main]`). |
| SeaweedFS / S3 storage | New service block + `STORAGE_PROVIDER=s3`. |
| MCP HTTP service in prod | New `mcp` service (contingent on a real remote client + auth revisit). |
| Public HTTPS domain | Edit the Caddyfile site block + add a DNS record; drop `tls internal`. Or use Tailscale's built-in HTTPS: replace `tls internal` with the Tailscale DNS provider (one line). |
| Managed Postgres | Swap the `db` service. |
| Observability stack | New `logging` service block appended to the compose. |
| WAL archiving / PITR | Arrives naturally with managed Postgres or the Helm chart. |

When proposing a Stage-2 change, start from the rejection rationale in
ADR-0017 — each rejected alternative is documented there.

---

## 11. Resource limits & log rotation

ADR-0017 §2 requires every prod container to carry an explicit `mem_limit`/`cpus`
bound and a capped `json-file` log driver. Both are set in
`docker-compose.prod.yml` and asserted by `tests/deploy/test_compose_config.py`
(presence + shape, not the chosen numbers).

### 10.1 Baseline (4 GB VM)

| service | `mem_limit` | `cpus` | rationale |
|---|---|---|---|
| `db` | `1400m` | `1.0` | Largest budget — Postgres `shared_buffers`/`work_mem` need it most |
| `api` | `700m` | `0.5` | uvicorn working set + request handling |
| `worker` | `500m` | `0.5` | Capped below `db` and `api` — a runaway ingest degrades throughput, not the db/api |
| `caddy` | `64m` | `0.25` | Small fixed budget — reverse proxy stays responsive under load |

Sum ≈ 2.66 GB < 4 GB, leaving ~1.4 GB for the OS, the Docker daemon, and
headroom.

### 10.2 2 GB VM variant

If the VM is 2 GB, halve the budgets (tight): `db 768m / api 384m / worker
256m / caddy 48m`. Note that `worker 256m` is tight for large-PDF parsing —
watch the first big ingest.

### 10.3 Log rotation

Every service uses `driver: json-file` with `max-size: "10m"` and
`max-file: "3"` (~30 MB per service worst case), so a chatty container cannot
silently fill the VM's disk. No log shipping in Stage-1 (Stage-2 adds a
`logging` aggregation service additively).

### 10.4 Resizing

The numbers are baked into `docker-compose.prod.yml` (not env-interpolated —
Compose has no per-resource env override that's cleaner than editing the
file). To resize: edit the `mem_limit`/`cpus` values in
`deploy/docker-compose.prod.yml`, re-run `docker compose up -d`. The
`.env.example` `## Resource limits` block records the same baseline for an
SME customer lifting the deployment (PRD-005 User Story 25).
