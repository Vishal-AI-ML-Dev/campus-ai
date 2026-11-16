"""Student career profile (skills, projects, ECA, internships, resume) ORM models."""

from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import SkillStatus, ECACategory, InternshipType


class Skill(Base):
    """A student-claimed skill plus its proof and verification state.

    This is the core of the 'verified data moat': a skill only counts once a
    mentor marks it `verified`. `ai_score` is left NULL here and filled later
    by the AI worker (proof analysis).
    A student cannot claim the same skill name twice (unique constraint).
    """

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("student_id", "name", name="uq_skill_student_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this skill belongs to. Mirrors the student's tenant,
    # but is stored on the row so every query can filter by it directly (and so
    # a future Postgres RLS policy can enforce isolation at the DB layer too).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SkillStatus] = mapped_column(
        Enum(SkillStatus, name="skill_status"),
        default=SkillStatus.pending,
        nullable=False,
        index=True,
    )
    # Filled later by the AI worker (0-100 confidence that the proof is real).
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Skill student={self.student_id} name={self.name!r} "
            f"status={self.status.value}>"
        )


class Project(Base):
    """A project (individual or group) with proof links.

    The project record itself is just metadata + evidence; the actual
    verification happens per contributor in `project_members`, so a group
    project credits only the members a mentor has individually verified.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this project belongs to - the owner's institute. Every
    # member must belong to the same institute, so the whole project (and all
    # its member rows) is scoped to a single tenant.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comma-separated tech list, e.g. 'FastAPI, PostgreSQL, Docker'.
    tech_stack: Mapped[str | None] = mapped_column(String(300), nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    demo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} title={self.title!r} group={self.is_group}>"


class ProjectMember(Base):
    """One contributor on a project, verified individually (anti-freeloader).

    Reuses the shared `skill_status` enum type for its verification lifecycle
    (the type is created by the skills migration; `create_type=False` here so
    this table does not try to recreate it).
    A student appears at most once per project (unique constraint).
    """

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "student_id", name="uq_project_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) of the parent project. Mirrored onto each member row so
    # the review queue and staff reads can filter by tenant directly.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # What this member actually did on the project (their claim to verify).
    contribution: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SkillStatus] = mapped_column(
        Enum(SkillStatus, name="skill_status", create_type=False),
        default=SkillStatus.pending,
        nullable=False,
        index=True,
    )
    # Filled later by the AI worker (0-100 confidence that the proof is real).
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    project: Mapped["Project"] = relationship(back_populates="members")

    def __repr__(self) -> str:
        return (
            f"<ProjectMember project={self.project_id} student={self.student_id} "
            f"status={self.status.value}>"
        )


class ExtraCurricular(Base):
    """A student-claimed extra-curricular activity + proof + verification state.

    Same 'verified data moat' lifecycle as skills (reuses `SkillStatus`): a
    claim counts toward the resume/profile only once a teacher/TPO marks it
    `verified`. Categorised (sports/cultural/...) so recruiters see a
    well-rounded, *verified* picture beyond academics.
    A student cannot log the same activity title twice (unique constraint).
    """

    __tablename__ = "extracurriculars"
    __table_args__ = (
        UniqueConstraint("student_id", "title", name="uq_eca_student_title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Institute this activity belongs to (inherited from the student).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[ECACategory] = mapped_column(
        Enum(ECACategory, name="eca_category"),
        default=ECACategory.other,
        nullable=False,
        index=True,
    )
    organization: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SkillStatus] = mapped_column(
        Enum(SkillStatus, name="skill_status"),
        default=SkillStatus.pending,
        nullable=False,
        index=True,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<ExtraCurricular student={self.student_id} title={self.title!r} "
            f"status={self.status.value}>"
        )


class Internship(Base):
    """A student-claimed internship / OJT / training + proof + verification.

    Reuses the same verified-data moat as skills/ECA (`SkillStatus`): an entry
    counts toward the resume/recruiter profile only once a teacher/TPO marks it
    `verified`. Captures real work experience (org, role, dates, mode) so the
    profile shows verifiable industry exposure beyond academics. A student
    cannot log the same (organization, role) pair twice (unique constraint).
    """

    __tablename__ = "internships"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "organization",
            "role_title",
            name="uq_internship_student_org_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this internship belongs to. Mirrors the student's
    # tenant; stored on the row so every query can filter by it directly.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization: Mapped[str] = mapped_column(String(200), nullable=False)
    role_title: Mapped[str] = mapped_column(String(150), nullable=False)
    internship_type: Mapped[InternshipType] = mapped_column(
        Enum(InternshipType, name="internship_type"),
        default=InternshipType.internship,
        nullable=False,
        index=True,
    )
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills_used: Mapped[str | None] = mapped_column(String(300), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_ongoing: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    certificate_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    status: Mapped[SkillStatus] = mapped_column(
        Enum(SkillStatus, name="skill_status"),
        default=SkillStatus.pending,
        nullable=False,
        index=True,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Internship student={self.student_id} org={self.organization!r} "
            f"role={self.role_title!r} status={self.status.value}>"
        )


class Resume(Base):
    """A saved, versioned snapshot of a student's AI-generated resume.

    Every call to POST /resume/generate stores one immutable version here, so a
    student keeps a history, can reopen/compare past drafts, rename them, and
    mark ONE as their primary copy. The Markdown is built only from VERIFIED
    data (same moat as the live generator), so stored versions never contain
    unproven claims.

    At most one version per student is `is_primary` at a time (enforced in the
    router when toggling, not by a DB constraint).
    """

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this resume version belongs to. Mirrors the student's
    # tenant; stored on the row so every query can filter by it directly.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A friendly label (auto-generated on save, editable later).
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    # The role this draft was tailored for, if any (for context in the list).
    target_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # The full Markdown resume content (the actual saved document).
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    # Which LLM provider produced it (groq | gemini | ...), for transparency.
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # The student's chosen "current" resume (at most one true per student).
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Phase 5S: storage object path of ONE resume file the owner uploaded
    # ({tenant}/resume/{student_id}/...). Downloads are authorized from this
    # row (owner only). NULL = no file attached. Unique when set.
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_resumes_file_path_unique",
            "file_path",
            unique=True,
            postgresql_where=text("file_path IS NOT NULL"),
            sqlite_where=text("file_path IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Resume id={self.id} student={self.student_id} "
            f"title={self.title!r} primary={self.is_primary}>"
        )
