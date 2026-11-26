"""Backup retention: keep the newest N daily and N weekly bundles (Phase 10.6).

    python retention.py <backup_dir> [--keep-daily 14] [--keep-weekly 8] [--dry-run]

Bundles are ``campus-backup-<YYYYMMDDTHHMMSSZ>.tar.gpg`` (+ ``.sha256``).
Kept: the newest bundle of each of the last ``keep_daily`` days that have a
bundle, plus the newest bundle of each of the last ``keep_weekly`` ISO weeks.
Everything else (and its checksum file) is deleted. Files that do not match
the name pattern are never touched.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

PATTERN = re.compile(r"^campus-backup-(\d{8}T\d{6}Z)\.tar\.gpg$")


def plan(names: list[str], keep_daily: int, keep_weekly: int) -> tuple[set[str], set[str]]:
    stamped = sorted(
        ((datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ"), n) for n in names if (m := PATTERN.match(n))),
        reverse=True,
    )
    keep: set[str] = set()
    days: list = []
    weeks: list = []
    for when, name in stamped:
        day, week = when.date(), when.isocalendar()[:2]
        if day not in days and len(days) < keep_daily:
            days.append(day)
            keep.add(name)
        if week not in weeks and len(weeks) < keep_weekly:
            weeks.append(week)
            keep.add(name)
    return keep, {n for _, n in stamped} - keep


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("directory", type=Path)
    p.add_argument("--keep-daily", type=int, default=14)
    p.add_argument("--keep-weekly", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    keep, drop = plan([f.name for f in a.directory.iterdir() if f.is_file()], a.keep_daily, a.keep_weekly)
    for name in sorted(drop):
        for f in (a.directory / name, a.directory / f"{name}.sha256"):
            if f.exists() and not a.dry_run:
                f.unlink()
    print(f"[retention] kept {len(keep)}, {'would delete' if a.dry_run else 'deleted'} {len(drop)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
