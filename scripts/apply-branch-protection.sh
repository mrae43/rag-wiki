#!/usr/bin/env bash
# =============================================================================
# Apply branch-protection rulesets to a GitHub repository via the Rulesets API.
# ADR-0018 / PR-B.
# =============================================================================
#
# Reads `.github/branch-ruleset.json` (the main ruleset definition) and applies
# it to the repository identified by GITHUB_OWNER / GITHUB_REPO (auto-detected
# from `git remote get-url origin` if not set).
#
# Idempotent: finds an existing ruleset by name (from the JSON's "name" field)
# and updates it via PUT; creates via POST if none exists.
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
set -euo pipefail

RULESET_FILE=".github/branch-ruleset.json"

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

if [ ! -f "$RULESET_FILE" ]; then
  echo "Ruleset definition not found: $RULESET_FILE" >&2
  echo "Run this script from the repository root." >&2
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
  # Handle both HTTPS (https://github.com/owner/repo.git) and SSH (git@github.com:owner/repo.git)
  GITHUB_OWNER="$(echo "$remote_url" | sed -nE 's#.*[:/]([^/]+)/([^/]+)(\.git)?$#\1#p')"
  GITHUB_REPO="$(echo "$remote_url" | sed -nE 's#.*[:/]([^/]+)/([^/]+)(\.git)?$#\2#p')"
fi

if [ -z "$GITHUB_OWNER" ] || [ -z "$GITHUB_REPO" ]; then
  echo "Failed to parse owner/repo from git remote: $remote_url" >&2
  echo "Set GITHUB_OWNER and GITHUB_REPO env vars explicitly." >&2
  exit 1
fi

echo "Target repository: ${GITHUB_OWNER}/${GITHUB_REPO}"

# ---------------------------------------------------------------------------
# Read ruleset name from JSON
# ---------------------------------------------------------------------------
ruleset_name="$(jq -r '.name' "$RULESET_FILE")"
if [ -z "$ruleset_name" ] || [ "$ruleset_name" = "null" ]; then
  echo "No 'name' field found in $RULESET_FILE" >&2
  exit 1
fi

echo "Ruleset name: $ruleset_name"

# ---------------------------------------------------------------------------
# Find existing ruleset by name (idempotent), or use explicit RULESET_ID
# ---------------------------------------------------------------------------
RULESET_ID="${RULESET_ID:-}"

if [ -z "$RULESET_ID" ]; then
  echo "Looking up existing ruleset '$ruleset_name'..."
  existing="$(gh api "/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets" --jq \
    ".[] | select(.name == \"$ruleset_name\") | {id: .id}" 2>/dev/null || true)"
  RULESET_ID="$(echo "$existing" | jq -r '.id // empty')"
fi

api_url="/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets"
method="POST"

if [ -n "$RULESET_ID" ]; then
  api_url="/repos/${GITHUB_OWNER}/${GITHUB_REPO}/rulesets/${RULESET_ID}"
  method="PUT"
  echo "Found existing ruleset ID $RULESET_ID — updating via $method $api_url"
else
  echo "No existing ruleset found — creating via $method $api_url"
fi

# ---------------------------------------------------------------------------
# Apply the ruleset
# ---------------------------------------------------------------------------
response="$(gh api \
  --method "$method" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  "$api_url" \
  --input "$RULESET_FILE" 2>&1)"

echo "$response"

if echo "$response" | jq -e '.id' >/dev/null 2>&1; then
  echo ""
  echo "✅ Ruleset '$ruleset_name' applied successfully (ID: $(echo "$response" | jq -r '.id'))."
else
  echo ""
  echo "❌ Failed to apply ruleset." >&2
  exit 1
fi
