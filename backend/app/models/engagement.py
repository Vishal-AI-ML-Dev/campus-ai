"""Engagement (announcements, calendar, assignments, materials, doubts) ORM models."""

from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import SubmissionStatus, MaterialCategory, DoubtStatus


class Announcement(Base):
    """An institute broadcast posted by an admin to everyone or one role.

    `audience` is one of "all", "student", "teacher", "tpo": a reader sees an
    announcement when it targets everyone or their own role. Stored as a short
    string (not an Enum) so new audiences can be added without a DB migration.
    The author link is SET NULL so deleting an admin keeps historical posts.
    """

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) this announcement was posted in. Readers only ever see
    # announcements from their own institute.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # "all" | "student" | "teacher" | "tpo" - who should see this announcement.
    audience: Mapped[str] = mapped_column(
        String(20), default="all", nullable=False, index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Announcement id={self.id} title={self.title!r} "
            f"audience={self.audience}>"
        )


class CalendarEvent(Base):
    """An academic-calendar entry posted by an admin.

    `category` is one of "holiday", "exam", "event", "deadline" (drives the
    colour/label in the UI). Like announcements, `audience` is a short string
    ("all" | "student" | "teacher" | "tpo") so a reader sees entries meant for
    everyone or their own role. An optional `end_date` supports multi-day
    entries (exam weeks, holidays). Both stored as plain strings (no Enum) so
    new categories/audiences need no DB migration.
    """

    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) this calendar entry belongs to (mirrors the creator's).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "holiday" | "exam" | "event" | "deadline"
    category: Mapped[str] = mapped_column(
        String(20), default="event", nullable=False
    )
    # "all" | "student" | "teacher" | "tpo" - who should see this entry.
    audience: Mapped[str] = mapped_column(
        String(20), default="all", nullable=False, index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<CalendarEvent id={self.id} title={self.title!r} "
            f"date={self.event_date}>"
        )


class Assignment(Base):
    """An assignment a teacher posts for a whole section (optionally tied to a
    subject). Students in that section submit work against it."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) of the posting teacher. Mirrored here so reads can
    # filter by institute directly.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    max_marks: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Assignment id={self.id} title={self.title!r} "
            f"section={self.section_id}>"
        )


class Submission(Base):
    """A student's submission to an assignment (one per student per assignment).

    Re-submitting updates the same row and resets it to 'submitted', clearing
    any earlier grade so the teacher reviews the new work.
    """

    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id",
            "student_id",
            name="uq_submission_assignment_student",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"),
        default=SubmissionStatus.submitted,
        nullable=False,
    )
    marks: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Submission id={self.id} assignment={self.assignment_id} "
            f"student={self.student_id} status={self.status.value}>"
        )


class Material(Base):
    """A study material/resource shared with a section in the Study Hub.

    Teachers (and admins) upload reference material for a section, optionally
    tied to a subject. The payload is link-based (an external URL such as a
    Drive/PDF/YouTube link) and/or inline notes text - no file storage yet
    (signed-URL uploads can be added later). Students of that section browse
    these read-only.
    """

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) of the uploading staff. Mirrored here so reads can
    # filter by institute directly.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inline notes body and/or an external resource link; at least one is
    # required (validated at the API layer).
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[MaterialCategory] = mapped_column(
        Enum(MaterialCategory, name="material_category"),
        default=MaterialCategory.notes,
        nullable=False,
        index=True,
    )
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Material id={self.id} title={self.title!r} "
            f"section={self.section_id} category={self.category.value}>"
        )


class Doubt(Base):
    """A question posted to a section's doubt forum.

    Posted by a student (only in their own section) or by staff (any section),
    and optionally tied to a subject. Becomes `resolved` when one of its
    answers is accepted.
    """

    __tablename__ = "doubts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DoubtStatus] = mapped_column(
        Enum(DoubtStatus, name="doubt_status"),
        default=DoubtStatus.open,
        nullable=False,
        index=True,
    )
    asked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    answers: Mapped[list["DoubtAnswer"]] = relationship(
        back_populates="doubt", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Doubt id={self.id} section={self.section_id} "
            f"status={self.status.value}>"
        )


class DoubtAnswer(Base):
    """An answer to a doubt. Can be upvoted, and one can be accepted as solution."""

    __tablename__ = "doubt_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doubt_id: Mapped[int] = mapped_column(
        ForeignKey("doubts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    answered_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_accepted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    doubt: Mapped["Doubt"] = relationship(back_populates="answers")
    votes: Mapped[list["AnswerVote"]] = relationship(
        back_populates="answer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<DoubtAnswer id={self.id} doubt={self.doubt_id} "
            f"accepted={self.is_accepted}>"
        )


class AnswerVote(Base):
    """One upvote on an answer by one user (at most one per answer+user)."""

    __tablename__ = "answer_votes"
    __table_args__ = (
        UniqueConstraint("answer_id", "user_id", name="uq_answer_vote_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answer_id: Mapped[int] = mapped_column(
        ForeignKey("doubt_answers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    answer: Mapped["DoubtAnswer"] = relationship(back_populates="votes")

    def __repr__(self) -> str:
        return f"<AnswerVote answer={self.answer_id} user={self.user_id}>"
