"""AI-worker client for the mock interview engine (feature-flagged).

A separate module so ``ai_client.py`` stays untouched; it reuses
``ai_client._worker_request`` (shared token header, raise-for-status) but
overrides its retry policy: ONE retry, and only when the worker could not be
reached (cold start / connection refused). A 5xx or read timeout means an LLM
call may already have run, so it is not repeated (no double LLM spend).

Any failure is raised as ``InterviewEngineUnavailable``. Logs carry the
error class only, never resumes, job descriptions or answers.

Correlation (Phase 7.1): before each worker call a fresh invocation id is
created and sent as ``X-AI-Invocation-ID``, together with the current
request's ``X-Request-ID``. Both are fixed before the retry loop, so a retry
of the same call sends the same ids. The ids are random tokens and never
derived from interview content.

Operations (Phase 7.3-7.5): every call - success, fallback, failure, or a
circuit-breaker refusal - writes exactly one content-free row to the AI usage
ledger keyed by its invocation id (``ai_usage``; never raises), updates the
backend AI metrics, and reports its result to the circuit breaker
(``ai_circuit``). The ledger needs a ``ai_usage.usage_context`` set by the
caller; without one nothing is written.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt

from ai_client import _worker_request
from app.observability import ai_metrics
from app.observability.observability import (
    invocation_id_ctx,
    new_correlation_id,
    request_id_ctx,
    safe_correlation_id,
)
from app.services import ai_circuit, ai_usage

logger = logging.getLogger("campus_ai.mock_interview")

INVOCATION_HEADER = "X-AI-Invocation-ID"
REQUEST_ID_HEADER = "X-Request-ID"


def correlation_headers(invocation_id: str) -> dict[str, str]:
    """Headers for one worker call. Outside an HTTP request (no request id),
    the invocation id doubles as the request id so worker logs still join."""
    request_id = safe_correlation_id(request_id_ctx.get()) or invocation_id
    return {REQUEST_ID_HEADER: request_id, INVOCATION_HEADER: invocation_id}

_INTERVIEW_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# A report scores every answer (several LLM calls, partly in parallel).
_REPORT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class InterviewEngineUnavailable(RuntimeError):
    """The AI worker could not produce a result for this step.

    ``status_code``: the worker's HTTP status, if it answered.
    ``timed_out``: the worker may still be running the step (read timeout).
    ``circuit_open``: refused by the circuit breaker; no worker call was made.
    """

    def __init__(
        self,
        path: str,
        *,
        status_code: int | None = None,
        timed_out: bool = False,
        invocation_id: str | None = None,
        circuit_open: bool = False,
    ):
        super().__init__(path)
        self.status_code = status_code
        self.timed_out = timed_out
        self.invocation_id = invocation_id
        self.circuit_open = circuit_open


def _classify(exc: Exception) -> tuple[str, int | None]:
    """Ledger outcome + worker HTTP status for a failed call (class only)."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "unreachable", None
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", None
    if status_code == 502:
        return "provider_error", status_code  # the worker's LLM step failed
    return "worker_error", status_code


