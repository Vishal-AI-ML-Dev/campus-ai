"""Execution limits (P8-S3, spec §4.4). The values here are CEILINGS: calibration may only tighten them.

Per-test CPU = problem ``time_ms`` x language multiplier, capped at 6 s; per-test wall = 2.5 x CPU. Total wall per
submission (compile + all tests) = 30 s. Output capture = 64 KB per stream per test. At most 40 tests per submission.
The request schema carries no limit fields, so a caller can never raise any of these.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .languages import Language

CPU_CEILING_MS = 6000
WALL_FACTOR = 2.5
SUBMISSION_WALL_S = 30.0
OUTPUT_CAP_BYTES = 64 * 1024
MAX_TESTS = 40
FSIZE_BYTES = 16 * 1024 * 1024
WORKSPACE_TMPFS_MB = 64
# What the response may show of a visible/custom run (spec §3.3).
DISPLAY_STDOUT_BYTES = 4 * 1024
DISPLAY_STDERR_BYTES = 4 * 1024
DISPLAY_COMPILE_BYTES = 8 * 1024
TIMING_ROUND_MS = 10


class LimitError(ValueError):
    pass


@dataclass(frozen=True)
class TestLimits:
    cpu_ms: int
    cpu_rlimit_s: int  # RLIMIT_CPU granularity is one second; rounded up, never above the ceiling
    wall_s: float
    memory_mb: int
    pids: int
    nofile: int


def test_limits(lang: Language, time_ms: int, memory_profile: str, factor: float = 1.0) -> TestLimits:
    """Limits for one test. ``factor`` comes from calibration and may only tighten (0 < factor <= 1)."""
    if not 0 < factor <= 1.0:
        raise LimitError("calibration may only tighten limits (0 < factor <= 1)")
    if memory_profile not in lang.run_memory_mb:
        raise LimitError("unknown memory profile")
    cpu_ms = min(int(time_ms * lang.cpu_multiplier * factor), CPU_CEILING_MS)
    if cpu_ms < 100:
        raise LimitError("cpu limit below 100 ms")
    return TestLimits(
        cpu_ms=cpu_ms,
        cpu_rlimit_s=min(math.ceil(cpu_ms / 1000), CPU_CEILING_MS // 1000),
        wall_s=cpu_ms * WALL_FACTOR / 1000,
        memory_mb=lang.run_memory_mb[memory_profile],
        pids=lang.pids,
        nofile=lang.nofile,
    )


def round_ms(ms: float) -> int:
    """Timings are rounded to 10 ms before they leave the runner (oracle resistance)."""
    return int(round(ms / TIMING_ROUND_MS)) * TIMING_ROUND_MS
