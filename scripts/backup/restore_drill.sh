#!/usr/bin/env bash
# Campus AI - ISOLATED restore drill for an encrypted bundle (Phase 10.6/10.9, D6).
#
#   scripts/backup/restore_drill.sh <campus-backup-*.tar.gpg>
#
# Never touches a real database: restores into a NEW throwaway, digest-pinned
# Postgres container (random localhost port, random password, tmpfs data),
# removed afterwards. Steps and checks (any failure -> exit non-zero):
#   1. bundle SHA-256 matches its .sha256 file
#   2. decrypt with the PRIVATE key (temporary GNUPGHOME, deleted afterwards)
#   3. db.dump SHA-256 matches the manifest; Storage copy re-hashes clean
#   4. pg_restore into the fresh cluster; runtime role re-created from the
#      canonical backend/scripts/app_role.template.sql (roles are cluster-level
#      and not in a pg_dump)
#   5. Alembic head == manifest; EXACT row count of every table == manifest
#   6. as the NOBYPASSRLS app_user: no rows without a tenant; one tenant's own
#      users visible with the tenant GUC set
#   7. elapsed time vs the RTO target (8 h)
# Prints one JSON report line (counts and timings only, no row contents).
#
# Env:
#   BACKUP_GPG_PRIVATE_KEY  (required) path to the armoured private key
#   RESTORE_PG_IMAGE        default: postgres:16 pinned by digest (below)
#   RTO_SECONDS             default 28800 (8 h)
#   PYTHON                  default python3
#   GPG_DOCKER_IMAGE        optional: decrypt inside this (pinned) image
set -euo pipefail

BUNDLE="${1:?usage: restore_drill.sh <bundle.tar.gpg>}"
: "${BACKUP_GPG_PRIVATE_KEY:?set BACKUP_GPG_PRIVATE_KEY}"
IMAGE="${RESTORE_PG_IMAGE:-postgres:16@sha256:63e83b5a3dc36a917dccb76fe1bd2ac51f1ec9c1deb47ff20d4f04233bf6eae9}"
RTO_SECONDS="${RTO_SECONDS:-28800}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
started=$(date +%s)
work="$(mktemp -d)"
name="campus-restore-drill-$$-$(date +%s)"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT

echo "[drill] 1/7 bundle checksum"
( cd "$(dirname "$BUNDLE")" && sha256sum -c --status "$(basename "$BUNDLE").sha256" )

echo "[drill] 2/7 decrypt"
mkdir "$work/b"
if [ -n "${GPG_DOCKER_IMAGE:-}" ]; then
  MSYS2_ARG_CONV_EXCL="*" docker run --rm -i -v "$(dirname "$BACKUP_GPG_PRIVATE_KEY"):/keys:ro" --entrypoint sh "$GPG_DOCKER_IMAGE" \
    -c "gpg --batch --quiet --import /keys/$(basename "$BACKUP_GPG_PRIVATE_KEY") && gpg --batch --quiet --decrypt" \
    < "$BUNDLE" | tar -C "$work/b" -xf -
else
  export GNUPGHOME="$work/gnupg"; mkdir -m 700 "$GNUPGHOME"
  gpg --batch --quiet --import "$BACKUP_GPG_PRIVATE_KEY"
  gpg --batch --quiet --decrypt "$BUNDLE" | tar -C "$work/b" -xf -
fi
[ -f "$work/b/manifest.json" ] || { echo "[drill] decryption failed" >&2; exit 4; }

echo "[drill] 3/7 inner checksums"
"$PYTHON" - "$work/b" <<'PY'
import hashlib, json, sys
from pathlib import Path
b = Path(sys.argv[1]); m = json.loads((b / "manifest.json").read_text())
assert hashlib.sha256((b / "db.dump").read_bytes()).hexdigest() == m["db_dump_sha256"], "db.dump checksum mismatch"
PY
storage_status=$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['storage']['status'])" "$work/b/manifest.json")
storage_objects=0
if [ "$storage_status" = "included" ]; then
  "$PYTHON" "$HERE/storage_backup.py" verify "$work/b/storage"
  storage_objects=$("$PYTHON" -c "import json,sys;print(json.load(open(sys.argv[1]))['count'])" "$work/b/storage/manifest.json")
fi

