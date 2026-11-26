"""Deterministic interview policy: no LLM, fully unit-testable.

Everything that decides the SHAPE of the interview (what happens next, the
current difficulty, when to stop) lives here, so the LLM only writes text and
judges answers; it never controls flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from interview.schemas import (
    HARD_MAX_TURNS,
    Difficulty,
    InterviewPlan,
    PlanTopic,
    TranscriptTurn,
    TurnEvaluation,
)

LEVELS: tuple[Difficulty, ...] = ("easy", "medium", "hard")
END_GRACE_SECONDS = 45
MAX_FOLLOW_UPS = {"easy": 1, "medium": 2, "hard": 2}

_DONT_KNOW = re.compile(
    r"^\s*(?:i\s*(?:do\s*not|don['’]?t)\s*know|no\s*idea|not\s*sure|skip|pass|"
    r"pata\s*nahi|nahi\s*pata|mujhe\s*nahi\s*pata|maloom\s*nahi|"
    r"पता\s*नहीं|मुझे\s*नहीं\s*पता|idk|n/?a|-+|\.+)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessedAnswer:
    text: str
    word_count: int
    dont_know: bool


def process_answer(answer: str | None) -> ProcessedAnswer:
    """Normalize the latest answer and flag empty / "don't know" replies."""
    text = (answer or "").strip()
    words = len(text.split())
    return ProcessedAnswer(
        text=text, word_count=words, dont_know=(not text) or bool(_DONT_KNOW.match(text))
    )


def dont_know_evaluation() -> TurnEvaluation:
    return TurnEvaluation(
        coverage="none",
        depth=1,
        evidence="",
        follow_up_recommended=False,
        difficulty_signal=-1,
        dont_know=True,
        source="rule",
    )


def heuristic_evaluation(processed: ProcessedAnswer) -> TurnEvaluation:
    """Fallback evaluation when the LLM evaluator is unavailable.

    Deliberately conservative: never recommends a harder level, never invents
    evidence, and is marked ``source="heuristic"`` so Phase 4 can ignore it.
    """
    if processed.dont_know:
        return dont_know_evaluation()
    if processed.word_count < 25:
        coverage, depth = "partial", 2
    else:
        coverage, depth = "full", 3
    return TurnEvaluation(
        coverage=coverage,
        depth=depth,
        follow_up_recommended=False,
        difficulty_signal=0,
        source="heuristic",
    )


def current_level(base: Difficulty, transcript: list[TranscriptTurn]) -> Difficulty:
    """Base difficulty shifted by the last three evaluated answers' signals."""
    signals = [t.evaluation.difficulty_signal for t in transcript if t.evaluation][-3:]
    idx = LEVELS.index(base) + sum(signals)
    return LEVELS[max(0, min(len(LEVELS) - 1, idx))]


def follow_ups_in_a_row(transcript: list[TranscriptTurn]) -> int:
    """How many trailing turns are follow-ups on the current topic."""
    count = 0
    for turn in reversed(transcript):
        if not turn.is_follow_up:
            break
        count += 1
    return count


def untouched_topics(plan: InterviewPlan, transcript: list[TranscriptTurn]) -> list[PlanTopic]:
    asked = {(t.topic or "").strip().lower() for t in transcript}
    return [tp for tp in plan.topics if tp.name.strip().lower() not in asked]


def topic_named(plan: InterviewPlan, name: str | None) -> PlanTopic:
    for tp in plan.topics:
        if tp.name.strip().lower() == (name or "").strip().lower():
            return tp
    return PlanTopic(name=(name or "General")[:100], source="general")


@dataclass(frozen=True)
class Decision:
    action: str  # follow_up | next_topic | end
    topic: PlanTopic | None
    end_reason: str | None = None


def decide(
    *,
    plan: InterviewPlan,
    transcript: list[TranscriptTurn],
    evaluation: TurnEvaluation,
    remaining_seconds: int,
    difficulty: Difficulty,
) -> Decision:
    """Follow up, move to the next topic, or end. Pure function.

    ``difficulty`` is the CONFIGURED difficulty (the follow-up allowance is a
    session setting; the adaptive level only changes question depth).
    """
    answered = sum(1 for t in transcript if t.answer is not None)
    if remaining_seconds <= END_GRACE_SECONDS:
        return Decision("end", None, "time_up")
    if answered >= min(plan.question_budget, HARD_MAX_TURNS) or len(transcript) >= HARD_MAX_TURNS:
        return Decision("end", None, "question_budget_reached")

    current = topic_named(plan, transcript[-1].topic)
    remaining = untouched_topics(plan, transcript)
    budget_left = plan.question_budget - answered

    wants_probe = not evaluation.dont_know and (
        evaluation.coverage != "full" or evaluation.follow_up_recommended
    )
    can_probe = follow_ups_in_a_row(transcript) < MAX_FOLLOW_UPS[difficulty]
    spare_budget = budget_left > len(remaining)

    if wants_probe and can_probe and spare_budget:
        return Decision("follow_up", current)
    if remaining:
        return Decision("next_topic", remaining[0])
    if can_probe and not evaluation.dont_know:
        return Decision("follow_up", current)
    return Decision("end", None, "topics_covered")
