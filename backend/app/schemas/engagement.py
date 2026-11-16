"""Engagement (announcements, calendar, assignments, materials, doubts) schemas."""

from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from app.models import SubmissionStatus, MaterialCategory, DoubtStatus


class AnnouncementCreate(BaseModel):
    """Admin posts an announcement to everyone or to a single role."""

    title: str = Field(min_length=1, max_length=200, examples=["Mid-sem exams"])
    body: str = Field(min_length=1, max_length=5000)
    audience: Literal["all", "student", "teacher", "tpo"] = "all"


class AnnouncementOut(BaseModel):
    id: int
    title: str
    body: str
    audience: str
    author_id: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CalendarEventCreate(BaseModel):
    """Admin adds an academic-calendar entry (holiday/exam/event/deadline)."""

    title: str = Field(
        min_length=1, max_length=200, examples=["Semester exams begin"]
    )
    description: str | None = Field(default=None, max_length=2000)
    event_date: date
    end_date: date | None = None
    category: Literal["holiday", "exam", "event", "deadline"] = "event"
    audience: Literal["all", "student", "teacher", "tpo"] = "all"


class CalendarEventOut(BaseModel):
    id: int
    title: str
    description: str | None
    event_date: date
    end_date: date | None
    category: str
    audience: str
    created_by_id: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignmentCreate(BaseModel):
    """Teacher posts an assignment for a section (optionally tied to a subject)."""

    section_id: int = Field(gt=0)
    subject_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    due_date: datetime
    max_marks: float = Field(default=100.0, gt=0)


class AssignmentOut(BaseModel):
    id: int
    section_id: int
    subject_id: int | None
    title: str
    description: str | None
    due_date: datetime
    max_marks: float
    created_by_id: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignmentWithStatusOut(AssignmentOut):
    """An assignment + the logged-in student's own submission state."""

    submitted: bool = False
    submission_status: SubmissionStatus | None = None
    marks: float | None = None


class SubmissionCreate(BaseModel):
    """A student turns in work: free text and/or a link (at least one)."""

    content: str | None = Field(default=None, max_length=10000)
    link: str | None = Field(default=None, max_length=500)


class SubmissionGrade(BaseModel):
    """A teacher grades a submission (marks must be within the assignment max)."""

    marks: float = Field(ge=0)
    feedback: str | None = Field(default=None, max_length=2000)


class SubmissionOut(BaseModel):
    id: int
    assignment_id: int
    student_id: int
    content: str | None
    link: str | None
    status: SubmissionStatus
    marks: float | None
    feedback: str | None
    graded_by_id: int | None
    submitted_at: datetime
    graded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class MaterialCreate(BaseModel):
    """Teacher/admin uploads a study material for a section.

    Provide inline `content` (notes text) and/or an external `link` - at least
    one is required (validated in the router).
    """

    section_id: int = Field(gt=0, examples=[1])
    subject_id: int | None = Field(default=None, gt=0, examples=[1])
    title: str = Field(
        min_length=1, max_length=200, examples=["Unit 1 - ER Models"]
    )
    description: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=20000)
    link: str | None = Field(
        default=None,
        max_length=500,
        examples=["https://drive.google.com/notes"],
    )
    category: MaterialCategory = MaterialCategory.notes


class MaterialOut(BaseModel):
    """A study material as returned to staff and students."""

    id: int
    section_id: int
    subject_id: int | None
    title: str
    description: str | None
    content: str | None
    link: str | None
    category: MaterialCategory
    uploaded_by_id: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DoubtCreate(BaseModel):
    """A student/staff posts a question to a section's doubt forum."""

    section_id: int = Field(gt=0, examples=[1])
    subject_id: int | None = Field(default=None, gt=0, examples=[1])
    title: str = Field(
        min_length=1, max_length=200, examples=["Doubt in normalization"]
    )
    body: str = Field(min_length=1, max_length=5000)


class AnswerCreate(BaseModel):
    """An answer to a doubt."""

    body: str = Field(min_length=1, max_length=5000)


class AnswerOut(BaseModel):
    """An answer with its live upvote count + whether the viewer upvoted it."""

    id: int
    doubt_id: int
    body: str
    answered_by_id: int | None
    is_accepted: bool
    upvote_count: int
    viewer_has_upvoted: bool
    created_at: datetime


class DoubtOut(BaseModel):
    """A doubt list item, with a live answer count."""

    id: int
    section_id: int
    subject_id: int | None
    title: str
    body: str
    status: DoubtStatus
    asked_by_id: int | None
    answer_count: int
    created_at: datetime
    resolved_at: datetime | None


class DoubtDetailOut(DoubtOut):
    """A doubt plus all of its answers (accepted first, then most upvoted)."""

    answers: list[AnswerOut]
