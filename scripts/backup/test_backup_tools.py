"""Phase 10.6: offline tests for storage_backup.py and retention.py.

A tiny fake of the Supabase Storage REST API runs on 127.0.0.1 (synthetic
files only); nothing leaves the machine.

    backend/.venv/Scripts/python -m pytest scripts/backup -p no:cacheprovider
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retention  # noqa: E402
import storage_backup  # noqa: E402

KEY = "synthetic-service-key-not-real"
FILES = {
    "tenant-1/resumes/a.pdf": b"%PDF synthetic A",
    "tenant-1/resumes/b.pdf": b"%PDF synthetic B" * 50,
    "tenant-2/x.txt": b"synthetic",
}


class _FakeStorage(BaseHTTPRequestHandler):
    store: dict[str, bytes] = {}

    def log_message(self, *_a):  # quiet
        pass

    def _auth(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def do_POST(self):  # noqa: N802
        if not self._auth():
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/storage/v1/object/list/"):
            req = json.loads(body)
            prefix, items = req["prefix"], {}
            for path in self.store:
                if path.startswith(prefix):
                    head, _, rest = path[len(prefix):].partition("/")
                    items[head] = {"name": head, "id": None if rest else "id-" + head}
            page = sorted(items.values(), key=lambda i: i["name"])[req["offset"]: req["offset"] + req["limit"]]
            data = json.dumps(page).encode()
        else:  # upload
            path = self.path.split("/storage/v1/object/campus-uploads/", 1)[1]
            if path in self.store and self.headers.get("x-upsert") == "false":
                self.send_response(409)
                self.end_headers()
                return
            self.store[path] = body
            data = b"{}"
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if not self._auth():
            return
        path = self.path.split("/storage/v1/object/campus-uploads/", 1)[1]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(self.store[path])


@pytest.fixture
def fake_storage(monkeypatch):
    _FakeStorage.store = dict(FILES)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeStorage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SUPABASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", KEY)
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "campus-uploads")
    monkeypatch.setattr(storage_backup, "PAGE", 1)  # exercise pagination
    yield _FakeStorage
    server.shutdown()


def test_backup_copies_every_object_with_checksums(tmp_path, fake_storage):
    m = storage_backup.backup(tmp_path)
    assert m["count"] == 3 and m["bytes"] == sum(len(v) for v in FILES.values())
    for entry in m["objects"]:
        assert entry["sha256"] == hashlib.sha256(FILES[entry["path"]]).hexdigest()
    assert storage_backup.verify(tmp_path) == []
    manifest_text = (tmp_path / "manifest.json").read_text()
    assert KEY not in manifest_text and "synthetic A" not in manifest_text


def test_verify_detects_tampering_and_loss(tmp_path, fake_storage):
    storage_backup.backup(tmp_path)
    (tmp_path / "objects" / "tenant-2" / "x.txt").write_bytes(b"changed")
    (tmp_path / "objects" / "tenant-1" / "resumes" / "a.pdf").unlink()
    problems = storage_backup.verify(tmp_path)
    assert sorted(p.split(":")[0] for p in problems) == ["checksum mismatch", "missing"]


def test_restore_needs_apply_and_never_overwrites(tmp_path, fake_storage):
    storage_backup.backup(tmp_path)
    fake_storage.store = {}
    assert storage_backup.restore(tmp_path, apply=False) == 0
    assert fake_storage.store == {}
    assert storage_backup.restore(tmp_path, apply=True) == 3
    assert fake_storage.store == FILES


@pytest.mark.parametrize("path", ["../etc/passwd", "/abs", "a/../../b", "a\\b", "a//b"])
def test_unsafe_object_paths_are_rejected(path):
    with pytest.raises(ValueError):
        storage_backup._safe_rel(path)


def test_retention_keeps_14_daily_and_8_weekly():
    names = [f"campus-backup-2026{m:02d}{d:02d}T020000Z.tar.gpg" for m in (9, 10, 11) for d in range(1, 29)]
    names += ["campus-backup-20261128T030000Z.tar.gpg", "notes.txt"]
    keep, drop = retention.plan(names, keep_daily=14, keep_weekly=8)
    assert "campus-backup-20261128T030000Z.tar.gpg" in keep  # newest of its day wins
    assert "campus-backup-20261128T020000Z.tar.gpg" in drop
    assert "notes.txt" not in keep | drop
    days = {n[14:22] for n in keep}
    assert len(keep) <= 14 + 8 and len(days) == len(keep)
    assert all(n[14:20] >= "202609" for n in keep)
