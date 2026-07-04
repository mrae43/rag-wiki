# ADR-0018: Branch protection, release, and security policy for the public portfolio repo

## Status

Accepted

## Context

`rag-wiki` is a personal portfolio repo (`mrae43/rag-wiki`) that ships a
self-hosted LLM-maintained knowledge wiki over Postgres + a knowledge graph.
ADR-0017 closed the Stage-1 deployment contract — Compose-on-VM, trusted
clients only, manual-gated CI/CD, Tailscale-internal TLS — but stopped short of
codifying the rules that govern the `main` branch itself, the release pipeline,
and the security/hygiene posture on a public repository. With PRs #92–#96
(Stage-1 deploy) merged and the quality gate green, the repo now visibly runs a
real deploy pipeline (build → scan → push → manual deploy) from `main`, so the
absence of branch protection and release discipline has become the most
conspicuous "is this actually best practice?" gap for a public reviewer.

This ADR records a cluster of 14 decisions that **extend** ADR-0017 §4
(manual-gate-on-`main`) — they do not supersede it. ADR-0017's "no staging env
in Stage-1" is reaffirmed, not revisited. Every decision here meets the three
ADR criteria:

1. **Hard-to-reverse** — a branch-protection ruleset with no admin bypass is a
   public, declarable commitment posture; flipping it off in the future is a
   visible, auditable admin action.
2. **Surprising-without-context** — a future reader sees `environment:
   production` plus "no bypass" plus tag-triggered releases on a *solo* repo and
   reasonably asks why a one-person project adopts the discipline of a team.
3. **Real trade-offs** — solo convenience vs. portfolio discipline; classic
   branch protection vs. repository Rulesets; defer-some-security-now vs.
   ship-it-now.

