"""AI Mock Interview session service (Phase 3: text HR + technical).

Owns persistence and the session state machine; the AI worker is stateless
and gets the config, plan and transcript replayed on every call.

    setup --start--> in_progress --(engine ends | finish | time over)--> completed
      \\                  \\--abandon--> abandoned
       \\--abandon / finish without answers--> abandoned

Security:
  * Every session is loaded with tenant_id AND student_id filters (404 else).
  * ``resume_id`` has only a single-column FK (Phase 2 finding M1), so resume
    ownership is enforced HERE: the resume must belong to the same student and
    institute, checked on create and again whenever its text is loaded.

Concurrency, idempotency, cost:
  * An answer is saved (``UPDATE ... WHERE answer_text IS NULL``) and committed
    BEFORE the worker is called, so a provider failure never loses it.
  * ``turn_seq`` is the idempotency key: same seq + same text is a no-op,
    a different text is a 409.
  * Every AI step (start, next question) first takes a short LEASE on the
    session: a compare-and-set on ``updated_at`` that also stores
    ``plan_json["lease"]``. Only one AI call per session can run at a time
    (others get 409), and the step's result is written with another
    compare-and-set on the lease's ``updated_at``, so a concurrent finish or
    abandon always wins over a late AI result. No migration needed.
  * The DB transaction is closed before each worker call (no connection is
    held "idle in transaction" while the LLM thinks).
  * ``updated_at`` is always written explicitly from Python (microsecond
    precision on SQLite too), so the compare-and-set token is exact.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MockInterviewSession, MockInterviewTurn, Resume, User
from app.routers.mentor import _build_verified_profile
from app.schemas.mock_interview import MockInterviewSessionCreate
from app.services import mock_interview_ai as engine

logger = logging.getLogger("campus_ai.mock_interview")

ACTIVE_STATUSES = ("setup", "in_progress")
TERMINAL_STATUSES = ("completed", "abandoned", "failed", "evaluating")
MAX_SESSIONS_PER_DAY = 10
HARD_MAX_TURNS = 30
RESUME_CONTEXT_CHARS = 8_000
# Worker call: 60 s timeout + one connect retry; the lease outlives it.
LEASE_SECONDS = 150
# After the time runs out the student may still submit the last answer; an
# untouched session is closed automatically once this grace has passed.
OVERDUE_GRACE_SECONDS = 300
# Matches the worker's profile_loader caps (and stays under its schema limits).
MAX_PROFILE_SKILLS = 40
MAX_PROFILE_PROJECTS = 10

_ENGINE_DOWN = (
    "The AI interviewer is unavailable right now. Nothing you typed was lost; "
    "please try again in a moment."
)
_BUSY = "The interviewer is still preparing the next question. Please wait a moment."
_FALLBACK_CLOSING = "Thank you for taking this practice interview."


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Lookups (tenant + ownership)
# ---------------------------------------------------------------------------


def _owned(user: User):
    return (
        MockInterviewSession.tenant_id == user.tenant_id,
        MockInterviewSession.student_id == user.id,
    )


def get_owned_session(db: Session, session_id: int, user: User) -> MockInterviewSession:
    s = db.scalar(
        select(MockInterviewSession).where(MockInterviewSession.id == session_id, *_owned(user))
    )
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    return expire_if_overdue(db, s)


def owned_resume(db: Session, resume_id: int | None, user: User) -> Resume | None:
    """The caller's own resume in the caller's institute, else None."""
    if resume_id is None:
        return None
    return db.scalar(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.student_id == user.id,
            Resume.tenant_id == user.tenant_id,
        )
    )


def list_resumes(db: Session, user: User) -> list[Resume]:
    return list(
        db.scalars(
            select(Resume)
            .where(Resume.student_id == user.id, Resume.tenant_id == user.tenant_id)
            .order_by(Resume.is_primary.desc(), Resume.created_at.desc())
            .limit(50)
        )
    )


def list_sessions(db: Session, user: User) -> list[MockInterviewSession]:
    return list(
        db.scalars(
            select(MockInterviewSession)
            .where(*_owned(user))
            .order_by(MockInterviewSession.created_at.desc(), MockInterviewSession.id.desc())
            .limit(50)
        )
    )


def active_session(db: Session, user: User) -> MockInterviewSession | None:
    s = db.scalar(
        select(MockInterviewSession)
        .where(*_owned(user), MockInterviewSession.status.in_(ACTIVE_STATUSES))
        .order_by(MockInterviewSession.id.desc())
        .limit(1)
    )
    if s is None:
        return None
    s = expire_if_overdue(db, s)
    return s if s.status in ACTIVE_STATUSES else None


def _turns(db: Session, s: MockInterviewSession) -> list[MockInterviewTurn]:
    return list(
        db.scalars(
            select(MockInterviewTurn)
            .where(
                MockInterviewTurn.session_id == s.id,
                MockInterviewTurn.tenant_id == s.tenant_id,
            )
            .order_by(MockInterviewTurn.seq.asc())
        )
    )


def _plan_meta(s: MockInterviewSession) -> dict:
    """A fresh copy of plan_json (never mutate the loaded JSON in place)."""
    return dict(s.plan_json) if isinstance(s.plan_json, dict) else {}


def remaining_seconds(s: MockInterviewSession, now: datetime | None = None) -> int | None:
    started = _aware(s.started_at)
    if started is None:
        return None
    elapsed = ((now or utcnow()) - started).total_seconds()
    return int(s.duration_minutes * 60 - elapsed)


def _reload(db: Session, session_id: int) -> MockInterviewSession:
    s = db.get(MockInterviewSession, session_id, populate_existing=True)
    assert s is not None
    return s


# ---------------------------------------------------------------------------
# Lease (one AI step per session at a time) - see module docstring
# ---------------------------------------------------------------------------


def _lease_active(meta: dict, now: datetime) -> bool:
    lease = meta.get("lease")
    if not isinstance(lease, dict):
        return False
    try:
        until = _aware(datetime.fromisoformat(str(lease.get("until"))))
    except ValueError:
        return False
    return until is not None and until > now


def _claim(
    db: Session,
    s: MockInterviewSession,
    *,
    seconds: int = LEASE_SECONDS,
    busy: str = _BUSY,
) -> tuple[datetime, dict]:
    """Take the session's AI lease or raise 409. Returns (token, base meta)."""
    now = utcnow()
    meta = _plan_meta(s)
    if _lease_active(meta, now):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    meta.pop("lease", None)
    leased = {**meta, "lease": {"until": (now + timedelta(seconds=seconds)).isoformat()}}
    result = db.execute(
        update(MockInterviewSession)
        .where(
            MockInterviewSession.id == s.id,
            MockInterviewSession.tenant_id == s.tenant_id,
            MockInterviewSession.updated_at == s.updated_at,
        )
        .values(plan_json=leased, updated_at=now)
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=busy)
    return now, meta


