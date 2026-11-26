"""Schemas for faculty interview-preparation campaigns (Phase 9.3).

Privacy (P9-D7, option A): the progress schemas carry identity, assignment
and COMPLETION fields only. There is deliberately no score, transcript,
recording, feedback, report or answer field anywhere in this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CampaignStatus = Literal["draft", "active", "closed", "cancelled", "archived"]
CampaignTarget = Literal["active", "closed", "cancelled", "archived"]
CampaignType = Literal["hr", "technical", "coding"]
Completion = Literal["pending", "completed", "completed_late"]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split()) if "\n" not in value else value.strip()
    return value or None


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    interview_type: CampaignType | None = None
    target_role: str | None = Field(default=None, max_length=150)
    drive_id: int | None = Field(default=None, ge=1)
    starts_at: datetime
    due_at: datetime

    @field_validator("title", "target_role")
    @classmethod
    def _one_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @field_validator("description")
    @classmethod
    def _desc(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("starts_at", "due_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must include a timezone offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _window(self) -> "CampaignIn":
        if self.title is None or len(self.title) < 3:
            raise ValueError("Title must have at least 3 characters")
        if self.starts_at >= self.due_at:
            raise ValueError("The campaign must start before its due date")
        return self


class CampaignStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: CampaignTarget


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_ids: list[int] = Field(default_factory=list, max_length=500)
    section_ids: list[int] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _some(self) -> "AssignIn":
        if not self.student_ids and not self.section_ids:
            raise ValueError("Choose at least one student or section")
        if any(i <= 0 for i in self.student_ids + self.section_ids):
            raise ValueError("Invalid id")
        self.student_ids = sorted(set(self.student_ids))
        self.section_ids = sorted(set(self.section_ids))
        return self


class CampaignOut(BaseModel):
    id: int
    title: str
    description: str | None
    status: CampaignStatus
    interview_type: CampaignType | None
    target_role: str | None
    drive_id: int | None
    drive_label: str | None
    starts_at: datetime
    due_at: datetime
    created_by_role: str
    created_by_name: str | None
    is_mine: bool
    assigned: int
    completed: int
    completed_late: int
    created_at: datetime


class AssignOut(BaseModel):
    added: int
    already_assigned: int
    total_assigned: int


class CandidateOut(BaseModel):
    student_id: int
    full_name: str
    section_id: int | None
    section_name: str | None
    department_name: str | None
    assigned: bool


class ProgressRowOut(BaseModel):
    """Completion status ONLY (P9-D7)."""

    model_config = ConfigDict(extra="forbid")

    student_id: int
    full_name: str
    section_name: str | None
    completion: Completion
    completed_at: datetime | None
    assigned_at: datetime


class MyCampaignOut(BaseModel):
    id: int
    title: str
    description: str | None
    status: CampaignStatus
    interview_type: CampaignType | None
    target_role: str | None
    drive_id: int | None
    drive_label: str | None
    starts_at: datetime
    due_at: datetime
    completion: Completion
    completed_at: datetime | None


class TeacherSectionOut(BaseModel):
    id: int
    name: str
    department_name: str | None
