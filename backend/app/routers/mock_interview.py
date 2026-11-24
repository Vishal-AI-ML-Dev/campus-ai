"""AI Mock Interview endpoints (Placement Preparation). Feature-flagged.

Phase 1 ships only the isolation layer:

  * GET /mock-interview/status  -> {"enabled": bool} for the signed-in user.
    Always available (authenticated) so the frontend can decide whether to
    show the feature; it never reveals anything else.
  * GET /mock-interview/catalog -> static setup choices. Gated: 404 while the
    feature is off for the caller's institute.

Phase 3 adds the text interview (HR / technical, optional resume + JD
grounding), students only. Phase 4 adds the evidence-backed report
(``/sessions/{id}/report``) and the history list (``/history``). Business logic lives in
``app.services.mock_interview``; see docs/mock-interview/API.md.

Every mock-interview route except /status depends on ``require_mock_interview``
so it disappears (404) whenever the flag is off.

Flags (app/core/config.py, both default OFF / empty):
  AI_MOCK_INTERVIEW_ENABLED     master switch
  AI_MOCK_INTERVIEW_TENANT_IDS  optional comma-separated pilot allow-list
  AI_MOCK_INTERVIEW_VOICE_ENABLED  Phase 5 sub-flag: offer voice interviews
  AI_MOCK_INTERVIEW_VIDEO_ENABLED  Phase 6 sub-flag: offer video interviews
  AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS  Phase 6 video allow-list (empty = nobody)
  AI_MOCK_INTERVIEW_CODING_ENABLED  Phase 8.1 sub-flag: offer coding rounds
  AI_MOCK_INTERVIEW_CODING_TENANT_IDS  Phase 8.1 coding allow-list (empty = nobody)

Phase 8.1: coding sessions use the coding routes (``mock_interview_coding.py``);
the HR/technical question and report routes answer them with a documented 409
until coding feedback arrives (8.3).

Mounted under the `/mock-interview` prefix by `main.py` (at /v1 and the
deprecated unversioned path, like every feature router).
"""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import client_ip, limiter
from app.core.security import ACCESS_COOKIE_NAME, apply_tenant_guc, get_current_user
from app.models import User, UserRole
from app.schemas.mock_interview import (
    MockInterviewAnswerIn,
    MockInterviewCatalogOut,
    MockInterviewChoice,
    MockInterviewHistoryItemOut,
    MockInterviewOptionsOut,
    MockInterviewReportOut,
    MockInterviewResumeOption,
    MockInterviewSessionCreate,
    MockInterviewSessionOut,
    MockInterviewSessionSummaryOut,
    MockInterviewStatusOut,
)
from app.schemas.ai_operations import UsageMeOut
from app.services import mock_interview as svc
from app.services import mock_interview_coding as coding
from app.services import mock_interview_policy as policy
from app.services import mock_interview_report as report_svc

router = APIRouter(
    prefix="/mock-interview",
    tags=["mock-interview"],
    # Same tenant/RLS stamping as every tenant-scoped router.
    dependencies=[Depends(apply_tenant_guc)],
)

# Roles that may use the module. Recruiters (external company users) never can.
_ALLOWED_ROLES = frozenset(
    {UserRole.student, UserRole.teacher, UserRole.tpo, UserRole.admin}
)


def _allowed_tenant_ids(raw: str) -> frozenset[int] | None:
    """Parse the pilot allow-list. None = no restriction (all tenants).

    Fail closed: a non-empty value with no valid integer enables nobody.
    """
    if not raw.strip():
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        # ASCII 0-9 only: str.isdigit() also accepts e.g. "²", which int()
        # rejects, and that would turn every request into a 500.
        if part.isascii() and part.isdecimal():
            ids.add(int(part))
    return frozenset(ids)


def mock_interview_enabled_for(user: User) -> bool:
    """True when the feature is on for this user's institute and role.

    Reads settings at call time so the flag can be flipped (or patched in
    tests) without re-importing anything.
    """
    if not settings.AI_MOCK_INTERVIEW_ENABLED:
        return False
    if user.role not in _ALLOWED_ROLES:
        return False
    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_TENANT_IDS)
    return allowed is None or user.tenant_id in allowed


def mock_interview_voice_enabled() -> bool:
    """Phase 5 voice sub-flag. Only consulted behind the main gate, so the
    master switch and tenant allow-list always apply too."""
    return bool(settings.AI_MOCK_INTERVIEW_VOICE_ENABLED)


