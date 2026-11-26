"""Campus AI test-pack format v1: layout, validation and the one digest process.

Standard library only. This file is the single definition of the digest process. The private test-pack repository
keeps a byte-identical copy at ``tools/packformat.py``; both test suites check the same golden vector
(``tests/test_packformat.py``) so the two copies cannot drift silently.

Runtime layout (what the runner installs; nothing else is read)::

    manifest.json
    problems/<key>/problem.lock.json
    problems/<key>/tests/<group>/<NN>.in
    problems/<key>/tests/<group>/<NN>.out

``<group>`` is ``visible`` (copied from the public bundle) or one of the hidden groups ``basic``, ``edge``,
``performance``. ``<NN>`` is a two-digit number starting at ``01`` with no gaps. Reference solutions, generators,
labels, calibration evidence and review records live only in the private repository and are never in the runtime
archive.

Digest process (all SHA-256, lower-case hex):

1. File digest = sha256(raw bytes). Files are never normalised; the repository disables line-ending conversion.
2. Group digest = sha256 of ``b"campus-ai-group-v1\\n"`` followed by one line per test in ascending ``NN`` order:
   ``f"{NN}\\t{sha256(in)}\\t{len(in)}\\t{sha256(out)}\\t{len(out)}\\n"``.
3. Hidden test-set digest (the value written to the public ``hidden_test_set.digest_sha256``) = sha256 of
   ``b"campus-ai-testset-v1\\n"`` + canonical JSON of the lock's bound fields + ``b"\\n"`` + one line per hidden group
   in the fixed order basic, edge, performance: ``f"{group}\\t{count}\\t{group_digest}\\n"``.
4. Visible-set digest: the same as 3 with prefix ``campus-ai-visibleset-v1`` and the single group ``visible``.
5. Pack digest = sha256 of ``b"campus-ai-pack-v1\\n"`` + canonical JSON of ``{"format": 1, "pack_version": N}`` +
   ``b"\\n"`` + one line per problem in ascending key order: ``f"{key}\\t{hidden_digest}\\t{visible_digest}\\n"``.
6. The runtime archive is an uncompressed POSIX ustar file with entries in ascending path order, mtime 0, uid/gid 0,
   empty owner names, mode 0444 for files and 0555 for directories. Its file name is
   ``campus-ai-testpack-<pack digest>.tar``; the SHA-256 of the archive bytes is recorded beside it.

Canonical JSON = ``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`` encoded as ASCII.
No timestamps, host names or other nondeterministic data enter any digest.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FORMAT_VERSION = 1
HIDDEN_GROUPS: tuple[str, ...] = ("basic", "edge", "performance")
VISIBLE_GROUP = "visible"
ALL_GROUPS: tuple[str, ...] = (VISIBLE_GROUP, *HIDDEN_GROUPS)
CHECKERS: tuple[str, ...] = ("exact", "tokens", "float", "unordered_lines")
MEMORY_PROFILES: tuple[str, ...] = ("standard", "large")
MAX_TESTS_PER_SET = 40
MAX_TEST_FILE_BYTES = 8 * 1024 * 1024
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
TEST_NAME_RE = re.compile(r"^[0-9]{2}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Fields of problem.lock.json that are bound into the hidden test-set digest. Changing any of them changes the digest
# that the public bundle must carry, so a pack and a bundle can only match when they agree on all of them.
LOCK_BOUND_FIELDS: tuple[str, ...] = (
    "key",
    "problem_version",
    "test_set_id",
    "test_set_version",
    "checker",
    "time_ms",
    "memory_profile",
)
LOCK_FIELDS: frozenset[str] = frozenset(LOCK_BOUND_FIELDS) | {"groups", "format"}


class PackFormatError(ValueError):
    """The pack layout or a digest is invalid. Messages never contain test data."""


def canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TestCase:
    name: str
    input: bytes
    output: bytes


def group_digest(tests: Iterable[TestCase]) -> str:
    h = hashlib.sha256(b"campus-ai-group-v1\n")
    names: list[str] = []
    for t in tests:
        if not TEST_NAME_RE.match(t.name):
            raise PackFormatError("test names must be two digits")
        names.append(t.name)
        line = f"{t.name}\t{sha256_hex(t.input)}\t{len(t.input)}\t{sha256_hex(t.output)}\t{len(t.output)}\n"
        h.update(line.encode("ascii"))
    expected = [f"{i:02d}" for i in range(1, len(names) + 1)]
    if names != expected:
        raise PackFormatError("tests must be numbered 01..NN in order without gaps")
    return h.hexdigest()


def validate_lock(lock: dict) -> None:
    if not isinstance(lock, dict) or set(lock) != LOCK_FIELDS:
        raise PackFormatError("problem.lock.json has missing or unknown fields")
    if lock["format"] != FORMAT_VERSION:
        raise PackFormatError("unsupported lock format")
    if not isinstance(lock["key"], str) or not KEY_RE.match(lock["key"]):
        raise PackFormatError("invalid problem key")
    if lock["test_set_id"] != lock["key"]:
        raise PackFormatError("test_set_id must equal the problem key")
    for f in ("problem_version", "test_set_version"):
        v = lock[f]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise PackFormatError(f"{f} must be a positive integer")
    if lock["checker"] not in CHECKERS:
        raise PackFormatError("unknown checker")
    t = lock["time_ms"]
    if not isinstance(t, int) or isinstance(t, bool) or not 100 <= t <= 6000:
        raise PackFormatError("time_ms must be 100-6000")
    if lock["memory_profile"] not in MEMORY_PROFILES:
        raise PackFormatError("unknown memory profile")
    groups = lock["groups"]
    if not isinstance(groups, dict) or set(groups) != set(ALL_GROUPS):
        raise PackFormatError("groups must list visible, basic, edge and performance")
    for g, n in groups.items():
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= MAX_TESTS_PER_SET:
            raise PackFormatError("group counts must be 0-40")
    # U2 (P8-D7): every hidden group needs at least one test; weights are never redistributed.
    if any(groups[g] < 1 for g in HIDDEN_GROUPS):
        raise PackFormatError("every hidden group (basic, edge, performance) needs at least one test")
    if sum(groups[g] for g in HIDDEN_GROUPS) > MAX_TESTS_PER_SET:
        raise PackFormatError("at most 40 hidden tests")
    if groups[VISIBLE_GROUP] < 1:
        raise PackFormatError("at least one visible test")


def _bound(lock: dict) -> bytes:
    return canonical_json({f: lock[f] for f in LOCK_BOUND_FIELDS})


def hidden_set_digest(lock: dict, group_digests: dict[str, str]) -> str:
    validate_lock(lock)
    h = hashlib.sha256(b"campus-ai-testset-v1\n")
    h.update(_bound(lock) + b"\n")
    for g in HIDDEN_GROUPS:
        h.update(f"{g}\t{lock['groups'][g]}\t{group_digests[g]}\n".encode("ascii"))
    return h.hexdigest()


def visible_set_digest(lock: dict, visible_group_digest: str) -> str:
    validate_lock(lock)
    h = hashlib.sha256(b"campus-ai-visibleset-v1\n")
    h.update(_bound(lock) + b"\n")
    h.update(f"{VISIBLE_GROUP}\t{lock['groups'][VISIBLE_GROUP]}\t{visible_group_digest}\n".encode("ascii"))
    return h.hexdigest()


def pack_digest(pack_version: int, problems: dict[str, tuple[str, str]]) -> str:
    """``problems`` maps key -> (hidden digest, visible digest)."""
    if not isinstance(pack_version, int) or isinstance(pack_version, bool) or pack_version < 1:
        raise PackFormatError("pack_version must be a positive integer")
    h = hashlib.sha256(b"campus-ai-pack-v1\n")
    h.update(canonical_json({"format": FORMAT_VERSION, "pack_version": pack_version}) + b"\n")
    for key in sorted(problems):
        hidden, visible = problems[key]
        if not DIGEST_RE.match(hidden) or not DIGEST_RE.match(visible):
            raise PackFormatError("invalid digest")
        h.update(f"{key}\t{hidden}\t{visible}\n".encode("ascii"))
    return h.hexdigest()


# --------------------------------------------------------------------------------------------- reading a pack tree


@dataclass(frozen=True)
class ProblemSet:
    lock: dict
    groups: dict[str, tuple[TestCase, ...]]
    group_digests: dict[str, str]
    hidden_digest: str
    visible_digest: str


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PackFormatError("pack entries must be regular files (no links)")
    data = path.read_bytes()
    if len(data) > MAX_TEST_FILE_BYTES:
        raise PackFormatError("test file too large")
    return data


def read_problem(problem_dir: Path) -> ProblemSet:
    lock = json.loads(_read_regular(problem_dir / "problem.lock.json"))
    validate_lock(lock)
    if lock["key"] != problem_dir.name:
        raise PackFormatError("lock key does not match its directory")
    tests_dir = problem_dir / "tests"
    groups: dict[str, tuple[TestCase, ...]] = {}
    digests: dict[str, str] = {}
    for g in ALL_GROUPS:
        gdir = tests_dir / g
        if gdir.is_symlink() or not gdir.is_dir():
            raise PackFormatError("missing test group directory")
        names = sorted(p.name for p in gdir.iterdir())
        n = lock["groups"][g]
        expected = sorted([f"{i:02d}.in" for i in range(1, n + 1)] + [f"{i:02d}.out" for i in range(1, n + 1)])
        if names != expected:
            raise PackFormatError("test files do not match the lock's group counts")
        cases = tuple(
            TestCase(f"{i:02d}", _read_regular(gdir / f"{i:02d}.in"), _read_regular(gdir / f"{i:02d}.out"))
            for i in range(1, n + 1)
        )
        groups[g] = cases
        digests[g] = group_digest(cases)
    return ProblemSet(
        lock=lock,
        groups=groups,
        group_digests=digests,
        hidden_digest=hidden_set_digest(lock, digests),
        visible_digest=visible_set_digest(lock, digests[VISIBLE_GROUP]),
    )


def build_manifest(pack_version: int, problems: dict[str, ProblemSet]) -> dict:
    entries = {}
    for key in sorted(problems):
        ps = problems[key]
        entries[key] = {
            "problem_version": ps.lock["problem_version"],
            "test_set_version": ps.lock["test_set_version"],
            "groups": {g: ps.lock["groups"][g] for g in ALL_GROUPS},
            "group_digests": {g: ps.group_digests[g] for g in ALL_GROUPS},
            "hidden_digest": ps.hidden_digest,
            "visible_digest": ps.visible_digest,
        }
    return {
        "format": FORMAT_VERSION,
        "pack_version": pack_version,
        "pack_digest": pack_digest(pack_version, {k: (v["hidden_digest"], v["visible_digest"]) for k, v in entries.items()}),
        "problems": entries,
    }


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True).encode("ascii") + b"\n"


def read_pack(root: Path) -> tuple[dict, dict[str, ProblemSet]]:
    """Read and fully verify a runtime pack tree. Raises PackFormatError on any mismatch."""
    manifest = json.loads(_read_regular(root / "manifest.json"))
    if not isinstance(manifest, dict) or set(manifest) != {"format", "pack_version", "pack_digest", "problems"}:
        raise PackFormatError("manifest has missing or unknown fields")
    if manifest["format"] != FORMAT_VERSION:
        raise PackFormatError("unsupported manifest format")
    pdir = root / "problems"
    if pdir.is_symlink() or not pdir.is_dir():
        raise PackFormatError("missing problems directory")
    found = sorted(p.name for p in pdir.iterdir())
    if found != sorted(manifest["problems"]):
        raise PackFormatError("problem directories do not match the manifest")
    problems = {key: read_problem(pdir / key) for key in found}
    rebuilt = build_manifest(manifest["pack_version"], problems)
    if rebuilt != manifest:
        raise PackFormatError("manifest digests do not match the pack contents")
    return manifest, problems


# --------------------------------------------------------------------------------------------- archive


def runtime_entries(root: Path, manifest: dict) -> list[tuple[str, bytes]]:
    """(archive path, bytes) for every runtime file, sorted by path."""
    out: list[tuple[str, bytes]] = [("manifest.json", manifest_bytes(manifest))]
    for key, entry in manifest["problems"].items():
        base = root / "problems" / key
        out.append((f"problems/{key}/problem.lock.json", _read_regular(base / "problem.lock.json")))
        for g in ALL_GROUPS:
            for i in range(1, entry["groups"][g] + 1):
                for ext in ("in", "out"):
                    rel = f"problems/{key}/tests/{g}/{i:02d}.{ext}"
                    out.append((rel, _read_regular(root / rel)))
    out.sort(key=lambda e: e[0])
    return out


def _dirs_of(paths: Iterable[str]) -> list[str]:
    dirs: set[str] = set()
    for p in paths:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return sorted(dirs)


def build_archive(entries: list[tuple[str, bytes]]) -> bytes:
    """Deterministic uncompressed ustar archive (same entries -> same bytes on every platform)."""
    buf = io.BytesIO()
    members: list[tuple[str, bytes | None]] = [(d, None) for d in _dirs_of(p for p, _ in entries)]
    members += entries
    members.sort(key=lambda m: m[0])
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o555
                tar.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = 0o444
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def archive_name(manifest: dict) -> str:
    return f"campus-ai-testpack-{manifest['pack_digest']}.tar"
