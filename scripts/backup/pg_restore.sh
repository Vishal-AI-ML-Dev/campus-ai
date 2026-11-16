#!/usr/bin/env bash
# Campus AI - restore a Postgres dump created by pg_backup.sh.
#
# DESTRUCTIVE: --clean drops existing objects before recreating them. Refuses to
# run unless you pass --force (or set FORCE=1), so you can't nuke a DB by reflex.
#
# Env:
#   RESTORE_DATABASE_URL (required) target connection. Use the OWNER role.
#   RUN_MIGRATIONS       (default: 1) run `alembic upgrade head` after restore.
#
# Usage:
#   RESTORE_DATABASE_URL="postgresql://postgres:...@host:5432/postgres" \
#     scripts/backup/pg_restore.sh path/to/campus-<ts>.dump --force
set -euo pipefail

DUMP_FILE="${1:-}"
FORCE="${FORCE:-0}"
[ "${2:-}" = "--force" ] && FORCE=1

: "${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL (owner-role connection string)}"
[ -n "$DUMP_FILE" ] || { echo "usage: pg_restore.sh <dump-file> --force"; exit 2; }
[ -f "$DUMP_FILE" ] || { echo "no such file: $DUMP_FILE"; exit 2; }

if [ "$FORCE" != "1" ]; then
  echo "REFUSING: this DROPS and recreates objects in the target DB."
  echo "Re-run with --force (or FORCE=1) once you are sure of the target."
  exit 3
fi

# verify checksum if present
if [ -f "$DUMP_FILE.sha256" ]; then
  echo "[pg_restore] verifying checksum ..."
  sha256sum -c "$DUMP_FILE.sha256"
fi

RESTORE_URL="${RESTORE_DATABASE_URL/postgresql+psycopg:/postgresql:}"
RESTORE_URL="${RESTORE_URL/postgres+psycopg:/postgres:}"

echo "[pg_restore] restoring $DUMP_FILE ..."
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname="$RESTORE_URL" "$DUMP_FILE"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[pg_restore] alembic upgrade head (idempotent) ..."
  ( cd "$(dirname "$0")/../../backend" && \
    MIGRATION_DATABASE_URL="$RESTORE_DATABASE_URL" uv run alembic upgrade head )
fi

echo "[pg_restore] done."
echo "[pg_restore] Reminder: role grants are NOT in the dump. If app_user is new,"
echo "             provision it first - see backend/scripts/app_role.template.sql"
echo "             (an unverified draft; review it before applying anything)."
