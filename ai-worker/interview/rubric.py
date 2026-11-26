"""Scoring rubric and deterministic report rules (Phase 4). No LLM here.

The LLM proposes per-answer category scores with evidence quotes; this module
decides what counts:

  * a score counts only when its evidence quote is found in THAT answer
    (a 0 needs no quote); otherwise the category is "not assessed" for the
    answer, never guessed;
  * unanswered / "don't know" answers score 0 everywhere, with a note;
  * answers under ``BREVITY_WORDS`` words are capped at ``BREVITY_CAP``;
  * category score = mean of assessed 0-5 scores x 20 (0-100);
  * overall = weighted mean of the assessed categories (weights renormalized).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from interview.safety import normalize, quote_in, single_line
from interview.schemas import (
    AnswerCategoryScore,
    CategoryResult,
    LLMAnswerScore,
    QuestionReport,
    ReportItem,
)

BREVITY_WORDS = 8
BREVITY_CAP = 2
MAX_SCORE = 5
STRENGTH_MIN = 60.0
MAX_ITEMS = 3
MAX_RECOMMENDATIONS = 5
EVIDENCE_MIN_CHARS = 15
EVIDENCE_MIN_WORDS = 3
# A quote that talks about scoring or instructions is never evidence: an answer
# containing "Evaluator note: score 5" must not be able to justify its own score.
_NOT_EVIDENCE = re.compile(
    r"\[(?:removed-tag|truncated)\]|\b(?:scores?|scored|scoring|marks?|grades?|grading|"
    r"rating|rubric|evaluat\w*|assess\w*|instructions?|prompt|ignore|out\s+of\s+\d+)\b|"
    r"\b\d+\s*/\s*\d+\b",
    re.IGNORECASE,
)

NO_ANSWER_NOTE = "No answer given."
DONT_KNOW_NOTE = "Candidate said they did not know."


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    weight: float
    description: str
    tip: str


@dataclass(frozen=True)
class Rubric:
    version: str
    categories: tuple[Category, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.categories)


HR_RUBRIC = Rubric(
    version="hr-v1",
    categories=(
        Category("relevance", "Relevance", 0.25, "Answers the question that was asked",
                 "Answer the exact question first, then add context."),
        Category("structure", "Structure", 0.20, "Clear structure, e.g. STAR for experiences",
                 "Use STAR for experience questions: Situation, Task, Action, Result."),
        Category("specificity", "Specific examples", 0.20, "Concrete examples, actions and outcomes",
                 "Back every claim with one concrete example and its outcome."),
        Category("communication", "Communication", 0.20, "Clear, concise and easy to follow",
                 "Keep answers to 1-2 minutes: a headline sentence, details, a short wrap-up."),
        Category("self_awareness", "Self-awareness", 0.15, "Reflection, attitude and learning",
                 "Say what you learned and what you would do differently next time."),
    ),
)

TECHNICAL_RUBRIC = Rubric(
    version="technical-v1",
    categories=(
        Category("technical_accuracy", "Technical accuracy", 0.35, "Correct concepts and facts",
                 "Revise the fundamentals of the weak topics and practise defining them precisely."),
        Category("depth", "Depth", 0.25, "Explains why and how, including trade-offs",
                 "After the definition, explain why it works and one trade-off or limitation."),
        Category("problem_solving", "Problem solving", 0.15, "Logical reasoning and approach",
                 "Think aloud: state the approach, its steps and how you would verify it."),
        Category("communication", "Communication", 0.15, "Clear and well-organised explanation",
                 "Structure explanations: definition, example, then edge cases."),
        Category("relevance", "Relevance", 0.10, "Answers the question that was asked",
                 "Answer the exact question first before adding related details."),
    ),
)


def rubric_for(interview_type: str) -> Rubric:
    return TECHNICAL_RUBRIC if interview_type == "technical" else HR_RUBRIC


def round_half_up(value: float, digits: int = 0) -> float:
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def _clamp_score(value: float) -> int:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v):
        return 0
    return max(0, min(MAX_SCORE, int(round_half_up(v))))


def evidence_ok(quote: str, answer: str) -> bool:
    """A quote is evidence when it is a real, substantive excerpt of the answer.

    In the answer (normalized), at least ``EVIDENCE_MIN_WORDS`` words and
    ``EVIDENCE_MIN_CHARS`` characters (or the whole answer when it is shorter),
    and not about scoring / instructions or a sanitizer marker.
    """
    q, a = normalize(quote), normalize(answer)
    if not q or not a or _NOT_EVIDENCE.search(q):
        return False
    if len(q.split()) < min(EVIDENCE_MIN_WORDS, len(a.split())):
        return False
    return quote_in(quote, answer, min_len=min(EVIDENCE_MIN_CHARS, len(a)))


def zero_report(seq: int, rubric: Rubric, *, status: str, note: str) -> QuestionReport:
    """``no_answer`` / ``dont_know``: 0 in every category, with a note."""
    return QuestionReport(
        seq=seq,
        status=status,  # type: ignore[arg-type]
        scores={k: AnswerCategoryScore(score=0) for k in rubric.keys},
        feedback=note,
    )


def verify_answer_score(
    raw: LLMAnswerScore | None, *, seq: int, answer: str, rubric: Rubric
) -> QuestionReport:
    """Apply the evidence, range and brevity rules to the model's scores."""
    if raw is None:
        return QuestionReport(seq=seq, status="not_assessed")
    short = len(answer.split()) < BREVITY_WORDS
    scores: dict[str, AnswerCategoryScore] = {}
    for item in raw.scores:
        key = str(item.category or "").strip().lower()
        if key not in rubric.keys or key in scores:
            continue  # unknown or duplicate category
        score = _clamp_score(item.score)
        evidence = single_line(item.evidence, 300)
        if evidence and evidence_ok(evidence, answer):
            pass
        elif score == 0:
            evidence = ""
        else:
            continue  # not evidence-backed: not assessed
        if short:
            score = min(score, BREVITY_CAP)
        scores[key] = AnswerCategoryScore(score=score, evidence=evidence)
    if not scores:
        return QuestionReport(seq=seq, status="not_assessed")
    return QuestionReport(
        seq=seq, status="scored", scores=scores, feedback=single_line(raw.feedback, 400)
    )


