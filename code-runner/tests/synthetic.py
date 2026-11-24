"""Synthetic test packs for the runner's own tests. Never real problems, never real hidden data.

``SENTINELS`` are planted in hidden inputs, hidden expected outputs, the pack's directory names and test file
contents; the privacy tests assert that none of them ever reaches an API response, a log line, a metric or an error.
"""

from __future__ import annotations

import json
from pathlib import Path

from runner import packformat as pf

SENTINEL_INPUT = "ZQX-HIDDEN-INPUT-7F3A91"
SENTINEL_EXPECTED = "ZQX-HIDDEN-EXPECTED-C2D804"
SENTINEL_NAME = "ZQX-HIDDEN-NAME-0B6E11"
SENTINEL_STDERR = "ZQX-HIDDEN-STDERR-55AE20"
SENTINEL_REFERENCE = "ZQX-HIDDEN-REFERENCE-91CC3B"
SENTINELS = (SENTINEL_INPUT, SENTINEL_EXPECTED, SENTINEL_NAME, SENTINEL_STDERR, SENTINEL_REFERENCE)

KEY = "synthetic-echo"


def _lock(key: str, groups: dict[str, int], time_ms: int = 1000, checker: str = "tokens") -> dict:
    return {
        "format": 1, "key": key, "problem_version": 1, "test_set_id": key, "test_set_version": 1,
        "checker": checker, "time_ms": time_ms, "memory_profile": "standard", "groups": groups,
    }


def write_problem(root: Path, key: str, tests: dict[str, list[tuple[str, str]]], time_ms: int = 1000) -> None:
    pdir = root / "problems" / key
    groups = {g: len(tests.get(g, [])) for g in pf.ALL_GROUPS}
    for g in pf.ALL_GROUPS:
        gdir = pdir / "tests" / g
        gdir.mkdir(parents=True, exist_ok=True)
        for i, (inp, out) in enumerate(tests.get(g, []), 1):
            (gdir / f"{i:02d}.in").write_bytes(inp.encode())
            (gdir / f"{i:02d}.out").write_bytes(out.encode())
    (pdir / "problem.lock.json").write_bytes(pf.canonical_json(_lock(key, groups, time_ms)) + b"\n")


def echo_tests() -> dict[str, list[tuple[str, str]]]:
    """Problem: print the first token of the input. Hidden tests carry sentinels in input and expected output."""
    return {
        "visible": [("alpha\n", "alpha\n"), ("beta gamma\n", "beta\n")],
        "basic": [(f"{SENTINEL_EXPECTED}-b1 {SENTINEL_INPUT}\n", f"{SENTINEL_EXPECTED}-b1\n"),
                  (f"{SENTINEL_EXPECTED}-b2 {SENTINEL_INPUT}\n", f"{SENTINEL_EXPECTED}-b2\n")],
        "edge": [(f"{SENTINEL_EXPECTED}-e1 {SENTINEL_INPUT}\n", f"{SENTINEL_EXPECTED}-e1\n")],
        "performance": [(f"{SENTINEL_EXPECTED}-p1 {SENTINEL_INPUT} " + "x " * 1000 + "\n", f"{SENTINEL_EXPECTED}-p1\n")],
    }


def build_pack(root: Path, extra: dict[str, dict[str, list[tuple[str, str]]]] | None = None) -> dict:
    (root / "problems").mkdir(parents=True, exist_ok=True)
    write_problem(root, KEY, echo_tests())
    for key, tests in (extra or {}).items():
        write_problem(root, key, tests)
    # Non-runtime material that a real private pack also has: it must never be read or served by the runner.
    ref = root / "problems" / KEY / "reference"
    ref.mkdir(exist_ok=True)
    (ref / "main.py").write_text(f"# {SENTINEL_REFERENCE}\nprint(input().split()[0])\n")
    (root / "problems" / KEY / "labels.json").write_text(json.dumps({"basic": {"01": SENTINEL_NAME}}))
    problems = {p.name: pf.read_problem(p) for p in (root / "problems").iterdir()}
    manifest = pf.build_manifest(1, problems)
    (root / "manifest.json").write_bytes(pf.manifest_bytes(manifest))
    return manifest


def hidden_digest(root: Path, key: str = KEY) -> str:
    return json.loads((root / "manifest.json").read_text())["problems"][key]["hidden_digest"]
