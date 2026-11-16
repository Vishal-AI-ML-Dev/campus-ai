"""Readiness checks: is the service actually able to serve traffic?

Liveness vs readiness
---------------------
* ``/health`` (in main.py) is LIVENESS: "the process is up". Dependency-free
  and fast - an orchestrator uses it to decide whether to RESTART the
  container.
* ``/health/ready`` (here) is READINESS: "the process can serve real traffic",
  i.e. its REQUIRED dependencies are reachable. Used to decide whether to SEND
  traffic.

Policy
------
* Postgres is REQUIRED: if the DB is unreachable the service can't do useful
  work, so readiness fails (HTTP 503) and an alert is emitted (alerts.py).
* Redis is OPTIONAL (cache + rate-limit degrade to an in-process no-op), so it
  is reported for visibility but never fails readiness.

Location:
    E:\\campus-ai\\backend\\health.py
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text

import app.core.cache as cache
from app.observability.alerts import emit_alert
from app.core.db import engine

_log = logging.getLogger("campus-ai")


def _check_database() -> dict:
    """REQUIRED dependency: open a connection and run SELECT 1."""
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "required": True,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        # Public endpoint: report only the error CLASS. The message can carry
        # host/port/role names; it goes to the server log instead (Phase 6).
        _log.warning("readiness: database check failed: %s", exc)
        return {"status": "down", "required": True, "error": type(exc).__name__}


def _check_redis() -> dict:
    """OPTIONAL dependency: report Redis reachability without failing readiness."""
    if not cache.enabled():
        return {"status": "disabled", "required": False}
    start = time.perf_counter()
    try:
        cache.ping()
        return {
            "status": "ok",
            "required": False,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - optional dep, never fatal
        _log.warning("readiness: redis check failed: %s", exc)
        return {"status": "down", "required": False, "error": type(exc).__name__}


def readiness() -> tuple[dict, bool]:
    """Run all checks. Returns (report, ready); ready is False iff a REQUIRED
    dependency is down. Emits an alert on failure."""
    checks = {
        "database": _check_database(),
        "redis": _check_redis(),
    }
    ready = all(
        c["status"] == "ok"
        for c in checks.values()
        if c.get("required")
    )
    report = {"status": "ready" if ready else "not_ready", "checks": checks}
    if not ready:
        down = [
            name
            for name, c in checks.items()
            if c.get("required") and c["status"] != "ok"
        ]
        emit_alert(
            "readiness_failed",
            f"Readiness check failed: {', '.join(down)} unreachable.",
            severity="critical",
            components=down,
        )
    return report, ready