def mock_interview_video_enabled_for(user: User) -> bool:
    """Phase 6 video sub-flag, evaluated per user. Fail closed at every step.

    Requires ALL of: the master gate (master switch, master allow-list, an
    allowed role), the video switch, the institute in the separate video
    allow-list (an EMPTY video list enables nobody, unlike the master list),
    and the student role. Enforced here on the server, whatever the UI shows.
    """
    if not mock_interview_enabled_for(user):
        return False
    if user.role != UserRole.student:
        return False
    if not settings.AI_MOCK_INTERVIEW_VIDEO_ENABLED:
        return False
    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS)
    return allowed is not None and user.tenant_id in allowed


def mock_interview_coding_enabled_for(user: User) -> bool:
    """Phase 8.1 coding sub-flag, evaluated per user. Fail closed at every step.

    Same shape as the video gate: master gate, students only, the coding
    switch, and the institute in the separate coding allow-list (an EMPTY
    coding list enables nobody). Problem availability is checked separately.
    """
    if not mock_interview_enabled_for(user):
        return False
    if user.role != UserRole.student:
        return False
    if not settings.AI_MOCK_INTERVIEW_CODING_ENABLED:
        return False
    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_CODING_TENANT_IDS)
    return allowed is not None and user.tenant_id in allowed


def mock_interview_coding_available_for(user: User) -> bool:
    """Coding gate open AND at least one problem may be served (B1-b)."""
    return mock_interview_coding_enabled_for(user) and coding.problems_available()


def require_mock_interview(
    current_user: User = Depends(get_current_user),
) -> User:
    """Gate for every mock-interview route: 404 unless enabled for the caller.

    404 (not 403) so, to a signed-in user, a disabled feature looks like a
    route that does not exist. Unauthenticated callers get 401 first (the
    router-level auth dependency runs before this gate), and /status itself is
    always present by design, so the module's existence is not secret.
    """
    if not mock_interview_enabled_for(current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return current_user


@router.get("/status", response_model=MockInterviewStatusOut)
def mock_interview_status(
    current_user: User = Depends(get_current_user),
) -> MockInterviewStatusOut:
    """Is the AI Mock Interview available to me? (Used to show/hide the UI.)"""
    return MockInterviewStatusOut(enabled=mock_interview_enabled_for(current_user))


@router.get("/catalog", response_model=MockInterviewCatalogOut)
def mock_interview_catalog(
    _user: User = Depends(require_mock_interview),
) -> MockInterviewCatalogOut:
    """Setup choices for a new interview (static; no database access)."""
    return MockInterviewCatalogOut(
        interview_types=["hr", "technical", "coding", "company", "full"],
        modes=["text", "voice", "video"],
        difficulties=["easy", "medium", "hard"],
        languages=["en", "hi", "hinglish"],
        durations_minutes=[10, 20, 30, 45],
    )


# ---------------------------------------------------------------------------
# Phase 3: text interviews (students only)
# ---------------------------------------------------------------------------


def _caller_key(request: Request) -> str:
    """Rate-limit key per signed-in caller (hash of their access token).

    Per IP would throttle a whole campus behind one NAT (see rate_limit.py).
    Falls back to the IP when no token is present (those calls 401 anyway).
    """
    auth = request.headers.get("authorization") or ""
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    token = token or request.cookies.get(ACCESS_COOKIE_NAME) or ""
    if not token:
        return f"mi-ip:{client_ip(request)}"
    return "mi-user:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


# Each call can run several LLM requests; one student never needs more.
_AI_STEP_LIMIT = "20/minute"


def require_mock_interview_student(
    current_user: User = Depends(require_mock_interview),
) -> User:
    """Feature gate (404 when off) + students only (403 for staff)."""
    if current_user.role != UserRole.student:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mock interviews are available to students only",
        )
    return current_user


def _reject_coding(s, detail: str) -> None:
    """Phase 8.1: HR/technical question and report routes refuse coding rounds.

    409 until coding feedback arrives (8.3). Without this guard a coding
    session could reach the HR/technical engine or rubric (the worker falls
    back to the HR rubric for unknown types).
    """
    if coding.is_coding(s):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.get("/options", response_model=MockInterviewOptionsOut)
