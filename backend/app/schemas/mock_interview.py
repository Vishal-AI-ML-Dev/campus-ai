"""Pydantic schemas for the AI Mock Interview module (feature-flagged).

Kept in its own module (not re-exported from ``app.schemas``) so the feature
stays isolated from existing contracts.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MockInterviewStatusOut(BaseModel):
    """Whether the AI Mock Interview feature is available to the caller."""

    enabled: bool


class MockInterviewCatalogOut(BaseModel):
    """Static choices the interview setup screen offers.

    Only lists what the module is designed to support; individual modes are
    switched on in later phases.
    """

    interview_types: list[str]
    modes: list[str]
    difficulties: list[str]
    languages: list[str]
    durations_minutes: list[int]


# ---------------------------------------------------------------------------
# Phase 3: text interviews (HR / technical, optional resume + JD grounding)
# ---------------------------------------------------------------------------

JD_MAX_CHARS = 10_000
ANSWER_MAX_CHARS = 5_000
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(value: str | None) -> str | None:
    """Strip control characters (keeps newlines/tabs) and outer whitespace."""
    if value is None:
        return None
    return _CONTROL_CHARS.sub("", value).strip()


class MockInterviewChoice(BaseModel):
    value: str
    label: str


class MockInterviewResumeOption(BaseModel):
    id: int
    title: str
    target_role: str | None
    is_primary: bool
    created_at: datetime


class MockInterviewOptionsOut(BaseModel):
    """Phase 3 setup choices (a subset of /catalog) plus the caller's resumes."""

    interview_types: list[MockInterviewChoice]
    difficulties: list[MockInterviewChoice]
    durations_minutes: list[int]
    languages: list[MockInterviewChoice]
    resumes: list[MockInterviewResumeOption]
    # Phase 5: [text], or [text, voice] when the voice sub-flag is on.
    # Phase 6: "video" is appended only when the caller passes the video gate.
    modes: list[MockInterviewChoice] = Field(
        default_factory=lambda: [MockInterviewChoice(value="text", label="Text")]
    )
    job_description_max_chars: int = JD_MAX_CHARS
    answer_max_chars: int = ANSWER_MAX_CHARS


# Phase 8.1: coding rounds use their own durations (decision P8-D16).
INTERVIEW_DURATIONS = (15, 30, 45)
CODING_DURATIONS = (30, 45, 60)
CODING_LANGUAGES = ("python313", "cpp17", "java21", "c11")


