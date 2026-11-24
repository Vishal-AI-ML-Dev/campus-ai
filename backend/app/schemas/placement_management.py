"""Schemas for placement management v2 (Phase 9.1).

Every input is bounded. PUT bodies are full replacements: an omitted optional
field is stored as null (the UI always sends the whole form).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import ApplicationStatus
from app.schemas.placement import DriveCreate, DriveOut, EligibilityReason

PlacementStatus = Literal["active", "opted_out", "debarred"]
Lifecycle = Literal["draft", "published", "closed", "completed", "cancelled"]
LifecycleTarget = Literal["published", "closed", "completed", "cancelled"]

_ROLL_RE = re.compile(r"^[A-Za-z0-9/_.\-]{1,40}$")
_ACADEMIC_YEAR_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# --- Student placement profiles --------------------------------------------
class StudentProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roll_number: str | None = Field(default=None, max_length=40)
    graduation_year: int | None = Field(default=None, ge=2000, le=2100)
    tenth_percent: float | None = Field(default=None, ge=0, le=100)
    twelfth_percent: float | None = Field(default=None, ge=0, le=100)
    diploma_percent: float | None = Field(default=None, ge=0, le=100)
    active_backlogs: int | None = Field(default=None, ge=0, le=99)
    placement_status: PlacementStatus = "active"
    status_note: str | None = Field(default=None, max_length=300)

    @field_validator("roll_number")
    @classmethod
    def _roll(cls, value: str | None) -> str | None:
        value = _strip_or_none(value)
        if value is not None and not _ROLL_RE.match(value):
            raise ValueError("Roll number may contain letters, digits and / _ . - only")
        return value

    @field_validator("status_note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _strip_or_none(value)


class StudentProfileOut(BaseModel):
    roll_number: str | None = None
    graduation_year: int | None = None
    tenth_percent: float | None = None
    twelfth_percent: float | None = None
    diploma_percent: float | None = None
    active_backlogs: int | None = None
    placement_status: PlacementStatus = "active"
    status_note: str | None = None
    recorded: bool = Field(description="False when no profile row exists yet (all values null).")
    updated_at: datetime | None = None


class VerifiedMetricsOut(BaseModel):
    cgpa: float
    attendance: float
    verified_skills: list[str]
    verified_projects: int


class StudentListItemOut(BaseModel):
    student_id: int
    full_name: str
    email: str
    section_id: int | None
    section_name: str | None
    department_id: int | None
    department_name: str | None
    roll_number: str | None
    graduation_year: int | None
    placement_status: PlacementStatus
    profile_recorded: bool
    placed: bool


class ApplicationSummaryOut(BaseModel):
    application_id: int
    drive_id: int
    company_name: str
    role_title: str
    status: ApplicationStatus
    created_at: datetime


class StudentDetailOut(StudentListItemOut):
    profile: StudentProfileOut
    metrics: VerifiedMetricsOut
    applications: list[ApplicationSummaryOut]


class MyPlacementProfileOut(BaseModel):
    profile: StudentProfileOut
    metrics: VerifiedMetricsOut
    department_id: int | None
    department_name: str | None
    placed: bool


# --- Drive settings + lifecycle --------------------------------------------
class DriveSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applications_open_at: datetime | None = None
    applications_close_at: datetime | None = None
    academic_year: str | None = Field(default=None, max_length=9)
    graduation_years: list[int] = Field(default_factory=list, max_length=6)
    department_ids: list[int] = Field(default_factory=list, max_length=50)
    min_tenth_percent: float | None = Field(default=None, ge=0, le=100)
    min_twelfth_percent: float | None = Field(default=None, ge=0, le=100)
    max_active_backlogs: int | None = Field(default=None, ge=0, le=99)
    exclude_placed: bool = False

    @field_validator("applications_open_at", "applications_close_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Timestamps must include a timezone offset")
        return value

    @field_validator("academic_year")
    @classmethod
    def _academic_year(cls, value: str | None) -> str | None:
        value = _strip_or_none(value)
        if value is None:
            return None
        match = _ACADEMIC_YEAR_RE.match(value)
        if not match:
            raise ValueError("Academic year must look like 2026-27")
        start = int(match.group(1))
        if not 2000 <= start <= 2099 or int(match.group(2)) != (start + 1) % 100:
            raise ValueError("Academic year must span two consecutive years, e.g. 2026-27")
        return value

    @field_validator("graduation_years")
    @classmethod
    def _years(cls, value: list[int]) -> list[int]:
        if any(y < 2000 or y > 2100 for y in value):
            raise ValueError("Graduation years must be between 2000 and 2100")
        return sorted(set(value))

    @field_validator("department_ids")
    @classmethod
    def _depts(cls, value: list[int]) -> list[int]:
        if any(d <= 0 for d in value):
            raise ValueError("Invalid department id")
        return sorted(set(value))

    @model_validator(mode="after")
    def _window(self) -> "DriveSettingsIn":
        if (
            self.applications_open_at is not None
            and self.applications_close_at is not None
            and self.applications_open_at >= self.applications_close_at
        ):
            raise ValueError("Applications must open before they close")
        return self


class DriveSettingsOut(BaseModel):
    drive_id: int
    configured: bool = Field(description="False = legacy drive, values are derived defaults.")
    lifecycle: Lifecycle
    is_open: bool
    applications_open_at: datetime | None
    applications_close_at: datetime | None
    academic_year: str | None
    graduation_years: list[int]
    department_ids: list[int]
    min_tenth_percent: float | None
    min_twelfth_percent: float | None
    max_active_backlogs: int | None
    exclude_placed: bool


class DraftDriveIn(DriveCreate):
    """A new drive created as a DRAFT (students cannot see it until published)."""

    model_config = ConfigDict(extra="forbid")

    settings: DriveSettingsIn = Field(default_factory=DriveSettingsIn)


class DriveFieldsIn(DriveCreate):
    """Full replacement of a drive's own fields (company, role, legacy criteria)."""

    model_config = ConfigDict(extra="forbid")


class DriveWithSettingsOut(BaseModel):
    drive: DriveOut
    settings: DriveSettingsOut


class LifecycleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: LifecycleTarget


# --- Eligibility + applications ---------------------------------------------
class EligibilityRowOut(BaseModel):
    student_id: int
    full_name: str
    department_name: str | None
    graduation_year: int | None
    eligible: bool
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    placed: bool
    placement_status: PlacementStatus
    applied: bool
    reasons: list[EligibilityReason]


WindowState = Literal["not_open_yet", "open", "closed", "no_window"]


class MyEligibilityV2Out(BaseModel):
    drive_id: int
    eligible: bool
    lifecycle: Lifecycle
    window: WindowState
    applications_open_at: datetime | None
    applications_close_at: datetime | None
    already_applied: bool
    can_apply: bool
    blockers: list[str]
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    reasons: list[EligibilityReason]


class ApplicationStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus
    expected_status: ApplicationStatus | None = Field(
        default=None,
        description="Optional optimistic check: the status the caller last saw.",
    )
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _strip_or_none(value)


class ApplicationEventOut(BaseModel):
    id: int
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    actor_role: str | None
    note: str | None
    created_at: datetime
