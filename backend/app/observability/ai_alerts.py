"""Throttled, background alerts for AI operations (Mock Interview Phase 7.5).

Wraps the existing ``alerts.emit_alert`` (structured ``campus.alert`` log line
+ optional ``ALERT_WEBHOOK_URL``):

  * throttled per (name, key): at most one alert per
    ``AI_ALERT_THROTTLE_SECONDS`` (process-local);
  * dispatched on a single background thread, so the webhook's network call
    (up to 3 s) never runs on a student's request;
  * never raises - an alerting outage cannot fail an interview.

Payloads are content-free by construction: callers pass a fixed message and
ids/counts/thresholds only (step, tenant id, percentages) - never interview
text, user names or e-mails. The webhook URL is never logged.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.core.config import settings
from app.observability import alerts

logger = logging.getLogger("campus_ai.ai_alerts")

_lock = threading.Lock()
_last_sent: dict[tuple[str, str], float] = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-alerts")
_clock: Callable[[], float] = time.monotonic


def _deliver(name: str, message: str, severity: str, fields: dict[str, Any]) -> None:
    try:
        alerts.emit_alert(name, message, severity=severity, **fields)
    except Exception as exc:  # noqa: BLE001 - emit_alert should not raise; be sure
        logger.warning("ai alert %s not delivered (%s)", name, type(exc).__name__)


def alert(
    name: str,
    message: str,
    *,
    severity: str = "warning",
    key: str | int | None = None,
    **fields: Any,
) -> bool:
    """Queue an alert unless one with the same (name, key) was sent recently.

    Returns True when queued. Never raises.
    """
    try:
        throttle_key = (name, "" if key is None else str(key))
        now = _clock()
        with _lock:
            last = _last_sent.get(throttle_key)
            if last is not None and now - last < settings.AI_ALERT_THROTTLE_SECONDS:
                return False
            _last_sent[throttle_key] = now
        _executor.submit(_deliver, name, message, severity, dict(fields))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai alert %s not queued (%s)", name, type(exc).__name__)
        return False


def flush(timeout: float = 5.0) -> None:
    """Test/ops hook: wait until queued alerts have been handed to emit_alert."""
    _executor.submit(lambda: None).result(timeout=timeout)


def _reset() -> None:
    """Test hook: forget throttle state."""
    with _lock:
        _last_sent.clear()
