"""Pydantic schemas for the AI Mock Interview module (feature-flagged).

Kept in its own module (not re-exported from ``app.schemas``) so the feature
stays isolated from existing contracts.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    job_description_max_chars: int = JD_MAX_CHARS
    answer_max_chars: int = ANSWER_MAX_CHARS


class MockInterviewSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interview_type: Literal["hr", "technical"]
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    duration_minutes: Literal[15, 30, 45] = 30
    target_role: str = Field(min_length=2, max_length=150)
    company_name: str | None = Field(default=None, max_length=150)
    resume_id: int | None = Field(default=None, ge=1)
    job_description: str | None = Field(default=None, max_length=JD_MAX_CHARS)
    language: Literal["en", "hi", "hinglish"] = "en"

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
