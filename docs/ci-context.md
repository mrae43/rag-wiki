# CI/CD Pipeline — Source of Truth

This document is the reference standard for `ci.yml`. Use it to review the
current workflow file, catch drift, and onboard new pipeline changes. It is
derived from the stack in `tech-stack.md`: Python 3.12+, FastAPI, PostgreSQL
16+/pgvector, SQLAlchemy 2.0 (async) + Alembic, pytest/ruff/mypy, Docker +
Docker Compose, Helm.

Anything in the actual `ci.yml` that contradicts this doc should be treated as
a bug in one of the two — update whichever is wrong.

---

## 1. Principles

These are the non-negotiables the workflow should satisfy, in priority order:

1. **Fail fast, fail cheap** — cheapest/fastest checks (lint, format) run
   first and block everything else; expensive checks (integration tests,
   image builds) only run once cheap checks pass.
2. **Reproducibility** — pinned action versions (`@vX.Y.Z`, not `@main`),
   pinned Python/tool versions, lockfile-based installs. No "works on my
   machine" drift between local and CI.
3. **Separation of concerns** — one job per concern (lint, typecheck, test,
   build, scan, deploy). Don't collapse everything into a single monolithic
   job; parallelize what has no dependency relationship.
4. **Least privilege** — default `permissions: {}` at workflow level,
   elevated per-job only where needed (e.g. `packages: write` for the image
   push job, `id-token: write` for OIDC cloud auth).
5. **Deterministic caching** — cache keyed on lockfile hash, not branch name;
   cache invalidates automatically on dependency change.
6. **Config parity with tech-stack.md** — CI should exercise the same
   provider abstractions the app supports (Postgres/pgvector, local vs. S3
   storage) without hardcoding assumptions that only hold in prod.
7. **Readable over clever** — job and step names describe *intent*
   ("Run unit tests", not "pytest"), consistent naming/formatting across jobs.

---

## 2. Trigger strategy

| Trigger | Purpose |
|---|---|
| `pull_request` → `main` | Full validation gate (lint, typecheck, test, build — no push/deploy) |
| `push` → `main` | Same validation + build & push Docker images (tag: `sha`, `latest`) |
| `push` tag `v*.*.*` | Build, scan, push `:v*.*.*` image + create GitHub Release (`.github/workflows/release.yml` per ADR-0018 §#11); Helm chart version bump deferred |
| `workflow_dispatch` | Manual re-run / manual deploy trigger for a given environment |
| `schedule` (nightly, optional) | Slow checks not worth running per-PR: dependency audit, MinerU optional-path tests |

Use `paths:`/`paths-ignore:` filters so doc-only changes (`docs/**`, `*.md`)
don't trigger the full pipeline — except this file and `.github/workflows/**`
itself, which should always trigger at least the lint stage.

Use `concurrency` groups per ref (`group: ci-${{ github.ref }}`,
`cancel-in-progress: true`) so superseded pushes to the same branch don't
queue redundant runs.

---

## 3. Job graph (target shape)

```
                ┌───────────┐
                │   lint    │  ruff check + ruff format --check
                └─────┬─────┘
                      │
           ┌──────────┼──────────┐
           ▼          ▼          ▼
      ┌─────────┐ ┌────────┐ ┌───────────┐
      │ typecheck│ │  test  │ │ migrations │  (parallel, all depend on lint)
      │  (mypy)  │ │(pytest)│ │ (alembic)  │
      └─────────┘ └────┬───┘ └───────────┘
                        │
                        ▼
                ┌───────────────┐
                │  build-images  │  docker build: api, worker
                └───────┬────────┘
                         │
                         ▼
                ┌───────────────┐
                │  scan-images   │  vulnerability scan (e.g. Trivy)
                └───────┬────────┘
                         │
                 (push branch only)
                         ▼
                ┌───────────────┐
                │  push-images   │  push to registry, tag sha/latest
                └───────┬────────┘
                         │
                 (tag push only)
                         ▼
                ┌───────────────┐
                │  helm-lint /   │  chart lint + version bump
                │  helm-publish  │
                └───────────────┘
```

Rule of thumb: **PRs stop after `scan-images`.** Only pushes to `main` or
version tags proceed to `push-images` / `helm-publish`.

---

## 4. Job-by-job checklist

### `lint`
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] Runs on every trigger, no service containers needed.
- [ ] Should complete in well under a minute — this is the fail-fast gate.

### `typecheck`
- [ ] `mypy` against the package source (not just changed files).
- [ ] Depends on `lint` passing (no point type-checking unformatted code).
- [ ] Cache mypy's `.mypy_cache` keyed on lockfile hash + source hash.

### `test`
- [ ] `pytest` + `pytest-asyncio`, run against a real **Postgres 16 +
      pgvector** service container (not sqlite/mocked) — the app's ORM layer
      and vector column type are Postgres-specific per ADR-0003.
- [ ] Service container image should be the same `pgvector/pgvector:pg16`
      (or equivalent) used in `docker-compose.yml`, not a bare `postgres`
      image, to avoid "extension not found" drift between CI and local dev.
