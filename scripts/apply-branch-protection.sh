#!/usr/bin/env bash
# =============================================================================
# Apply branch-protection + tag-protection rulesets and repo toggles to a
# GitHub repository via the Rulesets API.  ADR-0018 / PR-B + PR-F.
# =============================================================================
#
# Reads `.github/branch-ruleset.json` (main branch ruleset) and
# `.github/tag-ruleset.json` (tag-protection ruleset) and applies each to the
# repository identified by GITHUB_OWNER / GITHUB_REPO (auto-detected from
# `git remote get-url origin` if not set).
#
# Then applies repo-level toggles:
#   - auto-delete head branches after merge
#   - default workflow permissions = read
#   - verify push-protection status
#
# Idempotent: for each ruleset, finds an existing ruleset by name (from the
# JSON's "name" field) and updates it via PUT; creates via POST if none exists.
#
# Prerequisites:
#   gh auth refresh -h github.com -s admin:repo_hook
#
# Usage:
#   ./scripts/apply-branch-protection.sh
#
# Env overrides:
#   GITHUB_OWNER    repo owner (default: auto-detect from git remote)
#   GITHUB_REPO     repo name  (default: auto-detect from git remote)
#   RULESET_ID      explicit ruleset ID to update (skip name-based lookup)
#   SKIP_TAG        set to "1" to skip the tag-protection ruleset
#   SKIP_TOGGLES    set to "1" to skip repo-level toggles
set -euo pipefail

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  echo "gh (GitHub CLI) is not installed or not on PATH." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for JSON processing." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------
if ! gh auth status -h github.com >/dev/null 2>&1; then
  echo "Not authenticated with gh. Run: gh auth login" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Determine owner / repo
# ---------------------------------------------------------------------------
GITHUB_OWNER="${GITHUB_OWNER:-}"
GITHUB_REPO="${GITHUB_REPO:-}"

if [ -z "$GITHUB_OWNER" ] || [ -z "$GITHUB_REPO" ]; then
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  if [ -z "$remote_url" ]; then
    echo "Could not detect repository from git remote." >&2
    echo "Set GITHUB_OWNER and GITHUB_REPO env vars." >&2
    exit 1
  fi
  GITHUB_OWNER="$(echo "$remote_url" | sed -nE 's#.*[:/]([^/]+)/([^/]+)(\.git)?$#\1#p')"
  GITHUB_REPO="$(echo "$remote_url" | sed -nE 's#.*[:/]([^/]+)/([^/]+)(\.git)?$#\2#p')"
fi

if [ -z "$GITHUB_OWNER" ] || [ -z "$GITHUB_REPO" ]; then
  echo "Failed to parse owner/repo from git remote: $remote_url" >&2
  echo "Set GITHUB_OWNER and GITHUB_REPO env vars explicitly." >&2
  exit 1
fi

echo "Target repository: ${GITHUB_OWNER}/${GITHUB_REPO}"
echo ""

