"""AI help inside a coding round (Phase 8.3, review-only).

Behind ``AI_MOCK_INTERVIEW_CODING_AI_ENABLED`` (default off) and the coding
gate. Code is sent to the AI worker as text to READ; nothing is executed
anywhere (decision P8-D8). Hidden tests, expected outputs and reference
solutions do not exist in this service and are never sent.

* **AI hint** - only after the authored hints run out and only within the
  per-problem hint cap (P8-D6). The cap is reserved atomically BEFORE the
  worker call and given back if the call fails, so concurrent requests can
  never exceed it. The hint text is filtered again here (<= 3 code lines, no
  verified-correctness claims) and is NOT stored (shown once). Hints never
  change any score (P8-D7 rule 1).
* **Explanation turns** - after "Done" the problem moves to ``explaining``
  (compare-and-set, so only one request asks the worker) and 1-2 typed
  questions are asked (decision B1-a: text only). They are ordinary
  ``mock_interview_turns`` rows with topic ``coding:<seq>:explain``, answered
  through the existing ``POST /sessions/{id}/answer``. If the worker is down a
  fixed question is used, and a failed second question simply closes the
  problem - an explanation never blocks the round.

Every worker call writes one content-free usage-ledger row (steps
``code_hint`` / ``code_followup``). Logs carry ids and counts only.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    MockInterviewCodeAttempt as Attempt,
    MockInterviewCodeSubmission as Submission,
    MockInterviewSession,
    MockInterviewTurn,
)
from app.services import coding_bank
from app.services import mock_interview as svc
from app.services import mock_interview_ai as engine
from app.services import mock_interview_coding as coding
from app.services import mock_interview_coding_guards as guards

logger = logging.getLogger("campus_ai.mock_interview.coding")

MAX_EXPLANATION_QUESTIONS = 2
# Worker call: 60 s timeout + one connect retry; same margin as the session lease.
HEAL_AFTER_SECONDS = svc.LEASE_SECONDS
EXPLAIN_TOPIC = "coding:{seq}:explain"
FALLBACK_QUESTIONS = (
    "Walk me through your approach. What are its time and space complexity, and why?",
    "Which edge cases did you consider, and how would you test your solution?",
)
MSG_HINT_DOWN = (
    "A personalised hint isn't available right now. Your hint count was not used; "
    "please try again in a moment."
)
MSG_HINT_UNSAFE = (
    "We couldn't produce a hint that doesn't give away the solution for your current code. "
    "This hint was used."
)
MSG_NOT_CURRENT = "That question is no longer the current one. Refresh to continue."
MSG_ALREADY_ANSWERED = "This question was already answered"
MSG_SPOKEN = "Spoken answers are only accepted in a voice interview"


def explain_topic(seq: int) -> str:
    return EXPLAIN_TOPIC.format(seq=seq)


def attempt_seq_of(topic: str | None) -> int | None:
    parts = (topic or "").split(":")
    if len(parts) == 3 and parts[0] == "coding" and parts[2] == "explain" and parts[1].isdecimal():
        return int(parts[1])
    return None


# ---------------------------------------------------------------------------
# Worker payload pieces (public problem material only)
# ---------------------------------------------------------------------------


def worker_problem(p: coding_bank.Problem, *, with_complexity: bool = False) -> dict:
    out = {
        "title": p.title,
        "difficulty": p.difficulty,
        "statement": p.statement,
        "input_format": p.input_format,
        "output_format": p.output_format,
        "constraints": p.constraints,
        "examples": [
            {"input": e.get("input", ""), "output": e.get("output", ""),
             "explanation": e.get("explanation")}
            for e in p.examples[:3]
        ],
    }
    if with_complexity:
        out["expected_complexity"] = dict(p.expected_complexity or {})
    return out


def latest_graded(db: Session, a: Attempt) -> Submission | None:
    """The newest graded/automatic submission of an attempt that holds code."""
    return db.scalar(
        select(Submission)
        .where(
            Submission.attempt_id == a.id,
            Submission.tenant_id == a.tenant_id,
            Submission.kind.in_(coding.GRADED_KINDS),
            Submission.status.in_(("not_executed", "completed")),
            Submission.source.is_not(None),
        )
        .order_by(Submission.id.desc())
        .limit(1)
    )


def _language(s: MockInterviewSession, a: Attempt) -> str:
    return a.language or (coding.config_of(s).get("languages") or list(coding_bank.LANGUAGES))[0]


def _current_code(db: Session, a: Attempt) -> tuple[str, str | None]:
    sub = latest_graded(db, a)
    if sub is not None and sub.source:
        return sub.source, sub.language
    return a.draft_source or "", a.language


# ---------------------------------------------------------------------------
# AI hint (called by coding.reveal_hint once the authored hints are used up)
# ---------------------------------------------------------------------------


def ai_hint(
    db: Session, s: MockInterviewSession, a: Attempt, problem: coding_bank.Problem, number: int
) -> str:
    """Hint ``number`` (> authored hints). The slot is already reserved; it is
    given back when no safe hint can be produced."""
    payload = {
        "problem": worker_problem(problem),
        "language": _language(s, a),
        # The student's current editor code; visible material only.
        "code": a.draft_source or "",
        "previous_hints": list(problem.hints)[:5],
        "hint_number": number,
    }
    s_id, tenant_id, student_id, a_id = s.id, s.tenant_id, s.student_id, a.id
    hint = None
    filtered = False
    try:
        with svc._ai_scope(tenant_id, student_id, s_id):
            out = engine.coding_hint(payload)
        hint = guards.safe_text(out.get("hint"), 600)
        filtered = hint is None
    except engine.InterviewEngineUnavailable as exc:
        # 422 = the worker produced only unsafe text (filtered), not an outage.
        filtered = exc.status_code == 422
    if hint is None and filtered:
        # Unsafe output keeps the slot used: otherwise a crafted draft could make
        # the AI retry for free until something slips through.
        logger.warning("coding round %s: AI hint %d filtered (slot kept)", s_id, number)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MSG_HINT_UNSAFE)
    if hint is None:
        # Give the reserved slot back (only if nothing else moved it).
        db.execute(
            update(Attempt)
            .where(Attempt.id == a_id, Attempt.tenant_id == tenant_id, Attempt.hints_used == number)
            .values(hints_used=number - 1)
        )
        db.commit()
        logger.warning("coding round %s: AI hint unavailable (slot restored)", s_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MSG_HINT_DOWN)
    logger.info("coding round %s: AI hint %d served", s_id, number)
    return hint


# ---------------------------------------------------------------------------
# Explanation turns
# ---------------------------------------------------------------------------


def explanation_turns(db: Session, s: MockInterviewSession) -> dict[int, list[MockInterviewTurn]]:
    """attempt seq -> its explanation turns, oldest first."""
    out: dict[int, list[MockInterviewTurn]] = {}
    for t in svc._turns(db, s):
        seq = attempt_seq_of(t.topic)
        if seq is not None:
            out.setdefault(seq, []).append(t)
    return out


def _ask(db: Session, s: MockInterviewSession, a: Attempt, asked: list[MockInterviewTurn]) -> str | None:
    """The next explanation question, or None to close the problem."""
    if len(asked) >= MAX_EXPLANATION_QUESTIONS:
        return None
    problem = coding_bank.get_problem(a.problem_key)
    fallback = FALLBACK_QUESTIONS[min(len(asked), len(FALLBACK_QUESTIONS) - 1)]
    if problem is None:
        return fallback if not asked else None
    code, language = _current_code(db, a)
    payload = {
        "problem": worker_problem(problem),
        "language": language or _language(s, a),
        "code": code,
        "explanation": [{"question": t.question, "answer": t.answer_text} for t in asked],
        "max_questions": MAX_EXPLANATION_QUESTIONS,
    }
    try:
        with svc._ai_scope(s.tenant_id, s.student_id, s.id):
            out = engine.coding_followup(payload)
    except engine.InterviewEngineUnavailable:
        # First question: always ask (fixed text). Later: just close.
        return fallback if not asked else None
    if out.get("action") != "ask":
        return None if asked else fallback
    question = guards.safe_text(out.get("question"), 300)
    if question is None or guards.too_much_code(question):
        return fallback if not asked else None
    return question


def _add_question(db: Session, s_id: int, tenant_id: int, a_id: int, a_seq: int, question: str) -> bool:
    """Append the question as the session's next turn, only while the problem is
    still being explained. A parallel insert of the same seq is retried."""
    for _ in range(3):
        still = db.execute(
            update(Attempt)
            .where(Attempt.id == a_id, Attempt.tenant_id == tenant_id, Attempt.status == "explaining")
            .values(updated_at=svc.utcnow())
        )
        if still.rowcount != 1:
            db.rollback()
            return False
        next_seq = int(
            db.scalar(
                select(func.coalesce(func.max(MockInterviewTurn.seq), 0)).where(
                    MockInterviewTurn.session_id == s_id, MockInterviewTurn.tenant_id == tenant_id
                )
            )
            or 0
        ) + 1
        db.add(
            MockInterviewTurn(
                tenant_id=tenant_id,
                session_id=s_id,
                seq=next_seq,
                topic=explain_topic(a_seq),
                question=question,
                is_follow_up=False,
            )
        )
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
    return False


def _close(db: Session, a_id: int, tenant_id: int) -> None:
    now = svc.utcnow()
    db.execute(
        update(Attempt)
        .where(Attempt.id == a_id, Attempt.tenant_id == tenant_id, Attempt.status == "explaining")
        .values(status="closed", closed_at=now, updated_at=now)
    )
    db.commit()


def done(db: Session, s: MockInterviewSession, seq: int) -> None:
    """ "Done with this problem": ask explanation questions when there is code."""
    a = coding.attempt(db, s, seq)
    if a.status == "closed":
        return  # idempotent
    if a.status == "explaining":
        # Idempotent. Also heals a problem left without an open question - but
        # only once it has been stuck longer than any in-flight question request
        # can take (a parallel "Done" may still be asking the worker).
        stuck_since = svc._aware(a.updated_at)
        if (
            s.status == "in_progress"
            and stuck_since is not None
            and (svc.utcnow() - stuck_since).total_seconds() > HEAL_AFTER_SECONDS
            and not any(t.answer_text is None for t in explanation_turns(db, s).get(a.seq, []))
        ):
            _close(db, a.id, s.tenant_id)
        return
    coding._require_open(s, a)
    if latest_graded(db, a) is None:
        # Nothing was submitted, so there is nothing to explain or review.
        coding.mark_done(db, s, seq)
        return
    s_id, tenant_id, a_id, a_seq = s.id, s.tenant_id, a.id, a.seq
    now = svc.utcnow()
    moved = db.execute(
        update(Attempt)
        .where(Attempt.id == a_id, Attempt.tenant_id == tenant_id, Attempt.status.in_(coding.OPEN_ATTEMPT))
        .values(status="explaining", updated_at=now)
    )
    db.commit()
    if moved.rowcount != 1:  # a parallel "Done" won (or the round ended)
        db.refresh(a)
        if a.status not in ("closed", "explaining"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=coding.MSG_PROBLEM_CLOSED)
        return
    stored = False
    try:
        a = coding.attempt(db, s, seq)
        question = _ask(db, s, a, []) or FALLBACK_QUESTIONS[0]
        stored = _add_question(db, s_id, tenant_id, a_id, a_seq, question)
    finally:
        if not stored:
            # Never leave the problem "explaining" without a question.
            db.rollback()
            _close(db, a_id, tenant_id)
            logger.info("coding round %s: problem %d closed without a question", s_id, a_seq)
    if stored:
        logger.info("coding round %s: problem %d explaining", s_id, a_seq)


def answer(
    db: Session, s: MockInterviewSession, turn_seq: int, text: str, source: str = "typed"
) -> MockInterviewSession:
    """Save a typed explanation answer, then ask the next question or close."""
    if s.status != "in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This interview is not in progress")
    if source != "typed":
        # Coding rounds are text-only (decision B1-a); same wording as Phase 5.
        raise HTTPException(status_code=422, detail=MSG_SPOKEN)
    turn = db.scalar(
        select(MockInterviewTurn).where(
            MockInterviewTurn.session_id == s.id,
            MockInterviewTurn.tenant_id == s.tenant_id,
            MockInterviewTurn.seq == turn_seq,
        )
    )
    a_seq = attempt_seq_of(turn.topic) if turn is not None else None
    if turn is None or a_seq is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_NOT_CURRENT)
    if turn.answer_text is not None:
        if turn.answer_text == text:
            return s  # idempotent repeat
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_ANSWERED)
    a = coding.attempt(db, s, a_seq)
    if a.status != "explaining":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=coding.MSG_PROBLEM_CLOSED)

    s_id, tenant_id, a_id = s.id, s.tenant_id, a.id
    saved = db.execute(
        update(MockInterviewTurn)
        .where(
            MockInterviewTurn.id == turn.id,
            MockInterviewTurn.tenant_id == tenant_id,
            MockInterviewTurn.answer_text.is_(None),
        )
        .values(answer_text=text, answer_source="typed", answered_at=svc.utcnow())
    )
    db.commit()
    if saved.rowcount != 1:
        db.refresh(turn)
        if turn.answer_text != text:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_ALREADY_ANSWERED)
        return svc._reload(db, s_id)  # a parallel identical request is handling it

    asked = explanation_turns(db, s).get(a_seq, [])
    if any(t.answer_text is None for t in asked):
        return svc._reload(db, s_id)
    a = coding.attempt(db, s, a_seq)
    question = _ask(db, s, a, asked)
    if question is None or not _add_question(db, s_id, tenant_id, a_id, a_seq, question):
        _close(db, a_id, tenant_id)
    return svc._reload(db, s_id)


def state_extras(db: Session, s: MockInterviewSession) -> dict[int, list[dict]]:
    """attempt seq -> explanation turns for ``GET /sessions/{id}/coding``."""
    return {
        seq: [
            {
                "turn_seq": t.seq,
                "question": t.question,
                "answer_text": t.answer_text,
                "answered_at": t.answered_at,
            }
            for t in turns
        ]
        for seq, turns in explanation_turns(db, s).items()
    }