- [ ] Run Alembic migrations against the service container *before* tests
      (`alembic upgrade head`) so tests run against the real, current schema
      rather than `Base.metadata.create_all()`.
- [ ] Coverage report uploaded as an artifact (and optionally to a coverage
      service); do not silently allow coverage regressions.
- [ ] `LocalStorageProvider` path tested by default; `S3StorageProvider`
      (aioboto3/SeaweedFS) covered either with a MinIO/SeaweedFS service
      container or clearly marked as an optional/nightly job — don't let it
      block every PR if it needs extra infra.

### `migrations` (can fold into `test`, or keep separate for visibility)
- [ ] `alembic upgrade head` then `alembic check` (or equivalent
      autogenerate-diff check) to catch model/migration drift — a model
      change without a matching migration should fail CI.
- [ ] Optionally verify `alembic downgrade -1` doesn't error, to keep
      downgrades honest.

### `build-images`
- [ ] Builds both **api** and **worker** images (per the two entrypoints in
      tech-stack.md — `uvicorn` app and `python -m rag_wiki.worker`).
- [ ] Uses Docker Buildx with layer caching (`cache-from`/`cache-to`, GitHub
      Actions cache backend) so rebuilds are incremental.
- [ ] Builds are tagged with the commit SHA at minimum; human-readable tags
      (`latest`, `vX.Y.Z`) applied only at push time, never at build time.
- [ ] Matrix over `[api, worker]` rather than two near-duplicate jobs.

### `scan-images`
- [ ] Vulnerability scan (e.g. Trivy/Grype) against built images before they
      are ever pushed.
- [ ] Fails the pipeline on `CRITICAL`/`HIGH` findings unless explicitly
      allow-listed (with a reason and expiry, not a silent ignore).

### `push-images` (main branch / tags only)
- [ ] Requires `packages: write` permission, scoped to this job only.
- [ ] Pushes `sha`-tagged image always; `latest` on `main`; `vX.Y.Z` on tag
      push.
- [ ] Uses OIDC federation to the registry/cloud provider instead of
      long-lived static credentials, where the registry supports it.

### `helm-lint` / `helm-publish`
- [ ] `helm lint` on every PR that touches the chart.
- [ ] Chart version bump + `helm package`/publish only on tagged releases —
      this is the enterprise deployment artifact per tech-stack.md, so it
      should follow the same semver as the image tags it references.

### Optional / nightly
- [ ] Dependency audit (`pip-audit` or equivalent) for the `openai` /
      `anthropic` client libs and other third-party deps.
- [ ] Optional-dependency-group smoke tests: `rag-wiki[mineru]`,
      `rag-wiki[s3]` — install the extra and run a minimal smoke test so
      these paths don't silently rot.

---

## 5. Environment & secrets

- All configuration is env-var driven (12-factor, per ADR-0004) — CI should
  set env vars the same way production does, via a workflow-level `env:` or
  per-job `env:` block, not by editing config files in-place.
- Secrets (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, registry credentials,
  `S3_*` creds) live in GitHub Environments/Secrets, scoped to the job that
  needs them — the `lint`/`typecheck` jobs need none of these.
- The `deploy` job uses a `production` GitHub Environment (restricted to
  `main` branch, no required reviewers). Deploy secrets (`DEPLOY_SSH_KEY`,
  `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`) are scoped to this environment,
  not repo-level, so they are only accessible during a manual `workflow_dispatch`
  deploy from `main`.
- Per-operation model config (`LLM_MODEL_CAPTION`, `LLM_MODEL_EXTRACTION`,
  etc. per ADR-0007) should point at cheap/mock models in CI, never
  production-tier models, to keep the pipeline fast and cheap. Real LLM
  provider calls in tests should be mocked unless a job is explicitly an
  "integration with live provider" job, run sparingly (nightly or manual).

---

## 6. Caching strategy

| Cache | Key basis | Used by |
|---|---|---|
| Python deps (`uv`/pip) | lockfile hash | lint, typecheck, test |
| `mypy` cache | lockfile hash + source hash | typecheck |
| Docker layer cache | Dockerfile + lockfile hash | build-images |
| Helm dependency cache | `Chart.lock` hash | helm-lint |

Never key a cache on branch name alone — it should be shareable across
branches and invalidate purely on content change.

---

## 7. Naming & readability conventions

- Workflow file: `.github/workflows/ci.yml` (single entrypoint) or split by
  concern (`ci.yml`, `release.yml`) if the combined file gets hard to scan —
  prefer one file until it exceeds ~200 lines.
- Job `name:` fields are human phrases ("Run unit tests"), job **ids** are
  short kebab/camel identifiers used only for `needs:` references.
- Step names always present and descriptive; avoid unnamed steps that show
  up as raw shell commands in the GitHub UI.