# ---------------------------------------------------------------------------
# apply_ruleset — idempotent apply of one ruleset JSON file
# Globals: GITHUB_OWNER, GITHUB_REPO
# Args:    $1 = path to ruleset JSON file (relative to repo root)
# ---------------------------------------------------------------------------
apply_ruleset() {
  local ruleset_file="$1"

  if [ ! -f "$ruleset_file" ]; then
    echo "Ruleset definition not found: $ruleset_file" >&2
    echo "Run this script from the repository root." >&2
    return 1
  fi

  local ruleset_name
  ruleset_name="$(jq -r '.name' "$ruleset_file")"
  if [ -z "$ruleset_name" ] || [ "$ruleset_name" = "null" ]; then
    echo "No 'name' field found in $ruleset_file" >&2
    return 1
  fi

  echo "---"
  echo "Ruleset file : $ruleset_file"
  echo "Ruleset name : $ruleset_name"

  # Look up existing ruleset by name (idempotent)
  local ruleset_id="${RULESET_ID:-}"
  if [ -z "$ruleset_id" ]; then
    echo "Looking up existing ruleset '$ruleset_name'..."
    local existing
    existing="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets" --jq \
      ".[] | select(.name == \"$ruleset_name\") | {id: .id}" 2>/dev/null || true)"
    ruleset_id="$(echo "$existing" | jq -r '.id // empty')"
  fi

  local api_url="/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets"
  local method="POST"

  if [ -n "$ruleset_id" ]; then
    api_url="/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets/${ruleset_id}"
    method="PUT"
    echo "Found existing ruleset ID $ruleset_id — updating via $method $api_url"
  else
    echo "No existing ruleset found — creating via $method $api_url"
  fi

  local response
  response="$(gh api \
    --method "$method" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "$api_url" \
    --input "$ruleset_file" 2>&1)"

  echo "$response"

  if echo "$response" | jq -e '.id' >/dev/null 2>&1; then
    echo ""
    echo "✅ Ruleset '$ruleset_name' applied successfully (ID: $(echo "$response" | jq -r '.id'))."
  else
    echo ""
    echo "❌ Failed to apply ruleset." >&2
    return 1
  fi
}

# ---------------------------------------------------------------------------
# 1. Main branch ruleset
# ---------------------------------------------------------------------------
apply_ruleset ".github/branch-ruleset.json"

# ---------------------------------------------------------------------------
# 2. Tag-protection ruleset (v*.*.* — no force-push, no deletion)
# ---------------------------------------------------------------------------
if [ "${SKIP_TAG:-}" != "1" ]; then
  apply_ruleset ".github/tag-ruleset.json"
else
  echo ""
  echo "SKIP_TAG=1 — skipping tag-protection ruleset."
fi

# ---------------------------------------------------------------------------
# 3. Repo-level toggles
# ---------------------------------------------------------------------------
if [ "${SKIP_TOGGLES:-}" != "1" ]; then
  echo ""
  echo "================================================"
  echo "Repo-level toggles"
  echo "================================================"

  # 3a. Auto-delete head branches after merge
  echo ""
  echo "---"
  echo "Setting auto-delete head branches after merge = ON..."
  gh api --method PATCH "/repos/${GITHUB_OWNER}/${GITHUB_REPO}" \
    -f delete_branch_on_merge=true >/dev/null
  echo "✅ auto-delete head branches = ON"

  # 3b. Default workflow permissions = read
  echo ""
  echo "---"
  echo "Setting default workflow permissions = read..."
  current_perms="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/permissions" --jq '.default_workflow_permissions' 2>/dev/null || true)"
  echo "Current default workflow permissions: ${current_perms:-unknown}"
  gh api --method PUT "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/permissions" \
    -f default_workflow_permissions="read" >/dev/null
  echo "✅ default workflow permissions = read"

  # 3c. Verify push-protection status
  echo ""
  echo "---"
  echo "Verifying push-protection status..."
  secret_scanning="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}" --jq '.security_and_analysis.advanced_security.status' 2>/dev/null || true)"
  push_protection="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}" --jq '.security_and_analysis.push_protection.status' 2>/dev/null || true)"
  secret_scanning_status="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}" --jq '.security_and_analysis.secret_scanning.status' 2>/dev/null || true)"
  echo "  Secret scanning (push protection): ${secret_scanning:-not found}"
  echo "  Push protection:                   ${push_protection:-not found}"
  echo "  Secret scanning (alerts):          ${secret_scanning_status:-not found}"
  if [ "$push_protection" = "enabled" ]; then
    echo "✅ Push protection is enabled."
  else
    echo "⚠️  Push protection is NOT enabled (status: ${push_protection:-unknown})."
    echo "   Enable it in: https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/settings/security_analysis"
  fi

  echo ""
  echo "✅ Repo-level toggles applied."
else
  echo ""
  echo "SKIP_TOGGLES=1 — skipping repo-level toggles."
fi

echo ""
echo "================================================"
echo "All done."
