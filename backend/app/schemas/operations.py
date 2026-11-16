"""Operations (audit, timetable, leave, leads, feedback) schemas."""

from datetime import date, datetime, time
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import LeaveRequestType, LeaveStatus


class LeadCreate(BaseModel):
    """Public contact / demo-request submission from the marketing site."""

    name: str = Field(min_length=1, max_length=255, examples=["Asha Mehta"])
    email: EmailStr
    institute: str | None = Field(default=None, max_length=255)
    role: str | None = Field(
        default=None, max_length=100, examples=["Administrator"]
    )
    message: str = Field(min_length=1, max_length=4000)


class LeadOut(BaseModel):
    id: int
    tenant_id: int | None
    name: str
    email: EmailStr
    institute: str | None
    role: str | None
    message: str
    handled: bool

    model_config = ConfigDict(from_attributes=True)


class FeedbackCreate(BaseModel):
    """Anonymous in-product feedback. Rating is an optional 1-5 score."""

    message: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=50, examples=["bug"])
    rating: int | None = Field(default=None, ge=1, le=5, examples=[5])


class FeedbackOut(BaseModel):
    id: int
    category: str | None
    rating: int | None
    message: str

    model_config = ConfigDict(from_attributes=True)


class AuditLogOut(BaseModel):
    id: int
    actor_id: int | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    summary: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TimetableEntryCreate(BaseModel):
    """Create a recurring weekly class slot for a section (staff only)."""

    section_id: int = Field(gt=0)
    subject_id: int | None = Field(default=None, gt=0)
    teacher_id: int | None = Field(default=None, gt=0)
    day_of_week: int = Field(ge=0, le=6, examples=[0])  # 0=Mon .. 6=Sun
    start_time: time = Field(examples=["09:00"])
    end_time: time = Field(examples=["10:00"])
    room: str | None = Field(default=None, max_length=100, examples=["Room 101"])


class TimetableEntryUpdate(BaseModel):
    """Patch an existing slot. Only the fields you send are changed.

    (`section_id` is intentionally immutable - delete + recreate to move a slot
    to another section.)
    """

    subject_id: int | None = Field(default=None, gt=0)
    teacher_id: int | None = Field(default=None, gt=0)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    room: str | None = Field(default=None, max_length=100)


class TimetableEntryOut(BaseModel):
    """A timetable slot enriched with section/subject/teacher display names."""

    id: int
    section_id: int
    section_name: str | None = None
    subject_id: int | None = None
    subject_name: str | None = None
    teacher_id: int | None = None
    teacher_name: str | None = None
    day_of_week: int
    start_time: time
    end_time: time
    room: str | None = None


class LeaveRequestCreate(BaseModel):
    """A user applies for their OWN leave or OD for a date range.

    `category` is validated in the router against the set allowed for the chosen
    `request_type` (medical/personal/emergency for leave; fest/technical/sports/
    competition/ncc_nss/industrial_visit/placement/other for OD).
    """

    request_type: LeaveRequestType
    category: str = Field(min_length=1, max_length=50, examples=["medical"])
    title: str = Field(min_length=1, max_length=200, examples=["Fever - need rest"])
    reason: str | None = Field(default=None, max_length=2000)
    event_name: str | None = Field(default=None, max_length=200)
    proof_url: str | None = Field(default=None, max_length=500)
    start_date: date
    end_date: date


class BulkODCreate(BaseModel):
    """Staff raises ON-DUTY for many students at once for a single event.

    Each student gets their own auto-approved OD row, linked by a shared bulk
    group id. Used for fests / sports / group events.
    """

    student_ids: list[int] = Field(min_length=1, examples=[[6, 7, 8]])
    category: str = Field(min_length=1, max_length=50, examples=["fest"])
    title: str = Field(min_length=1, max_length=200, examples=["TechFest 2026"])
    event_name: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)
    proof_url: str | None = Field(default=None, max_length=500)
    start_date: date
    end_date: date


class LeaveDecision(BaseModel):
    """A staff approve/reject decision on a pending request."""

    status: Literal["approved", "rejected"]
    review_note: str | None = Field(default=None, max_length=2000)


class LeaveRequestOut(BaseModel):
    """A leave/OD request enriched with student / section / reviewer names."""

    id: int
    request_type: LeaveRequestType
    category: str
    student_id: int
    student_name: str | None = None
    section_id: int | None = None
    section_name: str | None = None
    title: str
    reason: str | None = None
    event_name: str | None = None
    proof_url: str | None = None
    start_date: date
    end_date: date
    status: LeaveStatus
    applied_by_id: int | None = None
    reviewed_by_id: int | None = None
    reviewer_name: str | None = None
    review_note: str | None = None
    reviewed_at: datetime | None = None
    bulk_group_id: str | None = None
    days: int = 1
    created_at: datetime


class BulkODResult(BaseModel):
    """Summary returned after a bulk-OD action."""

    bulk_group_id: str
    created: int
    skipped: list[int] = []
    entries: list[LeaveRequestOut] = []
