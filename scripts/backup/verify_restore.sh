#!/usr/bin/env bash
# Campus AI - restore-verification drill.
#
# Restores a dump (from pg_backup.sh) into a THROWAWAY Docker Postgres and runs
# sanity checks, WITHOUT touching any real database. An untested backup is not a
# backup - run this after setting up nightly backups, then quarterly.
#
# Requirements: docker, pg_restore + psql (PostgreSQL 16 client) on PATH.
#
# Usage:
#   scripts/backup/verify_restore.sh path/to/campus-<ts>.dump
set -euo pipefail

DUMP_FILE="${1:?usage: verify_restore.sh <dump-file>}"
[ -f "$DUMP_FILE" ] || { echo "no such file: $DUMP_FILE"; exit 2; }

CONTAINER="campus-restore-check"
PGPORT="${PGPORT:-55432}"
PW="verify"

cleanup(){ docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[verify] starting throwaway postgres:16 on :${PGPORT} ..."
docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD="$PW" \
  -e POSTGRES_DB=campus -p "${PGPORT}:5432" postgres:16 >/dev/null

echo "[verify] waiting for readiness ..."
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U postgres -d campus >/dev/null 2>&1; then break; fi
  sleep 1
done

URL="postgresql://postgres:${PW}@localhost:${PGPORT}/campus"

if [ -f "$DUMP_FILE.sha256" ]; then
  echo "[verify] verifying checksum ..."
  sha256sum -c "$DUMP_FILE.sha256"
fi

echo "[verify] restoring dump ..."
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$URL" "$DUMP_FILE"

echo "[verify] sanity checks ..."
psql "$URL" -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) AS restored_tables FROM information_schema.tables WHERE table_schema='public';" \
  -c "SELECT version_num AS alembic_head FROM alembic_version;"

echo "[verify] OK - this dump is restorable."
