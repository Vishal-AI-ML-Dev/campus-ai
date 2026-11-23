"""AI Mock Interview coding round service (Phase 8.1, review-only).

A coding round is an ordinary ``mock_interview_sessions`` row with
``interview_type = "coding"`` (spec principle 5). Its choices (languages,
topics, practice problem) live in ``plan_json["coding"]``; its problems are
``mock_interview_code_attempts`` rows created at start; code the student
submits for grading is a ``mock_interview_code_submissions`` row.

Batch 1 is REVIEW-ONLY (decision P8-D8): nothing is ever executed - not here,
not anywhere. Graded submissions are stored with ``status = "not_executed"``;
no result is simulated or predicted. Execution arrives in 8.6 behind its own
gate, after the sandbox is approved (gate G4).

    session:  setup --start--> in_progress --(finish | time up)--> completed
                                    \\--abandon / finish with nothing submitted--> abandoned
    attempt:  pending --(first draft/hint/submit)--> working --(done)--> closed
              any open attempt --(session ends)--> closed | abandoned

Every limit is enforced on the server with an atomic conditional UPDATE
(decision P8-D6), so concurrent requests can never exceed it:
  * graded submissions per problem (3), one submission in flight per attempt;
  * hints per problem (by difficulty, capped by the authored hints);
  * draft saves use optimistic concurrency on ``draft_rev``.
Hints never reduce a score (P8-D7 proposal; analytics signal only).

Privacy: source code is student content. It is stored only in the two coding
tables, never logged, and never sent anywhere in Batch 1. Log lines carry ids
and counts only.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    MockInterviewCodeAttempt as Attempt,
    MockInterviewCodeSubmission as Submission,
    MockInterviewSession,
    User,
)
from app.schemas.mock_interview import CODING_DURATIONS, MockInterviewSessionCreate
from app.schemas.mock_interview_coding import SOURCE_MAX_BYTES
from app.services import coding_bank
from app.services import mock_interview as svc

logger = logging.getLogger("campus_ai.mock_interview.coding")

GRADED_PER_PROBLEM = 3
HINTS_BY_DIFFICULTY = {"easy": 3, "medium": 3, "hard": 2}
# Oracle resistance (spec 3.9): graded submissions per student per problem per 7 days.
WEEKLY_PROBLEM_GRADED_CAP = 6
RECENT_CODING_SESSIONS = 10
OPEN_ATTEMPT = ("pending", "working")
FINAL_ATTEMPT = ("closed", "abandoned")
GRADED_KINDS = ("graded", "auto")
# Submissions that count toward limits (runner failures and rejections never do).
COUNTED_STATUSES = ("queued", "running", "completed", "not_executed")

MSG_UNAVAILABLE = "Coding interviews are not available"
MSG_TEXT_ONLY = "Coding rounds are text-only for now"
MSG_NOT_IN_PROGRESS = "This coding round is not in progress"
MSG_ENDED = "This interview has ended"
MSG_PROBLEM_CLOSED = "This problem is closed. Your saved code is kept."
MSG_DRAFT_STALE = "Your code was changed in another tab or window. Reload to continue."
MSG_GRADED_LIMIT = (
    f"You have used all {GRADED_PER_PROBLEM} graded submissions for this problem. "
    "Your best result is kept."
)
MSG_WEEKLY_LIMIT = (
    f"You have submitted this problem {WEEKLY_PROBLEM_GRADED_CAP} times in the last 7 days. "
    "Try a different problem."
)
MSG_NO_HINTS = "There are no more hints for this problem."
MSG_LANGUAGE = "Choose one of the languages selected for this round."
MSG_TOO_LARGE = "Code is limited to 64 KB."
MSG_EMPTY = "Write some code before submitting."
MSG_KEY_REUSED = "This request key was already used for different code."
MSG_NO_PROBLEMS = "No coding problems are available right now."
MSG_BANK_DOWN = "Coding problems are unavailable right now. Please try again later."
MSG_NOT_CODING = "Coding round not found"
# Existing HR/technical routes reject coding sessions until 8.3 (documented 409s).
CODING_STEP_UNSUPPORTED = "Coding rounds don't use interview questions yet."
CODING_FEEDBACK_UNAVAILABLE = "Feedback for coding rounds is not available yet."
_CLOSING = "Your coding round has ended and your code is saved."


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def is_coding(s: MockInterviewSession) -> bool:
    return s.interview_type == "coding"


def config_of(s: MockInterviewSession) -> dict:
    meta = s.plan_json if isinstance(s.plan_json, dict) else {}
    cfg = meta.get("coding")
    return dict(cfg) if isinstance(cfg, dict) else {}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _too_large(source: str) -> bool:
    return len(source.encode("utf-8")) > SOURCE_MAX_BYTES


def hints_available(s: MockInterviewSession, problem: coding_bank.Problem | None) -> int:
    if problem is None:
        return 0
    return min(HINTS_BY_DIFFICULTY.get(s.difficulty, 2), len(problem.hints))


# ---------------------------------------------------------------------------
# Availability, catalog and create
# ---------------------------------------------------------------------------


def problems_available() -> bool:
    return bool(coding_bank.selectable_problems())


def catalog() -> dict:
    problems = sorted(
        coding_bank.selectable_problems(),
        key=lambda p: (coding_bank.DIFFICULTIES.index(p.difficulty), p.title),
    )
    return {
        "languages": [{"value": k, "label": v} for k, v in coding_bank.LANGUAGES.items()],
        "topics": [{"value": k, "label": v} for k, v in coding_bank.TOPICS.items()],
        "difficulties": [
            {"value": "easy", "label": "Beginner"},
            {"value": "medium", "label": "Intermediate"},
            {"value": "hard", "label": "Advanced"},
        ],
        "durations_minutes": list(CODING_DURATIONS),
        "graded_submissions_per_problem": GRADED_PER_PROBLEM,
        "hints_per_problem": dict(HINTS_BY_DIFFICULTY),
        "source_max_bytes": SOURCE_MAX_BYTES,
        "review_only": True,
        "problems": [
            {"key": p.key, "title": p.title, "difficulty": p.difficulty, "topics": list(p.topics)}
            for p in problems
        ],
    }


def validate_create(payload: MockInterviewSessionCreate) -> dict:
    """Check the coding choices against the bank; return what is stored in plan_json."""
    cfg = payload.coding
    languages = list(cfg.languages) if cfg else list(coding_bank.LANGUAGES)
    topics = list(cfg.topics) if cfg else []
    unknown = [t for t in topics if t not in coding_bank.TOPICS]
    if unknown:
        raise _http(422, "Choose topics from the list")
    problem_key = cfg.problem_key if cfg else None
    if problem_key is not None:
        problem = coding_bank.get_problem(problem_key)
        if problem is None or not coding_bank.selectable(problem):
            # Same answer for "unknown" and "not available" (no bank probing).
            raise _http(422, "That problem is not available")
    return {"languages": languages, "topics": topics, "problem_key": problem_key, "review_only": True}


# ---------------------------------------------------------------------------
# Start: assign problems (no AI call, no execution)
# ---------------------------------------------------------------------------


def _slots(s: MockInterviewSession) -> list[tuple[str, int]]:
    """Problems per round by difficulty (spec 3.4)."""
    if s.difficulty == "easy":
        return [("easy", 1 if s.duration_minutes <= 30 else 2)]
    if s.difficulty == "hard":
        return [("hard", 1), ("medium", 1)]
    return [("medium", 2)]


def _recent_keys(db: Session, user: User, exclude_session_id: int) -> set[str]:
    recent_ids = select(MockInterviewSession.id).where(
        MockInterviewSession.tenant_id == user.tenant_id,
        MockInterviewSession.student_id == user.id,
        MockInterviewSession.interview_type == "coding",
        MockInterviewSession.id != exclude_session_id,
    ).order_by(MockInterviewSession.id.desc()).limit(RECENT_CODING_SESSIONS)
    return set(
        db.scalars(
            select(Attempt.problem_key).where(
                Attempt.tenant_id == user.tenant_id,
                Attempt.session_id.in_(recent_ids.scalar_subquery()),
            )
        )
    )


def _rank(session_id: int, key: str) -> str:
    """Deterministic per session (seeded by session id), varies between sessions."""
    return hashlib.sha256(f"{session_id}:{key}".encode("utf-8")).hexdigest()


def select_problems(db: Session, user: User, s: MockInterviewSession) -> list[coding_bank.Problem]:
    cfg = config_of(s)
    key = cfg.get("problem_key")
    if key:
        p = coding_bank.get_problem(key)
        return [p] if p is not None and coding_bank.selectable(p) else []
    pool = coding_bank.selectable_problems()
    topics = set(cfg.get("topics") or [])
    recent = _recent_keys(db, user, s.id)
    chosen: list[coding_bank.Problem] = []
    for difficulty, count in _slots(s):
        taken = {p.key for p in chosen}
        candidates = [p for p in pool if p.difficulty == difficulty and p.key not in taken]
        # Prefer topic matches, then problems not seen in the last 10 coding
        # rounds; fall back to repeats rather than an empty round.
        candidates.sort(
            key=lambda p: (
                bool(topics) and not (topics & set(p.topics)),
                p.key in recent,
                _rank(s.id, p.key),
            )
        )
        chosen.extend(candidates[:count])
    return chosen


def start(db: Session, user: User, s: MockInterviewSession) -> MockInterviewSession:
    if s.status in svc.TERMINAL_STATUSES:
        raise _http(status.HTTP_409_CONFLICT, MSG_ENDED)
    if s.status == "in_progress":
        return s  # idempotent
    problems = select_problems(db, user, s)
    if not problems:
        raise _http(status.HTTP_409_CONFLICT, MSG_NO_PROBLEMS)
    s_id, tenant_id = s.id, s.tenant_id
    now = svc.utcnow()
    started = db.execute(
        update(MockInterviewSession)
        .where(
            MockInterviewSession.id == s_id,
            MockInterviewSession.tenant_id == tenant_id,
            MockInterviewSession.status == "setup",
            MockInterviewSession.updated_at == s.updated_at,
        )
        .values(status="in_progress", started_at=now, updated_at=now)
    )
    if started.rowcount != 1:
        db.rollback()
        return svc._reload(db, s_id)  # started (or ended) concurrently
    for seq, p in enumerate(problems, start=1):
        db.add(
            Attempt(
                tenant_id=tenant_id,
                session_id=s_id,
                seq=seq,
                problem_key=p.key,
                problem_version=p.version,
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.info("coding round %s: concurrent start resolved", s_id)
    logger.info("coding round %s: started with %d problem(s)", s_id, len(problems))
    return svc._reload(db, s_id)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def require_coding(s: MockInterviewSession) -> MockInterviewSession:
    if not is_coding(s):
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_CODING)
    return s


def attempts(db: Session, s: MockInterviewSession) -> list[Attempt]:
    return list(
        db.scalars(
            select(Attempt)
            .where(Attempt.session_id == s.id, Attempt.tenant_id == s.tenant_id)
            .order_by(Attempt.seq.asc())
        )
    )


def attempt(db: Session, s: MockInterviewSession, seq: int) -> Attempt:
    a = db.scalar(
        select(Attempt).where(
            Attempt.session_id == s.id, Attempt.tenant_id == s.tenant_id, Attempt.seq == seq
        )
    )
    if a is None:
        raise _http(status.HTTP_404_NOT_FOUND, "Problem not found")
    return a


def _problem_for(a: Attempt) -> coding_bank.Problem:
    if not coding_bank.bank().ok:
        raise _http(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_BANK_DOWN)
    p = coding_bank.get_problem(a.problem_key)
    if p is None:
        raise _http(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_BANK_DOWN)
    return p


def _require_open(s: MockInterviewSession, a: Attempt) -> None:
    if s.status != "in_progress":
        raise _http(status.HTTP_409_CONFLICT, MSG_NOT_IN_PROGRESS)
    if a.status not in OPEN_ATTEMPT:
        raise _http(status.HTTP_409_CONFLICT, MSG_PROBLEM_CLOSED)


def _require_language(s: MockInterviewSession, language: str) -> None:
    allowed = config_of(s).get("languages") or list(coding_bank.LANGUAGES)
    if language not in allowed:
        raise _http(422, MSG_LANGUAGE)


# ---------------------------------------------------------------------------
# Draft, graded submission, hint, done
# ---------------------------------------------------------------------------


def save_draft(
    db: Session, s: MockInterviewSession, seq: int, language: str, source: str, draft_rev: int
) -> dict:
    a = attempt(db, s, seq)
    _require_open(s, a)
    _require_language(s, language)
    if _too_large(source):
        raise _http(422, MSG_TOO_LARGE)
    now = svc.utcnow()
    saved = db.execute(
        update(Attempt)
        .where(
            Attempt.id == a.id,
            Attempt.tenant_id == s.tenant_id,
            Attempt.status.in_(OPEN_ATTEMPT),
            Attempt.draft_rev == draft_rev,
        )
        .values(
            draft_source=source,
            language=language,
            draft_rev=draft_rev + 1,
            status="working",
            started_at=func.coalesce(Attempt.started_at, now),
            updated_at=now,
        )
    )
    if saved.rowcount != 1:
        db.rollback()
        db.refresh(a)
        raise _http(
            status.HTTP_409_CONFLICT,
            MSG_PROBLEM_CLOSED if a.status not in OPEN_ATTEMPT else MSG_DRAFT_STALE,
        )
    db.commit()
    return {"seq": seq, "draft_rev": draft_rev + 1, "saved_at": now}


def _by_idempotency(db: Session, a: Attempt, key: str) -> Submission | None:
    return db.scalar(
        select(Submission).where(
            Submission.attempt_id == a.id,
            Submission.tenant_id == a.tenant_id,
            Submission.idempotency_key == key,
        )
    )


def _same_code(db: Session, a: Attempt, language: str, digest: str) -> Submission | None:
    """An earlier graded submission of identical code (spec 3.9: no new count)."""
    return db.scalar(
        select(Submission)
        .where(
            Submission.attempt_id == a.id,
            Submission.tenant_id == a.tenant_id,
            Submission.kind.in_(GRADED_KINDS),
            Submission.language == language,
            Submission.source_sha256 == digest,
            Submission.status.in_(("completed", "not_executed")),
        )
        .order_by(Submission.id.asc())
        .limit(1)
    )


def _weekly_graded(db: Session, user: User, problem_key: str, now: datetime) -> int:
    return int(
        db.scalar(
            select(func.count(Submission.id))
            .join(
                Attempt,
                (Attempt.id == Submission.attempt_id) & (Attempt.tenant_id == Submission.tenant_id),
            )
            .join(
                MockInterviewSession,
                (MockInterviewSession.id == Attempt.session_id)
                & (MockInterviewSession.tenant_id == Attempt.tenant_id),
            )
            .where(
                Submission.tenant_id == user.tenant_id,
                MockInterviewSession.student_id == user.id,
                Attempt.problem_key == problem_key,
                Submission.kind.in_(GRADED_KINDS),
                Submission.status.in_(COUNTED_STATUSES),
                Submission.created_at >= now - timedelta(days=7),
            )
        )
        or 0
    )


def _new_submission(
    s: MockInterviewSession, a: Attempt, *, kind: str, language: str, source: str,
    digest: str, key: str | None, now: datetime,
) -> Submission:
    return Submission(
        tenant_id=s.tenant_id,
        session_id=s.id,
        attempt_id=a.id,
        kind=kind,
        language=language,
        source=source,
        source_sha256=digest,
        execution_id=str(uuid.uuid4()),
        idempotency_key=key,
        # Review-only (P8-D8): stored for the later AI review, never executed.
        status="not_executed",
        created_at=now,
    )


def submit(
    db: Session, user: User, s: MockInterviewSession, seq: int, language: str, source: str, key: str
) -> Submission:
    a = attempt(db, s, seq)
    _require_open(s, a)
    _require_language(s, language)
    if _too_large(source):
        raise _http(422, MSG_TOO_LARGE)
    if not source.strip():
        raise _http(422, MSG_EMPTY)
    digest = _sha256(source)

    earlier = _by_idempotency(db, a, key)
    if earlier is not None:
        if earlier.source_sha256 != digest or earlier.language != language:
            raise _http(status.HTTP_409_CONFLICT, MSG_KEY_REUSED)
        return earlier
    duplicate = _same_code(db, a, language, digest)
    if duplicate is not None:
        return duplicate
    now = svc.utcnow()
    if _weekly_graded(db, user, a.problem_key, now) >= WEEKLY_PROBLEM_GRADED_CAP:
        raise _http(status.HTTP_429_TOO_MANY_REQUESTS, MSG_WEEKLY_LIMIT)

    sub = _new_submission(s, a, kind="graded", language=language, source=source,
                          digest=digest, key=key, now=now)
    db.add(sub)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _idempotent_retry(db, a, key, digest, language)
    counted = db.execute(
        update(Attempt)
        .where(
            Attempt.id == a.id,
            Attempt.tenant_id == s.tenant_id,
            Attempt.status.in_(OPEN_ATTEMPT),
            Attempt.submissions_used < GRADED_PER_PROBLEM,
            Attempt.in_flight_submission_id.is_(None),
        )
        .values(
            submissions_used=Attempt.submissions_used + 1,
            language=language,
            status="working",
            started_at=func.coalesce(Attempt.started_at, now),
            updated_at=now,
        )
    )
    if counted.rowcount != 1:
        db.rollback()
        db.refresh(a)
        if a.status not in OPEN_ATTEMPT:
            raise _http(status.HTTP_409_CONFLICT, MSG_PROBLEM_CLOSED)
        raise _http(status.HTTP_409_CONFLICT, MSG_GRADED_LIMIT)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _idempotent_retry(db, a, key, digest, language)
    db.refresh(sub)
    logger.info("coding round %s: graded submission %s stored (not executed)", s.id, sub.id)
    return sub


def _idempotent_retry(db: Session, a: Attempt, key: str, digest: str, language: str) -> Submission:
    """A parallel request with the same key won the insert: return its row."""
    earlier = _by_idempotency(db, a, key)
    if earlier is None:
        raise _http(status.HTTP_409_CONFLICT, MSG_GRADED_LIMIT)
    if earlier.source_sha256 != digest or earlier.language != language:
        raise _http(status.HTTP_409_CONFLICT, MSG_KEY_REUSED)
    return earlier


def reveal_hint(db: Session, s: MockInterviewSession, seq: int) -> dict:
    a = attempt(db, s, seq)
    _require_open(s, a)
    problem = _problem_for(a)
    available = hints_available(s, problem)
    now = svc.utcnow()
    taken = db.execute(
        update(Attempt)
        .where(
            Attempt.id == a.id,
            Attempt.tenant_id == s.tenant_id,
            Attempt.status.in_(OPEN_ATTEMPT),
            Attempt.hints_used < available,
        )
        .values(
            hints_used=Attempt.hints_used + 1,
            status="working",
            started_at=func.coalesce(Attempt.started_at, now),
            updated_at=now,
        )
    )
    if taken.rowcount != 1:
        db.rollback()
        db.refresh(a)
        raise _http(
            status.HTTP_409_CONFLICT,
            MSG_PROBLEM_CLOSED if a.status not in OPEN_ATTEMPT else MSG_NO_HINTS,
        )
    db.commit()
    db.refresh(a)
    number = a.hints_used
    return {
        "seq": seq,
        "hint_number": number,
        "hint": problem.hints[number - 1],
        "hints_used": number,
        "hints_available": available,
    }


def mark_done(db: Session, s: MockInterviewSession, seq: int) -> None:
    """Close one problem. Explanation turns ("explaining") arrive with 8.3."""
    a = attempt(db, s, seq)
    if a.status == "closed":
        return  # idempotent
    _require_open(s, a)
    now = svc.utcnow()
    closed = db.execute(
        update(Attempt)
        .where(Attempt.id == a.id, Attempt.tenant_id == s.tenant_id, Attempt.status.in_(OPEN_ATTEMPT))
        .values(status="closed", closed_at=now, updated_at=now)
    )
    db.commit()
    if closed.rowcount != 1:
        db.refresh(a)
        if a.status != "closed":
            raise _http(status.HTTP_409_CONFLICT, MSG_PROBLEM_CLOSED)


# ---------------------------------------------------------------------------
# End: finish / time up / abandon
# ---------------------------------------------------------------------------


def end(db: Session, s: MockInterviewSession, *, abandon: bool, reason: str = "ended_by_candidate") -> MockInterviewSession:
    """End the round in ONE transaction; always wins over a concurrent submit.

    Finish / time up: each open problem's saved draft becomes an automatic
    graded submission (unless 3 graded submissions already exist or identical
    code was already submitted). The round is completed when at least one
    graded submission exists, otherwise abandoned - like an interview with no
    answers.
    """
    if s.status in svc.TERMINAL_STATUSES:
        return s  # idempotent
    s_id, tenant_id = s.id, s.tenant_id
    now = svc.utcnow()
    meta = svc._plan_meta(s)
    meta.pop("lease", None)
    rows = attempts(db, s)

    auto = 0
    if not abandon:
        for a in rows:
            if a.status not in OPEN_ATTEMPT or not (a.draft_source or "").strip():
                continue
            if a.submissions_used >= GRADED_PER_PROBLEM or a.language is None:
                continue
            digest = _sha256(a.draft_source)
            if _same_code(db, a, a.language, digest) is not None:
                continue
            counted = db.execute(
                update(Attempt)
                .where(
                    Attempt.id == a.id,
                    Attempt.tenant_id == tenant_id,
                    Attempt.status.in_(OPEN_ATTEMPT),
                    Attempt.submissions_used < GRADED_PER_PROBLEM,
                )
                .values(submissions_used=Attempt.submissions_used + 1, updated_at=now)
            )
            if counted.rowcount == 1:
                db.add(_new_submission(s, a, kind="auto", language=a.language, source=a.draft_source,
                                       digest=digest, key=None, now=now))
                auto += 1
        db.flush()
    graded = int(
        db.scalar(
            select(func.count(Submission.id)).where(
                Submission.session_id == s_id,
                Submission.tenant_id == tenant_id,
                Submission.kind.in_(GRADED_KINDS),
            )
        )
        or 0
    )
    final_status = "abandoned" if abandon or graded == 0 else "completed"
    meta["end_reason"] = "abandoned" if final_status == "abandoned" else reason
    if final_status == "completed":
        meta["closing_message"] = _CLOSING
    ended = db.execute(
        update(MockInterviewSession)
        .where(
            MockInterviewSession.id == s_id,
            MockInterviewSession.tenant_id == tenant_id,
            MockInterviewSession.status.in_(svc.ACTIVE_STATUSES),
        )
        .values(status=final_status, plan_json=meta, completed_at=now, updated_at=now)
    )
    if ended.rowcount != 1:
        db.rollback()  # ended concurrently: keep the other result
        return svc._reload(db, s_id)
    db.execute(
        update(Attempt)
        .where(
            Attempt.session_id == s_id,
            Attempt.tenant_id == tenant_id,
            Attempt.status.not_in(FINAL_ATTEMPT),
        )
        .values(
            status="abandoned" if final_status == "abandoned" else "closed",
            closed_at=now,
            updated_at=now,
        )
    )
    db.commit()
    logger.info(
        "coding round %s: ended (%s, %s); %d automatic submission(s)", s_id, final_status,
        meta["end_reason"], auto,
    )
    return svc._reload(db, s_id)


def expire_if_overdue(db: Session, s: MockInterviewSession) -> MockInterviewSession:
    """Time up: end the round at once (no AI step can be in flight in Batch 1)."""
    if s.status != "in_progress":
        return s
    remaining = svc.remaining_seconds(s)
    if remaining is None or remaining > 0:
        return s
    return end(db, s, abandon=False, reason="time_up")


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def summary(db: Session, s: MockInterviewSession) -> dict:
    rows = attempts(db, s)
    graded = int(
        db.scalar(
            select(func.count(Submission.id)).where(
                Submission.session_id == s.id,
                Submission.tenant_id == s.tenant_id,
                Submission.kind.in_(GRADED_KINDS),
            )
        )
        or 0
    )
    return {
        "problems": len(rows),
        "problems_closed": sum(1 for a in rows if a.status == "closed"),
        "graded_submissions": graded,
        "review_only": True,
    }


def submission_view(sub: Submission, seq: int, best_id: int | None) -> dict:
    return {
        "id": sub.id,
        "attempt_seq": seq,
        "kind": sub.kind,
        "language": sub.language,
        "status": sub.status,
        "verdict": sub.verdict,
        "verification": "verified" if sub.status == "completed" else "not_verified",
        "is_best": best_id is not None and sub.id == best_id,
        "created_at": sub.created_at,
    }


def get_submission(db: Session, s: MockInterviewSession, submission_id: int) -> dict:
    sub = db.scalar(
        select(Submission).where(
            Submission.id == submission_id,
            Submission.session_id == s.id,
            Submission.tenant_id == s.tenant_id,
        )
    )
    if sub is None:
        raise _http(status.HTTP_404_NOT_FOUND, "Submission not found")
    a = db.scalar(select(Attempt).where(Attempt.id == sub.attempt_id, Attempt.tenant_id == s.tenant_id))
    return submission_view(sub, a.seq if a else 0, a.best_submission_id if a else None)


def list_submissions(db: Session, s: MockInterviewSession, seq: int) -> list[dict]:
    a = attempt(db, s, seq)
    rows = db.scalars(
        select(Submission)
        .where(Submission.attempt_id == a.id, Submission.tenant_id == s.tenant_id)
        .order_by(Submission.id.asc())
    )
    return [submission_view(sub, a.seq, a.best_submission_id) for sub in rows]


def state(db: Session, s: MockInterviewSession) -> dict:
    cfg = config_of(s)
    out = []
    for a in attempts(db, s):
        problem = _problem_for(a)
        available = hints_available(s, problem)
        out.append(
            {
                "seq": a.seq,
                "status": a.status,
                "language": a.language,
                "draft_source": a.draft_source,
                "draft_rev": a.draft_rev,
                "hints_used": a.hints_used,
                "hints_available": available,
                "submissions_used": a.submissions_used,
                "submissions_remaining": max(0, GRADED_PER_PROBLEM - a.submissions_used),
                "problem": coding_bank.public_view(problem, a.hints_used),
            }
        )
    remaining = svc.remaining_seconds(s) if s.status == "in_progress" else None
    return {
        "session_id": s.id,
        "status": s.status,
        "languages": cfg.get("languages") or list(coding_bank.LANGUAGES),
        "review_only": True,
        "remaining_seconds": max(0, remaining) if remaining is not None else None,
        "graded_submissions_per_problem": GRADED_PER_PROBLEM,
        "attempts": out,
    }
