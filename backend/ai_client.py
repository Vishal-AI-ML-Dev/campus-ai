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

Phase 10.3 (AI governance): while ``AI_GOVERNANCE_ENABLED`` is on, mentor,
mentor stream, resume, ATS and proof scoring go through ``_governed_call``
instead: kill switches, caps, concurrency and quota cooldown
(``app.services.ai_governance``), ONE retry only when the worker could not be
reached (a 5xx or read timeout may already have spent an LLM call, so it is
never repeated), a content-free usage-ledger row per call, the circuit breaker
and Prometheus counters. The kill switches apply even with governance off.
Refusals raise ``AIRefused`` (mapped to 429/503 by main.py).

No message broker/queue yet; that's the next upgrade (Celery/RQ + Redis) for
bulk/retry/heavy workloads. The retry layer here is the interim safety net.
"""

from __future__ import annotations

import json
import logging
import time
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
from app.observability import ai_metrics
from app.observability.observability import (
    invocation_id_ctx,
    new_correlation_id,
    request_id_ctx,
    safe_correlation_id,
)
from app.services import ai_circuit, ai_governance, ai_usage
from app.services.ai_governance import AIRefused

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
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Make one retrying HTTP call to the AI worker and raise on 4xx/5xx.

    Transient failures (network, timeout, 5xx) are retried with exponential
    backoff; a 4xx raises immediately (no retry). Callers decide whether to
    swallow the final exception (best-effort) or let it propagate.

    ``headers`` (optional, Phase 7.1): extra request headers such as
    correlation ids. They are fixed for the whole call, so every retry sends
    the same values; the worker token always wins over a same-named entry.
    """
    resp = httpx.request(
        method,
        f"{settings.AI_WORKER_URL}{path}",
        headers={**(headers or {}), **_worker_headers()},
        json=json,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp


# --------------------------------------------------------------------------
# Phase 10.3: governed calls (mentor, resume, ATS, proof scoring)
# --------------------------------------------------------------------------

# One retry, only when the worker could not be reached (no LLM call happened).
_CONNECT_ONLY = (httpx.ConnectError, httpx.ConnectTimeout)


def _governed() -> bool:
    return ai_governance.enabled()


def _who(tenant_id: int | None, user_id: int | None) -> tuple[int | None, int | None]:
    """Explicit ids win; otherwise the route's ``ai_governance.caller`` block."""
    if tenant_id is not None:
        return tenant_id, user_id
    return ai_governance.current_caller()


def _correlation_headers(invocation_id: str) -> dict[str, str]:
    request_id = safe_correlation_id(request_id_ctx.get()) or invocation_id
    return {"X-Request-ID": request_id, "X-AI-Invocation-ID": invocation_id}


def _classify(exc: Exception) -> tuple[str, int | None]:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(exc, _CONNECT_ONLY):
        return "unreachable", None
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", None
    if status_code in (429, 502):
        return "provider_error", status_code
    return "worker_error", status_code


class _CallRecord:
    """Bookkeeping for one governed call (ledger, metrics, breaker, tokens).

    The ledger context is passed explicitly (not via a context variable): a
    streaming generator runs each step in a different context copy.
    """

    def __init__(self, ctx: ai_usage.UsageContext, feature: str, step: str) -> None:
        self.ctx = ctx
        self.feature = feature
        self.step = step
        self.invocation_id = new_correlation_id()
        self.started = time.perf_counter()
        self.outcome = "worker_error"
        self.http_status: int | None = None
        self.error_class: str | None = None
        self.usage: dict | None = None
        self.called = False

    def fail(self, exc: Exception) -> None:
        self.outcome, self.http_status = _classify(exc)
        self.error_class = type(exc).__name__
        if ai_governance.is_quota_signal(self.http_status, None):
            self.error_class = "ProviderQuotaExhausted"

    def finish(self) -> None:
        seconds = time.perf_counter() - self.started
        if self.called and self.outcome != "cancelled":
            ai_circuit.record(
                self.step, success=not ai_circuit.is_failure(self.outcome, self.http_status)
            )
        if ai_governance.is_quota_signal(self.http_status, self.usage):
            ai_governance.mark_provider_quota_exhausted(self.feature)
        ai_governance.add_platform_tokens(ai_governance.usage_tokens(self.usage))
        ai_metrics.feature_call(self.feature, self.outcome)
        _ledger(self.ctx, event_id=self.invocation_id, step=self.step, outcome=self.outcome,
                latency_ms=seconds * 1000, http_status=self.http_status,
                error_class=self.error_class, usage=self.usage)


def _ledger(ctx: ai_usage.UsageContext, **fields) -> None:
    """Write one content-free ledger row; never raises (ai_usage.record)."""
    try:
        ai_usage.record(ai_usage.build_event(ctx=ctx, request_id=request_id_ctx.get(), **fields))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai usage: event not built (%s)", type(exc).__name__)


def _usage_ctx(feature: str, tenant_id: int, user_id: int | None,
               subject_type: str | None = None) -> ai_usage.UsageContext:
    return ai_usage.UsageContext(tenant_id=int(tenant_id), user_id=user_id, feature=feature,
                                 subject_type=subject_type)


def _refusal_record(ctx: ai_usage.UsageContext, step: str, refusal: AIRefused) -> None:
    """Content-free ledger row for a call refused before the worker."""
    _ledger(ctx, event_id=new_correlation_id(), step=step, outcome="quota_blocked",
            latency_ms=0, error_class=refusal.reason)


def _governed_call(
    feature: str,
    step: str,
    path: str,
    payload: dict,
    timeout: httpx.Timeout,
    *,
    tenant_id: int | None,
    user_id: int | None,
    subject_type: str | None = None,
) -> dict | None:
    """One governed worker call. Returns the JSON reply or None on failure.

    Raises ``AIRefused`` when governance refuses the call (no worker call made)
    or the provider reports its quota as exhausted.
    """
    if tenant_id is None:
        # No institute context: the ledger and institute caps cannot apply.
        # Fail closed rather than run an ungoverned call.
        raise AIRefused(feature, "platform_unavailable", retry_after=60)
    usage_ctx = _usage_ctx(feature, tenant_id, user_id, subject_type)
    admission = ai_governance.admit(feature, tenant_id=tenant_id, user_id=user_id)
    try:
        admission.__enter__()
    except AIRefused as refusal:
        _refusal_record(usage_ctx, step, refusal)
        raise
    rec = _CallRecord(usage_ctx, feature, step)
    token = invocation_id_ctx.set(rec.invocation_id)
    try:
        if not ai_circuit.allow(step):
            rec.outcome, rec.error_class = "circuit_open", "CircuitOpen"
            ai_governance.refund_platform_request()  # no provider call was made
            raise AIRefused(feature, "circuit_open", retry_after=60)
        rec.called = True
        try:
            resp = _worker_request.retry_with(
                stop=stop_after_attempt(2),
                retry=retry_if_exception_type(_CONNECT_ONLY),
            )(
                "POST", path, json=payload, timeout=timeout,
                headers=_correlation_headers(rec.invocation_id),
            )
            rec.http_status = resp.status_code
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - normalized below
            rec.fail(exc)
            logger.warning("AI %s call failed: %s (status=%s)", step, rec.error_class, rec.http_status)
            if ai_governance.is_quota_signal(rec.http_status, None):
                raise AIRefused(feature, "provider_quota",
                                retry_after=settings.AI_QUOTA_COOLDOWN_SECONDS) from exc
            return None
        if isinstance(data, dict):
            usage = data.pop("usage", None)
            rec.usage = usage if isinstance(usage, dict) else None
            rec.outcome = "ok"
            return data
        rec.error_class = "InvalidResponse"
        return None
    finally:
        rec.finish()
        invocation_id_ctx.reset(token)
        admission.__exit__(None, None, None)


def _sse_error(detail: str, code: str) -> bytes:
    event = {"type": "error", "detail": detail, "code": code}
    return f"data: {json.dumps(event)}\n\n".encode()


def _is_quota_error_frame(frame: bytes) -> bool:
    """True only for an SSE frame ``data: {"type": "error", "code": "quota_exhausted", ...}``."""
    for line in frame.split(b"\n"):
        if not line.startswith(b"data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            return False
        return (
            isinstance(event, dict)
            and event.get("type") == "error"
            and event.get("code") == "quota_exhausted"
        )
    return False


def _governed_stream(
    feature: str,
    step: str,
    path: str,
    payload: dict,
    *,
    tenant_id: int,
    user_id: int | None,
) -> Iterator[bytes]:
    """Governed SSE proxy. Admission errors become one SSE error event.

    Closing the generator (client disconnected) closes the upstream stream,
    releases the concurrency slot and records outcome ``cancelled``.
    """
    usage_ctx = _usage_ctx(feature, tenant_id, user_id)
    admission = ai_governance.admit(feature, tenant_id=tenant_id, user_id=user_id)
    try:
        admission.__enter__()
    except AIRefused as refusal:
        _refusal_record(usage_ctx, step, refusal)
        yield _sse_error(refusal.message, refusal.reason)
        return
    rec = _CallRecord(usage_ctx, feature, step)
    unavailable = "AI mentor is unavailable right now."
    try:
        if not ai_circuit.allow(step):
            rec.outcome, rec.error_class = "circuit_open", "CircuitOpen"
            ai_governance.refund_platform_request()  # no provider call was made
            yield _sse_error(unavailable, "circuit_open")
            return
        rec.called = True
        try:
            with httpx.stream(
                "POST",
                f"{settings.AI_WORKER_URL}{path}",
                headers={**_correlation_headers(rec.invocation_id), **_worker_headers()},
                json=payload,
                timeout=_STREAM_TIMEOUT,
            ) as resp:
                rec.http_status = resp.status_code
                resp.raise_for_status()
                buffer = b""
                for chunk in resp.iter_raw():
                    if chunk:
                        # Only a worker ERROR event with code quota_exhausted
                        # counts - never text inside an answer (a student could
                        # otherwise make the model print the word and trigger a
                        # platform-wide cooldown).
                        buffer += chunk
                        while b"\n\n" in buffer:
                            frame, buffer = buffer.split(b"\n\n", 1)
                            if _is_quota_error_frame(frame):
                                rec.usage = {"quota_exhausted": True}
                        yield chunk
            rec.outcome = "ok"
            if rec.usage and rec.usage.get("quota_exhausted"):
                rec.outcome, rec.error_class = "provider_error", "ProviderQuotaExhausted"
        except GeneratorExit:
            rec.outcome, rec.error_class = "cancelled", "ClientDisconnected"
            raise
        except Exception as exc:  # noqa: BLE001 - surface as one SSE error
            rec.fail(exc)
            logger.warning("AI %s stream failed: %s", step, rec.error_class)
            quota = ai_governance.is_quota_signal(rec.http_status, None)
            yield _sse_error(unavailable, "quota_exhausted" if quota else "unavailable")
    finally:
        rec.finish()
        admission.__exit__(None, None, None)


def _request_score(
    claim_type: str,
    title: str,
    evidence_url: str | None,
    evidence_note: str | None,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> int | None:
    """Call the worker's /score/proof. Returns the 0-100 score, or None on error.

    Failures are swallowed (logged only): a missing AI score must never break
    the user's create/claim flow - the mentor can still verify manually.
    Phase 10.3: a governance refusal (kill switch, cap, cooldown) is also just
    "no score" - the claim stays pending manual verification.
    """
    payload = {
        "claim_type": claim_type,
        "title": title,
        "evidence_url": evidence_url,
        "evidence_note": evidence_note,
    }
    try:
        ai_governance.check_switches(ai_governance.FEATURE_PROOF_SCORING)
        if _governed():
            data = _governed_call(
                ai_governance.FEATURE_PROOF_SCORING, "proof_score", "/score/proof", payload,
                _SCORE_TIMEOUT, tenant_id=tenant_id, user_id=user_id, subject_type=claim_type,
            )
            return None if data is None else int(data["score"])
    except AIRefused as refusal:
        logger.info("AI scoring skipped (%s)", refusal.reason)
        return None
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        logger.warning("AI worker scoring failed: %s", type(exc).__name__)
        return None
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
            "skill", skill.name, skill.evidence_url, skill.evidence_note,
            tenant_id=tenant_id, user_id=skill.student_id,
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
                "project", title, project.repo_url, member.contribution,
                tenant_id=tenant_id, user_id=member.student_id,
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
    profile: dict,
    question: str,
    history: list[dict],
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> dict | None:
    """Synchronous mentor chat: send the verified profile + question to the
    worker and return its JSON reply ({answer, provider}), or None on error.

    Phase 10.3: raises ``AIRefused`` when a kill switch or governance refuses.
    """
    ai_governance.check_switches(ai_governance.FEATURE_MENTOR)
    if _governed():
        tenant_id, user_id = _who(tenant_id, user_id)
        return _governed_call(
            ai_governance.FEATURE_MENTOR, "mentor_chat", "/mentor/chat",
            {"profile": profile, "question": question, "history": history},
            _CHAT_TIMEOUT, tenant_id=tenant_id, user_id=user_id,
        )
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
    profile: dict,
    question: str,
    history: list[dict],
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> Iterator[bytes]:
    """Proxy the worker's /mentor/chat/stream Server-Sent Events to the caller.

    Yields the worker's raw SSE bytes unchanged, so the token framing is
    preserved end to end. A stream cannot be safely retried mid-flight, so a
    connection/handshake failure is turned into a single SSE error event rather
    than raised - the frontend then shows it instead of hanging.
    """
    payload = {"profile": profile, "question": question, "history": history}
    try:
        ai_governance.check_switches(ai_governance.FEATURE_MENTOR)
    except AIRefused as refusal:
        event = {"type": "error", "detail": refusal.message, "code": refusal.reason}
        yield f"data: {json.dumps(event)}\n\n".encode()
        return
    if _governed():
        if tenant_id is None:
            event = {"type": "error", "detail": "AI mentor is unavailable right now.",
                     "code": "platform_unavailable"}
            yield f"data: {json.dumps(event)}\n\n".encode()
            return
        yield from _governed_stream(
            ai_governance.FEATURE_MENTOR, "mentor_stream", "/mentor/chat/stream", payload,
            tenant_id=tenant_id, user_id=user_id,
        )
        return
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


def generate_resume(
    profile: dict, *, tenant_id: int | None = None, user_id: int | None = None
) -> dict | None:
    """Synchronous: send the verified profile to the worker's /resume/draft and
    return its JSON reply ({markdown, provider}), or None on error.

    Phase 10.3: raises ``AIRefused`` when a kill switch or governance refuses.
    """
    ai_governance.check_switches(ai_governance.FEATURE_RESUME)
    if _governed():
        tenant_id, user_id = _who(tenant_id, user_id)
        return _governed_call(
            ai_governance.FEATURE_RESUME, "resume_draft", "/resume/draft", {"profile": profile},
            _RESUME_TIMEOUT, tenant_id=tenant_id, user_id=user_id,
        )
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


def score_resume_ats(
    resume_text: str,
    job_description: str,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> dict | None:
    """Synchronous: score a resume against a job description via the worker's
    /resume/ats-score. Returns the JSON reply, or None on error.

    Phase 10.3: raises ``AIRefused`` when a kill switch or governance refuses.
    """
    ai_governance.check_switches(ai_governance.FEATURE_ATS)
    if _governed():
        tenant_id, user_id = _who(tenant_id, user_id)
        return _governed_call(
            ai_governance.FEATURE_ATS, "ats_score", "/resume/ats-score",
            {"resume_text": resume_text, "job_description": job_description},
            _RESUME_TIMEOUT, tenant_id=tenant_id, user_id=user_id,
        )
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
