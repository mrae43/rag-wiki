#!/usr/bin/env bash
# =============================================================================
# Stage-1 Postgres logical backup (Compose-on-VM, trusted-clients-only).
# ADR-0017 / PRD-002 §Ops floor.
# =============================================================================
#
# Runs `pg_dump` against the `db` service in the prod compose stack, gzip-
# compresses the dump to BACKUP_DIR, and prunes backups older than RETENTION_DAYS.
# Intended to run as a daily cron entry ON THE VM HOST (not inside a container),
# so it shells out to `docker compose exec -T db pg_dump ...`.
#
# Cron example (daily at 02:30):
#   30 2 * * *  /opt/rag-wiki/scripts/backup.sh >> /var/log/rag-wiki-backup.log 2>&1
#
# Env overrides (with defaults):
#   COMPOSE_FILE    path to the prod compose file        (deploy/docker-compose.prod.yml)
#   BACKUP_DIR      destination dir for .sql.gz files    (/backups)
#   DB_USER         postgres user                        (ragwiki)
#   DB_NAME         postgres database                    (ragwiki)
#   RETENTION_DAYS  prune files older than N days       (7)
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
DB_USER="${DB_USER:-ragwiki}"
DB_NAME="${DB_NAME:-ragwiki}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

timestamp="$(date +%F)"
dump_file="${BACKUP_DIR}/ragwiki-${timestamp}.sql.gz"

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
# pipefail + the implicit gzip exit status surface a failed pg_dump as a
# non-zero script exit (the empty/truncated .sql.gz is left in place so the
# failure is visible in the next pruning pass).
docker compose -f "$COMPOSE_FILE" exec -T db pg_dump -U "$DB_USER" "$DB_NAME" \
  | gzip > "$dump_file"

if [ ! -s "$dump_file" ]; then
  echo "[$(date -Is)] ERROR: backup file is empty: $dump_file" >&2
  exit 1
fi

echo "[$(date -Is)] backup complete: $(du -h "$dump_file" | cut -f1) -> $dump_file"

# 7-day retention. -mtime +N selects files modified more than N*24h ago.
echo "[$(date -Is)] pruning backups older than ${RETENTION_DAYS} days in ${BACKUP_DIR}..."
find "$BACKUP_DIR" -name 'ragwiki-*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "[$(date -Is)] done."
