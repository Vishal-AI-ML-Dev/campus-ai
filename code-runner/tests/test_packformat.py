"""Test-pack format v1: one digest process, reproducibility, tamper detection, U2 and the 40-test cap."""

import os
import tarfile
import io
from pathlib import Path

import pytest

from runner import packformat as pf
from runner.pack import PackError, load_pack

import synthetic

# Golden vector, computed once by this module and frozen: any change to the digest process breaks this test, and the
# private test-pack repository checks the same values with its copy of packformat.py (the copies must not drift).
GOLDEN = {
    "hidden": "1d5fc4e3cf5e71b5ccef21c5213abad77ce51cc708aa6b2f5f3c5d8f1cebfc19",
    "visible": "163a2aa0ded0149b5c5635c93da798fa9f6ee92c621dc99f0bb12df729eeb17d",
    "pack": "6bea93b85b3191371217ce09638082b04b79086ab3c1b89fab0bffe2882386fd",
    "archive": "56f071ffcd5d98bd2b1b8b862ec4f5b7b5e85cb9bc1950ce24f74bc55ea37e23",
}
GOLDEN_TESTS = {"visible": [("1\n", "1\n")], "basic": [("2\n", "4\n")], "edge": [("0\n", "0\n")],
                "performance": [("3\n", "9\n")]}


def _golden(root: Path):
    (root / "problems").mkdir(parents=True)
    synthetic.write_problem(root, "golden-sample", GOLDEN_TESTS)
    ps = pf.read_problem(root / "problems" / "golden-sample")
    manifest = pf.build_manifest(1, {"golden-sample": ps})
    (root / "manifest.json").write_bytes(pf.manifest_bytes(manifest))
    return ps, manifest


def test_golden_vector(tmp_path):
    ps, manifest = _golden(tmp_path)
    assert ps.hidden_digest == GOLDEN["hidden"]
    assert ps.visible_digest == GOLDEN["visible"]
    assert manifest["pack_digest"] == GOLDEN["pack"]
    archive = pf.build_archive(pf.runtime_entries(tmp_path, manifest))
    assert pf.sha256_hex(archive) == GOLDEN["archive"]


def test_rebuild_is_reproducible_and_archive_is_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _, ma = _golden(a)
    _, mb = _golden(b)
    assert ma == mb
    xa = pf.build_archive(pf.runtime_entries(a, ma))
    xb = pf.build_archive(pf.runtime_entries(b, mb))
    assert xa == xb
    with tarfile.open(fileobj=io.BytesIO(xa)) as tar:
        members = tar.getmembers()
    names = [m.name for m in members]
    assert names == sorted(names)
    assert all(m.mtime == 0 and m.uid == 0 and m.gid == 0 and m.uname == "" and m.gname == "" for m in members)
    assert all((m.mode == 0o444) if m.isfile() else (m.mode == 0o555) for m in members)
    # Only runtime material: no references, generators, labels or calibration.
    assert all(n in ("manifest.json", "problems") or n.startswith("problems/") for n in names)
    assert not any("reference" in n or "labels" in n or "calibration" in n or "generator" in n for n in names)
    assert pf.archive_name(ma) == f"campus-ai-testpack-{ma['pack_digest']}.tar"


def test_synthetic_pack_archive_excludes_non_runtime_files(pack_dir):
    manifest, _ = pf.read_pack(pack_dir)
    archive = pf.build_archive(pf.runtime_entries(pack_dir, manifest))
    assert synthetic.SENTINEL_REFERENCE.encode() not in archive
    assert synthetic.SENTINEL_NAME.encode() not in archive


@pytest.mark.parametrize("target", ["tests/basic/01.in", "tests/edge/01.out", "tests/performance/01.in",
                                    "tests/visible/01.out", "problem.lock.json"])
def test_any_changed_byte_is_detected(pack_dir, target):
    path = pack_dir / "problems" / synthetic.KEY / target
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises((pf.PackFormatError, ValueError)):
        pf.read_pack(pack_dir)


def test_extra_or_missing_test_files_are_rejected(pack_dir):
    gdir = pack_dir / "problems" / synthetic.KEY / "tests" / "basic"
    (gdir / "03.in").write_bytes(b"x\n")
    with pytest.raises(pf.PackFormatError):
        pf.read_pack(pack_dir)
    (gdir / "03.in").unlink()
    (gdir / "02.out").unlink()
    with pytest.raises(pf.PackFormatError):
        pf.read_pack(pack_dir)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinks_are_refused(pack_dir):
    target = pack_dir / "problems" / synthetic.KEY / "tests" / "basic" / "01.in"
    target.unlink()
    target.symlink_to("/etc/hostname")
    with pytest.raises(pf.PackFormatError):
        pf.read_pack(pack_dir)


@pytest.mark.parametrize("group", ["basic", "edge", "performance"])
def test_u2_every_hidden_group_needs_a_test(group):
    lock = synthetic._lock("u2-sample", {"visible": 1, "basic": 1, "edge": 1, "performance": 1})
    lock["groups"][group] = 0
    with pytest.raises(pf.PackFormatError, match="every hidden group"):
        pf.validate_lock(lock)


def test_at_most_40_hidden_tests():
    lock = synthetic._lock("cap-sample", {"visible": 1, "basic": 20, "edge": 20, "performance": 1})
    with pytest.raises(pf.PackFormatError, match="at most 40"):
        pf.validate_lock(lock)
    lock["groups"]["edge"] = 19
    pf.validate_lock(lock)


def test_lock_rejects_unknown_fields_and_bad_values():
    base = synthetic._lock("lock-sample", {"visible": 1, "basic": 1, "edge": 1, "performance": 1})
    for mutate in (
        lambda d: d.update(extra=1),
        lambda d: d.update(checker="custom"),
        lambda d: d.update(time_ms=7000),
        lambda d: d.update(memory_profile="huge"),
        lambda d: d.update(test_set_id="other-key"),
        lambda d: d.update(format=2),
    ):
        lock = dict(base, groups=dict(base["groups"]))
        mutate(lock)
        with pytest.raises(pf.PackFormatError):
            pf.validate_lock(lock)


def test_manifest_with_wrong_digest_is_rejected(pack_dir):
    text = (pack_dir / "manifest.json").read_text()
    manifest = pf.read_pack(pack_dir)[0]
    bad = text.replace(manifest["pack_digest"], "0" * 64)
    (pack_dir / "manifest.json").write_text(bad)
    with pytest.raises(pf.PackFormatError):
        pf.read_pack(pack_dir)


def test_load_pack_refuses_a_writable_pack(pack_dir):
    with pytest.raises(PackError, match="pack_not_read_only"):
        load_pack(pack_dir, require_read_only=True)


def test_load_pack_error_never_contains_test_data(pack_dir):
    (pack_dir / "manifest.json").write_text("{not json")
    with pytest.raises(PackError) as info:
        load_pack(pack_dir, require_read_only=False)
    assert str(info.value) == "pack_invalid"
    for s in synthetic.SENTINELS:
        assert s not in str(info.value)
