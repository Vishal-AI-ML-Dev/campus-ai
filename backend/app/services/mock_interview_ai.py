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
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt

from ai_client import _worker_request
from app.observability.observability import (
    invocation_id_ctx,
    new_correlation_id,
    request_id_ctx,
    safe_correlation_id,
)

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
    """

    def __init__(
        self,
        path: str,
        *,
        status_code: int | None = None,
        timed_out: bool = False,
        invocation_id: str | None = None,
    ):
        super().__init__(path)
        self.status_code = status_code
        self.timed_out = timed_out
        self.invocation_id = invocation_id


def _post(path: str, payload: dict, timeout: httpx.Timeout = _INTERVIEW_TIMEOUT) -> dict:
    # Assigned BEFORE the call and outside the retry loop (Phase 7.1).
    invocation_id = new_correlation_id()
    token = invocation_id_ctx.set(invocation_id)
    try:
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
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - normalized for the router
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            # The JSON log line also carries request_id and invocation_id.
            logger.warning(
                "mock interview worker call %s failed: %s (status=%s)",
                path,
                type(exc).__name__,
                status_code,
            )
            raise InterviewEngineUnavailable(
                path,
                status_code=status_code,
                timed_out=isinstance(exc, httpx.ReadTimeout),
                invocation_id=invocation_id,
            ) from exc
        if not isinstance(data, dict):
            raise InterviewEngineUnavailable(path, invocation_id=invocation_id)
        return data
    finally:
        invocation_id_ctx.reset(token)


def plan_interview(payload: dict) -> dict:
    """POST /interview/plan -> {"plan": {...}, "provider": str}."""
    data = _post("/interview/plan", payload)
    if not isinstance(data.get("plan"), dict):
        raise InterviewEngineUnavailable("/interview/plan")
    return data


def interview_turn(payload: dict) -> dict:
    """POST /interview/turn -> {"action", "question", "evaluation", ...}."""
    data = _post("/interview/turn", payload)
    if data.get("action") not in ("opening", "follow_up", "next_topic", "end"):
        raise InterviewEngineUnavailable("/interview/turn")
    return data


def generate_report(payload: dict) -> dict:
    """POST /interview/report -> evidence-backed report (Phase 4)."""
    data = _post("/interview/report", payload, timeout=_REPORT_TIMEOUT)
    if not isinstance(data.get("questions"), list):
        raise InterviewEngineUnavailable("/interview/report")
    return data
