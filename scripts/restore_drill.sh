#!/usr/bin/env bash
# =============================================================================
# Monthly restore drill — Stage-1 Postgres backup verification (ADR-0017 §7).
# PRD-005 Gap #4, PR-3.
# =============================================================================
#
# Restores a custom-format dump (``pg_dump -Fc``) into a scratch Postgres
# database, confirms the six core tables exist, then drops the scratch DB.
# Intended as a manual monthly procedure, and wired into CI to lock pass/fail
# semantics.
#
# Usage:
#   scripts/restore_drill.sh <dump_file> <scratch_db_url>
#
# ``scratch_db_url`` is a standard Postgres connection URI:
#   postgresql://user:password@host:port/scratch_db_name
#
# The script refuses to restore into a database named ``ragwiki`` (the prod
# default) as a defensive guard. Override with ``PROD_DB_NAME`` env var.
#
# Exit codes:
#   0 — all six core tables present, scratch DB dropped
#   1 — any failure (dump invalid, restore error, missing table)
#   2 — usage error
#
# The scratch database is created, and a ``trap`` guarantees it is dropped
# whether the drill succeeds or fails (User Story 26).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable prod DB name guard (default matches deploy/.env.example POSTGRES_DB).
PROD_DB_NAME="${PROD_DB_NAME:-ragwiki}"

dump_file="${1:-}"
scratch_db_url="${2:-}"

usage() {
    echo "usage: $(basename "$0") <dump_file> <scratch_db_url>" >&2
    exit 2
}

[ -n "$dump_file" ] || usage
[ -n "$scratch_db_url" ] || usage

# ---------------------------------------------------------------------------
# Parse the connection URI — extract the database name and set PG* env vars
# so that createdb/dropdb (which don't support URIs) connect to the right
# host/port/user.
# ---------------------------------------------------------------------------
# shellcheck disable=SC2154
eval "$(
  python3 -c "
import sys, urllib.parse
p = urllib.parse.urlparse(sys.argv[1])
user     = p.username or ''
password = p.password or ''
host     = p.hostname or 'localhost'
port     = p.port or 5432
dbname   = p.path.lstrip('/').split('?')[0]
print(f'PGUSER={user!r}')
print(f'PGPASSWORD={password!r}')
print(f'PGHOST={host!r}')
print(f'PGPORT={port!r}')
print(f'PGDATABASE={dbname!r}')
print(f'scratch_db_name={dbname!r}')
" "$scratch_db_url"
)"

export PGUSER PGPASSWORD PGHOST PGPORT

# Guard: never restore into a database matching the prod name.
if [ "$scratch_db_name" = "$PROD_DB_NAME" ]; then
    echo "ERROR: refusing to restore into a database named '${PROD_DB_NAME}' (matches prod DB name)." >&2
    echo "  Choose a distinct scratch name, e.g. 'ragwiki_drill_$(date +%Y%m%d)'." >&2
    exit 1
fi

echo "=== Restore drill start ==="
echo "  dump:  ${dump_file}"
echo "  db:    ${scratch_db_name}"
echo "  host:  ${PGHOST}:${PGPORT}"

# ---- Step 1: validate the dump file ---------------------------------------
echo "--- Step 1: validate dump ---"
uv run python "${SCRIPT_DIR}/dump_validator.py" "$dump_file"

# ---- Step 2: create scratch database --------------------------------------
echo "--- Step 2: create scratch database '${scratch_db_name}' ---"
if ! createdb "$scratch_db_name" 2>/dev/null; then
    echo "ERROR: scratch database '${scratch_db_name}' already exists." >&2
    echo "  Drop it or choose a different name:" >&2
    echo "    dropdb '${scratch_db_name}'" >&2
    exit 1
fi

# Trap: always drop the scratch DB on exit (User Story 26).
cleanup() {
    echo "--- Cleanup: drop scratch database '${scratch_db_name}' ---"
    dropdb "$scratch_db_name" 2>/dev/null || true
}
trap cleanup EXIT

# ---- Step 3: restore the dump into the scratch DB -------------------------
echo "--- Step 3: pg_restore into '${scratch_db_name}' ---"
pg_restore -d "$scratch_db_url" "$dump_file"

# ---- Step 4: verify core tables -------------------------------------------
echo "--- Step 4: verify core tables ---"
CORE_TABLES=(sources chunks entities relations wiki_pages jobs)
missing=0
for table in "${CORE_TABLES[@]}"; do
    count=$(psql -d "$scratch_db_url" -Atc \
        "SELECT count(*) FROM pg_class WHERE relname='${table}' AND relkind='r';")
    if [ "$count" -gt 0 ] 2>/dev/null; then
        echo "  [ok]  ${table}"
    else
        echo "  [MISSING]  ${table}" >&2
        missing=1
    fi
done

if [ "$missing" -ne 0 ]; then
    echo "ERROR: one or more core tables are missing — restore drill FAILED" >&2
    exit 1
fi

echo "=== Restore drill PASSED — all six core tables present. ==="
# trap cleanup fires automatically
