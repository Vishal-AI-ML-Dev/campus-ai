"""Schemas for placement management v2 (Phase 9.1).

Every input is bounded. PUT bodies are full replacements: an omitted optional
field is stored as null (the UI always sends the whole form).
"""

from __future__ import annotations

import re
from datetime import date, datetime
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


# =============================================================================
# Phase 9.2 - management UI support, bulk status, withdrawal, academic import
# =============================================================================
class PlacementStatusOut(BaseModel):
    """Probe used by the frontend: only reachable when the gate is open."""

    enabled: bool = True
    role: str


ApplicationCounts = dict[str, int]


class StaffDriveListItemOut(BaseModel):
    id: int
    company_name: str
    role_title: str
    location: str | None
    package_lpa: float | None
    is_open: bool
    deadline: date | None
    created_at: datetime
    lifecycle: Lifecycle
    configured: bool
    academic_year: str | None
    applications_open_at: datetime | None
    applications_close_at: datetime | None
    window: "WindowState"
    applications_total: int
    status_counts: ApplicationCounts


class ApplicantV2Out(BaseModel):
    application_id: int
    student_id: int
    full_name: str
    roll_number: str | None
    department_name: str | None
    graduation_year: int | None
    status: ApplicationStatus
    eligible: bool = Field(description="Eligibility v2 evaluated now (not only at apply time).")
    failed_criteria: list[str]
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    placed: bool
    note: str | None
    offer_status: str | None
    contact_revealed: bool
    applied_at: datetime
    decided_at: datetime | None


BulkTarget = Literal["shortlisted", "rejected"]


class BulkStatusItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: int = Field(ge=1)
    expected_status: ApplicationStatus = Field(description="The status the TPO saw when selecting the row.")


class BulkStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_status: BulkTarget
    items: list[BulkStatusItemIn] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    dry_run: bool = Field(default=True, description="True = preview only; nothing is written.")

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _strip_or_none(value)

    @model_validator(mode="after")
    def _unique(self) -> "BulkStatusIn":
        ids = [i.application_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each application may appear only once")
        return self


BulkErrorCode = Literal["not_found", "changed", "invalid_transition", "note_required", "same_status"]


class BulkStatusResultOut(BaseModel):
    application_id: int
    ok: bool
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus
    error_code: BulkErrorCode | None = None
    detail: str | None = None


class BulkStatusOut(BaseModel):
    dry_run: bool
    to_status: ApplicationStatus
    succeeded: int
    failed: int
    results: list[BulkStatusResultOut]


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_status: ApplicationStatus | None = None


class StudentDriveOut(BaseModel):
    """A drive as a student sees it, with their own eligibility summary."""

    id: int
    company_name: str
    role_title: str
    description: str | None
    location: str | None
    package_lpa: float | None
    required_skills: str | None
    lifecycle: Lifecycle
    window: "WindowState"
    applications_open_at: datetime | None
    applications_close_at: datetime | None
    eligible: bool
    failed_criteria: list[str]
    already_applied: bool
    application_status: ApplicationStatus | None
    can_apply: bool


class MyApplicationV2Out(BaseModel):
    application_id: int
    drive_id: int
    company_name: str
    role_title: str
    lifecycle: Lifecycle
    status: ApplicationStatus
    note: str | None
    offer_status: str | None
    applied_at: datetime
    decided_at: datetime | None
    can_withdraw: bool
    withdraw_blocker: str | None


ImportAction = Literal["create", "update", "unchanged", "error"]


class ImportRowOut(BaseModel):
    row: int
    action: ImportAction
    student_id: int | None = None
    student_name: str | None = None
    email: str | None = None
    roll_number: str | None = None
    values: dict[str, float | int | str | None] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ImportPreviewOut(BaseModel):
    file_sha256: str
    file_type: Literal["csv", "xlsx"]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    to_create: int
    to_update: int
    unchanged: int
    ignored_columns: list[str]
    rows: list[ImportRowOut]


class ImportCommitOut(BaseModel):
    file_sha256: str
    total_rows: int
    created: int
    updated: int
    unchanged: int
    invalid: int