The decisions span four groups: **branching & merge policy** (#1–#6),
**CI/CD pipeline** (#7–#8, #11, #14), **security & hygiene** (#9–#10, #13), and
**documentation** (#12, this ADR). They are implemented across PR-B through
PR-F and applied via repo-side actions R1–R5 (all completed).

## Decision

Adopt a **main-only, ruleset-enforced, no-bypass** branching policy with a
separate tag-triggered release pipeline, a `production` GitHub Environment
guarding deploys, and free-tier security tooling (Dependabot, CodeQL, push
protection) on the public repo. Branch protection is codified in-repo as an
idempotent `gh api` script + a human-readable ruleset JSON, applied via the
GitHub **Rulesets** mechanism (not classic branch protection, which is in
maintenance and receives no new features).

### Branching & merge policy

1. **main-only branching.** Honor ADR-0017 §4 ("no staging env in Stage-1",
   "manual-gate-on-`main`"). `main` is the only long-lived branch — no
   `develop`, no `staging`. The existing `workflow_dispatch`-only `deploy` job
   *is* the staging gate (merges don't auto-deploy). A staging branch would
   contradict the accepted ADR; any staging-env work is deferred to Stage-2 as
   an additive overlay.

2. **Require PR, 0 approvals.** Solo dev on a personal account cannot
   self-approve; "required approvals > 0" is impossible without a second
   reviewer or a rubber-stamp bot. The PR is therefore purely a CI vehicle: it
   runs the six gate jobs against the merge commit, and the author self-merges
   once green.

3. **All six gate jobs required + require up-to-date.** `Lint`, `Typecheck
   (mypy)`, `Run unit tests`, `Check migrations (drift + upgrade + downgrade)`,
   `Build image`, `Scan image (Trivy)` are required status checks (matches
   `docs/ci-context.md` §8). "Require branches up to date before merge" forces a
   rebase so CI ran on the real merge commit (no stale-approval-on-old-base
   drift). `push-images` and `deploy` are **not** required — they only run on
   `main` push / `workflow_dispatch`, never on PRs.

4. **Squash-only + require linear history.** Only "squash merge" is enabled in
   repo settings; "merge commit" and "rebase merge" are disabled. `main`
   enforces linear history (blocks merge commits). Each PR lands as exactly one
   semantic commit, matching the existing log style (`#92`–`#96` are already
   squash-style). Trivially revertable (`git revert <sha>`).

5. **Block force-push and deletion on `main`.** Force-push would orphan every
   `ghcr.io/.../rag-wiki:sha-<short>` image tag and break the documented
   rollback path (`edit IMAGE_TAG` + `compose pull`). Branch deletion breaks the
   `push` trigger. Both are ruleset-enforced.

6. **No admin bypass.** Turn ON "Do not allow bypassing the above settings."
   The repo owner is bound to the same rules as anyone else: PR-required,
   required status checks, up-to-date, linear history, no force-push. This is the
   strongest portfolio "I follow my own rules" signal. Deploy stays a separate
   `workflow_dispatch` regardless, so bypassing merge wouldn't skip the deploy
   gate anyway. **Recovery / escape hatch:** deleting a ruleset is an
   owner-level admin action *not covered by the no-bypass setting* — it remains
   available via `gh api -X DELETE /repos/{owner}/{repo}/rulesets/{id}` or the
   repo Settings UI. This is documented here so the no-bypass posture is a
   discipline choice, not a lock-in trap.

### CI/CD pipeline

7. **Add `environment: production` to the `deploy` job.** Currently
   `.github/workflows/ci.yml` has no `environment:` key on the `deploy` job.
   Add it; create a `production` GitHub Environment restricted to the `main`
   branch with **no required reviewers** (solo). Move the four deploy secrets
   (`DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`) from
   repo-level to environment-scoped. Benefits: a Deployments tab on the public
   repo (portfolio artifact), tighter secret scoping, and an explicit "deploy
   only from `main`" branch gate.

8. **Repository Ruleset + in-repo codification.** Use GitHub's **Rulesets**
   mechanism (classic branch protection is in maintenance and receives no new
   features). Codify the ruleset in-repo as `scripts/apply-branch-protection.sh`
   (an idempotent `gh api` PUT/POST to `/repos/{owner}/{repo}/rulesets`) plus
   `.github/branch-ruleset.json` (a human-readable ruleset definition). The
   script is re-runnable after any change, so a portfolio reviewer can see the
   exact enforced config in-repo without navigating to Settings, and the config
   survives a repo transfer/recreation. `bypass_actors`/`bypass_teams` are
   omitted (empty) to realize decision #6. Required status checks use the exact
   job display `name:` strings (`Lint`, `Typecheck (mypy)`, …) — GitHub matches
   required checks by display name, not job `id`.

11. **Tag-triggered release workflow.** Add `.github/workflows/release.yml`
    (separate from `ci.yml` per `docs/ci-context.md` §7 "split by concern").
    Trigger: `push` of tags matching `v*.*.*`. Builds + scans + pushes a
    `:v*.*.*` image to GHCR (in addition to the existing `:latest` +
    `:sha-<short>` from the main-push path), and creates a GitHub Release with
    auto-generated notes (PR titles since the last tag). This establishes
    semver discipline, makes the Releases page a portfolio artifact, and gives a
    true release-version rollback path: edit `IMAGE_TAG=v0.1.0` in `.env` on the
    VM + `compose pull && up -d`. The build+scan+push steps are shared with
    `ci.yml`'s `push-images` job via a reusable workflow
    (`_build-scan-push.yml`, `workflow_call`) so the tag-release path can never
    drift from the main-push path. This realizes ci-context.md §2's
    intended-but-unimplemented tag trigger.

14. **Repo workflow-perms default = read; conversation-resolution = off;
    protect `v*.*.*` tags.** (a) Repo-level "Workflow permissions" default =
    read (`ci.yml` already uses `permissions: {}` workflow-level + per-job
    elevation; this is the safe repo-wide fallback). (b) "Require conversation
    resolution before merge" = off (solo dev, 0 approvals — conversations are
    rare; forcing resolution would block self-merge on self-noted PRs). (c) A
    second ruleset protects `v*.*.*` tags from force-push/deletion — since tag
    releases are now a thing, rewriting or deleting a tag would orphan the
    `:v*.*.*` image and the GitHub Release.

### Security & hygiene

9. **Prune stale branches + enable auto-delete.** ~30+ stale
   `feat/*`/`fix/*`/`refactor/*`/`chore/*` branches on remote are pre-Stage-1
   leftovers (verified: none appear in recent `git log`). Delete each via
   `git push origin --delete <branch>`, cross-checking `gh pr list --state open`
   first to avoid clobbering an open PR head. Enable repo setting "Automatically
   delete head branches after merge" for future PRs.

10. **Dependabot + push protection + CodeQL.** All free for public repos. (a)
    `.github/dependabot.yml`: ecosystems `pip` (watches `pyproject.toml`),
    `github-actions` (watches SHA-pinned actions in `ci.yml`), and `docker`
    (watches `Dockerfile`'s `python:3.12-slim` base image); weekly schedule.
    Native `uv.lock` support is monitored — if Dependabot's `uv` ecosystem is
    not yet available, fall back to `pip` watching `pyproject.toml` and note the
    gap in ci-context.md §10. (b) Verify/enable Push Protection (free for public;
    secret scanning is already on per the pre-decision `gh api` check; push
    protection is its active-block sibling — its status field was blank in the
    probe, so it's verified in the Settings UI at apply time). (c)
    `.github/workflows/codeql.yml`: CodeQL Python analysis on PR (paths-filtered
    to `rag_wiki/**`, `tests/**`, `pyproject.toml`, `uv.lock` so doc-only PRs
    don't pay the ~5–10 min cost) plus a weekly `schedule` for off-cycle
    catch-up. Catches injected vulnerabilities (SQLI, hardcoded creds) that
    Dependabot/Trivy don't.

13. **Defer signed commits to Stage-2.** Do **not** require commit signature
    verification on `main` now. The setup burden (a GPG/sigstore key per
    machine) exceeds the marginal solo-dev value; push protection + secret
    scanning + 2FA cover the realistic threat model for a public portfolio
    repo. Noted as a Stage-2 enhancement.

### Documentation

12. **New ADR-0018 + ci-context.md cross-refs.** This ADR. Updates
    `docs/ci-context.md` §8 (point to ADR-0018 as the branch-protection policy
    owner) and §10 (remove items now decided by ADR-0018; note Dependabot/CodeQL
    as implemented rather than deferred). Updates the `AGENTS.md` ADR index
    table with this row.

## Rationale

- **Solo dev but portfolio-grade.** The repo's value is partly as a portfolio
  artifact demonstrating real engineering discipline. Self-binding rules with
  no bypass, squash-only linear history, required status checks, tag-triggered
  releases, and visible CI hygiene collectively communicate "I follow my own
  rules even when no one is looking" more credibly than any prose in a README —
  while costing the solo author almost nothing (a self-merge after green CI is a
  single click).
- **Rulesets over classic branch protection.** Classic branch protection is in
  maintenance and receives no new features; Rulesets are GitHub's current
  first-class mechanism, support the same constraints (required checks,
  up-to-date, linear, no force-push, no bypass) plus tag protection. Codifying
  as in-repo JSON + an idempotent `gh api` script means the config is reviewable
  in a PR, survives repo transfer/recreation, and a reviewer doesn't have to
  trust a screenshot of Settings.
- **`environment: production` is cheap and visible.** A GitHub Environment adds
  a Deployments tab (portfolio artifact), scopes the four deploy secrets to the
  one job that needs them, and adds an implicit "deploy only from `main`"
  branch restriction — all for the cost of one YAML key. Solo + no required
  reviewers means it never blocks the author, so it's pure upside.
- **Tag-triggered release realizes ci-context.md §2.** §2 documents the
  tag-protection trigger as intended, but it was never implemented (only
  `:latest` + `:sha-<short>` exist today). A `v*.*.*` tag pipeline yields a
  GitHub Releases page, a true release-version rollback path, and the semver
  discipline a portfolio project is expected to show. A reusable workflow keeps
  the tag path byte-identical to the main push path.
- **Free-tier security tooling is high-leverage.** Dependabot, CodeQL, and push
  protection are free for public repos and cover complementary vectors:
  Dependabot (known vulns in deps, including actions and base images), CodeQL
  (injected vulns in app code — SQLI, hardcoded creds), push protection
  (accidentally-committed live secrets actively blocked, not just flagged).
  Skipping them is pure leaving-value-on-the-floor.
- **Deferring signed commits is a deliberate Stage-2 call.** The marginal
  threat-model value of commit signatures for a solo public portfolio repo is
  small next to push protection + secret scanning + 2FA; the setup burden per
  machine is real. Recording the deferral (and that it's additive — signature
  verification can be added to the ruleset in Stage-2 with no rewrite) stops a
  future reader from assuming it was forgotten.
- **No-bypass is a posture, not a lock-in.** The escape hatch (delete the
  ruleset via `gh api` or the UI) is documented here precisely so the no-bypass
  setting reads as a discipline choice rather than a trap. Deploy remains a
  separate `workflow_dispatch` either way, so merging without a bypass doesn't
  gate deployment — recovery is always one documented admin command away.

## Consequences

- New files (PR-A): this ADR; updated `docs/ci-context.md` §8/§10; updated
  `AGENTS.md` ADR index row.
- New files (PR-B): `scripts/apply-branch-protection.sh`,
  `.github/branch-ruleset.json`, and a doc note in `scripts/README.md` (or
  `deploy/README.md`). The script requires `gh auth refresh -h github.com -s
  admin:repo_hook` before running (ruleset write scope; the minimal scope that
  works should be verified at apply time).
- New files (PR-C): `.github/workflows/ci.yml` gains `environment: production`
  on the `deploy` job; the four deploy secrets move from repo-scoped to
  environment-scoped. ci-context.md §5 gains a note.
- New files (PR-D): `.github/workflows/release.yml` and a reusable
  `.github/workflows/_build-scan-push.yml`; ci-context.md §2 cross-ref updated.
- New files (PR-E): `.github/dependabot.yml`, `.github/workflows/codeql.yml`;
  ci-context.md §10 updated.
- PR-F extends `scripts/apply-branch-protection.sh` with the tag-protection
  ruleset and repo toggles (auto-delete head branches, workflow-perms=read,
  push-protection verify).
- Repo-side actions (manual / `gh api`, not in a PR): R1 apply the main ruleset;
  R2 create the `production` Environment and move secrets; R3 apply the tag
  ruleset; R4 set repo toggles; R5 prune ~30+ stale branches. See the open
  handoff doc for the live task table.
- No changes to: ADR-0017 (extended, not superseded — its §4 manual-gate-on-`main`
  and "no staging env in Stage-1" are reaffirmed), ADRs 0001–0016, any
  `rag_wiki/**` application code, the `Dockerfile`/`docker-entrypoint.sh` (already
  production-shaped), or `deploy/docker-compose.prod.yml`/`Caddyfile` (Stage-1
  artifacts). Every Stage-2 enhancement (Helm chart, shared API key, auto-deploy,
  SeaweedFS, MCP HTTP service, managed Postgres, observability stack, signed
  commits, staging env) remains additive to these artifacts — no Stage-1 artifact
  is rewritten.
- If a second maintainer ever joins, decisions #2 (0 approvals) and #13
  (deferred signatures) are the obvious first revisits — both add a config knob,
  neither requires restructuring this ADR.