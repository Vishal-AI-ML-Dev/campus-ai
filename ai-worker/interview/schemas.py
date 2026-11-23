"""Request/response and LLM-output schemas for the interview engine.

Two kinds of models live here:
  * HTTP contracts with the backend (``PlanRequest`` ... ``TurnResponse``).
  * Structured LLM outputs (``LLM*``), validated before use. Everything the
    model returns is clamped / checked again in code, never trusted blindly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from interview.safety import safe_label

InterviewType = Literal["hr", "technical"]
Difficulty = Literal["easy", "medium", "hard"]
Language = Literal["en", "hi", "hinglish"]
TopicSource = Literal["intro", "role", "resume", "jd", "general"]
Coverage = Literal["full", "partial", "none"]
Action = Literal["opening", "follow_up", "next_topic", "end"]

MAX_RESUME_CHARS = 8_000
MAX_JD_CHARS = 10_000
MAX_ANSWER_CHARS = 5_000
MAX_QUESTION_CHARS = 1_000
HARD_MAX_TURNS = 30


# --- Contracts with the backend ---------------------------------------------
class InterviewConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interview_type: InterviewType
    difficulty: Difficulty = "medium"
    duration_minutes: int = Field(default=30, ge=5, le=120)
    target_role: str = Field(min_length=1, max_length=150)
    company_name: str | None = Field(default=None, max_length=150)
    language: Language = "en"

    @field_validator("target_role")
    @classmethod
    def _role(cls, v: str) -> str:
        return safe_label(v, 150) or "Software Engineer"

    @field_validator("company_name")
    @classmethod
    def _company(cls, v: str | None) -> str | None:
        return safe_label(v, 150) or None if v else None


class ProfileProject(BaseModel):
    title: str = Field(max_length=300)
    contribution: str | None = Field(default=None, max_length=1000)


class CandidateProfile(BaseModel):
    """The student's VERIFIED data only (same moat as the mentor)."""

    model_config = ConfigDict(extra="ignore")

    verified_skills: list[str] = Field(default_factory=list, max_length=100)
    projects: list[ProfileProject] = Field(default_factory=list, max_length=50)
    cgpa: float | None = None


class PlanTopic(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    area: str = Field(default="", max_length=100)
    focus: str = Field(default="", max_length=300)
    source: TopicSource = "role"
    # Verbatim snippet from the resume/JD that justifies a resume/jd topic.
    grounding: str = Field(default="", max_length=300)


class JDAnalysis(BaseModel):
    role_title: str = Field(default="", max_length=150)
    required_skills: list[str] = Field(default_factory=list, max_length=15)
    responsibilities: list[str] = Field(default_factory=list, max_length=8)
    seniority: str = Field(default="", max_length=50)


class InterviewPlan(BaseModel):
    version: int = 1
    topics: list[PlanTopic] = Field(min_length=1, max_length=12)
    question_budget: int = Field(ge=1, le=HARD_MAX_TURNS)
    jd_analysis: JDAnalysis | None = None
    planner: Literal["llm", "fallback"] = "llm"


class PlanRequest(BaseModel):
    config: InterviewConfig
    profile: CandidateProfile = Field(default_factory=CandidateProfile)
    resume_text: str | None = Field(default=None, max_length=50_000)
    job_description: str | None = Field(default=None, max_length=MAX_JD_CHARS)


class StepUsage(BaseModel):
    """Phase 7.2: LLM usage of one worker request (numbers/ids only, no text).

    Token counts are None when the provider reported none (unknown, not zero).
    """

    provider: str | None = None
    model: str | None = None
    llm_calls: int = 0
    failed_calls: int = 0
    fallback_used: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_reported: int = 0
    tokens_unreported: int = 0
    llm_latency_ms: int = 0


class PlanResponse(BaseModel):
    plan: InterviewPlan
    provider: str
    usage: StepUsage | None = None  # Phase 7.2, additive


class TurnEvaluation(BaseModel):
    coverage: Coverage = "none"
    depth: int = Field(default=1, ge=1, le=5)
    missing_points: list[str] = Field(default_factory=list, max_length=3)
    evidence: str = Field(default="", max_length=300)
    follow_up_recommended: bool = False
    follow_up_angle: str = Field(default="", max_length=300)
    difficulty_signal: int = Field(default=0, ge=-1, le=1)
    dont_know: bool = False
    source: Literal["llm", "heuristic", "rule"] = "llm"


class TranscriptTurn(BaseModel):
    seq: int = Field(ge=1)
    topic: str | None = Field(default=None, max_length=100)
    question: str = Field(max_length=3_000)
    is_follow_up: bool = False
    answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    evaluation: TurnEvaluation | None = None


class TurnRequest(BaseModel):
    config: InterviewConfig
    plan: InterviewPlan
    profile: CandidateProfile = Field(default_factory=CandidateProfile)
    resume_text: str | None = Field(default=None, max_length=50_000)
    job_description: str | None = Field(default=None, max_length=MAX_JD_CHARS)
    # Oldest first. Empty => opening. Otherwise the LAST turn must be answered.
    transcript: list[TranscriptTurn] = Field(default_factory=list, max_length=HARD_MAX_TURNS)
    remaining_seconds: int = Field(ge=-86_400, le=86_400)


class NextQuestion(BaseModel):
    seq: int
    topic: str
    is_follow_up: bool
    text: str


class TurnResponse(BaseModel):
    action: Action
    # Evaluation of the last answer (None for the opening).
    evaluation: TurnEvaluation | None = None
    question: NextQuestion | None = None
    closing_message: str | None = None
    end_reason: str | None = None
    level: Difficulty
    provider: str
    usage: StepUsage | None = None  # Phase 7.2, additive


# --- Structured LLM outputs ---------------------------------------------------
class LLMPlanTopic(BaseModel):
    name: str = Field(description="Short topic name, e.g. 'DBMS normalization'")
    area: str = Field(default="", description="Library area, e.g. 'dbms' or 'teamwork'")
    focus: str = Field(default="", description="What to probe in one sentence")
    source: str = Field(default="role", description="role | resume | jd | general")
    grounding: str = Field(
        default="",
        description="For resume/jd topics: an exact short quote from that text",
    )


class LLMPlan(BaseModel):
    topics: list[LLMPlanTopic]


class LLMJDAnalysis(BaseModel):
    role_title: str = ""
    required_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    seniority: str = ""


class LLMEvaluation(BaseModel):
    coverage: str = Field(description="full | partial | none")
    depth: int = Field(description="1 (superficial) to 5 (expert)")
    missing_points: list[str] = Field(default_factory=list)
    evidence: str = Field(default="", description="Exact short quote from the answer")
    follow_up_recommended: bool = False
    follow_up_angle: str = ""
    difficulty_signal: int = Field(default=0, description="-1 easier, 0 same, 1 harder")


class LLMQuestion(BaseModel):
    preamble: str = Field(
        default="",
        description="Optional one-sentence neutral transition; no praise or scores",
    )
    question: str = Field(description="Exactly one interview question")


class LLMClosing(BaseModel):
    message: str


# --- Phase 4: final report ----------------------------------------------------
QuestionStatus = Literal["scored", "no_answer", "dont_know", "not_assessed"]


class ReportTurn(BaseModel):
    seq: int = Field(ge=1)
    topic: str | None = Field(default=None, max_length=100)
    question: str = Field(max_length=3_000)
    is_follow_up: bool = False
    answer: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)