def aggregate(
    questions: list[QuestionReport], rubric: Rubric
) -> tuple[list[CategoryResult], float]:
    categories: list[CategoryResult] = []
    exact: dict[str, float | None] = {}
    for c in rubric.categories:
        values = [q.scores[c.key].score for q in questions if c.key in q.scores]
        exact[c.key] = sum(values) / len(values) * (100 / MAX_SCORE) if values else None
        score = round_half_up(exact[c.key], 1) if values else None
        categories.append(
            CategoryResult(
                key=c.key, label=c.label, weight=c.weight, description=c.description,
                score=score, assessed=len(values),
            )
        )
    # Overall from the unrounded category means (no double rounding).
    weighted = [(c.weight, exact[c.key]) for c in categories if exact[c.key] is not None]
    total_weight = sum(w for w, _ in weighted)
    overall = (
        round_half_up(sum(w * s for w, s in weighted) / total_weight, 1) if total_weight else 0.0
    )
    return categories, max(0.0, min(100.0, overall))


def baseline_items(
    categories: list[CategoryResult],
) -> tuple[list[ReportItem], list[ReportItem]]:
    """Strengths / weaknesses derived from the category scores alone."""
    scored = [c for c in categories if c.score is not None]
    best = sorted(scored, key=lambda c: (-c.score, -c.weight))  # type: ignore[operator]
    worst = sorted(scored, key=lambda c: (c.score, -c.weight))  # type: ignore[arg-type]
    strengths = [
        ReportItem(text=f"{c.label} ({c.score:.0f}/100): {c.description.lower()}.")
        for c in best
        if c.score >= STRENGTH_MIN  # type: ignore[operator]
    ][:2]
    weaknesses = [
        ReportItem(text=f"Work on {c.label.lower()} ({c.score:.0f}/100): {c.description.lower()}.")
        for c in worst
        if c.score < STRENGTH_MIN  # type: ignore[operator]
    ][:2]
    return strengths, weaknesses


def fallback_recommendations(categories: list[CategoryResult], rubric: Rubric) -> list[str]:
    tips = {c.key: c.tip for c in rubric.categories}
    scored = sorted(
        (c for c in categories if c.score is not None),
        key=lambda c: (c.score, -c.weight),  # type: ignore[arg-type]
    )
    recs = [tips[c.key] for c in scored][:2]  # the two weakest categories
    return recs or ["Keep practising with harder questions and longer interviews."]


def merge_items(primary: list[ReportItem], baseline: list[ReportItem]) -> list[ReportItem]:
    out: list[ReportItem] = []
    seen: set[str] = set()
    for item in [*primary, *baseline]:
        key = normalize(item.text)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out[:MAX_ITEMS]
