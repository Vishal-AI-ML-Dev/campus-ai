"""AI Mock Interview reports and history (Phase 4).

A completed interview gets ONE final, evidence-backed report:

  * The AI worker scores the transcript (only the questions and answers; no
    resume, JD or profile is sent) and the result is re-validated here before
    anything is stored: rubric keys only, seqs from this session, 0-5 / 0-100
    ranges, every non-zero score carrying an evidence quote.
  * Generation is single-flight per session (the Phase 3 lease) and
    idempotent: an existing report is returned without any AI call, and the
    unique ``session_id`` key is the last line of defence.
  * A worker failure stores nothing; the student retries.
  * Reports are final (no regeneration, no score-shopping).

Storage (existing Phase 2 columns): ``category_scores_json`` holds
``{"categories": [...], "questions": [...]}``; question text and answers are
joined from the turns at read time, never copied.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    MockInterviewReport,
    MockInterviewSession,
    MockInterviewTurn,
    User,
)
from app.schemas.mock_interview import WorkerReport
from app.services import mock_interview as svc
from app.services import mock_interview_ai as engine

logger = logging.getLogger("campus_ai.mock_interview")

REPORT_LEASE_SECONDS = 180  # worker timeout 120 s + one connect retry
# After this many "no answer could be assessed with evidence" results the
# session gets a final "couldn't assess" answer instead of endless paid retries.
MAX_UNASSESSABLE_ATTEMPTS = 3
MAX_SCORE = 5
# The rubric (keys + weights) is mirrored from ai-worker/interview/rubric.py so
# the backend can recompute every aggregate itself (it never trusts the
# worker's arithmetic). The contract fixture test keeps the two in sync.
RUBRICS = {
    "hr-v1": {
        "relevance": 0.25,
        "structure": 0.20,
        "specificity": 0.20,
        "communication": 0.20,
        "self_awareness": 0.15,
    },
    "technical-v1": {
        "technical_accuracy": 0.35,
        "depth": 0.25,
        "problem_solving": 0.15,
        "communication": 0.15,
        "relevance": 0.10,
    },
}
RUBRIC_FOR_TYPE = {"hr": "hr-v1", "technical": "technical-v1"}

# Modality-independent (Phase 5): answers may be typed or speech-to-text.
# Built at read time, so it applies to existing reports too; nothing is stored.
DISCLAIMER = (
    "This is AI-generated practice feedback based on the answers you submitted "
    "during this practice session, including typed or speech-to-text answers. "
    "It can make mistakes, and practice scores do not predict selection in a "
    "real interview."
)
_REPORT_DOWN = (
    "We couldn't prepare your feedback report right now. Your answers are saved; "
    "please try again in a moment."
)
_REPORT_BUSY = "Your feedback report is already being prepared. Please wait a moment."
_UNASSESSABLE = (
    "We couldn't assess the answers in this interview reliably, so no report "
    "can be produced for it. Try another practice interview."
)


def _not_found_report() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Report not generated yet"
    )


def stored_report(db: Session, s: MockInterviewSession) -> MockInterviewReport | None:
    return db.scalar(
        select(MockInterviewReport).where(
            MockInterviewReport.session_id == s.id,
            MockInterviewReport.tenant_id == s.tenant_id,
        )
    )


# ---------------------------------------------------------------------------
# Validation of the worker's output
# ---------------------------------------------------------------------------

_MD_CHARS = re.compile(r"[*_`#>|]")
_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Same normalization as the worker's ``safety.normalize`` (+ invisible chars)."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf").lower()
    return _SPACES.sub(" ", _MD_CHARS.sub(" ", text)).strip()


def _round_half_up(value: float, digits: int = 1) -> float:
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def _validated(
    raw: dict, interview_type: str, answers: dict[int, str | None]
) -> WorkerReport:
    """Strict checks on top of the schema, then recompute every aggregate.

    ``answers``: seq -> stored answer text (None = unanswered), for every turn.
    Raises ValueError (or ValidationError) when the output is unusable.
    """
    report = WorkerReport.model_validate(raw)
    expected = RUBRIC_FOR_TYPE.get(interview_type)
    if expected is None or report.rubric_version != expected:
        raise ValueError("unexpected rubric")
    weights = RUBRICS[expected]
    cat_keys = [c.key for c in report.categories]
    if sorted(cat_keys) != sorted(weights):
        raise ValueError("unexpected categories")
    if [q.seq for q in report.questions] != list(answers):
        raise ValueError("questions do not match the transcript")

    for q in report.questions:
        answer = answers[q.seq]
        if not set(q.scores) <= set(weights):
            raise ValueError("unexpected score key")
        if (q.status == "no_answer") != (answer is None):
            raise ValueError("answered / unanswered mismatch")
        if q.status in ("no_answer", "dont_know") and (
            set(q.scores) != set(weights) or any(sc.score for sc in q.scores.values())
        ):
            raise ValueError("missing answers must score 0 in every category")
        if q.status == "scored" and not q.scores:
            raise ValueError("scored question without scores")
        if q.status == "not_assessed" and q.scores:
            raise ValueError("unassessed question with scores")
        for sc in q.scores.values():
            if sc.score > 0 and not (
                sc.evidence.strip() and _norm(sc.evidence) in _norm(answer or "")
            ):
                raise ValueError("score without evidence from the answer")
    for item in [*report.strengths, *report.weaknesses]:
        if item.seq is not None and not (
            answers.get(item.seq)
            and item.evidence.strip()
            and _norm(item.evidence) in _norm(answers[item.seq] or "")
        ):
            raise ValueError("item cites something the candidate did not say")

    # Authoritative aggregates (same rules as the worker's rubric.aggregate).
    exact: dict[str, float | None] = {}
    for c in report.categories:
        values = [q.scores[c.key].score for q in report.questions if c.key in q.scores]
        exact[c.key] = sum(values) / len(values) * (100 / MAX_SCORE) if values else None
        c.weight = weights[c.key]
        c.assessed = len(values)
        c.score = _round_half_up(exact[c.key]) if values else None
    weighted = [(weights[k], v) for k, v in exact.items() if v is not None]
    if not weighted:
        raise ValueError("nothing was assessed")
    total = sum(w for w, _ in weighted)
    report.overall_score = max(
        0.0, min(100.0, _round_half_up(sum(w * v for w, v in weighted) / total))
    )
    return report


# ---------------------------------------------------------------------------
# Generate / read
# ---------------------------------------------------------------------------


def get_report(db: Session, s: MockInterviewSession) -> MockInterviewReport:
    r = stored_report(db, s)
    if r is None:
        raise _not_found_report()
    return r


def _slim_plan(plan) -> dict | None:
    """Topic names + focus only: no resume/JD grounding quotes or JD analysis."""
    if not isinstance(plan, dict) or not isinstance(plan.get("topics"), list):
        return None
    topics = [
        {
            "name": str(t.get("name") or "")[:100],
            "focus": str(t.get("focus") or "")[:300],
        }
        for t in plan["topics"]
        if isinstance(t, dict) and t.get("name")
    ][:12]
    if not topics:
        return None
    budget = plan.get("question_budget")
    budget = (
        budget if isinstance(budget, int) and 1 <= budget <= svc.HARD_MAX_TURNS else 1
    )
    return {"topics": topics, "question_budget": budget}


def generate_report(db: Session, s: MockInterviewSession) -> MockInterviewReport:
    existing = stored_report(db, s)
    if existing is not None:
        return existing  # final: no AI call, no regeneration
    if s.status == "abandoned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled interviews don't have a feedback report.",
        )
    if s.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finish the interview to get your feedback report.",
        )
    meta = svc._plan_meta(s)
    if int(meta.get("report_unassessable") or 0) >= MAX_UNASSESSABLE_ATTEMPTS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_UNASSESSABLE)
    turns = svc._turns(db, s)
    # Completed sessions always have >= 1 answer (Phase 3: finish/time-out
    # without answers -> abandoned); kept as a guard.
    if not any(t.answer_text is not None for t in turns):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There are no answers to evaluate in this interview.",
        )

    # Capture plain values now: the lease commit below expires ORM objects.
    s_id, tenant_id, interview_type = s.id, s.tenant_id, s.interview_type
    student_id = s.student_id
    answers = {t.seq: t.answer_text for t in turns}
    payload = {
        "config": svc._config(s),
        "plan": _slim_plan(meta.get("plan")),
        "transcript": [
            {
                "seq": t.seq,
                "topic": t.topic,
                "question": t.question,
                "is_follow_up": t.is_follow_up,
                "answer": t.answer_text,
            }
            for t in turns  # <= 30 turns (Phase 3 hard cap); worker needs seqs 1..n
        ],
    }
    token, meta = svc._claim(db, s, seconds=REPORT_LEASE_SECONDS, busy=_REPORT_BUSY)
    try:
        with svc._ai_scope(tenant_id, student_id, s_id):  # usage ledger (Phase 7.3)
            raw_report = engine.generate_report(payload)
        report = _validated(raw_report, interview_type, answers)
    except (engine.InterviewEngineUnavailable, ValidationError, ValueError) as exc:
        timed_out = getattr(exc, "timed_out", False)
        unassessable = getattr(exc, "status_code", None) == 422
        logger.warning(
            "mock interview %s: report unavailable (%s, status=%s)",
            s_id,
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        if timed_out:
            # The worker may still be running it: keep the lease (it expires)
            # so a retry can't start a second paid run in parallel.
            db.rollback()
        else:
            if unassessable:
                meta = {
                    **meta,
                    "report_unassessable": int(meta.get("report_unassessable") or 0)
                    + 1,
                }
            svc._release(db, s_id, tenant_id, token, meta)
            if (
                unassessable
                and meta["report_unassessable"] >= MAX_UNASSESSABLE_ATTEMPTS
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=_UNASSESSABLE
                ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_REPORT_DOWN
        ) from None

    # Release the lease and store the report in ONE commit. If either fails
    # (lease lost / parallel insert) the rollback also undoes the release; the
    # lease then simply expires, harmless on a completed session.
    if not svc._finalize(db, s_id, tenant_id, token, plan_json=meta):
        db.rollback()
        existing = db.scalar(
            select(MockInterviewReport).where(
                MockInterviewReport.session_id == s_id,
                MockInterviewReport.tenant_id == tenant_id,
            )
        )
        if existing is not None:
            return existing
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_REPORT_BUSY)
    db.add(
        MockInterviewReport(
            tenant_id=tenant_id,
            session_id=s_id,
            overall_score=report.overall_score,
            category_scores_json={
                "categories": [c.model_dump() for c in report.categories],
                "questions": [q.model_dump() for q in report.questions],
            },
            strengths_json=[i.model_dump() for i in report.strengths[:3]],
            weaknesses_json=[i.model_dump() for i in report.weaknesses[:3]],
            recommendations_json=list(report.recommendations[:5]),
            rubric_version=report.rubric_version,
            provider=(report.provider or None),
        )
    )
    try:
        db.commit()
    except IntegrityError:  # a parallel request stored it first
        db.rollback()
        logger.info("mock interview %s: concurrent report resolved", s_id)
    s = svc._reload(db, s_id)
    return get_report(db, s)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def history(db: Session, user: User) -> list[dict]:
    sessions = svc.list_sessions(db, user)
    ids = [s.id for s in sessions]
    reports: dict[int, MockInterviewReport] = {}
    answered: dict[int, int] = {}
    if ids:
        for r in db.scalars(
            select(MockInterviewReport).where(
                MockInterviewReport.tenant_id == user.tenant_id,
                MockInterviewReport.session_id.in_(ids),
            )
        ):
            reports[r.session_id] = r
        for sid, count in db.execute(
            select(MockInterviewTurn.session_id, func.count())
            .where(
                MockInterviewTurn.tenant_id == user.tenant_id,
                MockInterviewTurn.session_id.in_(ids),
                MockInterviewTurn.answer_text.is_not(None),
            )
            .group_by(MockInterviewTurn.session_id)
        ):
            answered[sid] = int(count)
    out = []
    for s in sessions:
        r = reports.get(s.id)
        out.append(
            {
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
                "answered": answered.get(s.id, 0),
                "report": (
                    {"overall_score": r.overall_score, "created_at": r.created_at}
                    if r
                    else None
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _int(value) -> int:
    try:
        return max(0, min(MAX_SCORE, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _items(value) -> list[dict]:
    out = []
    for item in _list(value):
        if isinstance(item, dict) and item.get("text"):
            out.append(
                {
                    "text": str(item["text"]),
                    "seq": item.get("seq")
                    if isinstance(item.get("seq"), int)
                    else None,
                    "evidence": str(item.get("evidence") or ""),
                }
            )
    return out


def to_report(db: Session, s: MockInterviewSession, r: MockInterviewReport) -> dict:
    data = r.category_scores_json if isinstance(r.category_scores_json, dict) else {}
    categories = [c for c in _list(data.get("categories")) if isinstance(c, dict)]
    labels = {c.get("key"): c.get("label") or c.get("key") for c in categories}
    order = [c.get("key") for c in categories]
    by_seq = {
        q.get("seq"): q for q in _list(data.get("questions")) if isinstance(q, dict)
    }
    questions = []
    for t in svc._turns(db, s):
        q = by_seq.get(t.seq)
        if q is None:
            continue
        raw_scores = q.get("scores") if isinstance(q.get("scores"), dict) else {}
        scores = [
            {
                "key": k,
                "label": str(labels.get(k) or k),
                "score": _int(raw_scores[k].get("score")),
                "evidence": str(raw_scores[k].get("evidence") or ""),
            }
            for k in order
            if isinstance(raw_scores.get(k), dict)
        ]
        questions.append(
            {
                "seq": t.seq,
                "topic": t.topic,
                "question": t.question,
                "is_follow_up": t.is_follow_up,
                "answer_text": t.answer_text,
                "status": q.get("status") or "not_assessed",
                "scores": scores,
                "feedback": str(q.get("feedback") or ""),
            }
        )
    return {
        "session_id": s.id,
        "interview_type": s.interview_type,
        "target_role": s.target_role,
        "company_name": s.company_name,
        "difficulty": s.difficulty,
        "language": s.language,
        "duration_minutes": s.duration_minutes,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
        "overall_score": r.overall_score,
        "rubric_version": r.rubric_version,
        "created_at": r.created_at,
        "categories": [
            {
                "key": str(c.get("key")),
                "label": str(c.get("label") or c.get("key")),
                "weight": float(c.get("weight") or 0),
                "description": str(c.get("description") or ""),
                "score": c.get("score"),
                "assessed": int(c.get("assessed") or 0),
            }
            for c in categories
        ],
        "questions": questions,
        "strengths": _items(r.strengths_json),
        "weaknesses": _items(r.weaknesses_json),
        "recommendations": [str(x) for x in _list(r.recommendations_json) if x],
        "disclaimer": DISCLAIMER,
    }
