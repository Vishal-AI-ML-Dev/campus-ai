#!/usr/bin/env bash
# Campus AI - restore-verification drill.
#
# For encrypted bundles from backup_all.sh use restore_drill.sh instead.
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
# Phase 10.6: random throwaway password and a digest-pinned image.
PW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
IMAGE="${RESTORE_PG_IMAGE:-postgres:16@sha256:63e83b5a3dc36a917dccb76fe1bd2ac51f1ec9c1deb47ff20d4f04233bf6eae9}"

cleanup(){ docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[verify] starting throwaway pinned postgres:16 on 127.0.0.1:${PGPORT} ..."
docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD="$PW" \
  -e POSTGRES_DB=campus -p "127.0.0.1:${PGPORT}:5432" "$IMAGE" >/dev/null

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
