"""Backend -> AI worker client (Simple HTTP integration).

Two kinds of calls:
  * proof scoring (skills/projects) -> runs in a BackgroundTask, fills `ai_score`
  * mentor chat -> runs synchronously in the request (the user awaits a reply)

We use our OWN database session for background work, because the request-scoped
session from `get_db` is already closed by the time a background task runs.

Resilience
----------
The AI worker lives on a separate service (e.g. a Hugging Face Space) that can
cold-start, blip, or briefly 5xx. Every outbound call now goes through
`_worker_request`, which retries transient failures (network / timeout / 5xx)
with exponential backoff (up to 3 attempts) via `tenacity`. A 4xx response is
NOT retried (it is deterministic - e.g. a 422 for a zero-face photo).
Best-effort callers still swallow the FINAL error and return None; the RAISING
callers (face ops) still propagate it so routes can map it to a clean response.

No message broker/queue yet; that's the next upgrade (Celery/RQ + Redis) for
bulk/retry/heavy workloads. The retry layer here is the interim safety net.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
from sqlalchemy import select
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.db import SessionLocal, set_current_tenant
from app.models import Project, ProjectMember, Skill

logger = logging.getLogger("campus_ai.ai_client")


def _worker_headers() -> dict[str, str]:
    """Send the shared secret to the worker when one is configured."""
    if settings.AI_WORKER_TOKEN:
        return {"X-Worker-Token": settings.AI_WORKER_TOKEN}
    return {}


# Scoring is one quick LLM round-trip; mentor chat can take a little longer.
_SCORE_TIMEOUT = httpx.Timeout(30.0)
_CHAT_TIMEOUT = httpx.Timeout(60.0)
# Streaming mentor chat: the connection stays open while tokens arrive, so we
# use a generous read timeout but a bounded connect timeout.
_STREAM_TIMEOUT = httpx.Timeout(120.0, connect=30.0)
# Resume drafting / ATS scoring are each a single (longer) LLM round-trip.
_RESUME_TIMEOUT = httpx.Timeout(90.0)
# Face ops; the FIRST call after a worker restart also downloads the
# InsightFace model (~300 MB), so we allow plenty of headroom.
_FACE_TIMEOUT = httpx.Timeout(180.0)

# Transport/timeout errors are always worth retrying (cold start, network blip).
_RETRYABLE_EXC = (httpx.TransportError, httpx.TimeoutException)


def _is_retryable_status(exc: BaseException) -> bool:
    """Retry only on server-side (5xx) HTTP errors; 4xx are deterministic."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code >= 500
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    retry=(
        retry_if_exception_type(_RETRYABLE_EXC)
        | retry_if_exception(_is_retryable_status)
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _worker_request(
    method: str,
    path: str,
    *,
    timeout: httpx.Timeout,
    json: dict | None = None,
) -> httpx.Response:
    """Make one retrying HTTP call to the AI worker and raise on 4xx/5xx.

    Transient failures (network, timeout, 5xx) are retried with exponential
    backoff; a 4xx raises immediately (no retry). Callers decide whether to
    swallow the final exception (best-effort) or let it propagate.
    """
    resp = httpx.request(
        method,
        f"{settings.AI_WORKER_URL}{path}",
        headers=_worker_headers(),
        json=json,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp


def _request_score(
    claim_type: str,
    title: str,
    evidence_url: str | None,
    evidence_note: str | None,
) -> int | None:
    """Call the worker's /score/proof. Returns the 0-100 score, or None on error.

    Failures are swallowed (logged only): a missing AI score must never break
    the user's create/claim flow - the mentor can still verify manually.
    """
    try:
        resp = _worker_request(
            "POST",
            "/score/proof",
            json={
                "claim_type": claim_type,
                "title": title,
                "evidence_url": evidence_url,
                "evidence_note": evidence_note,
            },
            timeout=_SCORE_TIMEOUT,
        )
        return int(resp.json()["score"])
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        logger.warning("AI worker scoring failed (%s): %s", title, exc)
        return None


def _tenant_job_session(job: str, row_id: int, tenant_id: int | None):
    """Open a job session stamped with the job's tenant, or None (fail closed).

    Background jobs run outside any request, so the RLS GUC is unset and every
    tenant-scoped row would be invisible. The enqueuing route passes the
    tenant from TRUSTED server-side data (the row it just wrote); it is set on
    this session so EVERY transaction (re-applied after commit by the
    after_begin listener) runs under that tenant. No tenant -> do nothing.
    """
    if not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id <= 0:
        logger.warning("%s %s skipped: missing tenant context", job, row_id)
        return None
    db = SessionLocal()
    set_current_tenant(db, tenant_id)
    return db


def score_skill(skill_id: int, tenant_id: int | None = None) -> None:
    """Background task: score one skill claim and persist its `ai_score`.

    ``tenant_id`` comes from the server (the claim's own tenant); without it,
    or if the row does not belong to it, the job does nothing (fail closed).
    """
    db = _tenant_job_session("score_skill", skill_id, tenant_id)
    if db is None:
        return
    try:
        skill = db.get(Skill, skill_id)
        if skill is None or skill.tenant_id != tenant_id:
            logger.warning("score_skill %s skipped: not found in tenant", skill_id)
            return
        score = _request_score(
            "skill", skill.name, skill.evidence_url, skill.evidence_note
        )
        if score is not None:
            skill.ai_score = score
            try:
                db.commit()
            except Exception as exc:  # noqa: BLE001 - background write must never crash the app
                db.rollback()
                logger.warning(
                    "Skipping AI score for skill %s "
                    "(row changed/deleted mid-scoring): %s",
                    skill_id,
                    exc,
                )
    finally:
        db.close()


def score_project_members(project_id: int, tenant_id: int | None = None) -> None:
    """Background task: score every member's contribution on a project.

    Each member is scored individually (with the project as context) so the
    per-member verification stays meaningful. Tenant handling as score_skill:
    server-supplied ``tenant_id`` required, row must belong to it.
    """
    db = _tenant_job_session("score_project_members", project_id, tenant_id)
    if db is None:
        return
    try:
        project = db.get(Project, project_id)
        if project is None or project.tenant_id != tenant_id:
            logger.warning(
                "score_project_members %s skipped: not found in tenant", project_id
            )
            return
        members = list(
            db.scalars(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.tenant_id == tenant_id,
                )
            )
        )
        for member in members:
            title = f"{project.title} - {member.contribution or 'contribution'}"
            score = _request_score(
                "project", title, project.repo_url, member.contribution
            )
            if score is not None:
                member.ai_score = score
        try:
            db.commit()
        except Exception as exc:  # noqa: BLE001 - background write must never crash the app
            db.rollback()
            logger.warning(
                "Skipping AI scores for project %s "
                "(rows changed/deleted mid-scoring): %s",
                project_id,
                exc,
            )
    finally:
        db.close()


def ask_mentor(
    profile: dict, question: str, history: list[dict]
) -> dict | None:
    """Synchronous mentor chat: send the verified profile + question to the
    worker and return its JSON reply ({answer, provider}), or None on error.
    """
    try:
        resp = _worker_request(
            "POST",
            "/mentor/chat",
            json={"profile": profile, "question": question, "history": history},
            timeout=_CHAT_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI mentor chat failed: %s", exc)
        return None


def stream_mentor(
    profile: dict, question: str, history: list[dict]
) -> Iterator[bytes]:
    """Proxy the worker's /mentor/chat/stream Server-Sent Events to the caller.

    Yields the worker's raw SSE bytes unchanged, so the token framing is
    preserved end to end. A stream cannot be safely retried mid-flight, so a
    connection/handshake failure is turned into a single SSE error event rather
    than raised - the frontend then shows it instead of hanging.
    """
    payload = {"profile": profile, "question": question, "history": history}
    try:
        with httpx.stream(
            "POST",
            f"{settings.AI_WORKER_URL}/mentor/chat/stream",
            headers=_worker_headers(),
            json=payload,
            timeout=_STREAM_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_raw():
                if chunk:
                    yield chunk
    except Exception as exc:  # noqa: BLE001 - surface as an SSE error, never fatal
        logger.warning("AI mentor stream failed: %s", exc)
        event = {"type": "error", "detail": "AI mentor is unavailable right now."}
        yield f"data: {json.dumps(event)}\n\n".encode()


def generate_resume(profile: dict) -> dict | None:
    """Synchronous: send the verified profile to the worker's /resume/draft and
    return its JSON reply ({markdown, provider}), or None on error.
    """
    try:
        resp = _worker_request(
            "POST",
            "/resume/draft",
            json={"profile": profile},
            timeout=_RESUME_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI resume draft failed: %s", exc)
        return None


def score_resume_ats(resume_text: str, job_description: str) -> dict | None:
    """Synchronous: score a resume against a job description via the worker's
    /resume/ats-score. Returns the JSON reply, or None on error.
    """
    try:
        resp = _worker_request(
            "POST",
            "/resume/ats-score",
            json={"resume_text": resume_text, "job_description": job_description},
            timeout=_RESUME_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI ATS scoring failed: %s", exc)
        return None


def enroll_face(student_id: int, image_base64: str, *, tenant_id: int) -> dict:
    """Enroll a student's reference face via the worker's /face/enroll.

    ``tenant_id`` is REQUIRED and is stored on the vector-store point so that
    matching can be filtered to one institute (the worker rejects requests
    without it). Callers must have already verified the student belongs to
    ``tenant_id``.

    Returns the worker's JSON ({student_id, enrolled, det_score, message}).
    Unlike the background scorers, this RAISES on transport/HTTP errors so the
    caller can map them to a clean response (e.g. a 422 when the photo has zero
    or multiple faces) instead of silently swallowing them.
    """
    resp = _worker_request(
        "POST",
        "/face/enroll",
        json={
            "student_id": student_id,
            "tenant_id": tenant_id,
            "image_base64": image_base64,
        },
        timeout=_FACE_TIMEOUT,
    )
    return resp.json()


def delete_face_enrollment(student_id: int, *, tenant_id: int) -> None:
    """Remove a student's enrolled face from the worker (and thus Qdrant).

    The worker deletes only a point matching BOTH the student and the tenant.
    Callers must have already verified the student belongs to ``tenant_id``.
    """
    _worker_request(
        "DELETE",
        f"/face/enroll/{int(student_id)}?tenant_id={int(tenant_id)}",
        timeout=_SCORE_TIMEOUT,
    )


def match_faces(
    image_base64: str, *, tenant_id: int, score_threshold: float | None = None
) -> dict:
    """Match every face in a class photo against ONE institute's enrollments.

    ``tenant_id`` is REQUIRED; the worker filters candidate embeddings to that
    tenant. The backend must still treat the result as untrusted and keep only
    its own tenant's students (defence in depth).

    Returns the worker's JSON ({detected_faces, matched[], unmatched_faces,
    threshold}). Raises on transport/HTTP errors so the caller can map them.
    """
    body: dict = {"image_base64": image_base64, "tenant_id": tenant_id}
    if score_threshold is not None:
        body["score_threshold"] = score_threshold
    resp = _worker_request(
        "POST",
        "/face/match",
        json=body,
        timeout=_FACE_TIMEOUT,
    )
    return resp.json()