def mock_interview_options(
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> MockInterviewOptionsOut:
    """Setup choices for a Phase 3 text interview, plus the caller's resumes."""
    # Phase 7.6: an institute may turn voice/video off (never on); True when
    # institute operations are off.
    institute_voice, institute_video = policy.modes_allowed(db, user.tenant_id)
    return MockInterviewOptionsOut(
        interview_types=[
            MockInterviewChoice(value="hr", label="HR"),
            MockInterviewChoice(value="technical", label="Technical"),
        ]
        # Phase 8.1: only when the coding gate admits the caller.
        + (
            [MockInterviewChoice(value="coding", label="Coding")]
            if mock_interview_coding_available_for(user)
            else []
        ),
        difficulties=[
            MockInterviewChoice(value="easy", label="Beginner"),
            MockInterviewChoice(value="medium", label="Intermediate"),
            MockInterviewChoice(value="hard", label="Advanced"),
        ],
        durations_minutes=[15, 30, 45],
        languages=[
            MockInterviewChoice(value="en", label="English"),
            MockInterviewChoice(value="hi", label="Hindi"),
            MockInterviewChoice(value="hinglish", label="Hinglish"),
        ],
        modes=[MockInterviewChoice(value="text", label="Text")]
        + (
            [MockInterviewChoice(value="voice", label="Voice")]
            if mock_interview_voice_enabled() and institute_voice
            else []
        )
        + (
            [MockInterviewChoice(value="video", label="Video")]
            if mock_interview_video_enabled_for(user) and institute_video
            else []
        ),
        resumes=[
            MockInterviewResumeOption(
                id=r.id,
                title=r.title,
                target_role=r.target_role,
                is_primary=r.is_primary,
                created_at=r.created_at,
            )
            for r in svc.list_resumes(db, user)
        ],
    )


@router.post(
    "/sessions",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
)
def create_mock_interview_session(
    payload: MockInterviewSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Create an interview (status ``setup``). Nothing is sent to the AI yet."""
    initial_meta = None
    if payload.interview_type == "coding":
        # Phase 8.1: same contract as voice/video - unavailable input is 422.
        if not mock_interview_coding_available_for(user):
            raise HTTPException(status_code=422, detail=coding.MSG_UNAVAILABLE)
        if payload.mode != "text":
            raise HTTPException(status_code=422, detail=coding.MSG_TEXT_ONLY)  # decision B1-a
        initial_meta = {"coding": coding.validate_create(payload)}
    if payload.has_company_context:
        # Phase 8.4: company profile / role template (HR/technical only;
        # generation only, P8-D11). Same 422 contract as other unavailable input.
        from app.routers.mock_interview_company import company_enabled_for
        from app.services import mock_interview_company as company

        if not company_enabled_for(user):
            raise HTTPException(status_code=422, detail="Company profiles are not available")
        initial_meta = {"company": company.build_snapshot(db, user, payload)}
    if payload.mode == "voice" and not mock_interview_voice_enabled():
        raise HTTPException(
            status_code=422,  # literal: the constant name differs across Starlette versions
            detail="Voice interviews are not available",
        )
    if payload.mode == "video" and not mock_interview_video_enabled_for(user):
        raise HTTPException(
            status_code=422,  # same contract as voice: an unavailable mode is invalid input
            detail="Video interviews are not available",
        )
    return svc.to_detail(db, svc.create_session(db, user, payload, initial_meta))


@router.get("/usage/me", response_model=UsageMeOut)
def my_mock_interview_usage(
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Phase 7.6: can I start a new interview, and how many are left today?

    404 unless institute operations (AI_MOCK_INTERVIEW_OPS_ENABLED) are on for
    the caller's institute. Only the caller's own numbers are returned; an
    institute limit shows as ``reason = "institute_limit"`` without totals.
    """
    if not policy.ops_enabled_for(user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return policy.availability(db, user)


@router.get("/sessions", response_model=list[MockInterviewSessionSummaryOut])
def list_mock_interview_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> list:
    """My interviews, newest first (max 50)."""
    return svc.list_sessions(db, user)


@router.get(
    "/sessions/active",
    response_model=MockInterviewSessionOut | None,
    response_model_exclude_unset=True,
)
def get_active_mock_interview_session(
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict | None:
    """My interview in ``setup`` or ``in_progress`` (to resume after a refresh), or null."""
    s = svc.active_session(db, user)
    return svc.to_detail(db, s) if s else None


@router.get(
    "/sessions/{session_id}", response_model=MockInterviewSessionOut, response_model_exclude_unset=True
)
def get_mock_interview_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """One of my interviews with its transcript (404 if not mine)."""
    return svc.to_detail(db, svc.get_owned_session(db, session_id, user))


@router.post(
    "/sessions/{session_id}/start",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
)
@limiter.limit(_AI_STEP_LIMIT, key_func=_caller_key)
def start_mock_interview_session(
    request: Request,
    response: Response,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Plan the interview and ask the opening question. Idempotent.

    Phase 8.1: a coding round starts without any AI call (problems are
    assigned from the bank and the timer starts).
    """
    s = svc.get_owned_session(db, session_id, user)
    if coding.is_coding(s):
        return svc.to_detail(db, coding.start(db, user, s))
    return svc.to_detail(db, svc.start_session(db, user, s))


@router.post(
    "/sessions/{session_id}/answer",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
)
@limiter.limit(_AI_STEP_LIMIT, key_func=_caller_key)
def answer_mock_interview_question(
    request: Request,
    response: Response,
    session_id: int,
    payload: MockInterviewAnswerIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Save my answer to question ``turn_seq``, then get the next question.

    The answer is saved before the AI is called. On 503 it is kept and
    ``POST /next`` continues. Re-sending the same answer is a no-op.
    """
    s = svc.get_owned_session(db, session_id, user)
    if coding.is_coding(s) and coding.ai_enabled() and mock_interview_coding_enabled_for(user):
        # Phase 8.3: typed explanation answers in a coding round (B1-a).
        from app.services import mock_interview_coding_ai as coding_ai

        return svc.to_detail(
            db, coding_ai.answer(db, s, payload.turn_seq, payload.answer, source=payload.source)
        )
    _reject_coding(s, coding.CODING_STEP_UNSUPPORTED)
    return svc.to_detail(
        db,
        svc.submit_answer(db, user, s, payload.turn_seq, payload.answer, source=payload.source),
    )


@router.post(
    "/sessions/{session_id}/next",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
)
@limiter.limit(_AI_STEP_LIMIT, key_func=_caller_key)
def next_mock_interview_question(
    request: Request,
    response: Response,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Fetch the next question after a saved answer (retry path). Idempotent."""
    s = svc.get_owned_session(db, session_id, user)
    _reject_coding(s, coding.CODING_STEP_UNSUPPORTED)
    return svc.to_detail(db, svc.advance(db, user, s))


@router.post(
    "/sessions/{session_id}/finish",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
)
def finish_mock_interview_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """End early: completed if at least one answer was given, else abandoned.

    Phase 8.1 coding round: saved drafts become automatic graded submissions;
    completed when anything was submitted, else abandoned.
    """
    s = svc.get_owned_session(db, session_id, user)
    if coding.is_coding(s):
        return svc.to_detail(db, coding.end(db, s, abandon=False))
    return svc.to_detail(db, svc.end_session(db, s, abandon=False))


@router.post(
    "/sessions/{session_id}/abandon",
    response_model=MockInterviewSessionOut,
    response_model_exclude_unset=True,
)
def abandon_mock_interview_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Abandon the interview. Idempotent."""
    s = svc.get_owned_session(db, session_id, user)
    if coding.is_coding(s):
        return svc.to_detail(db, coding.end(db, s, abandon=True))
    return svc.to_detail(db, svc.end_session(db, s, abandon=True))


# ---------------------------------------------------------------------------
# Phase 4: reports + history (students only, own sessions only)
# ---------------------------------------------------------------------------

# A report scores every answer (several LLM calls); it is generated once.
_REPORT_LIMIT = "5/minute"


@router.get("/history", response_model=list[MockInterviewHistoryItemOut])
def mock_interview_history(
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> list:
    """My interviews, newest first (max 50), with the report score when one exists."""
    return report_svc.history(db, user)


@router.get("/sessions/{session_id}/report", response_model=MockInterviewReportOut)
def get_mock_interview_report(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """My stored feedback report (404 if not mine, or not generated yet)."""
    s = svc.get_owned_session(db, session_id, user)
    _reject_coding(s, coding.CODING_FEEDBACK_UNAVAILABLE)
    return report_svc.to_report(db, s, report_svc.get_report(db, s))


@router.post("/sessions/{session_id}/report", response_model=MockInterviewReportOut)
@limiter.limit(_REPORT_LIMIT, key_func=_caller_key)
def generate_mock_interview_report(
    request: Request,
    response: Response,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_mock_interview_student),
) -> dict:
    """Generate my report for a completed interview, or return the existing one.

    Idempotent and final: once stored, the same report is returned with no AI
    call. 409 for cancelled / unfinished interviews, 503 when the AI is down
    (nothing is stored; try again).
    """
    s = svc.get_owned_session(db, session_id, user)
    _reject_coding(s, coding.CODING_FEEDBACK_UNAVAILABLE)
    return report_svc.to_report(db, s, report_svc.generate_report(db, s))
