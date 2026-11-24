"""The installed (read-only) test pack. Verified completely at startup; nothing is ever written to it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import packformat as pf


class PackError(Exception):
    """The installed pack is missing, writable or does not match its manifest (fixed message, no test data)."""


class PackMismatch(Exception):
    """The request names a problem/version/digest the installed pack does not have (HTTP 409)."""


@dataclass(frozen=True)
class InstalledPack:
    root: Path
    manifest: dict
    problems: dict[str, pf.ProblemSet]

    @property
    def digest(self) -> str:
        return self.manifest["pack_digest"]

    def problem(self, key: str, version: int) -> pf.ProblemSet:
        ps = self.problems.get(key)
        if ps is None or ps.lock["problem_version"] != version:
            raise PackMismatch()
        return ps


def _writable_anywhere(root: Path) -> bool:
    if os.access(root, os.W_OK):
        return True
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            if os.access(os.path.join(dirpath, name), os.W_OK):
                return True
    return False


def load_pack(root: Path, require_read_only: bool = True) -> InstalledPack:
    try:
        manifest, problems = pf.read_pack(root)
    except (OSError, ValueError, KeyError) as exc:  # PackFormatError is a ValueError
        raise PackError("pack_invalid") from exc
    if require_read_only and _writable_anywhere(root):
        raise PackError("pack_not_read_only")
    return InstalledPack(root=root, manifest=manifest, problems=problems)
