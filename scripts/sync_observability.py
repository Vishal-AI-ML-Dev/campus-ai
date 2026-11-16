#!/usr/bin/env python3
"""Sync shared observability modules from the single source of truth.

Single source of truth:
    packages/campus_obs/{observability,errors,metrics}.py

Targets (kept byte-identical):
    backend/app/observability/
    ai-worker/

Usage:
    python scripts/sync_observability.py           # copy canonical -> targets
    python scripts/sync_observability.py --check    # verify in sync (CI); exit 1 on drift

Wire --check into CI / pre-commit so any drift fails the build.
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packages" / "campus_obs"
MODULES = ("observability.py", "errors.py", "metrics.py")
TARGET_DIRS = (
    ROOT / "backend" / "app" / "observability",
    ROOT / "ai-worker",
)


def main() -> int:
    check = "--check" in sys.argv[1:]
    drift: list[str] = []
    for mod in MODULES:
        src = SRC / mod
        if not src.exists():
            print(f"ERROR: canonical file missing: {src}")
            return 2
        for tdir in TARGET_DIRS:
            dst = tdir / mod
            if check:
                if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
                    drift.append(str(dst.relative_to(ROOT)))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"synced  {mod:20s} -> {dst.relative_to(ROOT)}")

    if check:
        if drift:
            print("OUT OF SYNC:")
            for d in drift:
                print(f"  - {d}")
            print("Fix: python scripts/sync_observability.py")
            return 1
        print("observability modules in sync")
    else:
        print("done: observability modules synced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