class MockInterviewCodingConfigIn(BaseModel):
    """Phase 8.1: optional coding-round choices (only with ``interview_type="coding"``).

    Topics and ``problem_key`` are checked against the problem bank by the
    service (422 when unknown or not available).
    """

    model_config = ConfigDict(extra="forbid")

    languages: list[Literal["python313", "cpp17", "java21", "c11"]] = Field(
        default_factory=lambda: list(CODING_LANGUAGES), min_length=1, max_length=4
    )
    topics: list[str] = Field(default_factory=list, max_length=5)
    # Practice mode: one specific problem (spec 3.4).
    problem_key: str | None = Field(default=None, min_length=3, max_length=80)

    @field_validator("languages")
    @classmethod
    def _distinct_languages(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("Choose each language only once")
        return v

    @field_validator("topics")
    @classmethod
    def _distinct_topics(cls, v: list[str]) -> list[str]:
        v = [t.strip() for t in v]
        if len(set(v)) != len(v) or any(not t or len(t) > 40 for t in v):
            raise ValueError("Choose each topic only once")
        return v


class MockInterviewSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Phase 8.1: "coding" only while the coding gate admits the caller (422
    # otherwise, checked in the router like voice/video).
    interview_type: Literal["hr", "technical", "coding"]
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    # 15/30/45 for hr/technical (unchanged); 30/45/60 for coding (P8-D16).
    duration_minutes: Literal[15, 30, 45, 60] = 30
    target_role: str = Field(min_length=2, max_length=150)
    company_name: str | None = Field(default=None, max_length=150)
    resume_id: int | None = Field(default=None, ge=1)
    job_description: str | None = Field(default=None, max_length=JD_MAX_CHARS)
    language: Literal["en", "hi", "hinglish"] = "en"
    # Phase 5: voice is accepted only while AI_MOCK_INTERVIEW_VOICE_ENABLED is on.
    # Phase 6: video only while the video flag + video allow-list admit the
    # caller (checked in the router; 422 otherwise, as for voice).
    mode: Literal["text", "voice", "video"] = "text"
    # Phase 8.1: coding-round choices; only allowed with interview_type="coding".
    coding: MockInterviewCodingConfigIn | None = None
    # Phase 8.4 (additive, optional; HR/technical only, and only while
    # AI_MOCK_INTERVIEW_COMPANY_ENABLED admits the caller - 422 otherwise):
    # a published company profile of the caller's institute and/or a generic
    # role template, plus the student's own skill focus. Question generation
    # only; evaluation never changes (P8-D11).
    company_profile_id: int | None = Field(default=None, ge=1)
    role_template_key: str | None = Field(default=None, min_length=1, max_length=60)
    focus_skills: list[str] | None = Field(default=None, max_length=20)

    @field_validator("focus_skills")
    @classmethod
    def _focus(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        for item in v:
            item = " ".join((_clean(item) or "").split())[:60]
            if item and item.lower() not in {x.lower() for x in out}:
                out.append(item)
        return out or None

    @property
    def has_company_context(self) -> bool:
        return bool(self.company_profile_id or self.role_template_key or self.focus_skills)

    @model_validator(mode="after")
    def _coding_rules(self) -> "MockInterviewSessionCreate":
        if self.interview_type == "coding" and self.has_company_context:
            raise ValueError("Company profiles apply to HR and technical interviews")
        if self.interview_type == "coding":
            if self.duration_minutes not in CODING_DURATIONS:
                raise ValueError("Coding rounds last 30, 45 or 60 minutes")
        else:
            if self.duration_minutes not in INTERVIEW_DURATIONS:
                raise ValueError("Interviews last 15, 30 or 45 minutes")
            if self.coding is not None:
                raise ValueError("Coding options are only allowed for a coding round")
        return self

    @field_validator("target_role")
    @classmethod
    def _role(cls, v: str) -> str:
        v = " ".join((_clean(v) or "").split())
        if len(v) < 2:
            raise ValueError("Enter the job role (at least 2 characters)")
        return v

    @field_validator("company_name", "job_description")
    @classmethod
    def _optional_text(cls, v: str | None) -> str | None:
        v = _clean(v)
        return v or None


class MockInterviewAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The question being answered; doubles as the idempotency key.
    turn_seq: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=ANSWER_MAX_CHARS)
    # Phase 5: "stt" = dictated in the browser (voice sessions; Phase 6 adds
    # video sessions). A label only; the text is validated and scored exactly
    # like a typed answer.
    source: Literal["typed", "stt"] = "typed"

    @field_validator("answer")
    @classmethod
    def _answer(cls, v: str) -> str:
        v = _clean(v) or ""
        if not v:
            raise ValueError("Answer cannot be empty")
        return v


class MockInterviewTurnOut(BaseModel):
    seq: int
    topic: str | None
    question: str
    is_follow_up: bool
    answer_text: str | None
    answered_at: datetime | None
    answer_source: Literal["typed", "stt"] | None = None


class MockInterviewProgressOut(BaseModel):
    answered: int
    question_budget: int | None
    topics_total: int | None
    topics_covered: int


class MockInterviewSessionSummaryOut(BaseModel):
    id: int
    interview_type: str
    mode: str
    status: str
    target_role: str | None
    company_name: str | None
    difficulty: str
    language: str
    duration_minutes: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MockInterviewCodingSummaryOut(BaseModel):
    """Phase 8.1: coding-round progress (coding sessions only; additive, P8-D16)."""

    problems: int
    problems_closed: int
    graded_submissions: int
    # Batch 1 is always review-only: no code is executed (P8-D8).
    review_only: bool


class CompanySourceStudentOut(BaseModel):
    """Phase 8.4: a plain-text citation - label and (public sources) domain only."""

    kind: Literal["drive", "jd", "institute_note", "public_url"]
    label: str
    domain: str | None = None


class CompanyContextOut(BaseModel):
    """Phase 8.4: the company/role context a session was created with (frozen).

    Generation only (P8-D11): the session is evaluated with the standard
    HR/technical rubric; there is no company-fit or competency score.
    """

    profile_id: int | None
    name: str | None
    role_title: str
    role_template_label: str | None
    sources: list[CompanySourceStudentOut]
    attribution: str
    disclaimer: str
    evaluation: Literal["standard_rubric"] = "standard_rubric"


class MockInterviewSessionOut(MockInterviewSessionSummaryOut):
    resume_id: int | None
    has_job_description: bool
    # start | answer | next_question | none
    awaiting: Literal["start", "answer", "next_question", "none"]
    current_turn_seq: int | None
    remaining_seconds: int | None
    progress: MockInterviewProgressOut
    turns: list[MockInterviewTurnOut]
    closing_message: str | None
    end_reason: str | None
    # Phase 8.1: present only for coding sessions (omitted from the JSON of
    # every other session, so existing responses are byte-identical).
    coding_summary: MockInterviewCodingSummaryOut | None = None
    # Phase 8.4: present only for sessions created with a company profile or
    # role template (omitted otherwise, so existing responses are unchanged).
    company_context: CompanyContextOut | None = None


# ---------------------------------------------------------------------------
# Phase 4: evaluation report + history
# ---------------------------------------------------------------------------

ReportQuestionStatus = Literal["scored", "no_answer", "dont_know", "not_assessed"]


class MockInterviewReportCategoryOut(BaseModel):
    key: str
    label: str
    weight: float
    description: str
    # 0-100; null when no answer could be assessed for this category.
    score: float | None
    assessed: int


class MockInterviewReportScoreOut(BaseModel):
    key: str
    label: str
    score: int  # 0-5
    evidence: str  # verbatim quote from the answer ("" only for a 0)


class MockInterviewReportQuestionOut(BaseModel):
    seq: int
    topic: str | None
    question: str
    is_follow_up: bool
    answer_text: str | None
    status: ReportQuestionStatus
    scores: list[MockInterviewReportScoreOut]
    feedback: str


class MockInterviewReportItemOut(BaseModel):
    text: str
    seq: int | None
    evidence: str


class MockInterviewReportOut(BaseModel):
    session_id: int
    interview_type: str
    target_role: str | None
    company_name: str | None
    difficulty: str
    language: str
    duration_minutes: int
    started_at: datetime | None
    completed_at: datetime | None
    overall_score: float
    rubric_version: str
    created_at: datetime
    categories: list[MockInterviewReportCategoryOut]
    questions: list[MockInterviewReportQuestionOut]
    strengths: list[MockInterviewReportItemOut]
    weaknesses: list[MockInterviewReportItemOut]
    recommendations: list[str]
    disclaimer: str


class MockInterviewReportBriefOut(BaseModel):
    overall_score: float
    created_at: datetime


class MockInterviewHistoryItemOut(MockInterviewSessionSummaryOut):
    answered: int
    report: MockInterviewReportBriefOut | None


# --- Worker report output, re-validated before anything is stored -----------
class _WorkerModel(BaseModel):
    """Unknown fields are dropped; every kept text field is control-char free."""

    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="after")
    @classmethod
    def _strip_controls(cls, v):
        return _clean(v) if isinstance(v, str) else v


class WorkerAnswerScore(_WorkerModel):
    score: int = Field(ge=0, le=5)
    evidence: str = Field(default="", max_length=300)


class WorkerQuestion(_WorkerModel):
    seq: int = Field(ge=1)
    status: ReportQuestionStatus
    scores: dict[str, WorkerAnswerScore] = Field(default_factory=dict, max_length=10)
    feedback: str = Field(default="", max_length=400)


class WorkerCategory(_WorkerModel):
    key: str = Field(max_length=40)
    label: str = Field(max_length=60)
    weight: float = Field(ge=0, le=1)
    description: str = Field(default="", max_length=200)
    score: float | None = Field(default=None, ge=0, le=100)
    assessed: int = Field(default=0, ge=0)


class WorkerItem(_WorkerModel):
    text: str = Field(min_length=1, max_length=300)
    seq: int | None = Field(default=None, ge=1)
    evidence: str = Field(default="", max_length=300)


class WorkerReport(_WorkerModel):
    rubric_version: str = Field(min_length=1, max_length=20)
    overall_score: float = Field(ge=0, le=100)
    categories: list[WorkerCategory] = Field(min_length=1, max_length=10)
    questions: list[WorkerQuestion] = Field(min_length=1, max_length=30)
    strengths: list[WorkerItem] = Field(default_factory=list, max_length=5)
    weaknesses: list[WorkerItem] = Field(default_factory=list, max_length=5)
    recommendations: list[str] = Field(default_factory=list, max_length=5)
    provider: str | None = Field(default=None, max_length=50)

    @field_validator("recommendations")
    @classmethod
    def _recs(cls, v: list[str]) -> list[str]:
        out = [(_clean(r) or "")[:300] for r in v]
        return [r for r in out if r]
