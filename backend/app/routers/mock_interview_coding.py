"""AI Mock Interview coding round endpoints (Phase 8.1, review-only).

Mounted under ``/mock-interview`` (at /v1 and the deprecated unversioned path,
like every feature router). Students only; every route 404s unless the coding
gate admits the caller (master switch + master allow-list + coding switch +
the institute in the fail-closed coding allow-list).

Sessions are created, started, finished and abandoned through the existing
``/mock-interview/sessions`` routes; these routes add the coding parts:

    GET  /coding/catalog                                     choices + practice problems
    GET  /sessions/{id}/coding                               problems, drafts, counters
    PUT  /sessions/{id}/coding/attempts/{seq}/draft          autosave (optimistic rev)
    POST /sessions/{id}/coding/attempts/{seq}/submissions    graded submit -> not_executed
    GET  /sessions/{id}/coding/attempts/{seq}/submissions    attempt history
    GET  /sessions/{id}/coding/submissions/{sid}             one submission
    POST /sessions/{id}/coding/attempts/{seq}/hints          next authored hint
    POST /sessions/{id}/coding/attempts/{seq}/done           close one problem
    GET  /sessions/{id}/coding/feedback                      stored feedback (8.3)
    POST /sessions/{id}/coding/feedback                      generate once (8.3)

No code is executed (decision P8-D8; Run arrives with 8.6). Only public
problem material is returned. Source code is never logged. Phase 8.3 AI help
(AI hints, explanation turns, AI-estimate feedback) needs
``AI_MOCK_INTERVIEW_CODING_AI_ENABLED`` as well.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.core.security import apply_tenant_guc
from app.models import User
from app.routers.mock_interview import (
    _caller_key,
    mock_interview_coding_available_for,
    mock_interview_coding_enabled_for,
    require_mock_interview_student,
)
from app.schemas.mock_interview_coding import (
    CodingCatalogOut,
    CodingDraftIn,
    CodingDraftOut,
    CodingFeedbackOut,
    CodingHintOut,
    CodingStateOut,
    CodingSubmissionOut,
    CodingSubmitIn,
)
from app.services import mock_interview as svc
from app.services import mock_interview_coding as coding
from app.services import mock_interview_coding_ai as coding_ai
from app.services import mock_interview_coding_report as coding_report

router = APIRouter(
    prefix="/mock-interview",
    tags=["mock-interview"],
    dependencies=[Depends(apply_tenant_guc)],
)


def require_coding_student(
    user: User = Depends(require_mock_interview_student),
) -> User:
    """Master gate + students only + coding gate (404 when closed)."""
    if not mock_interview_coding_enabled_for(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return user


def _coding_session(db: Session, session_id: int, user: User):
    """The caller's own coding session (404 otherwise); expires it when time is up."""
    return coding.require_coding(svc.get_owned_session(db, session_id, user))


