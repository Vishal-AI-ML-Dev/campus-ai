#!/usr/bin/env bash
# Campus AI - Qdrant snapshot backup.
#
# Creates + downloads a snapshot per collection so the vector data (face
# embeddings + RAG knowledge base) can be restored.
#
# Env:
#   QDRANT_URL          (required) e.g. https://xyz.cloud.qdrant.io:6333
#   QDRANT_API_KEY      (required)
#   QDRANT_COLLECTIONS  (default: "student_faces career_kb") space-separated.
#   BACKUP_DIR          (default: ./backups)
#
# Restore (manual): upload a snapshot back with
#   PUT {QDRANT_URL}/collections/{c}/snapshots/upload  (multipart field 'snapshot')
set -euo pipefail

: "${QDRANT_URL:?set QDRANT_URL}"
: "${QDRANT_API_KEY:?set QDRANT_API_KEY}"
COLLECTIONS="${QDRANT_COLLECTIONS:-student_faces career_kb}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"

base="${QDRANT_URL%/}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"

for c in $COLLECTIONS; do
  echo "[qdrant] snapshotting collection: $c"
  if ! resp="$(curl -fsSL -H "api-key: ${QDRANT_API_KEY}" -X POST "${base}/collections/${c}/snapshots")"; then
    echo "[qdrant] WARN: could not snapshot '$c' (missing collection?), skipping"
    continue
  fi
  name="$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["name"])')"
  out="${BACKUP_DIR}/qdrant-${c}-${ts}.snapshot"
  echo "[qdrant] downloading $name -> $out"
  curl -fsSL -H "api-key: ${QDRANT_API_KEY}" -o "$out" "${base}/collections/${c}/snapshots/${name}"
  echo "[qdrant] wrote $(du -h "$out" | cut -f1)"
done
echo "[qdrant] done."
