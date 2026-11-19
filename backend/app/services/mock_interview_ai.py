"""AI-worker client for the mock interview engine (feature-flagged).

A separate module so ``ai_client.py`` stays untouched; it reuses
``ai_client._worker_request`` (shared token header, raise-for-status) but
overrides its retry policy: ONE retry, and only when the worker could not be
reached (cold start / connection refused). A 5xx or read timeout means an LLM
call may already have run, so it is not repeated (no double LLM spend).

Any failure is raised as ``InterviewEngineUnavailable``. Logs carry the
error class only, never resumes, job descriptions or answers.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt

from ai_client import _worker_request

logger = logging.getLogger("campus_ai.mock_interview")

_INTERVIEW_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class InterviewEngineUnavailable(RuntimeError):
    """The AI worker could not produce a result for this step."""


def _post(path: str, payload: dict) -> dict:
    try:
        resp = _worker_request.retry_with(
            stop=stop_after_attempt(2),
            retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
        )(
            "POST", path, json=payload, timeout=_INTERVIEW_TIMEOUT
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - normalized for the router
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        logger.warning(
            "mock interview worker call %s failed: %s (status=%s)",
            path,
            type(exc).__name__,
            status_code,
        )
        raise InterviewEngineUnavailable(path) from exc
    if not isinstance(data, dict):
        raise InterviewEngineUnavailable(path)
    return data


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
