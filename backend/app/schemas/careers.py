"""Student career profile (skills, ECA, internships, projects) schemas."""

from datetime import date
from pydantic import BaseModel, ConfigDict, Field

from app.models import SkillStatus, ECACategory, InternshipType


class SkillCreate(BaseModel):
    """A student claims a skill and (optionally) links proof. Starts pending."""

    name: str = Field(min_length=1, max_length=100, examples=["FastAPI"])
    evidence_url: str | None = Field(
        default=None, max_length=500, examples=["https://github.com/me/project"]
    )
    evidence_note: str | None = Field(default=None, max_length=2000)


class SkillOut(BaseModel):
    id: int
    student_id: int
    name: str
    evidence_url: str | None
    evidence_note: str | None
    status: SkillStatus
    ai_score: float | None
    review_note: str | None

    model_config = ConfigDict(from_attributes=True)


class SkillDecision(BaseModel):
    """A mentor's verify/flag decision on a pending skill."""

    status: SkillStatus = Field(examples=["verified"])  # must be verified|flagged
    review_note: str | None = Field(default=None, max_length=500)


class ECACreate(BaseModel):
    """A student logs an extra-curricular activity + optional proof. Starts pending."""

    title: str = Field(
        min_length=1, max_length=150, examples=["Inter-college Football Captain"]
    )
    category: ECACategory = Field(default=ECACategory.other, examples=["sports"])
    organization: str | None = Field(
        default=None, max_length=150, examples=["Sports Committee"]
    )
    description: str | None = Field(default=None, max_length=2000)
    evidence_url: str | None = Field(
        default=None, max_length=500, examples=["https://example.com/certificate.pdf"]
    )


class ECAOut(BaseModel):
    id: int
    student_id: int
    title: str
    category: ECACategory
    organization: str | None
    description: str | None
    evidence_url: str | None
    status: SkillStatus
    review_note: str | None

    model_config = ConfigDict(from_attributes=True)


class ECADecision(BaseModel):
    """A teacher/TPO's verify/flag decision on a pending ECA claim."""

    status: SkillStatus = Field(examples=["verified"])  # must be verified|flagged
    review_note: str | None = Field(default=None, max_length=500)


class InternshipCreate(BaseModel):
    """A student logs an internship / OJT + optional proof. Starts pending."""

    organization: str = Field(
        min_length=1, max_length=200, examples=["Infosys"]
    )
    role_title: str = Field(
        min_length=1, max_length=150, examples=["Software Engineering Intern"]
    )
    internship_type: InternshipType = Field(
        default=InternshipType.internship, examples=["internship"]
    )
    mode: str | None = Field(default=None, max_length=20, examples=["remote"])
    location: str | None = Field(
        default=None, max_length=150, examples=["Bengaluru"]
    )
    description: str | None = Field(default=None, max_length=2000)
    skills_used: str | None = Field(
        default=None, max_length=300, examples=["Python, FastAPI, PostgreSQL"]
    )
    start_date: date | None = Field(default=None, examples=["2025-05-01"])
    end_date: date | None = Field(default=None, examples=["2025-07-31"])
    is_ongoing: bool = Field(default=False)
    certificate_url: str | None = Field(
        default=None,
        max_length=500,
        examples=["https://example.com/certificate.pdf"],
    )


class InternshipOut(BaseModel):
    id: int
    student_id: int
    organization: str
    role_title: str
    internship_type: InternshipType
    mode: str | None
    location: str | None
    description: str | None
    skills_used: str | None
    start_date: date | None
    end_date: date | None
    is_ongoing: bool
    certificate_url: str | None
    status: SkillStatus
    review_note: str | None

    model_config = ConfigDict(from_attributes=True)


class InternshipDecision(BaseModel):
    """A teacher/TPO's verify/flag decision on a pending internship claim."""

    status: SkillStatus = Field(examples=["verified"])  # must be verified|flagged
    review_note: str | None = Field(default=None, max_length=500)


class ProjectMemberInput(BaseModel):
    """A teammate to add on a group project (besides yourself, the owner)."""

    student_id: int
    contribution: str | None = Field(
        default=None, max_length=500, examples=["Built the auth + DB layer"]
    )


class ProjectCreate(BaseModel):
    """Create a project. For a group project, set is_group=true and list members."""

    title: str = Field(min_length=1, max_length=200, examples=["Campus Attendance API"])
    description: str | None = Field(default=None, max_length=4000)
    tech_stack: str | None = Field(
        default=None, max_length=300, examples=["FastAPI, PostgreSQL, Docker"]
    )
    repo_url: str | None = Field(
        default=None, max_length=500, examples=["https://github.com/me/campus-api"]
    )
    demo_url: str | None = Field(default=None, max_length=500)
    is_group: bool = False
    # Teammates besides you. Ignored when is_group is false. You (the creator)
    # are always added automatically as a member.
    members: list[ProjectMemberInput] = Field(default_factory=list)


class ProjectMemberOut(BaseModel):
    id: int
    project_id: int
    student_id: int
    contribution: str | None
    status: SkillStatus
    ai_score: float | None
    review_note: str | None

    model_config = ConfigDict(from_attributes=True)


class ProjectOut(BaseModel):
    id: int
    owner_id: int
    title: str
    description: str | None
    tech_stack: str | None
    repo_url: str | None
    demo_url: str | None
    is_group: bool
    members: list[ProjectMemberOut]

    model_config = ConfigDict(from_attributes=True)


class ProjectMemberDecision(BaseModel):
    """A mentor's verify/flag decision on one member's contribution."""

    status: SkillStatus = Field(examples=["verified"])  # must be verified|flagged
    review_note: str | None = Field(default=None, max_length=500)


class ProjectMemberQueueOut(BaseModel):
    """A pending contribution in the mentor's review queue, with project context."""

    member_id: int
    project_id: int
    project_title: str
    repo_url: str | None
    student_id: int
    contribution: str | None
    status: SkillStatus
