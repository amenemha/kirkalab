#!/bin/sh
# deploy/backup.sh — periodic PostgreSQL backups for the Kirkalab prod stack.
# Runs inside a postgres:16-alpine container (has pg_dump).
# Connection is taken from PG* env vars; host defaults to the compose service "db".
# A compressed dump is written every 24h and dumps older than
# BACKUP_KEEP_DAYS are pruned.
set -eu

PGHOST="${PGHOST:-db}"
BACKUP_DIR="/backups"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

while true; do
  TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
  OUTFILE="$BACKUP_DIR/${PGDATABASE}_${TIMESTAMP}.sql.gz"

  echo "[backup] $(date -u +%FT%TZ) dumping ${PGDATABASE} from ${PGHOST} -> ${OUTFILE}"
  if pg_dump -h "$PGHOST" "$PGDATABASE" | gzip > "$OUTFILE"; then
    echo "[backup] done: ${OUTFILE}"
  else
    echo "[backup] ERROR: pg_dump failed" >&2
    rm -f "$OUTFILE"
  fi

  echo "[backup] pruning dumps older than ${BACKUP_KEEP_DAYS} day(s)"
  find "$BACKUP_DIR" -name "*.sql.gz" -type f -mtime "+${BACKUP_KEEP_DAYS}" -delete

  # Sleep 24h until the next run.
  sleep 86400
done
