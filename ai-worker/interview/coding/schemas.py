"""Contracts for the coding review steps (Phase 8.3, review-only).

Requests carry PUBLIC problem material, the candidate's code and their typed
explanation only. There is no field for hidden tests, expected outputs,
reference solutions or execution results: in review-only mode none exist, and
the worker must never receive or invent them (decision P8-D7 rule 7).

Model outputs are parsed with ``extra="forbid"``: an answer that adds keys such
as ``correctness``, ``overall_score`` or ``tests_passed`` is unusable and
rejected (objective categories are never AI-produced, rule 3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from interview.schemas import StepUsage

CodeLanguage = Literal["python313", "cpp17", "java21", "c11"]
Difficulty = Literal["easy", "medium", "hard"]
RUBRIC_VERSION = "coding-v1-unverified"
AI_CATEGORIES = ("code_quality", "reasoning", "testing")

MAX_CODE_CHARS = 64 * 1024
MAX_TEXT = 6_000
MAX_ANSWER_CHARS = 5_000


# --- Requests (from the backend) ---------------------------------------------
class CodingExample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input: str = Field(default="", max_length=4_096)
    output: str = Field(default="", max_length=4_096)
    explanation: str | None = Field(default=None, max_length=MAX_TEXT)


class CodingProblem(BaseModel):
    """Public problem material only (the same text the student sees)."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    difficulty: Difficulty = "medium"
    statement: str = Field(min_length=1, max_length=MAX_TEXT)
    input_format: str = Field(default="", max_length=MAX_TEXT)
    output_format: str = Field(default="", max_length=MAX_TEXT)
    constraints: str = Field(default="", max_length=MAX_TEXT)
    examples: list[CodingExample] = Field(default_factory=list, max_length=6)
    # Rubric reference for the review (e.g. {"time": "O(n)", "space": "O(1)"}).
    expected_complexity: dict[str, str] = Field(default_factory=dict, max_length=4)


class ExplanationTurn(BaseModel):
    question: str = Field(min_length=1, max_length=1_000)
    answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)


class HintRequest(BaseModel):
    problem: CodingProblem
    language: CodeLanguage
    code: str = Field(default="", max_length=MAX_CODE_CHARS)
    previous_hints: list[str] = Field(default_factory=list, max_length=5)
    hint_number: int = Field(ge=1, le=5)


class FollowupRequest(BaseModel):
    problem: CodingProblem
    language: CodeLanguage
    code: str = Field(default="", max_length=MAX_CODE_CHARS)
    # Earlier explanation questions of THIS problem, oldest first.
    explanation: list[ExplanationTurn] = Field(default_factory=list, max_length=2)
    max_questions: int = Field(default=2, ge=1, le=2)


class ReviewRequest(BaseModel):
    problem: CodingProblem
    language: CodeLanguage
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    explanation: list[ExplanationTurn] = Field(default_factory=list, max_length=2)
    hints_used: int = Field(default=0, ge=0, le=5)


# --- Model output (strict) -----------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMHint(_Strict):
    hint: str = Field(min_length=1, max_length=800)


class LLMFollowup(_Strict):
    ask: bool = True
    question: str = Field(default="", max_length=600)


class LLMCodeQuote(_Strict):
    line: int = Field(ge=1, le=10_000)
    quote: str = Field(min_length=1, max_length=300)


class LLMTextQuote(_Strict):
    quote: str = Field(min_length=1, max_length=300)


class LLMCodeQuality(_Strict):
    score: int = Field(ge=0, le=5)
    evidence: list[LLMCodeQuote] = Field(default_factory=list, max_length=3)
    rationale: str = Field(default="", max_length=600)


class LLMExplainedCategory(_Strict):
    score: int = Field(ge=0, le=5)
    evidence: list[LLMTextQuote] = Field(default_factory=list, max_length=3)
    rationale: str = Field(default="", max_length=600)


class LLMComplexity(_Strict):
    time: str = Field(default="", max_length=60)
    space: str = Field(default="", max_length=60)
    justification: str = Field(default="", max_length=600)
    quote: LLMCodeQuote | None = None


class LLMObservation(_Strict):
    line: int | None = Field(default=None, ge=1, le=10_000)
    text: str = Field(min_length=1, max_length=400)


class LLMReview(_Strict):
    code_quality: LLMCodeQuality
    reasoning: LLMExplainedCategory
    testing: LLMExplainedCategory
    complexity: LLMComplexity
    observations: list[LLMObservation] = Field(default_factory=list, max_length=5)
    consider_cases: list[str] = Field(default_factory=list, max_length=5)
    testing_notes: list[str] = Field(default_factory=list, max_length=5)


# --- Responses (to the backend) ------------------------------------------------
class HintResponse(BaseModel):
    hint: str
    provider: str
    usage: StepUsage | None = None


class FollowupResponse(BaseModel):
    action: Literal["ask", "end"]
    question: str | None = None
    # "fallback": the model failed and a fixed question was used.
    source: Literal["llm", "fallback"] = "llm"
    provider: str
    usage: StepUsage | None = None


class CodeQuote(BaseModel):
    line: int
    quote: str


class TextQuote(BaseModel):
    quote: str


class Estimate(BaseModel):
    """One AI-estimate category (0-5). ``assessed`` False = no valid evidence."""

    score: int = Field(ge=0, le=5)
    assessed: bool
    evidence: list[CodeQuote] | list[TextQuote] = Field(default_factory=list)
    rationale: str = ""


class Complexity(BaseModel):
    time: str = ""
    space: str = ""
    justification: str = ""
    quote: CodeQuote | None = None


class Observation(BaseModel):
    line: int | None = None
    text: str


class ReviewResponse(BaseModel):
    rubric_version: Literal["coding-v1-unverified"] = RUBRIC_VERSION
    code_quality: Estimate
    reasoning: Estimate
    testing: Estimate
    complexity: Complexity
    observations: list[Observation] = Field(default_factory=list, max_length=3)
    consider_cases: list[str] = Field(default_factory=list, max_length=3)
    testing_notes: list[str] = Field(default_factory=list, max_length=3)
    provider: str
    usage: StepUsage | None = None
