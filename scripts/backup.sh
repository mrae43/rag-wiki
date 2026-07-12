#!/usr/bin/env bash
# =============================================================================
# Stage-1 Postgres logical backup (Compose-on-VM, trusted-clients-only).
# ADR-0017 / PRD-002 §Ops floor, PRD-005 Gap #3.
# =============================================================================
#
# Runs `pg_dump -Fc` (Postgres custom format) against the `db` service in the
# prod compose stack, writes the dump to BACKUP_DIR, validates it with
# `pg_restore --list`, and prunes backups older than RETENTION_DAYS.
#
# Custom format is used because `pg_restore --list` can verify the catalog
# header without a live database connection, catching zero-byte or
# structurally-corrupt dumps immediately. Legacy `.sql.gz` files from the
# pre-PRD-005 format are still pruned so they age out under the same rule.
#
# Intended to run as a daily cron entry ON THE VM HOST (not inside a container),
# so it shells out to `docker compose exec -T db pg_dump ...`.
#
# Cron example (daily at 02:30):
#   30 2 * * *  /opt/rag-wiki/scripts/backup.sh >> /var/log/rag-wiki-backup.log 2>&1
#
# Env overrides (with defaults):
#   COMPOSE_FILE    path to the prod compose file        (deploy/docker-compose.prod.yml)
#   BACKUP_DIR      destination dir for .dump files      (/backups)
#   DB_USER         postgres user                        (ragwiki)
#   DB_NAME         postgres database                    (ragwiki)
#   RETENTION_DAYS  prune files older than N days       (7)
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
DB_USER="${DB_USER:-ragwiki}"
DB_NAME="${DB_NAME:-ragwiki}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

timestamp="$(date +%F)"
dump_file="${BACKUP_DIR}/ragwiki-${timestamp}.dump"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not on PATH." >&2
  exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Compose file not found: $COMPOSE_FILE" >&2
  echo "Set COMPOSE_FILE to the path of deploy/docker-compose.prod.yml." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "[$(date -Is)] backing up '${DB_NAME}' (user '${DB_USER}') to ${dump_file}..."

# -T: disable TTY allocation so the dump streams to stdout under cron.
# -Fc: custom format — smaller than plain SQL and readable by pg_restore --list
# for validation. The binary archive is written directly to the .dump file.
docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -U "$DB_USER" -Fc "$DB_NAME" \
  > "$dump_file"

echo "[$(date -Is)] validating ${dump_file}..."
if ! uv run python "${SCRIPT_DIR}/dump_validator.py" "$dump_file"; then
  echo "[$(date -Is)] ERROR: backup validation failed for ${dump_file}" >&2
  exit 1
fi

echo "[$(date -Is)] backup complete: $(du -h "$dump_file" | cut -f1) -> $dump_file"

# 7-day retention. -mtime +N selects files modified more than N*24h ago.
# Prune both the current .dump format and legacy .sql.gz files from the
# pre-PRD-005 transition period.
echo "[$(date -Is)] pruning backups older than ${RETENTION_DAYS} days in ${BACKUP_DIR}..."
find "$BACKUP_DIR" -name 'ragwiki-*.dump' -mtime "+${RETENTION_DAYS}" -delete
find "$BACKUP_DIR" -name 'ragwiki-*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[$(date -Is)] done."