def _post(
    path: str,
    payload: dict,
    timeout: httpx.Timeout = _INTERVIEW_TIMEOUT,
    check: Callable[[dict], bool] | None = None,
    step: str | None = None,
) -> dict:
    """One worker call: circuit check, call, usage ledger, metrics (Phase 7.1-7.5).

    The retry policy, timeouts, payload and the data returned to the caller
    are unchanged; the worker's additive ``usage`` block is removed from the
    data and recorded in the ledger instead. ``step`` (Phase 8.3) names the
    ledger/breaker step; default = the last path segment, as before.
    """
    step = step or path.rsplit("/", 1)[-1]
    # Assigned BEFORE the call and outside the retry loop (Phase 7.1).
    invocation_id = new_correlation_id()
    token = invocation_id_ctx.set(invocation_id)
    started = time.perf_counter()
    outcome, http_status, error_class, usage = "worker_error", None, None, None
    called = False
    try:
        if not ai_circuit.allow(step):
            outcome, error_class = "circuit_open", "CircuitOpen"
            logger.warning("mock interview worker call %s refused: circuit open", path)
            raise InterviewEngineUnavailable(path, invocation_id=invocation_id, circuit_open=True)
        called = True
        try:
            resp = _worker_request.retry_with(
                stop=stop_after_attempt(2),
                retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
            )(
                "POST",
                path,
                json=payload,
                timeout=timeout,
                headers=correlation_headers(invocation_id),
            )
            http_status = resp.status_code
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - normalized for the router
            outcome, http_status = _classify(exc)
            error_class = type(exc).__name__
            # The JSON log line also carries request_id and invocation_id.
            logger.warning(
                "mock interview worker call %s failed: %s (status=%s)",
                path,
                error_class,
                http_status,
            )
            raise InterviewEngineUnavailable(
                path,
                status_code=http_status,
                timed_out=isinstance(exc, httpx.ReadTimeout),
                invocation_id=invocation_id,
            ) from exc
        if isinstance(data, dict):
            usage = data.pop("usage", None)  # Phase 7.2 block -> ledger only
        if not isinstance(data, dict) or (check is not None and not check(data)):
            error_class = "InvalidResponse"
            raise InterviewEngineUnavailable(path, invocation_id=invocation_id)
        fallback = isinstance(usage, dict) and usage.get("fallback_used") is True
        outcome = "fallback" if fallback else "ok"
        return data
    finally:
        seconds = time.perf_counter() - started
        if called:
            ai_circuit.record(step, success=not ai_circuit.is_failure(outcome, http_status))
        ai_metrics.observe_call(step, outcome, seconds)
        ai_usage.record_call(
            event_id=invocation_id,
            step=step,
            outcome=outcome,
            latency_ms=seconds * 1000,
            http_status=http_status,
            error_class=error_class,
            usage=usage,
            request_id=request_id_ctx.get(),
        )
        invocation_id_ctx.reset(token)


def plan_interview(payload: dict) -> dict:
    """POST /interview/plan -> {"plan": {...}, "provider": str}."""
    return _post("/interview/plan", payload, check=lambda d: isinstance(d.get("plan"), dict))


def interview_turn(payload: dict) -> dict:
    """POST /interview/turn -> {"action", "question", "evaluation", ...}."""
    return _post(
        "/interview/turn",
        payload,
        check=lambda d: d.get("action") in ("opening", "follow_up", "next_topic", "end"),
    )


def generate_report(payload: dict) -> dict:
    """POST /interview/report -> evidence-backed report (Phase 4)."""
    return _post(
        "/interview/report",
        payload,
        timeout=_REPORT_TIMEOUT,
        check=lambda d: isinstance(d.get("questions"), list),
    )


# --- Phase 8.3: coding review (review-only; code is read, never executed) ----
# Ledger steps (spec 6.5): code_hint, code_review, code_followup.


def coding_hint(payload: dict) -> dict:
    """POST /interview/coding/hint -> {"hint": str}."""
    return _post(
        "/interview/coding/hint",
        payload,
        check=lambda d: isinstance(d.get("hint"), str) and bool(d["hint"].strip()),
        step="code_hint",
    )


def coding_followup(payload: dict) -> dict:
    """POST /interview/coding/followup -> {"action": "ask"|"end", "question"}."""
    return _post(
        "/interview/coding/followup",
        payload,
        check=lambda d: d.get("action") in ("ask", "end"),
        step="code_followup",
    )


def jd_skills(payload: dict) -> dict:
    """Phase 8.4: POST /interview/jd/skills -> {"skills": [...]} (ledger step jd_skills)."""
    return _post(
        "/interview/jd/skills",
        payload,
        check=lambda d: isinstance(d.get("skills"), list),
        step="jd_skills",
    )


def coding_review(payload: dict) -> dict:
    """POST /interview/coding/review -> AI estimates with evidence."""
    return _post(
        "/interview/coding/review",
        payload,
        timeout=_REPORT_TIMEOUT,
        check=lambda d: d.get("rubric_version") == "coding-v1-unverified",
        step="code_review",
    )
