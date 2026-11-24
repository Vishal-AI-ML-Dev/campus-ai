"""Pydantic schemas for the AI Mock Interview coding round (Phase 8.1).

Only PUBLIC problem material is ever returned (spec 3.2): statements,
examples, visible tests, starter code and the hints the student has revealed.
No hidden-test data, expected complexity or reference material exists in these
models. Batch 1 is review-only (P8-D8): code is stored, never executed.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CodeLanguage = Literal["python313", "cpp17", "java21", "c11"]
# Source limits (spec 3.8). Measured in UTF-8 bytes by the service.
SOURCE_MAX_BYTES = 64 * 1024
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class CodingChoiceOut(BaseModel):
    value: str
    label: str


class CodingCatalogOut(BaseModel):
    languages: list[CodingChoiceOut]
    topics: list[CodingChoiceOut]
    difficulties: list[CodingChoiceOut]
    durations_minutes: list[int]
    graded_submissions_per_problem: int
    hints_per_problem: dict[str, int]
    source_max_bytes: int
    # Batch 1: always true (no sandbox; Run is unavailable).
    review_only: bool
    # Practice mode: problems the student may pick by key.
    problems: list["CodingProblemBriefOut"]


class CodingProblemBriefOut(BaseModel):
    key: str
    title: str
    difficulty: str
    topics: list[str]


class CodingExampleOut(BaseModel):
    input: str
    output: str
    explanation: str | None = None


class CodingVisibleTestOut(BaseModel):
    input: str
    output: str


class CodingProblemOut(BaseModel):
    key: str
    version: int
    title: str
    difficulty: str
    topics: list[str]
    statement: str
    input_format: str
    output_format: str
    constraints: str
    examples: list[CodingExampleOut]
    visible_tests: list[CodingVisibleTestOut]
    starter: dict[str, str]
    time_limit_ms: int
    memory_limit_mb: int
    hints_revealed: list[str]


class CodingSubmissionOut(BaseModel):
    id: int
    attempt_seq: int
    kind: Literal["visible", "custom", "graded", "auto"]
    language: str
    status: Literal["queued", "running", "completed", "error", "not_executed", "rejected"]
    verdict: str | None
    # Batch 1: always "not_verified" (code was not run; nothing is simulated).
    verification: Literal["verified", "not_verified"]
    # Which graded submission the final result uses (8.6; null in review-only).
    is_best: bool
    created_at: datetime


class CodingExplanationTurnOut(BaseModel):
    """Phase 8.3: one typed explanation question after "Done" (B1-a: text only).

    Answered through the existing ``POST /sessions/{id}/answer`` with
    ``turn_seq``.
    """

    turn_seq: int
    question: str
    answer_text: str | None
    answered_at: datetime | None


class CodingAttemptOut(BaseModel):
    seq: int
    status: Literal["pending", "working", "explaining", "auto_submitted", "closed", "abandoned"]
    language: str | None
    draft_source: str | None
    draft_rev: int
    hints_used: int
    hints_available: int
    submissions_used: int
    submissions_remaining: int
    problem: CodingProblemOut
    # Phase 8.3 (additive): explanation questions for this problem, oldest first.
    explanation: list[CodingExplanationTurnOut] = Field(default_factory=list)


class CodingStateOut(BaseModel):
    session_id: int
    status: str
    languages: list[str]
    review_only: bool
    remaining_seconds: int | None
    graded_submissions_per_problem: int
    attempts: list[CodingAttemptOut]
    # Phase 8.3 (additive): AI help is on (AI hints after the authored ones,
    # explanation questions after "Done", AI-estimate feedback afterwards).
    ai_enabled: bool = False


class CodingDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: CodeLanguage
    # Length is re-checked in UTF-8 bytes by the service.
    source: str = Field(max_length=SOURCE_MAX_BYTES)
    draft_rev: int = Field(ge=0)


class CodingDraftOut(BaseModel):
    seq: int
    draft_rev: int
    saved_at: datetime


class CodingSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: CodeLanguage
    source: str = Field(min_length=1, max_length=SOURCE_MAX_BYTES)
    # Client retry key: a repeat with the same key returns the same submission.
    idempotency_key: str

    @field_validator("idempotency_key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _IDEMPOTENCY_RE.match(v):
            raise ValueError("idempotency_key must be 8-64 characters: letters, digits, - or _")
        return v


class CodingHintOut(BaseModel):
    seq: int
    hint_number: int
    hint: str
    hints_used: int
    hints_available: int
    # Phase 8.3 (additive): "ai" = tailored to the current code after the
    # authored hints ran out. AI hint text is not stored (shown once).
    source: Literal["authored", "ai"] = "authored"


# ---------------------------------------------------------------------------
# Phase 8.3: review-only coding feedback (decision O1 = option A)
# ---------------------------------------------------------------------------
# No field here can carry an overall, problem, session or AI-only total score
# (P8-D7 rules 6-8, U3): objective categories are "not_verified" and the three
# AI categories are shown one by one as labelled "AI estimate" values.


class CodingEvidenceOut(BaseModel):
    # Code quotes cite a 1-based line; explanation quotes have line = null.
    line: int | None = None
    quote: str


class CodingEstimateOut(BaseModel):
    key: Literal["code_quality", "reasoning", "testing"]
    label: str
    weight_percent: int
    kind: Literal["ai_estimate"] = "ai_estimate"
    # 0-5; null = not assessed (no valid evidence).
    score: int | None
    assessed: bool
    evidence: list[CodingEvidenceOut]
    rationale: str
    # Why this individual AI estimate is uncertain (always present).
    uncertainty: str


class CodingObjectiveOut(BaseModel):
    key: Literal["correctness", "robustness", "efficiency"]
    label: str
    weight_percent: int
    # Review-only: the code was not executed, so these are never scored.
    status: Literal["not_verified"] = "not_verified"


class CodingComplexityOut(BaseModel):
    time: str
    space: str
    justification: str
    quote: CodingEvidenceOut | None = None


class CodingObservationOut(BaseModel):
    line: int | None = None
    text: str


class CodingProblemFeedbackOut(BaseModel):
    attempt_seq: int
    problem_title: str
    language: str | None
    status: Literal["ai_estimated", "not_submitted"]
    verification: Literal["not_verified"] = "not_verified"
    objective: list[CodingObjectiveOut]
    estimates: list[CodingEstimateOut]
    complexity: CodingComplexityOut | None = None
    observations: list[CodingObservationOut]
    consider_cases: list[str]
    testing_notes: list[str]
    hints_used: int
    explanation_answers: int


class CodingFeedbackOut(BaseModel):
    session_id: int
    rubric_version: Literal["coding-v1-unverified"]
    review_only: Literal[True] = True
    verification: Literal["not_verified"] = "not_verified"
    uncertainty_note: str
    disclaimer: str
    provider: str | None = None
    created_at: datetime
    problems: list[CodingProblemFeedbackOut]


CodingCatalogOut.model_rebuild()
