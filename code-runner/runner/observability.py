"""Content-free logs and token-protected metrics (P8-S5, spec §4.8 / §9.7).

Logs: exactly one JSON line per execution, built ONLY from ``ExecutionLog`` fields, each of which is an enum, an
integer, a UUID or a digest. Values are re-validated here, so even a bug elsewhere cannot push source, input, output,
stderr, hidden data or tokens into a log line. Other events (startup, self-check, reaper) use fixed event names and
fixed reason codes only.

Metrics: Prometheus text format; label values are drawn from fixed enums (language, kind, verdict, outcome, reason),
never from request data. No tenant labels (the runner is tenant-unaware). No Sentry or other third-party telemetry.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from dataclasses import asdict

from .executor import ExecutionLog

LANGS = ("python313", "cpp17", "java21", "c11")
KINDS = ("visible", "custom", "graded")
VERDICTS = (
    "accepted", "wrong_answer", "compile_error", "runtime_error", "time_limit", "memory_limit", "output_limit",
    "sandbox_error",
)
OUTCOMES = ("completed", "sandbox_error", "rejected")
LIMITS = ("none", "time", "memory", "output")
ERROR_CLASSES = (
    "none", "container_start_failed", "exec_failed", "restore_failed", "unsafe_configuration", "docker_unavailable",
    "internal",
)
REJECT_REASONS = ("unauthorized", "invalid_request", "too_large", "pack_mismatch", "not_accepting", "queue_full",
                  "degraded", "replay_conflict")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_logger = logging.getLogger("campus_ai_runner")


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.handlers[:] = [handler]
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def _enum(value: str, allowed: tuple[str, ...]) -> str:
    return value if value in allowed else "invalid"


def safe_request_id(value: str | None) -> str:
    return value if value and _REQ_ID.match(value) else "none"


def execution_line(log: ExecutionLog) -> str:
    d = asdict(log)
    clean = {
        "event": "execution",
        "execution_id": d["execution_id"] if _UUID.match(d["execution_id"]) else "invalid",
        "request_id": safe_request_id(d["request_id"]),
        "language": _enum(d["language"], LANGS),
        "kind": _enum(d["kind"], KINDS),
        "verdict": _enum(d["verdict"], VERDICTS),
        "outcome": _enum(d["outcome"], OUTCOMES),
        "limit_hit": _enum(d["limit_hit"], LIMITS),
        "error_class": _enum(d["error_class"], ERROR_CLASSES),
        "compile_ms": int(d["compile_ms"]),
        "run_ms": int(d["run_ms"]),
        "tests_run": int(d["tests_run"]),
        "pack_digest": d["pack_digest"] if _DIGEST.match(d["pack_digest"]) else "invalid",
    }
    return json.dumps(clean, sort_keys=True)


def log_execution(log: ExecutionLog) -> None:
    _logger.info(execution_line(log))


def log_event(event: str, reason: str = "none", count: int | None = None) -> None:
    """Fixed-vocabulary operational events (startup, self_check, reaper, rejected)."""
    if not re.match(r"^[a-z_]{1,40}$", event) or not re.match(r"^[a-z_]{1,60}$", reason):
        event, reason = "invalid_event", "none"
    payload: dict = {"event": event, "reason": reason}
    if count is not None:
        payload["count"] = int(count)
    _logger.info(json.dumps(payload, sort_keys=True))


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.executions: dict[tuple[str, str, str], int] = {}
        self.rejections: dict[str, int] = {}
        self.sandbox_errors: dict[str, int] = {}
        self.busy = 0
        self.queued = 0
        self.orphans = 0
        self.accepting = 0
        self.degraded = 0

    def record_execution(self, log: ExecutionLog) -> None:
        key = (_enum(log.language, LANGS), _enum(log.kind, KINDS), _enum(log.verdict, VERDICTS))
        with self._lock:
            self.executions[key] = self.executions.get(key, 0) + 1
            if log.outcome == "sandbox_error":
                cls = _enum(log.error_class, ERROR_CLASSES)
                self.sandbox_errors[cls] = self.sandbox_errors.get(cls, 0) + 1

    def record_rejection(self, reason: str) -> None:
        reason = _enum(reason, REJECT_REASONS)
        with self._lock:
            self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def render(self) -> str:
        with self._lock:
            lines = [
                "# TYPE runner_executions_total counter",
                *(
                    f'runner_executions_total{{language="{lang}",kind="{kind}",verdict="{v}"}} {n}'
                    for (lang, kind, v), n in sorted(self.executions.items())
                ),
                "# TYPE runner_rejections_total counter",
                *(f'runner_rejections_total{{reason="{r}"}} {n}' for r, n in sorted(self.rejections.items())),
                "# TYPE runner_sandbox_errors_total counter",
                *(f'runner_sandbox_errors_total{{class="{c}"}} {n}' for c, n in sorted(self.sandbox_errors.items())),
                "# TYPE runner_busy gauge",
                f"runner_busy {self.busy}",
                "# TYPE runner_queued gauge",
                f"runner_queued {self.queued}",
                "# TYPE runner_orphans gauge",
                f"runner_orphans {self.orphans}",
                "# TYPE runner_accepting gauge",
                f"runner_accepting {self.accepting}",
                "# TYPE runner_degraded gauge",
                f"runner_degraded {self.degraded}",
            ]
        return "\n".join(lines) + "\n"