@router.get("/coding/catalog", response_model=CodingCatalogOut)
def coding_catalog(user: User = Depends(require_coding_student)) -> dict:
    """Languages, topics, durations, limits and the problems a student may pick."""
    if not mock_interview_coding_available_for(user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return coding.catalog()


@router.get("/sessions/{session_id}/coding", response_model=CodingStateOut)
def coding_state(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """My coding round: assigned problems (public parts), drafts and counters."""
    return coding.state(db, _coding_session(db, session_id, user))


@router.put(
    "/sessions/{session_id}/coding/attempts/{seq}/draft", response_model=CodingDraftOut
)
@limiter.limit("30/minute", key_func=_caller_key)
def save_coding_draft(
    request: Request,
    response: Response,
    session_id: int,
    seq: int,
    payload: CodingDraftIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """Autosave my code. 409 when another tab saved first (reload) or the problem is closed."""
    s = _coding_session(db, session_id, user)
    return coding.save_draft(db, s, seq, payload.language, payload.source, payload.draft_rev)


@router.post(
    "/sessions/{session_id}/coding/attempts/{seq}/submissions",
    response_model=CodingSubmissionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("3/minute", key_func=_caller_key)
def submit_coding_solution(
    request: Request,
    response: Response,
    session_id: int,
    seq: int,
    payload: CodingSubmitIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """Submit code for grading. Review-only: stored, never executed (``not_executed``).

    At most 3 graded submissions per problem (atomic, server-side). A repeat
    with the same ``idempotency_key``, or identical code, returns the earlier
    submission without using another one.
    """
    s = _coding_session(db, session_id, user)
    sub = coding.submit(db, user, s, seq, payload.language, payload.source, payload.idempotency_key)
    a = coding.attempt(db, s, seq)
    return coding.submission_view(sub, a.seq, a.best_submission_id)


@router.get(
    "/sessions/{session_id}/coding/attempts/{seq}/submissions",
    response_model=list[CodingSubmissionOut],
)
def list_coding_submissions(
    session_id: int,
    seq: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> list:
    """Every submission for one problem (metadata only), oldest first."""
    return coding.list_submissions(db, _coding_session(db, session_id, user), seq)


@router.get(
    "/sessions/{session_id}/coding/submissions/{submission_id}",
    response_model=CodingSubmissionOut,
)
@limiter.limit("60/minute", key_func=_caller_key)
def get_coding_submission(
    request: Request,
    response: Response,
    session_id: int,
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """One of my submissions (status and verification only; no code)."""
    return coding.get_submission(db, _coding_session(db, session_id, user), submission_id)


@router.post(
    "/sessions/{session_id}/coding/attempts/{seq}/hints", response_model=CodingHintOut
)
@limiter.limit("5/minute", key_func=_caller_key)
def reveal_coding_hint(
    request: Request,
    response: Response,
    session_id: int,
    seq: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """Reveal the next authored hint. Hints never lower a score."""
    return coding.reveal_hint(db, _coding_session(db, session_id, user), seq)


@router.post(
    "/sessions/{session_id}/coding/attempts/{seq}/done", response_model=CodingStateOut
)
@limiter.limit("20/minute", key_func=_caller_key)
def finish_coding_problem(
    request: Request,
    response: Response,
    session_id: int,
    seq: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """Close one problem (its saved code stays). Idempotent.

    Phase 8.3 (AI help on): a problem with submitted code moves to
    ``explaining`` and gets a typed explanation question instead (answered via
    ``POST /sessions/{id}/answer``); it closes after 1-2 questions.
    """
    s = _coding_session(db, session_id, user)
    if coding.ai_enabled():
        coding_ai.done(db, s, seq)
    else:
        coding.mark_done(db, s, seq)
    return coding.state(db, svc._reload(db, s.id))


# ---------------------------------------------------------------------------
# Phase 8.3: review-only coding feedback (decision O1 = option A)
# ---------------------------------------------------------------------------

# Each problem's review is one LLM call; feedback is generated once.
_FEEDBACK_LIMIT = "5/minute"


@router.get("/sessions/{session_id}/coding/feedback", response_model=CodingFeedbackOut)
def get_coding_feedback(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """My stored coding feedback (404 if not mine, or not generated yet).

    AI estimates only; objective categories are "not verified" and there is
    no score of any kind (review-only, P8-D7).
    """
    return coding_report.get_feedback(db, _coding_session(db, session_id, user))


@router.post("/sessions/{session_id}/coding/feedback", response_model=CodingFeedbackOut)
@limiter.limit(_FEEDBACK_LIMIT, key_func=_caller_key)
def generate_coding_feedback(
    request: Request,
    response: Response,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_coding_student),
) -> dict:
    """Generate my feedback for a completed coding round, or return the stored one.

    Idempotent and final. 409 while AI help is off, for cancelled or unfinished
    rounds; 503 when the AI is unavailable (nothing is stored; try again).
    """
    return coding_report.generate(db, _coding_session(db, session_id, user))
