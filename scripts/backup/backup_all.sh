#!/usr/bin/env bash
# Campus AI - daily ENCRYPTED backup: Postgres + Storage files (Phase 10.6, D6).
#
# One bundle per run: campus-backup-<UTC ts>.tar.gpg (+ .sha256) containing
#   db.dump        pg_dump custom format (schema + data + RLS policies)
#   storage/       every object of the private Storage bucket + its manifest
#   manifest.json  UTC time, Alembic head, EXACT row count per table, SHA-256
#                  of db.dump, storage object count/bytes - no row contents
# The bundle is encrypted to a PUBLIC key (GPG); the private key stays offline
# with its owner (D7), so a copy of the bundle alone reveals nothing.
#
# Fail closed: any step failing -> no bundle, a failure heartbeat and a
# structured `campus.alert backup_failed` line on stderr; exit non-zero.
# Destination is OUT OF SCOPE until D7 (owner decision) - this writes to
# BACKUP_DIR only. Never configure a public location.
#
# Env:
#   BACKUP_DATABASE_URL     (required) libpq URL of a role that can read every
#                           row: tables have FORCE RLS, so it needs BYPASSRLS
#                           (pg_dump refuses rather than dump a partial copy).
#   BACKUP_GPG_PUBLIC_KEY   (required) path to the ASCII-armoured public key.
#   BACKUP_DIR              (default ./backups) local output directory.
#   BACKUP_STORAGE          required (default) | skip  - skip ONLY when the
#                           deployment has no Storage configured; recorded.
#   BACKUP_HEARTBEAT_URL    optional ping URL (healthchecks-style; "/fail"
#                           appended on failure). Never printed.
#   PG_DUMP / PSQL          commands (default pg_dump / psql; e.g.
#                           "docker exec -i <container> pg_dump").
#   PYTHON                  default python3
#   GPG_DOCKER_IMAGE        optional: run gpg inside this (pinned) image instead
#                           of the host gpg (e.g. hosts where gpg-agent cannot run)
#   KEEP_DAILY / KEEP_WEEKLY  retention (default 14 / 8)
set -euo pipefail

: "${BACKUP_DATABASE_URL:?set BACKUP_DATABASE_URL}" "${BACKUP_GPG_PUBLIC_KEY:?set BACKUP_GPG_PUBLIC_KEY}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_STORAGE="${BACKUP_STORAGE:-required}"
PG_DUMP="${PG_DUMP:-pg_dump}"
PSQL="${PSQL:-psql}"
PYTHON="${PYTHON:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DB_URL="${BACKUP_DATABASE_URL/postgresql+psycopg:/postgresql:}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
work="$(mktemp -d)"
stage="start"

ping_heartbeat() {
  [ -n "${BACKUP_HEARTBEAT_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "${BACKUP_HEARTBEAT_URL}$1" || echo "[backup] heartbeat ping failed" >&2
}
on_exit() {
  rc=$?
  rm -rf "$work"
  if [ "$rc" -ne 0 ]; then
    rm -f "$BACKUP_DIR/campus-backup-${ts}.tar.gpg.partial"
    printf '{"logger":"campus.alert","alert":true,"alert_name":"backup_failed","severity":"critical","stage":"%s","exit_code":%d}\n' "$stage" "$rc" >&2
    ping_heartbeat "/fail"
  fi
}
trap on_exit EXIT

mkdir -p "$BACKUP_DIR" "$work/bundle"

stage="pg_dump"
echo "[backup] database dump ..."
$PG_DUMP --format=custom --no-owner --no-privileges "$DB_URL" > "$work/bundle/db.dump"
[ -s "$work/bundle/db.dump" ] || { echo "[backup] empty dump" >&2; exit 3; }

stage="row_counts"
$PSQL "$DB_URL" -v ON_ERROR_STOP=1 -tA -F '|' -c "SET row_security = off" -c "
  SELECT 'alembic', version_num FROM alembic_version
  UNION ALL
  SELECT 'table:' || table_name,
         (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM public.%I', table_name), false, true, '')))[1]::text
  FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  ORDER BY 1" | tr -d '\r' > "$work/counts.txt"

stage="storage"
if [ "$BACKUP_STORAGE" = "skip" ]; then
  echo "[backup] storage SKIPPED (BACKUP_STORAGE=skip)"
  storage_status="skipped"
else
  "$PYTHON" "$HERE/storage_backup.py" backup "$work/bundle/storage"
  storage_status="included"
fi

stage="manifest"
"$PYTHON" - "$work" "$ts" "$storage_status" <<'PY'
import hashlib, json, sys
from pathlib import Path
work, ts, storage_status = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
counts, head = {}, None
for line in (work / "counts.txt").read_text().splitlines():
    if "|" not in line:  # blank or a command tag such as "SET"
        continue
    key, value = line.split("|", 1)
    if key == "alembic":
        head = value
    else:
        counts[key[len("table:"):]] = int(value)
dump = (work / "bundle" / "db.dump").read_bytes()
storage = {"status": storage_status}
sm = work / "bundle" / "storage" / "manifest.json"
if sm.exists():
    m = json.loads(sm.read_text())
    storage.update(count=m["count"], bytes=m["bytes"], bucket=m["bucket"])
manifest = {"format": "campus-backup/1", "created_utc": ts, "alembic_head": head,
            "row_counts": counts, "db_dump_sha256": hashlib.sha256(dump).hexdigest(),
            "db_dump_bytes": len(dump), "storage": storage}
(work / "bundle" / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
print(f"[backup] manifest: head={head} tables={len(counts)} rows={sum(counts.values())} storage={storage_status}")
PY

stage="encrypt"
out="$BACKUP_DIR/campus-backup-${ts}.tar.gpg"
if [ -n "${GPG_DOCKER_IMAGE:-}" ]; then
  # MSYS2_ARG_CONV_EXCL: stops Git Bash on Windows rewriting /keys (no-op elsewhere).
  tar -C "$work/bundle" -cf - . | MSYS2_ARG_CONV_EXCL="*" docker run --rm -i \
    -v "$(dirname "$BACKUP_GPG_PUBLIC_KEY"):/keys:ro" --entrypoint gpg "$GPG_DOCKER_IMAGE" \
    --batch --quiet --yes --trust-model always \
    --recipient-file "/keys/$(basename "$BACKUP_GPG_PUBLIC_KEY")" --encrypt > "$out.partial"
else
  export GNUPGHOME="$work/gnupg"; mkdir -m 700 "$GNUPGHOME"
  tar -C "$work/bundle" -cf - . | gpg --batch --quiet --yes --trust-model always \
    --recipient-file "$BACKUP_GPG_PUBLIC_KEY" --encrypt --output "$out.partial"
fi
[ -s "$out.partial" ] || { echo "[backup] encryption produced no output" >&2; exit 4; }
mv "$out.partial" "$out"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )

stage="retention"
"$PYTHON" "$HERE/retention.py" "$BACKUP_DIR" --keep-daily "${KEEP_DAILY:-14}" --keep-weekly "${KEEP_WEEKLY:-8}"

stage="done"
ping_heartbeat ""
echo "[backup] OK $out"
