"""Back up / restore uploaded files in Supabase Storage (Phase 10.6, D6).

A database-only backup is not enough: resumes and other uploads live in the
PRIVATE Storage bucket. This script uses only the Python standard library.

    backup:  python storage_backup.py backup  <out_dir>
    restore: python storage_backup.py restore <in_dir>   (needs --apply)
    verify:  python storage_backup.py verify  <in_dir>

Env:
    SUPABASE_URL              project base URL (https://<ref>.supabase.co)
    SUPABASE_SERVICE_KEY      server-only key (never printed, never written)
    SUPABASE_STORAGE_BUCKET   bucket name (default campus-uploads)

``backup`` is READ-ONLY against Storage: it lists every object (recursively)
and downloads each one into ``<out_dir>/objects/<path>``, then writes
``manifest.json`` with each object's path, size and SHA-256 (no file
contents, no signed URLs, no key). ``restore`` uploads the files back into a
bucket (e.g. a NEW project after a disaster) and refuses without ``--apply``;
it never overwrites (``x-upsert: false``). ``verify`` re-hashes a local copy
against its manifest. Object paths are checked so a hostile name can never
escape ``out_dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

PAGE = 100
TIMEOUT = 60


def _cfg() -> tuple[str, str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "campus-uploads")
    if not url or not key:
        sys.exit("storage_backup: SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
    return url, key, bucket


def _request(method: str, url: str, key: str, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {key}", "apikey": key, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - fixed https base
        return resp.read()


def _safe_rel(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    raw_parts = path.split("/")
    if rel.is_absolute() or any(part in ("", ".", "..") for part in raw_parts) or "\\" in path:
        raise ValueError(f"unsafe object path rejected: {path!r}")
    return rel


def list_objects(url: str, key: str, bucket: str, prefix: str = "") -> list[str]:
    """Every object path under ``prefix`` (folders are entries without an id)."""
    out: list[str] = []
    offset = 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": PAGE, "offset": offset,
                           "sortBy": {"column": "name", "order": "asc"}}).encode()
        items = json.loads(_request("POST", f"{url}/storage/v1/object/list/{bucket}", key, body,
                                    {"Content-Type": "application/json"}))
        for item in items:
            full = f"{prefix}{item['name']}"
            if item.get("id") is None:  # a folder
                out.extend(list_objects(url, key, bucket, full + "/"))
            else:
                out.append(full)
        if len(items) < PAGE:
            return out
        offset += PAGE


def backup(out_dir: Path) -> dict:
    url, key, bucket = _cfg()
    objects_dir = out_dir / "objects"
    entries = []
    for path in sorted(list_objects(url, key, bucket)):
        rel = _safe_rel(path)
        data = _request("GET", f"{url}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", key)
        target = objects_dir.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries.append({"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"bucket": bucket, "objects": entries, "count": len(entries),
                "bytes": sum(e["size"] for e in entries)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def verify(in_dir: Path) -> list[str]:
    manifest = json.loads((in_dir / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for entry in manifest["objects"]:
        target = in_dir / "objects" / Path(*_safe_rel(entry["path"]).parts)
        if not target.is_file():
            problems.append(f"missing: {entry['path']}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            problems.append(f"checksum mismatch: {entry['path']}")
    return problems


def restore(in_dir: Path, apply: bool) -> int:
    url, key, bucket = _cfg()
    problems = verify(in_dir)
    if problems:
        sys.exit("storage_backup: local copy failed verification: " + "; ".join(problems[:5]))
    manifest = json.loads((in_dir / "manifest.json").read_text(encoding="utf-8"))
    if not apply:
        print(f"dry run: would upload {manifest['count']} objects to bucket {bucket!r}; add --apply")
        return 0
    for entry in manifest["objects"]:
        data = (in_dir / "objects" / Path(*_safe_rel(entry["path"]).parts)).read_bytes()
        _request("POST", f"{url}/storage/v1/object/{bucket}/{urllib.parse.quote(entry['path'])}", key,
                 data, {"Content-Type": "application/octet-stream", "x-upsert": "false"})
    return manifest["count"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("action", choices=("backup", "restore", "verify"))
    p.add_argument("directory", type=Path)
    p.add_argument("--apply", action="store_true", help="restore only: actually upload")
    a = p.parse_args(argv)
    if a.action == "backup":
        a.directory.mkdir(parents=True, exist_ok=True)
        m = backup(a.directory)
        print(f"storage_backup: {m['count']} objects, {m['bytes']} bytes")
    elif a.action == "verify":
        problems = verify(a.directory)
        print("storage_backup: verify " + ("OK" if not problems else f"FAILED ({len(problems)})"))
        return 1 if problems else 0
    else:
        print(f"storage_backup: restored {restore(a.directory, a.apply)} objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