echo "[drill] 4/7 restore into a fresh isolated cluster"
pw="$("$PYTHON" -c 'import secrets;print(secrets.token_urlsafe(24))')"
app_pw="$("$PYTHON" -c 'import secrets;print(secrets.token_urlsafe(24))')"
MSYS2_ARG_CONV_EXCL="*" docker run -d --rm --name "$name" -e POSTGRES_PASSWORD="$pw" -e POSTGRES_DB=campus \
  --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 "$IMAGE" >/dev/null
for _ in $(seq 1 60); do docker exec "$name" pg_isready -U postgres -d campus -q 2>/dev/null && break; sleep 1; done
sleep 2
PSQL="docker exec -i $name psql -v ON_ERROR_STOP=1 -U postgres -d campus"
restore_started=$(date +%s)
docker exec -i "$name" pg_restore --no-owner --no-privileges --exit-on-error -U postgres -d campus < "$work/b/db.dump"
{ sed -e '/^[\\]echo .Set a NEW/d' -e '/^[\\]password/d' "$ROOT/backend/scripts/app_role.template.sql"
  echo "ALTER ROLE app_user PASSWORD :'app_pw';"
} | $PSQL -q -v app_pw="$app_pw" -f - > /dev/null
restore_seconds=$(( $(date +%s) - restore_started ))

echo "[drill] 5/7 alembic head + exact row counts"
$PSQL -tA -F '|' -c "SET row_security = off" -c "
  SELECT 'alembic', version_num FROM alembic_version
  UNION ALL
  SELECT 'table:' || table_name,
         (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM public.%I', table_name), false, true, '')))[1]::text
  FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  ORDER BY 1" | tr -d '\r' > "$work/restored_counts.txt"

echo "[drill] 6/7 RLS as app_user"
tenant=$($PSQL -tA -c "SET row_security = off" -c "SELECT tenant_id FROM users GROUP BY tenant_id ORDER BY count(*) DESC LIMIT 1" | tr -d '\r' | tail -1)
APP="docker exec -i -e PGPASSWORD=$app_pw $name psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U app_user -d campus -tA"
no_tenant_rows=$($APP -c "SELECT count(*) FROM mock_interview_sessions" | tr -d '\r')
tenant_users=0
if [ -n "$tenant" ]; then
  tenant_users=$($APP -c "BEGIN" -c "SELECT set_config('app.current_tenant_id', '$tenant', true)" \
    -c "SELECT count(*) FROM users WHERE tenant_id = $tenant" -c "COMMIT" | tr -d '\r' | grep -E '^[0-9]+$' | tail -1)
fi

elapsed=$(( $(date +%s) - started ))
"$PYTHON" - "$work" "$elapsed" "$restore_seconds" "$RTO_SECONDS" "$no_tenant_rows" "${tenant_users:-0}" "$storage_status" "$storage_objects" <<'PY'
import json, sys
from pathlib import Path
work = Path(sys.argv[1]); elapsed, restore_s, rto = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
no_tenant_rows, tenant_users = int(sys.argv[5] or 0), int(sys.argv[6] or 0)
storage_status, storage_objects = sys.argv[7], int(sys.argv[8])
m = json.loads((work / "b" / "manifest.json").read_text())
restored, head = {}, None
for line in (work / "restored_counts.txt").read_text().splitlines():
    if "|" not in line:  # blank or a command tag such as "SET"
        continue
    k, v = line.split("|", 1)
    if k == "alembic":
        head = v
    else:
        restored[k[len("table:"):]] = int(v)
mismatch = sorted(t for t in set(m["row_counts"]) | set(restored) if m["row_counts"].get(t) != restored.get(t))
report = {
    "drill": "restore", "backup_created_utc": m["created_utc"], "alembic_head": head,
    "alembic_matches": head == m["alembic_head"], "tables": len(restored),
    "rows": sum(restored.values()), "row_count_mismatches": mismatch,
    "rls_rows_without_tenant": no_tenant_rows, "rls_tenant_users_visible": tenant_users,
    "storage": storage_status, "storage_objects_verified": storage_objects,
    "restore_seconds": restore_s, "elapsed_seconds": elapsed, "rto_seconds": rto,
    "within_rto": elapsed <= rto,
}
ok = report["alembic_matches"] and not mismatch and no_tenant_rows == 0 and report["within_rto"] \
     and (tenant_users > 0 or not m["row_counts"].get("users"))
report["result"] = "PASS" if ok else "FAIL"
print("[drill] 7/7 " + json.dumps(report, sort_keys=True))
sys.exit(0 if ok else 1)
PY