def _finalize(db: Session, s_id: int, tenant_id: int, token: datetime, **values: Any) -> bool:
    """Write the step's result only if nobody touched the session meanwhile."""
    result = db.execute(
        update(MockInterviewSession)
        .where(
            MockInterviewSession.id == s_id,
            MockInterviewSession.tenant_id == tenant_id,
            MockInterviewSession.updated_at == token,
        )
        .values(**values, updated_at=utcnow())
    )
    return result.rowcount == 1


def _release(db: Session, s_id: int, tenant_id: int, token: datetime, meta: dict) -> None:
    """Drop the lease after a failed step (best effort; it also expires)."""
    try:
        db.rollback()
        _finalize(db, s_id, tenant_id, token, plan_json=meta)
        db.commit()
    except Exception:  # noqa: BLE001 - never mask the original error
        db.rollback()


def _engine_down(session_id: int, step: str) -> HTTPException:
    logger.warning("mock interview %s: AI step %s unavailable", session_id, step)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ENGINE_DOWN)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_session(
    db: Session, user: User, payload: MockInterviewSessionCreate
) -> MockInterviewSession:
    # Serialize creates per student (Postgres row lock; no-op on SQLite) so two
    # parallel requests can't both pass the one-active / daily checks.
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    if active_session(db, user) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an interview in progress. Resume or end it first.",
        )
    recent = db.scalars(
        select(MockInterviewSession.created_at)
        .where(*_owned(user))
        .order_by(MockInterviewSession.id.desc())
        .limit(MAX_SESSIONS_PER_DAY)
    ).all()
    since = utcnow() - timedelta(hours=24)
    if sum(1 for c in recent if c is not None and _aware(c) > since) >= MAX_SESSIONS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You can start up to {MAX_SESSIONS_PER_DAY} practice interviews per day.",
        )
    if payload.resume_id is not None and owned_resume(db, payload.resume_id, user) is None:
        # Same answer for "not yours" and "does not exist".
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

    now = utcnow()
    s = MockInterviewSession(
        tenant_id=user.tenant_id,
        student_id=user.id,
        interview_type=payload.interview_type,
        mode=payload.mode,
        status="setup",
        target_role=payload.target_role,
        company_name=payload.company_name,
        difficulty=payload.difficulty,
        language=payload.language,
        duration_minutes=payload.duration_minutes,
        resume_id=payload.resume_id,
        job_description=payload.job_description,
        created_at=now,
        updated_at=now,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ---------------------------------------------------------------------------
# Worker payloads
# ---------------------------------------------------------------------------


def _config(s: MockInterviewSession) -> dict:
    return {
        "interview_type": s.interview_type,
        "difficulty": s.difficulty,
        "duration_minutes": s.duration_minutes,
        "target_role": s.target_role or "Software Engineer",
        "company_name": s.company_name,
        "language": s.language,
    }


def _profile(db: Session, user: User) -> dict:
    """Verified skills/projects/CGPA only, capped to the worker's limits."""
    full = _build_verified_profile(db, user.id)
    projects = [
        {
            "title": str(p.get("title") or "")[:300],
            "contribution": (str(p["contribution"])[:1000] if p.get("contribution") else None),
        }
        for p in (full.get("projects") or [])[:MAX_PROFILE_PROJECTS]
    ]
    return {
        "verified_skills": [str(x)[:100] for x in (full.get("verified_skills") or [])][
            :MAX_PROFILE_SKILLS
        ],
        "projects": projects,
        "cgpa": full.get("cgpa"),
    }


def _resume_text(db: Session, s: MockInterviewSession, user: User) -> str | None:
    # Ownership re-checked on every load (never trust the stored id alone).
    resume = owned_resume(db, s.resume_id, user)
    if resume is None:
        return None
    return (resume.markdown or "")[:RESUME_CONTEXT_CHARS] or None


def _transcript(turns: list[MockInterviewTurn]) -> list[dict]:
    return [
        {
            "seq": t.seq,
            "topic": t.topic,
            "question": t.question,
            "is_follow_up": t.is_follow_up,
            "answer": t.answer_text,
            "evaluation": t.turn_eval_json if isinstance(t.turn_eval_json, dict) else None,
        }
        for t in turns
    ]


def _new_turn(s_id: int, tenant_id: int, question: dict, seq: int) -> MockInterviewTurn:
    return MockInterviewTurn(
        tenant_id=tenant_id,
        session_id=s_id,
        seq=seq,
        topic=str(question.get("topic") or "")[:100] or None,
        question=str(question.get("text") or "").strip(),
        is_follow_up=bool(question.get("is_follow_up")),
    )


def _commit_step(db: Session, s_id: int) -> MockInterviewSession:
    """Commit a finalized step; a duplicate seq (should not happen under the
    lease) is rolled back and the current state returned."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("mock interview %s: concurrent update resolved", s_id)
    return _reload(db, s_id)


# ---------------------------------------------------------------------------
# Time-out
# ---------------------------------------------------------------------------


def expire_if_overdue(db: Session, s: MockInterviewSession) -> MockInterviewSession:
    """Close an in-progress session that ran out of time and was left alone.

    Without this an abandoned tab would block new interviews forever.
    """
    if s.status != "in_progress":
        return s
    remaining = remaining_seconds(s)
    if remaining is None or remaining > -OVERDUE_GRACE_SECONDS:
        return s
    if _lease_active(_plan_meta(s), utcnow()):
        return s  # an AI step is finishing; let it decide
    meta = _plan_meta(s)
    meta.pop("lease", None)
    meta.setdefault("closing_message", _FALLBACK_CLOSING)
    meta["end_reason"] = "time_up"
    answered = any(t.answer_text is not None for t in _turns(db, s))
    s_id, now = s.id, utcnow()
    done = db.execute(
        update(MockInterviewSession)
        .where(
            MockInterviewSession.id == s_id,
            MockInterviewSession.tenant_id == s.tenant_id,
            MockInterviewSession.updated_at == s.updated_at,
        )
        .values(
            plan_json=meta,
            status="completed" if answered else "abandoned",
            completed_at=now,
            updated_at=now,
        )
    )
    db.commit()
    if done.rowcount:
        logger.info("mock interview %s: closed after time-out", s_id)
    return _reload(db, s_id)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def start_session(db: Session, user: User, s: MockInterviewSession) -> MockInterviewSession:
    if s.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This interview has ended")
    if s.status == "in_progress":
        return s  # idempotent: already started (status + turn 1 commit together)

    s_id, tenant_id = s.id, s.tenant_id
    config = _config(s)
    profile = _profile(db, user)
    resume_text = _resume_text(db, s, user)
    jd = s.job_description
    token, meta = _claim(db, s)  # commits: no transaction held during AI calls
    plan = meta.get("plan")

    if not isinstance(plan, dict):
        try:
            planned = engine.plan_interview(
                {"config": config, "profile": profile, "resume_text": resume_text, "job_description": jd}
            )
        except engine.InterviewEngineUnavailable:
            _release(db, s_id, tenant_id, token, meta)
            raise _engine_down(s_id, "plan")
        plan = planned["plan"]
        meta = {**meta, "plan": plan, "provider": planned.get("provider")}

    try:
        opening = engine.interview_turn(
            {
                "config": config,
                "plan": plan,
                "profile": profile,
                "resume_text": resume_text,
                "job_description": jd,
                "transcript": [],
                "remaining_seconds": int(config["duration_minutes"]) * 60,
            }
        )
        question = opening.get("question")
        if not isinstance(question, dict) or not str(question.get("text") or "").strip():
            raise engine.InterviewEngineUnavailable("/interview/turn")
    except engine.InterviewEngineUnavailable:
        # Keep the plan so a retry doesn't plan (and pay) again.
        _release(db, s_id, tenant_id, token, meta)
        raise _engine_down(s_id, "opening")

    if not _finalize(
        db, s_id, tenant_id, token, plan_json=meta, status="in_progress", started_at=utcnow()
    ):
        db.rollback()
        return _reload(db, s_id)  # cancelled meanwhile
    db.add(_new_turn(s_id, tenant_id, question, seq=1))
    return _commit_step(db, s_id)


# ---------------------------------------------------------------------------
# Answer / next question
# ---------------------------------------------------------------------------


def submit_answer(
    db: Session,
    user: User,
    s: MockInterviewSession,
    turn_seq: int,
    answer: str,
    source: str = "typed",
) -> MockInterviewSession:
    if s.status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This interview is not in progress",
        )
    if source not in ("typed", "stt") or (source == "stt" and s.mode != "voice"):
        raise HTTPException(
            status_code=422,  # literal: the constant name differs across Starlette versions
            detail="Spoken answers are only accepted in a voice interview",
        )
    turns = _turns(db, s)
    target = next((t for t in turns if t.seq == turn_seq), None)
    if target is None or target is not turns[-1]:
        if target is not None and target.answer_text == answer:
            return s  # duplicate of an older, already-processed answer
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That question is no longer the current one. Refresh to continue.",
        )

    if target.answer_text is None:
        result = db.execute(
            update(MockInterviewTurn)
            .where(
                MockInterviewTurn.id == target.id,
                MockInterviewTurn.tenant_id == s.tenant_id,
                MockInterviewTurn.answer_text.is_(None),
            )
            .values(answer_text=answer, answer_source=source, answered_at=utcnow())
        )
        db.commit()
        if result.rowcount == 0:
            # Lost a race with another submission for the same question.
            db.refresh(target)
            if target.answer_text != answer:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This question was already answered",
                )
        s = _reload(db, s.id)
    elif target.answer_text != answer:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question was already answered",
        )
    # Saved (now or by an earlier identical submission): produce what comes next.
    return advance(db, user, s)


def advance(db: Session, user: User, s: MockInterviewSession) -> MockInterviewSession:
    """Generate the next question (or end) after the latest answer.

    Safe to call repeatedly: does nothing unless the session is in progress,
    the last turn is answered and no newer turn exists. 409 while another
    request is already generating for this session.
    """
    if s.status != "in_progress":
        return s
    turns = _turns(db, s)
    if not turns or turns[-1].answer_text is None:
        return s

    s_id, tenant_id = s.id, s.tenant_id
    last_id, last_seq = turns[-1].id, turns[-1].seq
    payload = {
        "config": _config(s),
        "plan": _plan_meta(s).get("plan"),
        "profile": _profile(db, user),
        "resume_text": _resume_text(db, s, user),
        "job_description": s.job_description,
        "transcript": _transcript(turns[-HARD_MAX_TURNS:]),
        "remaining_seconds": max(-86_400, min(86_400, remaining_seconds(s) or 0)),
    }
    if not isinstance(payload["plan"], dict):
        raise _engine_down(s_id, "plan-missing")
    token, meta = _claim(db, s)

    try:
        result = engine.interview_turn(payload)
    except engine.InterviewEngineUnavailable:
        _release(db, s_id, tenant_id, token, meta)
        raise _engine_down(s_id, "turn")

    question = result.get("question")
    ends = (
        result.get("action") == "end"
        or not isinstance(question, dict)
        or last_seq >= HARD_MAX_TURNS
    )
    if not ends and not str(question.get("text") or "").strip():
        _release(db, s_id, tenant_id, token, meta)
        raise _engine_down(s_id, "turn-empty")

    if ends:
        meta["closing_message"] = (
            str(result.get("closing_message") or "").strip() or _FALLBACK_CLOSING
        )
        meta["end_reason"] = str(result.get("end_reason") or "question_budget_reached")[:50]
        ok = _finalize(
            db, s_id, tenant_id, token, plan_json=meta, status="completed", completed_at=utcnow()
        )
    else:
        ok = _finalize(db, s_id, tenant_id, token, plan_json=meta)
    if not ok:
        db.rollback()  # finished / abandoned meanwhile: drop the late result
        return _reload(db, s_id)

    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict):
        db.execute(
            update(MockInterviewTurn)
            .where(MockInterviewTurn.id == last_id, MockInterviewTurn.tenant_id == tenant_id)
            .values(turn_eval_json=evaluation)
        )
    if not ends:
        db.add(_new_turn(s_id, tenant_id, question, seq=last_seq + 1))
    return _commit_step(db, s_id)


# ---------------------------------------------------------------------------
# Finish / abandon
# ---------------------------------------------------------------------------


def end_session(db: Session, s: MockInterviewSession, *, abandon: bool) -> MockInterviewSession:
    """End early. Always wins over an in-flight AI step (its result is dropped)."""
    if s.status in TERMINAL_STATUSES:
        return s  # idempotent
    answered = any(t.answer_text is not None for t in _turns(db, s))
    meta = _plan_meta(s)
    meta.pop("lease", None)
    if abandon or not answered:
        s.status = "abandoned"
        meta["end_reason"] = "abandoned"
    else:
        s.status = "completed"
        meta["end_reason"] = "ended_by_candidate"
    s.plan_json = meta
    now = utcnow()
    s.completed_at = now
    s.updated_at = now
    db.commit()
    return _reload(db, s.id)


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def to_detail(db: Session, s: MockInterviewSession) -> dict:
    turns = _turns(db, s)
    meta = _plan_meta(s)
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    if s.status == "setup" or (s.status == "in_progress" and not turns):
        awaiting = "start"
    elif s.status == "in_progress":
        awaiting = "answer" if turns[-1].answer_text is None else "next_question"
    else:
        awaiting = "none"
    remaining = remaining_seconds(s) if s.status == "in_progress" else None
    topics = plan.get("topics") if isinstance(plan.get("topics"), list) else None
    return {
        "id": s.id,
        "interview_type": s.interview_type,
        "mode": s.mode,
        "status": s.status,
        "target_role": s.target_role,
        "company_name": s.company_name,
        "difficulty": s.difficulty,
        "language": s.language,
        "duration_minutes": s.duration_minutes,
        "created_at": s.created_at,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
        "resume_id": s.resume_id,
        "has_job_description": bool(s.job_description),
        "awaiting": awaiting,
        "current_turn_seq": turns[-1].seq if awaiting == "answer" else None,
        "remaining_seconds": max(0, remaining) if remaining is not None else None,
        "progress": {
            "answered": sum(1 for t in turns if t.answer_text is not None),
            "question_budget": plan.get("question_budget"),
            "topics_total": len(topics) if topics is not None else None,
            "topics_covered": len({(t.topic or "").lower() for t in turns if t.topic}),
        },
        "turns": [
            {
                "seq": t.seq,
                "topic": t.topic,
                "question": t.question,
                "is_follow_up": t.is_follow_up,
                "answer_text": t.answer_text,
                "answered_at": t.answered_at,
                "answer_source": t.answer_source,
            }
            for t in turns
        ],
        "closing_message": meta.get("closing_message"),
        "end_reason": meta.get("end_reason"),
    }
