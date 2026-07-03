# Handoff: `branch-protection-release-policy`

**Date:** `2026-07-03` (grilling session) · `2026-07-04` (PR-A executed)
**This session did:** Grilled the user through 14 design-tree decisions for protecting the `main` branch of the public portfolio repo `mrae43/rag-wiki`, plus release/security/hygiene policy. All 14 decisions locked. **PR-A executed and merged as #97** — ADR-0018 written, `docs/ci-context.md` §8/§10 cross-referenced, `AGENTS.md` ADR-index row added.
**Next session goal:** Execute PR-B/F (codify the ruleset in-repo) + PR-C/D/E (CI/workflow changes) + repo-side actions. Follow the dependency graph and PR order below.

---

## Current State

### ADR Status
| ADR | Title | Status | Path |
|-----|-------|--------|------|
| ADR-0017 | Stage-1 deployment topology | Accepted | `docs/adr/0017-stage-1-deployment-topology.md` |
| ADR-0018 | Branch protection, release, and security policy for the public portfolio repo | **Accepted — written by PR-A (#97, merged)** | `docs/adr/0018-branch-protection-release-security-policy.md` |

All other ADRs (0001–0016) Accepted and untouched. ADR-0018 records a cluster of decisions that EXTEND ADR-0017 §4 (manual-gate-on-main) — it does not supersede it. ADR-0017's "no staging env in Stage-1" (line 77) is reaffirmed, not revisited.

### Implementation Plan Status (Task Breakdown)
> Source of truth for monitoring implementation. Update statuses as PRs open/merge.

| PR  | Step | Description | Status | Blocks / Blocked-by |
|-----|------|-------------|--------|---------------------|
| PR-A | 1 | Write ADR-0018 (`docs/adr/0018-*.md`) recording the why for all 14 decisions; update `docs/ci-context.md` §8/§10 cross-refs; update `AGENTS.md` ADR index table | ✅ **merged — #97** | — |
| PR-B | 2 | Create `scripts/apply-branch-protection.sh` (idempotent `gh api` PUT to `/rulesets`) + `.github/branch-ruleset.json` (main ruleset definition); document in `scripts/README.md` or `deploy/README.md` | `not started` | (was PR-A — unblocked) |
| PR-C | 3 | `.github/workflows/ci.yml`: add `environment: production` to `deploy` job; update `docs/ci-context.md` §5 (env-scoped secrets) | `not started` | (was PR-A — unblocked) |
| PR-D | 4 | `.github/workflows/release.yml`: tag-triggered (`v*.*.*`) build+scan+push `:v*.*.*` image + GitHub Release auto-notes; cross-ref `docs/ci-context.md` §2 | `not started` | (was PR-A — unblocked) |
| PR-E | 5 | `.github/dependabot.yml` (pip + github-actions + docker, weekly) + `.github/workflows/codeql.yml` (Python, PR + weekly); note in `docs/ci-context.md` §10 | `not started` | (was PR-A — unblocked) |
| PR-F | 6 | Extend `scripts/apply-branch-protection.sh` with: tag-protection ruleset (`v*.*.*` no force/delete), repo toggles (auto-delete head branches, workflow-perms=read, push-protection verify) | `not started` | PR-B |

**Dependency graph:**
```
PR-A (ADR-0018) ──┬── PR-B (main ruleset script+JSON) ──┐
                   ├── PR-C (deploy env in ci.yml) ─────┤
                   ├── PR-D (release workflow) ─────────┤
                   └── PR-E (dependabot+codeql) ─────────┴── PR-F (tag ruleset + repo toggles, extends PR-B's script)
```
PR-A first (records the why); PR-B/C/D/E parallel after A merges; PR-F last (extends PR-B's script).

### Repo-side actions (manual / `gh api`, NOT in a PR — done after relevant PRs merge)
| # | Action | Needs | Blocks |
|---|--------|-------|--------|
| R1 | Apply the `main` ruleset — run `scripts/apply-branch-protection.sh` | `gh auth refresh -h github.com -s admin:repo_hook` (ruleset write scope) | PR-B |
| R2 | Create `production` GitHub Environment — restricted to `main` branch, no required reviewers; move `DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_USER`/`DEPLOY_PATH` from repo secrets to environment-scoped | repo Settings UI or `gh api` | PR-C |
| R3 | Apply tag-protection ruleset (`v*.*.*` no force/delete) — second ruleset via the extended script | PR-F script | PR-D |
| R4 | Repo toggles: auto-delete head branches = on; workflow permissions default = read; push protection = on (verify — secret scanning already on) | repo Settings UI or `gh api` | PR-F |
| R5 | Prune ~30+ stale `feat/*`/`fix/*`/`refactor/*`/`chore/*` branches on remote (all pre-Stage-1 leftovers, none in recent `git log`) — `git push origin --delete <branch>` per branch | confirm list before deleting | — |

---

## Files

### Modified
```
PR-A (#97, merged):
  docs/adr/0018-branch-protection-release-security-policy.md  (NEW — Status/Context/Decision/Rationale/Consequences, all 14 decisions, no-bypass escape hatch in Decision body)
  docs/ci-context.md     (§8 declares ADR-0018 policy owner + expanded enforceable-rule checklist; §10 moves implemented items out of "Still deferred" into "Implemented per ADR-0018")
  AGENTS.md              (ADR index table — added row 0018, subsystem "CI/Security")
```

### Created
```
HANDOFF-branch-protection-release-policy.md  # this file — plan source of truth
docs/adr/0018-branch-protection-release-security-policy.md  # PR-A — the ADR itself
```

### Files to be touched (per PR, for the next agent)
```
PR-A: docs/adr/0018-branch-protection-release-security-policy.md (NEW), docs/ci-context.md (§8/§10 cross-refs), AGENTS.md (ADR index table)
PR-B: scripts/apply-branch-protection.sh (NEW), .github/branch-ruleset.json (NEW), scripts/README.md or deploy/README.md (doc note)
PR-C: .github/workflows/ci.yml (deploy job: add `environment: production`), docs/ci-context.md (§5 secrets note)
PR-D: .github/workflows/release.yml (NEW), docs/ci-context.md (§2 cross-ref)
PR-E: .github/dependabot.yml (NEW), .github/workflows/codeql.yml (NEW), docs/ci-context.md (§10 note)
PR-F: scripts/apply-branch-protection.sh (extend — add tag ruleset + repo toggles)
```

### Explicitly NOT touched
```
docs/adr/0017-stage-1-deployment-topology.md  # Accepted, not amended — ADR-0018 extends, doesn't supersede
rag_wiki/**                                   # no application code changes (pure CI/repo-config work)
Dockerfile, docker-entrypoint.sh              # already production-shaped, untouched
deploy/docker-compose.prod.yml, Caddyfile     # Stage-1 artifacts, untouched
```

---

## Key Decisions

> The 14 decisions locked in this grilling session. ADR-0018 (PR-A) will own the formal rationale; this section is the quick-reference. Each links to the grilling round that resolved it.

### Branching & merge policy
1. **main-only branching** — honor ADR-0017 §4 ("no staging env in Stage-1", "manual-gate-on-main"). No `develop`/`staging` branch; `main` is the only long-lived branch. The existing `workflow_dispatch`-only `deploy` job IS the staging gate (merges don't auto-deploy). A staging branch would contradict the accepted ADR — deferred to Stage-2 as additive overlay.
2. **Require PR, 0 approvals** — solo dev on a personal account cannot self-approve. PR is purely a CI vehicle: runs the 6 gate jobs against the merge commit; self-merge once green. "Required approvals > 0" is impossible without a 2nd reviewer/bot.
3. **All 6 gate jobs required + require up-to-date** — `Lint`, `Typecheck (mypy)`, `Run unit tests`, `Check migrations`, `Build image`, `Scan image (Trivy)` are required status checks (matches `docs/ci-context.md` §8). "Require branches up to date before merge" forces rebase so CI ran on the real merge commit. `push-images` and `deploy` are NOT required (they only run on `main` push / `workflow_dispatch`, never on PRs).
4. **Squash-only + require linear history** — repo keeps ONLY "squash merge" enabled; disable "merge commit" and "rebase merge". `main` enforces linear history (blocks merge commits). Each PR = one semantic commit. Matches existing log style (`#92`..`#96` are already squash-style). Trivially revertable.
5. **Block force-push + deletion on `main`** — force-push would orphan every `ghcr.io/.../rag-wiki:sha-<short>` image tag and break the documented rollback path (`edit IMAGE_TAG` + `compose pull`). Deletion breaks the `push` trigger.
6. **No admin bypass** — turn ON "Do not allow bypassing the above settings". The repo owner is bound to the same rules (PR-required, status checks, up-to-date, linear, no force-push). Strongest portfolio "I follow my own rules" signal. Deploy stays a separate `workflow_dispatch` regardless, so bypassing merge wouldn't skip the deploy gate anyway. **Recovery**: deleting a ruleset is an owner-level admin action NOT covered by the bypass setting — escape hatch always exists; document in ADR-0018.

### CI/CD pipeline
7. **Add `environment: production` to the `deploy` job** — currently `ci.yml:329-348` has no `environment:` key. Add it; create a `production` GitHub Environment restricted to `main` branch, no required reviewers (solo). Move 4 deploy secrets (`DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_USER`/`DEPLOY_PATH`) from repo-level to environment-scoped. Benefits: Deployments tab on the public repo (portfolio artifact), tighter secret scoping, "deploy only from main" branch gate.
8. **Repository Ruleset + in-repo codification** — use GitHub's current Rulesets mechanism (classic branch protection is in maintenance, no new features). Codify as `scripts/apply-branch-protection.sh` (idempotent `gh api` PUT to `/repos/{owner}/{repo}/rulesets`) + `.github/branch-ruleset.json` (human-readable ruleset definition). Script is re-runnable after any change. Portfolio reviewer can see the exact config in-repo without navigating to Settings. Survives repo transfer/recreation.
11. **Tag-triggered release workflow** — add `.github/workflows/release.yml` (separate from `ci.yml` per ci-context.md §7 "split by concern"). Trigger: `push: tags v*.*.*`. Builds+scans+pushes `:v*.*.*` image to GHCR (in addition to existing `:latest`+`:sha-<short>`). Creates GitHub Release with auto-generated notes from PR titles since last tag. Establishes semver discipline; Releases page is a portfolio artifact; `:v*.*.*` tags give true release-version rollback. Rollback becomes: edit `.env` `IMAGE_TAG=v0.1.0` on VM + `compose pull && up -d`. Implements ci-context.md §2's intended-but-unimplemented tag trigger.
14 (minor). **Repo workflow-perms default = read; conversation-resolution = off; protect `v*.*.*` tags** — (a) repo-level "Workflow permissions" default = read (ci.yml already uses `permissions: {}` workflow-level + per-job elevation; this is the safe fallback). (b) "Require conversation resolution before merge" = off (solo dev, 0 approvals — rarely open conversations; forcing would block self-merge on self-noted PRs). (c) Second ruleset protects `v*.*.*` tags from force-push/deletion (since tag releases are now a thing — rewriting a tag would orphan the `:v*.*.*` image and the GitHub Release).

### Security & hygiene
9. **Prune stale branches + enable auto-delete** — ~30+ stale `feat/*`/`fix/*`/`refactor/*`/`chore/*` branches on remote are pre-Stage-1 leftovers (verified: none in recent `git log`). Delete each via `git push origin --delete <branch>`. Enable repo setting "Automatically delete head branches after merge" for future PRs.
10. **Dependabot + push protection + CodeQL** — all free for public repos. (a) `.github/dependabot.yml`: ecosystems `pip` (watches `pyproject.toml`), `github-actions` (watches SHA-pinned actions in `ci.yml`), `docker` (watches `Dockerfile` base image `python:3.12-slim`); weekly schedule. (b) Verify/enable Push Protection (free for public; secret scanning already on per `gh api` check — push protection is its active-block sibling). (c) `.github/workflows/codeql.yml`: CodeQL Python analysis on PR (paths-filtered to `rag_wiki/**`) + weekly schedule. Catches injected vulns (SQLI, hardcoded creds) that Dependabot/Trivy don't.
13. **Defer signed commits to Stage-2** — do NOT require commit signature verification on `main` now. Setup burden (GPG/sigstore key per machine) exceeds marginal solo-dev value; push protection + secret scanning + 2FA cover the realistic threat model. Noted as Stage-2 enhancement in ADR-0018.

### Documentation
12. **New ADR-0018 + ci-context.md cross-refs** — write `docs/adr/0018-branch-protection-release-security-policy.md` recording the why for the cluster of decisions above. Meets all three ADR criteria: hard-to-reverse (commitment posture), surprising-without-context (future reader sees `environment: production` + no-bypass on a solo repo and wonders why), real trade-offs (solo convenience vs discipline; classic vs ruleset; defer vs now). Update `docs/ci-context.md` §8 (point to ADR-0018 for the policy) and §10 (remove items now decided). Update `AGENTS.md` ADR index table with ADR-0018 row.

---

## Gotchas & Constraints

- **No-bypass recovery** — if the ruleset locks you out, deleting a ruleset is an owner-level admin action NOT covered by the "no bypass" setting. You can always `gh api -X DELETE /repos/{owner}/{repo}/rulesets/{id}` or use the UI to recover. Document this escape hatch in ADR-0018 §Consequences so the no-bypass posture isn't scary.
- **`gh auth` scope for ruleset writes** — applying a ruleset via `gh api` requires the `admin:repo_hook` scope (verified: the `dependabot/alerts` and `code-scanning/alerts` calls returned 403 with "needs admin:repo_hook scope"). Run `gh auth refresh -h github.com -s admin:repo_hook` before R1/R3/R4. (Note: `admin:repo_hook` is broader than strictly needed; the `repo` scope may suffice for rulesets — verify at implementation time, request the minimal scope that works.)
- **Dependabot on `uv.lock`** — Dependabot's `pip` ecosystem watches `pyproject.toml` + `requirements.txt`; native `uv.lock` support may require the newer `uv` package-ecosystem (still rolling out in Dependabot). If `uv` ecosystem isn't available, fall back to `pip` watching `pyproject.toml` only and note the gap in `docs/ci-context.md` §10. Verify at PR-E implementation.
- **Push protection status unclear** — the `gh api repos/.../` call returned `secret_scanning.status = "enabled"` but the `push_protection.status` field came back blank (not "enabled"/"disabled"). Verify push protection is actually ON in repo Settings → Code security when applying R4. Secret scanning being on is necessary but not sufficient.
- **CodeQL adds ~5-10min per PR** — the `codeql.yml` workflow MUST use a `paths:` filter (only trigger on `rag_wiki/**`, `tests/**`, `pyproject.toml`, `uv.lock` changes) so doc-only PRs don't pay the cost. Run the full analysis on PR + a weekly `schedule` for off-cycle catch-up.
- **Release workflow DRY sub-decision** — `release.yml` duplicates build+scan+push logic from `ci.yml`'s `push-images` job. Two options at PR-D implementation: (a) extract a reusable workflow (`.github/workflows/_build-scan-push.yml` with `workflow_call`) and call it from both — cleaner, 3rd file; (b) duplicate the steps — simpler, drift risk. Recommend (a) reusable workflow since the tag-release path MUST stay identical to the main-push path or releases will diverge from `:latest`. Flag as the first implementation sub-decision in PR-D.
- **`AGENTS.md` ADR index table** — add an ADR-0018 row: `0018 | Deployment/CI | Branch protection, release & security policy for public portfolio repo`. The "Subsystem" column value is debatable (Deployment? CI? Security?) — pick "CI/Security" or split; match the table's existing style.
- **Stale branch list to confirm before R5** — do NOT blindly delete all non-`main` remote branches. Some may be active PR heads (e.g. `remotes/origin/pr-3/backup-script`, `pr-4/ci-push-deploy`, `pr-5/deploy-readme` are PR-1..5 head branches from the Stage-1 work — but those PRs are merged per the handoff, so they're safe to delete; still, list them in the PR/terminal and confirm before deleting). Use `gh pr list --state open` to cross-check no open PR references a branch before deleting it.
- **Ruleset "bypass actors" list must be empty** — when constructing `.github/branch-ruleset.json`, the `bypass_actors`/`bypass_teams` arrays must be omitted or empty to achieve "no bypass" (decision #6). A non-empty list would grant someone bypass — contradicting the decision.
- **Required status checks must use exact job `name:` strings** — GitHub matches required checks by the job's display `name:` field, not the job `id`. From `ci.yml`: `Lint`, `Typecheck (mypy)`, `Run unit tests`, `Check migrations (drift + upgrade + downgrade)`, `Build image`, `Scan image (Trivy)`. Use these exact strings in the ruleset's `required_status_checks` list, or the match silently fails and the gate is ineffective.
- **`environment: production` + `workflow_dispatch`** — adding `environment:` to the `deploy` job means the manual dispatch will pause at the environment gate. With no required reviewers (solo), it auto-resolves, but if a reviewer is accidentally added the deploy will hang. Keep `required_reviewers` empty in the environment config.

---

## Critical Snippets

### `ci.yml` — deploy job to modify in PR-C (lines 329-348)
```yaml
  deploy:
    name: Deploy to VM
    needs: push-images
    if: github.event_name == 'workflow_dispatch'
+   environment: production   # <-- PR-C adds this line
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - name: SSH to VM and roll the stack
        uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2 # v1.2.5
        with:
          host: ${{ secrets.DEPLOY_HOST }}      # <-- move to environment-scoped
          username: ${{ secrets.DEPLOY_USER }}   # <-- move to environment-scoped
          key: ${{ secrets.DEPLOY_SSH_KEY }}     # <-- move to environment-scoped
          script: |
            cd ${{ secrets.DEPLOY_PATH }}        # <-- move to environment-scoped
            docker compose -f deploy/docker-compose.prod.yml pull
            docker compose -f deploy/docker-compose.prod.yml up -d --remove-orphans
```

### `.github/branch-ruleset.json` — sketch for PR-B (main ruleset)
```json
{
  "name": "main-branch-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "bypass_actors": [],
  "rules": [
    { "type": "pull_request", "parameters": { "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false, "require_code_owner_review": false, "require_last_push_approval": false, "required_review_thread_resolution": false } },
    { "type": "required_status_checks", "parameters": { "strict_required_status_checks": true, "do_not_enforce_required_status_checks_on_create": false, "required_status_checks": [
      { "context": "Lint" }, { "context": "Typecheck (mypy)" }, { "context": "Run unit tests" },
      { "context": "Check migrations (drift + upgrade + downgrade)" },
      { "context": "Build image" }, { "context": "Scan image (Trivy)" }
    ] } },
    { "type": "non_fast_forward" },
    { "type": "deletion" },
    { "type": "update", "parameters": { "allow_force_pushes": false } }
  ]
}
```
Notes: `~DEFAULT_BRANCH` targets `main` regardless of rename. `strict_required_status_checks: true` = "require up to date before merge" (decision #3). `non_fast_forward` = linear history (decision #4). `deletion` + `update allow_force_pushes:false` = decision #5. Empty `bypass_actors` = decision #6. **Verify field names against the live GitHub Rulesets API schema at PR-B implementation time** — the API shape has evolved; the `gh api` call in the script should validate.

### Stale remote branches to prune in R5 (verify with `gh pr list --state open` first)
```
chore/ci, chore/credentials, chore/db, chore/docker-compose, chore/infra,
docs/chore, docs/chr,
feat/api-endpoint, feat/cd-deploy, feat/cli-export, feat/deploy-delivery,
feat/e2e-integration, feat/enity-relation-extraction-2, feat/entity-relation-extraction,
feat/graph-wiki-api, feat/health-check-metric, feat/healthcheck-metric,
feat/hybrid-retrieval, feat/hybrid-retrieval-2, feat/hybrid-retrieval-test,
feat/ingest-pipeline, feat/ingest-planner, feat/integration-quality-docs,
feat/lightweight-parser, feat/llm-provider, feat/mcp-server, feat/mcp-server-2,
feat/mcp-server-3, feat/mcp-server-4, feat/orchestrator-api, feat/query-planner,
feat/s3-storage, feat/s3-storage-provider, feat/schema, feat/schema-changes-planner,
feat/smoke-test, feat/source-job-api, feat/system-instructions, feat/wiki-page-syn-test,
feat/wiki-syn-core-lib, feat/wiki-syn-integration, feat/wiki-synthesize,
feat/wire-storage-provider,
fix/chat-provider, fix/ci-checks, fix/ci-error, fix/cli-export, fix/docker,
fix/embedding-dimension, fix/entity-relation-extraction, fix/error-handling,
fix/failing-test, fix/health-report, fix/hot-1, fix/hotfix, fix/hotfix-2,
fix/hybrid-retrieval-test, fix/ingest-query-planner, fix/ingestion-pipeline,
fix/ingestion-provider, fix/lightweight-parser, fix/llm-provider, fix/mcp-server,
fix/n1-query, fix/rename-file-path-to-storage-key, fix/schema, fix/storage-provider,
fix/system-instructions, fix/test-deps, fix/try-except, fix/venv-uv,
fix/warning-actions, fix/wiki-page-syn,
pr-3/backup-script, pr-4/ci-push-deploy, pr-5/deploy-readme,
refactor/improve-ci, refactor/rename-file-path-to-storage-key,
refactor/retrieval-comparison-query, refactor/structure
```
All are pre-Stage-1 leftovers or merged Stage-1 PR heads (PR-1..5 → #92..#96 all merged per `HANDOFF-stage-1-deployment.md`). Confirm no open PR references any of these before deleting.

---

## Artifacts

| Artifact | Path | Notes |
|----------|------|-------|
| ADR-0017 | `docs/adr/0017-stage-1-deployment-topology.md` | Accepted — owns topology/trust/CI/secrets/MCP/ops rationale; ADR-0018 extends §4, does not supersede |
| ADR-0018 | `docs/adr/0018-branch-protection-release-security-policy.md` | **Accepted (#97)** — owns the why for all 14 branch-protection/release/security decisions |
| ci-context.md | `docs/ci-context.md` | §2 (tag trigger — to be implemented by PR-D), §8 (branch-protection policy now cross-referenced to ADR-0018 — to be codified by PR-B), §10 (Dependabot/CodeQL now logged as "Implemented per ADR-0018" pending PR-E landing the actual files) |
| ci.yml | `.github/workflows/ci.yml` | PR-C modifies the `deploy` job (lines 329-348); PR-D adds `release.yml` alongside; PR-E adds `codeql.yml` alongside |
| Stage-1 handoff | `HANDOFF-stage-1-deployment.md` | Prior session's closeout — PRs #92-#96 merged, quality gate green (364 passed/7 skipped). This session builds on that completed baseline. |
| This handoff | `HANDOFF-branch-protection-release-policy.md` | Plan source of truth — 14 locked decisions + PR task breakdown with statuses |

---

## Next Session Checklist

> Tasks the next agent should do, in order. Update statuses in the Implementation Plan Status table above as each completes.

- [x] **PR-A**: Write `docs/adr/0018-branch-protection-release-security-policy.md` (Status/Context/Decision/Consequences/Rationale sections; record all 14 decisions; include the no-bypass recovery escape hatch in Consequences) — merged as #97
- [x] **PR-A**: Update `docs/ci-context.md` §8 (point to ADR-0018 for branch-protection policy) and §10 (remove items now decided by ADR-0018; note Dependabot/CodeQL as implemented) — merged as #97
- [x] **PR-A**: Update `AGENTS.md` ADR index table — add ADR-0018 row — merged as #97
- [x] **PR-A**: Run `uv run ruff check && ruff format && mypy && pytest` (docs-only change; should be green) — open PR, merge → #97 merged (ruff ✅; mypy/pytest skipped: the repo's `.venv` is root-owned and `scripts/fix-venv.sh`'s `sudo` step can't run non-interactively in the agent env — docs-only change so results match `main`. See "Gotchas discovered during PR-A" below.)
- [ ] **PR-B** (PR-A merged — unblocked): Create `scripts/apply-branch-protection.sh` (idempotent `gh api` PUT/POST to `/rulesets`; accept ruleset ID via env or create-new); create `.github/branch-ruleset.json` (use the sketch above, verify field names against live API)
- [ ] **PR-B**: Add doc note in `scripts/README.md` (create if missing) or `deploy/README.md` describing the script + `gh auth refresh -h github.com -s admin:repo_hook` prereq
- [ ] **PR-B**: Quality gate + open PR, merge
- [ ] **PR-C** (parallel with PR-B): `.github/workflows/ci.yml` — add `environment: production` to `deploy` job; update `docs/ci-context.md` §5 (env-scoped secrets)
- [ ] **PR-C**: Quality gate + open PR, merge
- [ ] **PR-D** (parallel): Create `.github/workflows/release.yml` (`v*.*.*` trigger; reuse build+scan+push via `_build-scan-push.yml` reusable workflow — sub-decision #1 in PR-D); update `docs/ci-context.md` §2 cross-ref
- [ ] **PR-D**: Quality gate + open PR, merge
- [ ] **PR-E** (parallel): Create `.github/dependabot.yml` (pip + github-actions + docker, weekly; verify `uv.lock` ecosystem support); create `.github/workflows/codeql.yml` (Python, paths-filtered to `rag_wiki/**`, PR + weekly schedule); update `docs/ci-context.md` §10
- [ ] **PR-E**: Quality gate + open PR, merge
- [ ] **PR-F** (after PR-B): Extend `scripts/apply-branch-protection.sh` — add tag-protection ruleset (`v*.*.*` no force/delete) + repo toggles (auto-delete head branches, workflow-perms=read, push-protection verify)
- [ ] **PR-F**: Quality gate + open PR, merge
- [ ] **R1**: Run `gh auth refresh -h github.com -s admin:repo_hook`; run `scripts/apply-branch-protection.sh` to apply the main ruleset
- [ ] **R2**: Create `production` GitHub Environment (restricted to `main`, no required reviewers); move 4 deploy secrets to environment-scoped
- [ ] **R3**: Re-run the extended script to apply the tag-protection ruleset
- [ ] **R4**: Verify/set repo toggles (auto-delete head branches=on, workflow-perms=read, push protection=on)
- [ ] **R5**: List stale branches, cross-check `gh pr list --state open`, delete each via `git push origin --delete <branch>`
- [ ] **Verify**: Open a test PR → confirm all 6 checks gate it; self-merge once green; head branch auto-deletes
- [ ] **Verify**: Try `git push` directly to `main` → confirm REJECTED (PR required)
- [ ] **Verify**: Trigger `workflow_dispatch` deploy → confirm it resolves the `production` environment and secrets
- [ ] **Verify**: Cut tag `v0.1.0` → confirm `release.yml` runs, GHCR gets `:v0.1.0`, Releases page shows the release
- [ ] **Verify**: Try `git push --force` to `main` and `git push origin :v0.1.0` (delete tag) → confirm both rejected
- [ ] Run `handoff` skill again at end of next session to update PR statuses and capture gotchas discovered while writing code

### Gotchas discovered during PR-A (new — not in the original grilling session)

- **`.venv` root-owned blocks `uv run`** — AGENTS.md documents this (`scripts/fix-venv.sh`) but `fix-venv.sh`'s `sudo chown` can't run non-interactively in the agent shell (no tty / no askpass). Workaround used to verify ruff: `uv venv --python /usr/bin/python3 /tmp/rag-venv && uv pip install --python /tmp/rag-venv ruff mypy && /tmp/rag-venv/bin/ruff …`. mypy/pytest skipped because the change is docs-only and would match `main` regardless. **For PR-B onward (workflow + script files, still no `.py` diffs)** the same workaround suffices. **If a future PR touches `.py` files**, reclaim `.venv` first interactively (`./scripts/fix-venv.sh` in a real tty), or install dev deps into the temp venv and run `mypy`/`pytest` from there — `uv pip install --python /tmp/rag-venv -e ".[dev]"` then `/tmp/rag-venv/bin/{mypy,pytest}`.
- **ADR section ordering:** `handoff` doc referenced "Status/Context/Decision/Consequences/Rationale sections"; the actual ADR-0017 format is **Status / Context / Decision / Rationale / Consequences** (Rationale before Consequences). ADR-0018 was written to match ADR-0017's ordering (Rationale then Consequences), so the section order derived from 0017 wins over the handoff's textual mention — no functional difference, just a heads-up for whoever audits the PR.
- **`docs/ci-context.md` had no trailing newline** (the §10 edit surfaced `No newline at end of file`). The edit preserved that shape (still no trailing newline after the rewritten §10 block). Ruff's `format --check` was happy with it; leave as-is unless a future PR reformats the whole file.

---

## Suggested Skills

- `grill-with-docs` — NOT needed next session; all 14 decisions are locked and recorded here + in ADR-0018 (#97 merged). Skip unless a new ambiguity surfaces during implementation.
- `handoff` — run again at the end of the next implementation session to update PR statuses (PR-B through PR-F) and capture any gotchas discovered while writing the ruleset JSON / release workflow / CodeQL config.
- (No `adr` skill available in this repo's skill list — ADR-0018 was written by hand following the format of `docs/adr/0017-*.md`.)

---

_Handoff generated by `handoff` skill, customized for repo-root persistence per user request (matches `HANDOFF-stage-1-deployment.md` convention). Do not edit the decision rationale sections — update statuses in the Implementation Plan Status table and the Next Session Checklist only. ADR-0018 (once written by PR-A) owns the formal rationale for all 14 decisions._
