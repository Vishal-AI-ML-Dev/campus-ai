#!/usr/bin/env bash
# Phase 10.4: prepare a DISPOSABLE Postgres for the RLS / tests_pg_api suites.
#
# Creates database campus_rls_test, migrates it to head as the owner role
# (postgres), then creates the NOBYPASSRLS runtime role exactly as the
# canonical backend/scripts/app_role.template.sql does - only its interactive
# `\password` step is replaced by a non-interactive ALTER ROLE using a
# throwaway password from APP_ROLE_PASSWORD (never printed). Prints
# EXPECT_SYSID=<cluster system identifier> for the fail-closed suite guard.
#
# NEVER point this at a real database: it refuses unless PG_DISPOSABLE=yes.
#
# Env:
#   PG_DISPOSABLE=yes          explicit confirmation that the cluster is throwaway
#   PSQL                       psql command (default "psql"; locally e.g.
#                              "docker exec -i <container> psql")
#   PGHOST/PGPORT/PGPASSWORD   for psql when it runs on this host (CI)
#   OWNER_SQLALCHEMY_URL       postgresql+psycopg://postgres:<pw>@<host>:<port>/campus_rls_test
#   APP_ROLE_PASSWORD          throwaway password for app_user
set -euo pipefail

[ "${PG_DISPOSABLE:-}" = "yes" ] || { echo "refusing: set PG_DISPOSABLE=yes for a throwaway cluster" >&2; exit 2; }
: "${OWNER_SQLALCHEMY_URL:?}" "${APP_ROLE_PASSWORD:?}"
PSQL="${PSQL:-psql}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DB=campus_rls_test

$PSQL -v ON_ERROR_STOP=1 -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"

( cd "$ROOT/backend"
  PY="${BACKEND_PYTHON:-python}"
  ENVIRONMENT=test SECRET_KEY="ci-throwaway-$(date +%s)-not-a-secret" \
  DATABASE_URL="$OWNER_SQLALCHEMY_URL" MIGRATION_DATABASE_URL="$OWNER_SQLALCHEMY_URL" \
    "$PY" -m alembic upgrade head )

# Canonical role script, minus the interactive password prompt.
{ sed -e '/^[\\]echo .Set a NEW/d' -e '/^[\\]password/d' "$ROOT/backend/scripts/app_role.template.sql"
  echo "ALTER ROLE app_user PASSWORD :'app_pw';"
} | $PSQL -v ON_ERROR_STOP=1 -U postgres -d "$DB" -q -v app_pw="$APP_ROLE_PASSWORD" -f - > /dev/null

# Verify the runtime role (fail closed) and report the cluster id.
$PSQL -v ON_ERROR_STOP=1 -U postgres -d "$DB" -tA -c "
  DO \$\$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_user'
               AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb)) THEN
      RAISE EXCEPTION 'app_user has a forbidden attribute';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tableowner='app_user') THEN
      RAISE EXCEPTION 'app_user owns a table';
    END IF;
  END \$\$;" >/dev/null
echo "EXPECT_SYSID=$($PSQL -U postgres -d "$DB" -tA -c 'SELECT system_identifier FROM pg_control_system()')"
