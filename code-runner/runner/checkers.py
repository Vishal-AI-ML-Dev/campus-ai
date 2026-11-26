"""Output checkers (trusted layer only). No custom checker code is ever loaded (spec §3.2)."""

from __future__ import annotations

import math


def _tokens(b: bytes) -> list[bytes]:
    return b.split()


def exact(expected: bytes, actual: bytes) -> bool:
    return expected == actual


def tokens(expected: bytes, actual: bytes) -> bool:
    return _tokens(expected) == _tokens(actual)


def float_tokens(expected: bytes, actual: bytes, abs_tol: float = 1e-6, rel_tol: float = 1e-6) -> bool:
    e, a = _tokens(expected), _tokens(actual)
    if len(e) != len(a):
        return False
    for x, y in zip(e, a):
        if x == y:
            continue
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return False
        if not math.isclose(fx, fy, rel_tol=rel_tol, abs_tol=abs_tol):
            return False
    return True


def unordered_lines(expected: bytes, actual: bytes) -> bool:
    def norm(b: bytes) -> list[bytes]:
        return sorted(line.strip() for line in b.strip().splitlines())

    return norm(expected) == norm(actual)


CHECKERS = {"exact": exact, "tokens": tokens, "float": float_tokens, "unordered_lines": unordered_lines}


def check(name: str, expected: bytes, actual: bytes) -> bool:
    return CHECKERS[name](expected, actual)
