"""Review-only coding feedback (Phase 8.3, rubric ``coding-v1-unverified``).

Decision O1 = option A (approved 2026-11-23): coding feedback lives in its own
table, ``mock_interview_code_reviews`` (one row per problem), behind its own
routes ``GET/POST /sessions/{id}/coding/feedback``. The HR/technical report
table and its NOT NULL ``overall_score`` are never touched; the existing
report routes keep answering coding rounds with their documented 409.

What review-only feedback is (decision P8-D7, rules 1-8, U3):
* the three objective categories (correctness 40 %, robustness 20 %,
  efficiency 10 %) are ``not_verified`` - the code was never executed;
* the three AI categories (code quality 15 %, reasoning 10 %, testing 5 %) are
  shown one by one as 0-5 "AI estimate" values; each needs verified evidence
  (a code quote at its line, or a quote from the candidate's own typed
  explanation), else it is "not assessed" (stored NULL);
* there is NO problem score, session score or AI-only subtotal - not stored,
  not computed, not returned (a DB CHECK also forbids it);
* nothing is simulated or inferred about correctness: sentences claiming test
  results or correctness are removed, and AI output that adds objective keys
  is rejected as a whole.

Generated once and final, like the Phase 4 report: one request at a time per
session (the session lease), all problems or nothing (a failed AI call stores
nothing and returns 503; retry later). One usage-ledger row per AI call
(step ``code_review``). Logs carry ids and classes only.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import MockInterviewCodeReview as Review
from app.models import MockInterviewSession
from app.services import coding_bank
from app.services import mock_interview as svc
from app.services import mock_interview_ai as engine
from app.services import mock_interview_coding as coding
from app.services import mock_interview_coding_ai as coding_ai
from app.services import mock_interview_coding_guards as guards

logger = logging.getLogger("campus_ai.mock_interview.coding")

RUBRIC_VERSION = "coding-v1-unverified"
# Reviews run one after another (<= 2 problems, 120 s each) + a connect retry.
FEEDBACK_LEASE_SECONDS = 300

# Approved coding-v1 categories (P8-D7, spec 7.1); weights are shown for
# transparency only - review-only feedback never combines them into a score.
OBJECTIVE = (
    ("correctness", "Correctness", 40),
    ("robustness", "Robustness and edge cases", 20),
    ("efficiency", "Efficiency", 10),
)
AI_ESTIMATES = (
    ("code_quality", "Code quality", 15),
    ("reasoning", "Reasoning", 10),
    ("testing", "Testing approach", 5),
)
# Per-estimate uncertainty (every individual AI estimate carries one).
ESTIMATE_UNCERTAINTY = {
    "code_quality": (
        "AI estimate from reading your code. The code was not run, so problems that only "
        "running it would show are not reflected."
    ),
    "reasoning": (
        "AI estimate from your typed explanation. It reflects what you wrote, not whether "
        "your code works."
    ),
    "testing": (
        "AI estimate from your typed explanation of how you would test. No tests were run."
    ),
}
NOT_ASSESSED_UNCERTAINTY = (
    "Not assessed: no verifiable evidence was found, so no estimate is given."
)

UNCERTAINTY_NOTE = (
    "These are AI estimates made by reading your code and your typed explanation. "
    "Your code was not run, so correctness, edge cases and efficiency are not verified "
    "and no score is given. The AI can be wrong; use this as practice feedback."
)
DISCLAIMER = (
    "This is AI-generated practice feedback. It can make mistakes, and practice "
    "feedback does not predict selection in a real interview."
)
MSG_UNAVAILABLE = coding.CODING_FEEDBACK_UNAVAILABLE
_DOWN = (
    "We couldn't prepare your coding feedback right now. Your code is saved; "
    "please try again in a moment."
)
_BUSY = "Your coding feedback is already being prepared. Please wait a moment."
_NOT_FOUND = "Feedback not generated yet"


# ---------------------------------------------------------------------------
# Strict view of the worker's answer (unknown keys anywhere => rejected)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Quote(_Strict):
    line: int | None = None
    quote: str = Field(max_length=300)


class _Estimate(_Strict):
    score: int = Field(ge=0, le=5)
    assessed: bool
    evidence: list[_Quote] = Field(default_factory=list, max_length=3)
    rationale: str = Field(default="", max_length=600)


class _Complexity(_Strict):
    time: str = Field(default="", max_length=60)
    space: str = Field(default="", max_length=60)
    justification: str = Field(default="", max_length=600)
    quote: _Quote | None = None


class _Observation(_Strict):
    line: int | None = None
    text: str = Field(max_length=400)


class WorkerCodeReview(_Strict):
    rubric_version: Literal["coding-v1-unverified"]
    code_quality: _Estimate
    reasoning: _Estimate
    testing: _Estimate
    complexity: _Complexity
    observations: list[_Observation] = Field(default_factory=list, max_length=3)
    consider_cases: list[str] = Field(default_factory=list, max_length=3)
    testing_notes: list[str] = Field(default_factory=list, max_length=3)
    provider: str | None = Field(default=None, max_length=40)


def _estimate(raw: _Estimate, evidence: list[dict]) -> dict:
    """Rule 2: a score counts only with verified evidence; else not assessed."""
    if not raw.assessed or not evidence:
        return {"score": None, "assessed": False, "evidence": [], "rationale": ""}
    return {
        "score": raw.score,
        "assessed": True,
        "evidence": evidence,
        "rationale": guards.safe_text(raw.rationale, 400) or "",
    }


def validated(raw: dict, code: str, answers: list[str]) -> tuple[dict, dict, str | None]:
    """Re-check everything the worker returned. Returns (estimates, feedback,
    provider). Raises ValidationError / ValueError when unusable."""
    review = WorkerCodeReview.model_validate(raw)
    lines = guards.code_lines(code)

    # Text aimed at the reviewer (in code comments/strings or in the answers)
    # withholds the estimates it could have steered: never a higher score.
    code_steered = guards.injection_suspected(code)
    answers_steered = any(guards.injection_suspected(a) for a in answers)

    def code_quote(q: _Quote | None) -> dict | None:
        if q is None:
            return None
        line = guards.code_quote_line(q.quote, q.line, lines)
        # The line actually matched is the one shown ("Your code, line N").
        return {"line": line, "quote": q.quote.strip()} if line is not None else None

    code_quotes = [] if code_steered else [
        x for x in (code_quote(q) for q in review.code_quality.evidence) if x
    ]

    def explained(est: _Estimate) -> dict:
        quotes = [] if answers_steered else [
            {"line": None, "quote": q.quote.strip()}
            for q in est.evidence
            if guards.explanation_quote_ok(q.quote, answers)
        ]
        return _estimate(est, quotes)

    estimates = {
        "code_quality": _estimate(review.code_quality, code_quotes),
        "reasoning": explained(review.reasoning),
        "testing": explained(review.testing),
    }

    c = review.complexity
    c_quote = code_quote(c.quote)
    time_label = guards.safe_text(c.time, 60) or ""
    space_label = guards.safe_text(c.space, 60) or ""
    complexity = None
    if time_label or space_label:
        complexity = {
            "time": time_label,
            "space": space_label,
            # The justification must quote the code it is about.
            "justification": (guards.safe_text(c.justification, 400) or "") if c_quote else "",
            "quote": c_quote,
        }
    observations = []
    for o in review.observations:
        text = guards.safe_text(o.text, 300)
        if text:
            line = o.line if isinstance(o.line, int) and 1 <= o.line <= len(lines) else None
            observations.append({"line": line, "text": text})
    feedback = {
        "complexity": complexity,
        "observations": observations[:3],
        "consider_cases": [
            t for t in (guards.safe_text(x, 300, no_missed=True) for x in review.consider_cases) if t
        ][:3],
        "testing_notes": [t for t in (guards.safe_text(x, 300) for x in review.testing_notes) if t][:3],
    }
    provider = (review.provider or "").strip()[:40] or None
    return estimates, feedback, provider


# ---------------------------------------------------------------------------
# Read / generate
# ---------------------------------------------------------------------------


def stored(db: Session, s: MockInterviewSession) -> list[Review]:
    return list(
        db.scalars(
            select(Review)
            .where(Review.session_id == s.id, Review.tenant_id == s.tenant_id)
            .order_by(Review.id.asc())
        )
    )


def get_feedback(db: Session, s: MockInterviewSession) -> dict:
    rows = stored(db, s)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return to_feedback(db, s, rows)


def generate(db: Session, s: MockInterviewSession) -> dict:
    rows = stored(db, s)
    if rows:
        return to_feedback(db, s, rows)  # final: no AI call, no regeneration
    if not coding.ai_enabled():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MSG_UNAVAILABLE)
    if s.status == "abandoned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled coding rounds don't have feedback.",
        )
    if s.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finish the coding round to get your feedback.",
        )
    if not coding_bank.bank().ok:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=coding.MSG_BANK_DOWN)

    explanations = coding_ai.explanation_turns(db, s)
    plans = []  # (attempt values, submission values, payload | None, answers)
    for a in coding.attempts(db, s):
        sub = coding_ai.latest_graded(db, a)
        answers = [t.answer_text for t in explanations.get(a.seq, []) if t.answer_text]
        problem = coding_bank.get_problem(a.problem_key)
        payload = None
        if sub is not None and problem is not None:
            payload = {
                "problem": coding_ai.worker_problem(problem, with_complexity=True),
                "language": sub.language,
                "code": sub.source,
                "explanation": [
                    {"question": t.question, "answer": t.answer_text}
                    for t in explanations.get(a.seq, [])
                ][:2],
                "hints_used": min(5, a.hints_used),
            }
        plans.append(
            {
                "attempt_id": a.id,
                "seq": a.seq,
                "hints_used": a.hints_used,
                "submission_id": sub.id if sub else None,
                "language": sub.language if sub else a.language,
                "code": sub.source if sub else "",
                "payload": payload,
                "answers": answers,
            }
        )
    if not any(p["payload"] for p in plans):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no submitted code to give feedback on in this round.",
        )

    s_id, tenant_id, student_id = s.id, s.tenant_id, s.student_id
    token, meta = svc._claim(db, s, seconds=FEEDBACK_LEASE_SECONDS, busy=_BUSY)
    results: dict[int, tuple[dict, dict, str | None]] = {}
    try:
        for p in plans:
            if p["payload"] is None:
                continue
            with svc._ai_scope(tenant_id, student_id, s_id):  # usage ledger
                raw = engine.coding_review(p["payload"])
            results[p["attempt_id"]] = validated(raw, p["code"], p["answers"])
    except (engine.InterviewEngineUnavailable, ValidationError, ValueError) as exc:
        logger.warning(
            "coding round %s: feedback unavailable (%s, status=%s)",
            s_id, type(exc).__name__, getattr(exc, "status_code", None),
        )
        if getattr(exc, "timed_out", False):
            db.rollback()  # the worker may still be running: keep the lease (it expires)
        else:
            svc._release(db, s_id, tenant_id, token, meta)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_DOWN) from None
    except Exception:
        # Unexpected error: give the lease back so a retry is not blocked for 5 min.
        svc._release(db, s_id, tenant_id, token, meta)
        raise

    # Release the lease and store every row in ONE commit (all or nothing).
    if not svc._finalize(db, s_id, tenant_id, token, plan_json=meta):
        db.rollback()
        s = svc._reload(db, s_id)
        rows = stored(db, s)
        if rows:
            return to_feedback(db, s, rows)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_BUSY)
    for p in plans:
        result = results.get(p["attempt_id"])
        estimates, feedback, provider = result if result else ({}, {}, None)
        feedback = {
            **feedback,
            "hints_used": p["hints_used"],
            "explanation_answers": len(p["answers"]),
        }
        db.add(
            Review(
                tenant_id=tenant_id,
                session_id=s_id,
                attempt_id=p["attempt_id"],
                submission_id=p["submission_id"],
                rubric_version=RUBRIC_VERSION,
                status="ai_estimated" if result else "not_submitted",
                verification="not_verified",
                language=p["language"],
                code_quality_score=(estimates.get("code_quality") or {}).get("score"),
                reasoning_score=(estimates.get("reasoning") or {}).get("score"),
                testing_score=(estimates.get("testing") or {}).get("score"),
                problem_score=None,  # never in review-only mode (P8-D7)
                estimates_json=estimates or None,
                feedback_json=feedback,
                provider=provider,
            )
        )
    try:
        db.commit()
    except IntegrityError:  # a parallel request stored it first
        db.rollback()
        logger.info("coding round %s: concurrent feedback resolved", s_id)
    s = svc._reload(db, s_id)
    logger.info("coding round %s: feedback stored (%d problem(s))", s_id, len(plans))
    return to_feedback(db, s, stored(db, s))


# ---------------------------------------------------------------------------
# Response shaping (re-validates stored JSON defensively; never a score)
# ---------------------------------------------------------------------------


def _int_or_none(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 5 else None


def _evidence(items) -> list[dict]:
    out = []
    for e in items if isinstance(items, list) else []:
        if isinstance(e, dict) and isinstance(e.get("quote"), str):
            line = e.get("line")
            out.append({"line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                        "quote": e["quote"][:300]})
    return out[:3]


def _strs(items) -> list[str]:
    return [x[:300] for x in (items if isinstance(items, list) else []) if isinstance(x, str)][:3]


def to_feedback(db: Session, s: MockInterviewSession, rows: list[Review]) -> dict:
    attempts = {a.id: a for a in coding.attempts(db, s)}
    problems = []
    for r in rows:
        a = attempts.get(r.attempt_id)
        problem = coding_bank.get_problem(a.problem_key) if a else None
        est = r.estimates_json if isinstance(r.estimates_json, dict) else {}
        fb = r.feedback_json if isinstance(r.feedback_json, dict) else {}
        estimates = []
        for key, label, weight in AI_ESTIMATES:
            e = est.get(key) if isinstance(est.get(key), dict) else {}
            score = _int_or_none(e.get("score"))
            assessed = bool(e.get("assessed")) and score is not None
            estimates.append(
                {
                    "key": key,
                    "label": label,
                    "weight_percent": weight,
                    "score": score if assessed else None,
                    "assessed": assessed,
                    "evidence": _evidence(e.get("evidence")) if assessed else [],
                    "rationale": str(e.get("rationale") or "")[:400] if assessed else "",
                    "uncertainty": ESTIMATE_UNCERTAINTY[key] if assessed else NOT_ASSESSED_UNCERTAINTY,
                }
            )
        complexity = fb.get("complexity") if isinstance(fb.get("complexity"), dict) else None
        if complexity is not None:
            quote = _evidence([complexity.get("quote")]) if complexity.get("quote") else []
            complexity = {
                "time": str(complexity.get("time") or "")[:60],
                "space": str(complexity.get("space") or "")[:60],
                "justification": str(complexity.get("justification") or "")[:400],
                "quote": quote[0] if quote else None,
            }
        observations = []
        for o in fb.get("observations") if isinstance(fb.get("observations"), list) else []:
            if isinstance(o, dict) and isinstance(o.get("text"), str):
                line = o.get("line")
                observations.append(
                    {"line": line if isinstance(line, int) and not isinstance(line, bool) else None,
                     "text": o["text"][:300]}
                )
        problems.append(
            {
                "attempt_seq": a.seq if a else 0,
                "problem_title": problem.title if problem else (a.problem_key if a else ""),
                "language": r.language,
                "status": r.status,
                "verification": "not_verified",
                "objective": [
                    {"key": k, "label": label, "weight_percent": w, "status": "not_verified"}
                    for k, label, w in OBJECTIVE
                ],
                "estimates": estimates,
                "complexity": complexity,
                "observations": observations[:3],
                "consider_cases": _strs(fb.get("consider_cases")),
                "testing_notes": _strs(fb.get("testing_notes")),
                "hints_used": int(fb.get("hints_used") or 0) if not isinstance(fb.get("hints_used"), bool) else 0,
                "explanation_answers": int(fb.get("explanation_answers") or 0)
                if not isinstance(fb.get("explanation_answers"), bool) else 0,
            }
        )
    problems.sort(key=lambda p: p["attempt_seq"])
    created: datetime = min(r.created_at for r in rows)
    return {
        "session_id": s.id,
        "rubric_version": RUBRIC_VERSION,
        "review_only": True,
        "verification": "not_verified",
        "uncertainty_note": UNCERTAINTY_NOTE,
        "disclaimer": DISCLAIMER,
        "provider": next((r.provider for r in rows if r.provider), None),
        "created_at": created,
        "problems": problems,
    }