class ReportRequest(BaseModel):
    config: InterviewConfig
    plan: InterviewPlan | None = None
    # Every asked turn, oldest first (the last one may be unanswered).
    transcript: list[ReportTurn] = Field(min_length=1, max_length=HARD_MAX_TURNS)


class AnswerCategoryScore(BaseModel):
    score: int = Field(ge=0, le=5)
    # Verbatim quote from the answer; empty only for a 0 score.
    evidence: str = Field(default="", max_length=300)


class QuestionReport(BaseModel):
    seq: int = Field(ge=1)
    status: QuestionStatus
    scores: dict[str, AnswerCategoryScore] = Field(default_factory=dict)
    feedback: str = Field(default="", max_length=400)


class CategoryResult(BaseModel):
    key: str
    label: str
    weight: float
    description: str
    # 0-100; None when no answer could be assessed for this category.
    score: float | None = Field(default=None, ge=0, le=100)
    assessed: int = 0


class ReportItem(BaseModel):
    text: str = Field(max_length=300)
    seq: int | None = None
    evidence: str = Field(default="", max_length=300)


class ReportResponse(BaseModel):
    rubric_version: str
    overall_score: float = Field(ge=0, le=100)
    categories: list[CategoryResult]
    questions: list[QuestionReport]
    strengths: list[ReportItem]
    weaknesses: list[ReportItem]
    recommendations: list[str]
    provider: str
    usage: StepUsage | None = None  # Phase 7.2, additive


class LLMCategoryScore(BaseModel):
    category: str = Field(description="One of the rubric category keys")
    score: float = Field(description="Integer 0 (absent) to 5 (excellent)")
    evidence: str = Field(
        default="", description="Exact short quote copied from THIS answer that justifies the score"
    )


class LLMAnswerScore(BaseModel):
    seq: int = Field(description="The question number being scored")
    scores: list[LLMCategoryScore] = Field(default_factory=list)
    feedback: str = Field(
        default="", description="1-2 sentences: what was good and what to improve"
    )


class LLMAnswerScores(BaseModel):
    answers: list[LLMAnswerScore] = Field(default_factory=list)


class LLMReportItem(BaseModel):
    text: str = Field(description="One sentence")
    seq: int = Field(description="Question number this is based on")
    quote: str = Field(description="Exact short quote from that answer")


class LLMReportSummary(BaseModel):
    strengths: list[LLMReportItem] = Field(default_factory=list)
    weaknesses: list[LLMReportItem] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
