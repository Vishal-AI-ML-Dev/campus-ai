"""Circuit breaker for interview AI calls (Mock Interview Phase 7.5, decision P7-D4).

ON by default - the approved exception to the default-OFF rule - because it
only ever SHORTENS a failure the student would get anyway (an immediate 503
with the usual "nothing you typed was lost" message instead of a 60-120 s
wait) and never blocks a healthy worker. Inert unless BOTH
``AI_MOCK_INTERVIEW_ENABLED`` and ``AI_CIRCUIT_ENABLED`` are true.

One breaker per step (``plan`` / ``turn`` / ``report``), per backend process:

  closed    calls pass. A failure is recorded in a sliding window
            (``AI_CIRCUIT_WINDOW_SECONDS``). The breaker OPENS after
            ``AI_CIRCUIT_FAILURE_THRESHOLD`` consecutive failures, or when at
            least ``AI_CIRCUIT_MIN_CALLS`` calls in the window have an error
            rate >= ``AI_CIRCUIT_ERROR_RATE``.
  open      calls are refused (no worker call, ledger outcome
            ``circuit_open``) for ``AI_CIRCUIT_OPEN_SECONDS``.
  half_open after that, exactly ONE trial call is let through; other calls
            are refused while it runs. Success -> closed (window reset);
            failure -> open again. A trial that never reports back (crashed
            request) is abandoned after the open period + the longest worker
            timeout, and a new trial is allowed.

Failures: worker unreachable, timeout, provider error (worker 502) and other
worker 5xx. A 4xx (e.g. a report the model could not assess) means the worker
is healthy and counts as success. No call is retried by the breaker; there is
no backup provider (P7-D6).

State is in-process: with several backend processes each opens on its own
(documented limitation; it can only shorten failures, never cause them).
Transitions emit content-free, throttled alerts (``ai_alerts``).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from app.core.config import settings
from app.observability import ai_alerts, ai_metrics

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
# Longest worker call (report: 120 s read + 10 s connect, one connect retry).
_MAX_CALL_SECONDS = 150.0

_clock: Callable[[], float] = time.monotonic


def active() -> bool:
    # Phase 10.3: also guards the governed mentor/resume/ATS/scoring calls
    # (steps mentor_chat, mentor_stream, resume_draft, ats_score, proof_score)
    # once AI_GOVERNANCE_ENABLED is on. Unchanged while both flags are off.
    return bool(
        (settings.AI_MOCK_INTERVIEW_ENABLED or settings.AI_GOVERNANCE_ENABLED)
        and settings.AI_CIRCUIT_ENABLED
    )


class _Breaker:
    def __init__(self, step: str) -> None:
        self.step = step
        self.lock = threading.Lock()
        self.state = CLOSED
        self.consecutive = 0
        self.window: deque[tuple[float, bool]] = deque()
        self.opened_at = 0.0
        self.trial_started: float | None = None

    def _prune(self, now: float) -> None:
        horizon = now - settings.AI_CIRCUIT_WINDOW_SECONDS
        while self.window and self.window[0][0] < horizon:
            self.window.popleft()

    def _set(self, state: str) -> None:
        self.state = state
        ai_metrics.circuit_state(self.step, state)

    def allow(self) -> bool:
        with self.lock:
            now = _clock()
            if self.state == CLOSED:
                return True
            if self.state == OPEN:
                if now - self.opened_at < settings.AI_CIRCUIT_OPEN_SECONDS:
                    return False
                self._set(HALF_OPEN)
                self.trial_started = now
                return True
            # HALF_OPEN: one trial at a time; replace a trial that never reported.
            stale = settings.AI_CIRCUIT_OPEN_SECONDS + _MAX_CALL_SECONDS
            if self.trial_started is not None and now - self.trial_started < stale:
                return False
            self.trial_started = now
            return True

    def record(self, success: bool) -> None:
        opened = recovered = False
        with self.lock:
            now = _clock()
            if self.state == HALF_OPEN:
                self.trial_started = None
                if success:
                    self.window.clear()
                    self.consecutive = 0
                    self._set(CLOSED)
                    recovered = True
                else:
                    self.opened_at = now
                    self._set(OPEN)
            elif self.state == OPEN:
                return  # a straggler that started before the breaker opened
            else:
                self.window.append((now, success))
                self._prune(now)
                self.consecutive = 0 if success else self.consecutive + 1
                failures = sum(1 for _, ok in self.window if not ok)
                calls = len(self.window)
                if not success and (
                    self.consecutive >= settings.AI_CIRCUIT_FAILURE_THRESHOLD
                    or (
                        calls >= settings.AI_CIRCUIT_MIN_CALLS
                        and failures / calls >= settings.AI_CIRCUIT_ERROR_RATE
                    )
                ):
                    self.opened_at = now
                    self._set(OPEN)
                    opened = True
            snapshot = (self.consecutive, len(self.window))
        if opened:
            ai_alerts.alert(
                "ai_circuit_open",
                f"AI circuit opened for AI step '{self.step}'; new {self.step} calls "
                f"get an immediate 503 for {settings.AI_CIRCUIT_OPEN_SECONDS}s.",
                severity="error",
                key=self.step,
                step=self.step,
                consecutive_failures=snapshot[0],
                window_calls=snapshot[1],
            )
        if recovered:
            ai_alerts.alert(
                "ai_circuit_closed",
                f"AI circuit closed again for AI step '{self.step}' (trial call succeeded).",
                severity="warning",
                key=self.step,
                step=self.step,
            )


_breakers: dict[str, _Breaker] = {}
_registry_lock = threading.Lock()


def _breaker(step: str) -> _Breaker:
    with _registry_lock:
        b = _breakers.get(step)
        if b is None:
            b = _breakers[step] = _Breaker(step)
        return b


def allow(step: str) -> bool:
    """May a call for ``step`` go to the worker now? Always True when inactive."""
    if not active():
        return True
    return _breaker(step).allow()


def would_refuse(step: str) -> bool:
    """Read-only: would ``allow(step)`` refuse a call right now?

    For route prechecks that must refuse BEFORE writing anything. Unlike
    ``allow`` it never changes state: it does not move an expired OPEN breaker
    to HALF_OPEN, never starts or replaces a half-open trial, and never creates
    a breaker. Always False when inactive or for a step with no breaker yet.

    Advisory only: the state can change between this check and the later
    ``allow`` call, which stays the authoritative gate.
    """
    if not active():
        return False
    with _registry_lock:
        b = _breakers.get(step)
    if b is None:
        return False
    with b.lock:
        now = _clock()
        if b.state == OPEN:
            return now - b.opened_at < settings.AI_CIRCUIT_OPEN_SECONDS
        if b.state == HALF_OPEN:
            stale = settings.AI_CIRCUIT_OPEN_SECONDS + _MAX_CALL_SECONDS
            return b.trial_started is not None and now - b.trial_started < stale
        return False


def record(step: str, success: bool) -> None:
    """Report the result of a call that ``allow`` let through."""
    if not active():
        return
    _breaker(step).record(success)


def is_failure(outcome: str, http_status: int | None) -> bool:
    """Does this ledger outcome count against the breaker?"""
    if outcome in ("unreachable", "timeout", "provider_error"):
        return True
    if outcome == "worker_error":
        return http_status is None or http_status >= 500
    return False


def snapshot() -> dict[str, dict]:
    """Per-step state for the admin health view (process-local)."""
    now = _clock()
    out: dict[str, dict] = {}
    with _registry_lock:
        breakers = list(_breakers.values())
    for b in breakers:
        with b.lock:
            retry_in = None
            if b.state == OPEN:
                retry_in = max(0, int(settings.AI_CIRCUIT_OPEN_SECONDS - (now - b.opened_at)))
            out[b.step] = {
                "state": b.state,
                "consecutive_failures": b.consecutive,
                "retry_in_seconds": retry_in,
            }
    return out


def _reset() -> None:
    """Test hook: forget every breaker."""
    with _registry_lock:
        _breakers.clear()
