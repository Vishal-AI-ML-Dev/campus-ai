#!/usr/bin/env bash
# Campus AI - Postgres logical backup (storage-agnostic).
#
# Creates a compressed custom-format pg_dump (restore with pg_restore.sh).
# Writes to $BACKUP_DIR and prints the file path. UPLOADING (GitHub artifact,
# Cloudflare R2, S3, ...) is left to the caller/workflow so this stays portable:
# to push off-site later, add one step after this script (e.g. `rclone copy`
# or `aws s3 cp "$out" s3://bucket/`).
#
# Env:
#   BACKUP_DATABASE_URL  (required) connection to dump. Use the OWNER role so the
#                        dump includes every table regardless of RLS. Accepts
#                        SQLAlchemy-style postgresql+psycopg://... too.
#   BACKUP_DIR           (default: ./backups) output directory.
#   RETENTION_DAYS       (default: 14) prune local dumps older than this.
#
# Usage:
#   BACKUP_DATABASE_URL="postgresql://postgres:...@host:5432/postgres" \
#     scripts/backup/pg_backup.sh
set -euo pipefail

: "${BACKUP_DATABASE_URL:?set BACKUP_DATABASE_URL (owner-role connection string)}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# pg_dump speaks libpq URLs; strip the SQLAlchemy driver suffix if present.
DUMP_URL="${BACKUP_DATABASE_URL/postgresql+psycopg:/postgresql:}"
DUMP_URL="${DUMP_URL/postgres+psycopg:/postgres:}"

mkdir -p "$BACKUP_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/campus-${ts}.dump"

echo "[pg_backup] dumping -> $out"
pg_dump --format=custom --no-owner --no-privileges --file="$out" "$DUMP_URL"

# checksum for integrity verification on restore
sha256sum "$out" > "$out.sha256"
echo "[pg_backup] wrote $(du -h "$out" | cut -f1) (+ .sha256)"

# prune old local dumps
find "$BACKUP_DIR" -name 'campus-*.dump' -type f -mtime "+${RETENTION_DAYS}" -print -delete || true
find "$BACKUP_DIR" -name 'campus-*.dump.sha256' -type f -mtime "+${RETENTION_DAYS}" -delete || true

echo "$out"
