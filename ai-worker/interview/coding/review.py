"""Coding review steps (Phase 8.3, review-only): hint, follow-up question, review.

Each step makes at most ONE structured LLM call through ``llm_io`` (untraced,
R1; usage counted, Phase 7.2) and then applies ``filters`` to everything the
model wrote. Code is read as text and NEVER executed. No hidden tests,
expected outputs, reference solutions or execution results exist here.
"""

from __future__ import annotations

import re

from interview.coding import filters
from interview.coding.prompts import followup_messages, hint_messages, review_messages
from interview.coding.schemas import (
    CodeQuote,
    Complexity,
    Estimate,
    FollowupRequest,
    HintRequest,
    LLMCodeQuote,
    LLMFollowup,
    LLMHint,
    LLMReview,
    Observation,
    ReviewRequest,
    TextQuote,
)
from interview.llm_io import LLMUnavailable, invoke_structured
from interview.safety import is_similar, single_line
from interview.usage import current_usage

# Used when the model is unavailable for an explanation question, so the
# candidate can always explain their solution (spec 9.8 "existing fallbacks").
FALLBACK_QUESTIONS = (
    "Walk me through your approach. What are its time and space complexity, and why?",
    "Which edge cases did you consider, and how would you test your solution?",
)
_MISSED_RE = re.compile(r"\bmiss(?:ed|es|ing)?\b|\bforg(?:ot|otten)\b", re.IGNORECASE)


class HintUnavailable(RuntimeError):
    """No safe hint could be produced (provider failure or filtered output)."""


class ReviewUnavailable(RuntimeError):
    """The review call failed or returned unusable output."""


# --- Hint ---------------------------------------------------------------------
def generate_hint(req: HintRequest) -> str:
    try:
        out = invoke_structured(
            LLMHint,
            hint_messages(
                problem=req.problem,
                language=req.language,
                code=req.code,
                previous_hints=req.previous_hints,
                hint_number=req.hint_number,
            ),
            step="code_hint",
        )
    except LLMUnavailable as exc:
        raise HintUnavailable("provider") from exc
    hint = filters.safe_text(out.hint, 600)
    if hint is None:
        # More than 3 lines of code (a solution in disguise) or only claims.
        usage = current_usage()
        if usage is not None:
            usage.mark_fallback()
        raise HintUnavailable("filtered")
    return hint


# --- Follow-up question ----------------------------------------------------------
def next_question(req: FollowupRequest) -> tuple[str, str | None, str]:
    """(action, question, source). The first question is always asked."""
    asked = len(req.explanation)
    if asked >= req.max_questions:
        return "end", None, "llm"
    fallback = FALLBACK_QUESTIONS[min(asked, len(FALLBACK_QUESTIONS) - 1)]
    try:
        out: LLMFollowup = invoke_structured(
            LLMFollowup,
            followup_messages(
                problem=req.problem,
                language=req.language,
                code=req.code,
                explanation=req.explanation,
            ),
            step="code_followup",
        )
    except LLMUnavailable:
        return "ask", fallback, "fallback"
    if asked > 0 and not out.ask:
        return "end", None, "llm"
    question = filters.safe_text(single_line(out.question, 400), 300)
    previous = [t.question for t in req.explanation]
    if not question or any(is_similar(question, p) for p in previous):
        usage = current_usage()
        if usage is not None:
            usage.mark_fallback()
        return "ask", fallback, "fallback"
    return "ask", question, "llm"


# --- Review -----------------------------------------------------------------------
def _code_quotes(items: list[LLMCodeQuote], lines: list[str]) -> list[CodeQuote]:
    out: list[CodeQuote] = []
    for e in items:
        line = filters.code_evidence_line(e.quote, e.line, lines)
        if line is not None and not filters.too_much_code(e.quote):
            out.append(CodeQuote(line=line, quote=e.quote.strip()[:300]))
    return out[:3]


def _estimate(score: int, evidence: list, rationale: str) -> Estimate:
    """Rule 2: a non-zero estimate needs evidence; otherwise 0 "not assessed"."""
    if not evidence:
        return Estimate(score=0, assessed=False, evidence=[], rationale="")
    return Estimate(
        score=max(0, min(5, int(score))),
        assessed=True,
        evidence=evidence,
        rationale=filters.safe_text(rationale, 400) or "",
    )


def review(req: ReviewRequest) -> dict:
    try:
        out: LLMReview = invoke_structured(
            LLMReview,
            review_messages(
                problem=req.problem,
                language=req.language,
                code=req.code,
                explanation=req.explanation,
            ),
            step="code_review",
        )
    except LLMUnavailable as exc:
        raise ReviewUnavailable("provider") from exc

    lines = filters.code_lines(req.code)
    answers = [t.answer for t in req.explanation if t.answer and t.answer.strip()]
    # Text aimed at the reviewer withholds the estimates it could steer.
    code_steered = filters.injection_suspected(req.code)
    answers_steered = any(filters.injection_suspected(a) for a in answers)

    code_quality = _estimate(
        out.code_quality.score,
        [] if code_steered else _code_quotes(out.code_quality.evidence, lines),
        out.code_quality.rationale,
    )

    def explained(cat) -> Estimate:
        if answers_steered:
            return _estimate(cat.score, [], "")
        quotes = [
            TextQuote(quote=e.quote.strip()[:300])
            for e in cat.evidence
            if filters.explanation_evidence_ok(e.quote, answers)
        ][:3]
        return _estimate(cat.score, quotes, cat.rationale)

    quote = _code_quotes([out.complexity.quote], lines) if out.complexity.quote else []
    time_label = single_line(out.complexity.time, 60)
    space_label = single_line(out.complexity.space, 60)
    complexity = Complexity(
        time="" if filters.claims_correctness(time_label) else time_label,
        space="" if filters.claims_correctness(space_label) else space_label,
        # The justification must quote the code it talks about.
        justification=(filters.safe_text(out.complexity.justification, 400) or "") if quote else "",
        quote=quote[0] if quote else None,
    )

    observations: list[Observation] = []
    for o in out.observations:
        text = filters.safe_text(o.text, 300)
        if text:
            line = o.line if o.line is not None and 1 <= o.line <= len(lines) else None
            observations.append(Observation(line=line, text=text))

    def notes(items: list[str], *, no_missed: bool = False) -> list[str]:
        kept = []
        for item in items:
            text = filters.safe_text(item, 300)
            if text and not (no_missed and _MISSED_RE.search(text)):
                kept.append(text)
        return kept[:3]

    return {
        "code_quality": code_quality,
        "reasoning": explained(out.reasoning),
        "testing": explained(out.testing),
        "complexity": complexity,
        "observations": observations[:3],
        "consider_cases": notes(out.consider_cases, no_missed=True),
        "testing_notes": notes(out.testing_notes),
    }
