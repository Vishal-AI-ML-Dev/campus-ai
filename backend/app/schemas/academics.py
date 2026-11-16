"""Academics (departments, sections, subjects, attendance, face, results) schemas."""

from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import AttendanceStatus


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150, examples=["Computer Science"])
    code: str = Field(min_length=1, max_length=20, examples=["CSE"])


class DepartmentOut(BaseModel):
    id: int
    name: str
    code: str

    model_config = ConfigDict(from_attributes=True)


class SectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50, examples=["A"])
    year: int | None = Field(default=None, ge=1, le=10, examples=[3])


class SectionOut(BaseModel):
    id: int
    name: str
    year: int | None
    department_id: int

    model_config = ConfigDict(from_attributes=True)


class SubjectCreate(BaseModel):
    department_id: int = Field(examples=[1])
    name: str = Field(min_length=1, max_length=150, examples=["Database Management Systems"])
    code: str = Field(min_length=1, max_length=30, examples=["CS501"])
    credits: int = Field(ge=1, le=10, examples=[4])
    semester: int = Field(ge=1, le=12, examples=[5])


class SubjectOut(BaseModel):
    id: int
    name: str
    code: str
    credits: int
    semester: int
    department_id: int

    model_config = ConfigDict(from_attributes=True)


class AttendanceMarkItem(BaseModel):
    """A single student's status within a bulk mark request."""

    student_id: int
    status: AttendanceStatus = AttendanceStatus.present


class AttendanceMarkRequest(BaseModel):
    """Mark (or re-mark) attendance for several students in one section/date."""

    section_id: int
    date: date
    records: list[AttendanceMarkItem] = Field(min_length=1)


class AttendanceRecordOut(BaseModel):
    id: int
    student_id: int
    section_id: int
    date: date
    status: AttendanceStatus
    # True when an `absent` record falls inside an approved leave/OD and is
    # therefore excused (does not count against the student's percentage).
    condoned: bool = False
    # Short human reason, e.g. "OD: Spring Fest" or "Leave: medical".
    condone_reason: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AttendanceSummaryOut(BaseModel):
    """Aggregated view for a single student.

    Absences covered by an approved leave/OD are condoned: counted as `excused`
    and removed from the percentage denominator. `raw_percentage` keeps the
    pre-condonation value for transparency.
    """

    total: int
    present: int
    absent: int  # absences that are NOT condoned
    late: int
    excused: int = 0  # condoned absences (approved OD / leave)
    percentage: float  # condoned: (present + late) / (total - excused) * 100
    raw_percentage: float = 0.0  # before condonation: (present + late) / total * 100


class FaceEnrollRequest(BaseModel):
    """Enroll (or re-enroll) a student's reference face from a base64 photo."""

    student_id: int = Field(gt=0)
    image_base64: str = Field(min_length=1)


class FaceEnrollmentOut(BaseModel):
    """A stored enrollment record (the embedding itself lives in Qdrant)."""

    student_id: int
    enrolled_by_id: int | None
    det_score: float | None
    enrolled_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FaceEnrollmentStatusOut(BaseModel):
    """A roster student + whether they have an enrolled reference face."""

    student_id: int
    full_name: str
    email: EmailStr
    enrolled: bool
    det_score: float | None = None
    enrolled_at: datetime | None = None


class FacePhotoMatchRequest(BaseModel):
    """Match a class photo against a section's enrolled students."""

    section_id: int = Field(gt=0)
    image_base64: str = Field(min_length=1)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class FaceMatchSuggestion(BaseModel):
    """One roster student + whether the class photo matched them."""

    student_id: int
    full_name: str
    enrolled: bool
    matched: bool
    score: float | None = None
    suggested_status: AttendanceStatus


class FaceMatchOutsider(BaseModel):
    """An enrolled student matched in the photo but NOT in this section."""

    student_id: int
    score: float


class FacePhotoMatchResponse(BaseModel):
    """Suggested attendance from a class photo (teacher confirms before marking)."""

    section_id: int
    detected_faces: int
    unmatched_faces: int
    threshold: float
    suggestions: list[FaceMatchSuggestion]
    matched_outside_section: list[FaceMatchOutsider]


class ResultCreate(BaseModel):
    """Teacher enters a student's marks for a subject. Grade point is derived."""

    student_id: int
    subject_id: int
    marks_obtained: float = Field(ge=0, examples=[78])
    max_marks: float = Field(default=100.0, gt=0, examples=[100])


class ResultOut(BaseModel):
    id: int
    student_id: int
    subject_id: int
    marks_obtained: float
    max_marks: float
    grade_point: float

    model_config = ConfigDict(from_attributes=True)


class SemesterGPA(BaseModel):
    """Credit-weighted SGPA for one semester."""

    semester: int
    sgpa: float
    credits: int


class AcademicSummaryOut(BaseModel):
    """A student's CGPA plus a per-semester SGPA breakdown."""

    cgpa: float
    total_credits: int
    semesters: list[SemesterGPA]