- Consistent verb tense across step names ("Install dependencies", "Run
  tests", "Build image" — not a mix of "Installing…"/"Test"/"build_image").

---

## 8. Branch protection (should match this file)

> **Policy owned by ADR-0018** — `docs/adr/0018-branch-protection-release-security-policy.md`.
> That ADR records the *why* for the rules below (solo portfolio repo,
> main-only branching, no admin bypass, Rulesets-over-classic-protection).
> This section is the *what* the enforced ruleset must satisfy.

- [ ] The six gate jobs are **required status checks** on `main`, matched by
      their exact display `name:` strings (GitHub matches required checks by
      `name:`, not job `id`): `Lint`, `Typecheck (mypy)`, `Run unit tests`,
      `Check migrations (drift + upgrade + downgrade)`, `Build image`,
      `Scan image (Trivy)`.
- [ ] Require branches to be up to date before merge
      (`strict_required_status_checks: true`).
- [ ] Require the workflow to have run on the exact merge commit (no stale
      approvals on force-pushed branches).
- [ ] PR required with 0 approving reviews (solo dev — see ADR-0018 §#2).
- [ ] Squash merge only; merge commits and rebase merges disabled; `main`
      enforces linear history (`non_fast_forward` rule).
- [ ] Block force-push and branch deletion on `main`
      (`update.allow_force_pushes=false` + `deletion` rule).
- [ ] No admin bypass (`bypass_actors`/`bypass_teams` empty) — see ADR-0018 §#6
      for the documented escape hatch (delete the ruleset via `gh api`).
- [ ] Codified in-repo as `scripts/apply-branch-protection.sh` +
      `.github/branch-ruleset.json` (idempotent `gh api` to `/rulesets`).
- [ ] A second ruleset protects `v*.*.*` tags from force-push/deletion
      (ADR-0018 §#14c; applied by the extended script in PR-F).

---

## 9. Review checklist (use this against the live `ci.yml`)

- [ ] Does the trigger section match §2?
- [ ] Does the job graph match §3 (no missing `needs:`, no accidental
      full-fan-out with everything depending on nothing)?
- [ ] Is Postgres in `test` running with the `pgvector` extension, matching
      Docker Compose's image, not a generic Postgres image?
- [ ] Are migrations checked for drift, not just applied blindly?
- [ ] Do `push-images`/`helm-publish` correctly gate on `main`/tag instead of
      running on every PR?
- [ ] Are permissions scoped per-job, not a single broad
      `permissions: write-all` at the top?
- [ ] Are all third-party actions pinned to a specific version (ideally a
      commit SHA, at minimum a version tag)?
- [ ] Are secrets referenced only in the jobs that need them?
- [ ] Are caches keyed on content hashes, not branch names?
- [ ] Do optional dependency groups (`[mineru]`, `[s3]`) have *some* CI
      coverage, even if only nightly?

---

## 10. Open questions (mirrors "Not yet decided" in tech-stack.md)

- Auth/RBAC has no CI implications yet — revisit once ADR-0004's auth design
  lands (may add a security-scan job for authz logic).
- Observability stack undecided — once chosen, add a CI check ensuring
  structured logging config doesn't silently break (e.g. schema validation
  on log format).

> **Implemented (no longer deferred) per ADR-0017:**
> - `push-images` — pushes `:latest` + `:sha-<short>` to GHCR on `main` push
>   or `workflow_dispatch`, using the auto-provisioned `GITHUB_TOKEN`.
> - `deploy` — `workflow_dispatch` only (manual-gate), SSHes to the VM and
>   runs `docker compose -f deploy/docker-compose.prod.yml pull && up -d
>   --remove-orphans`. Auto-deploy on `main` is a Stage-2 trigger flip.
>
> **Implemented (no longer deferred) per ADR-0018:**
> - Branch protection — codified as `scripts/apply-branch-protection.sh` +
>   `.github/branch-ruleset.json`; enforced via GitHub Rulesets (no admin
>   bypass). See §8 above and `docs/adr/0018-*.md`.
> - Tag-triggered release pipeline — `.github/workflows/release.yml` (`v*.*.*`
>   trigger, reusable `_build-scan-push.yml`); pushes `:v*.*.*` to GHCR and
>   creates a GitHub Release. Realizes §2's intended tag trigger.
> - `environment: production` on the `deploy` job — deploy secrets
>   (`DEPLOY_*`) moved to environment-scoped. See §5 above.
> - Dependabot (`.github/dependabot.yml`) — `pip` + `github-actions` +
>   `docker` ecosystems, weekly. (If native `uv.lock` ecosystem support is
>   unavailable, falls back to `pip` watching `pyproject.toml` only — verify
>   at apply time.)
> - CodeQL (`.github/workflows/codeql.yml`) — Python analysis on PR
>   (paths-filtered to `rag_wiki/**`, `tests/**`, `pyproject.toml`,
>   `uv.lock`) + weekly schedule.
> - Push protection — verified in repo Settings → Code security at apply
>   time (status field is blank in the `gh api` probe; secret scanning is
>   already enabled).
> - Repo toggles — auto-delete head branches = on; workflow permissions
>   default = read.
>
> Still deferred: `helm-lint/publish`, nightly `[mineru]/[s3]` smoke +
> SeaweedFS service container, `live-provider` job, `pip-audit`, **signed
> commit verification on `main` (ADR-0018 §#13 — Stage-2 enhancement)**,
> **staging environment/branch (ADR-0017 §4 — additive overlay in
> Stage-2)**.