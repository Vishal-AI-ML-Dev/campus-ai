"""Operations (audit, timetable, leave, leads, feedback) ORM models."""

from datetime import date, datetime, time
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import LeaveRequestType, LeaveStatus


class Lead(Base):
    """A demo-request/contact lead captured from the public marketing site.

    These are unauthenticated, public submissions (prospects, not users yet),
    so there are no foreign keys. The admin reviews them and flips `handled`.
    """

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Pre-signup, public lead: NO tenant yet. Nullable on purpose; an admin
    # claims it into their institute later (see leads.claim_lead).
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    institute: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    handled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Lead id={self.id} email={self.email!r} handled={self.handled}>"


class Feedback(Base):
    """Anonymous in-product feedback.

    Stored without a user link so it can be submitted from anywhere (including
    logged-out surfaces). `rating` is an optional 1-5 score.
    """

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Anonymous, possibly logged-out feedback: nullable tenant (see Lead).
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Feedback id={self.id} rating={self.rating}>"


class AuditLog(Base):
    """Append-only record of a governance action (for compliance / traceability).

    Written via `record_audit()` whenever an admin changes something sensitive
    (roles, account status, structure, etc.). `actor_email` is denormalised so
    the trail survives even if the acting user is later deleted (then
    `actor_id` becomes NULL). Rows are never updated or deleted in normal use.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant the action belongs to. Denormalised from the actor at write time
    # so the trail stays tenant-scoped even after the actor is deleted/SET NULL.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot of the actor's email so the trail is readable after user deletion.
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Machine-readable key, e.g. "user.role_change", "department.create".
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # What was acted on, e.g. "user", "department", "section".
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Human-readable one-line description shown in the UI.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r}>"


class TimetableEntry(Base):
    """A single recurring weekly class slot in a section's timetable.

    The timetable is modelled as recurring weekly slots (the same schedule
    repeats every week): one row = "Section A, Monday 09:00-10:00, DBMS, taught
    by Prof X, Room 101". `day_of_week` is 0=Monday .. 6=Sunday. A section may
    not have two slots that start at the same time on the same weekday
    (enforced by a unique constraint).
    """

    __tablename__ = "timetable_entries"
    __table_args__ = (
        UniqueConstraint(
            "section_id",
            "day_of_week",
            "start_time",
            name="uq_timetable_section_day_start",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) this slot belongs to (mirrors the section's tenant).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional subject taught in this slot (e.g. 'DBMS').
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The teacher who takes this class (optional staff account).
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 0=Monday .. 6=Sunday (kept as an int so the grid sorts naturally).
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    room: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<TimetableEntry id={self.id} section={self.section_id} "
            f"day={self.day_of_week} {self.start_time}-{self.end_time}>"
        )


class LeaveRequest(Base):
    """A student's leave or on-duty (OD) request covering a date range.

    One row = one student's request for [start_date, end_date]. A student raises
    their own request (status `pending`) and a class teacher / mentor / admin
    approves or rejects it.

    Bulk OD: for fests and group events many students go together, so a staff
    coordinator can raise OD for a whole list of students in one shot. Each
    selected student still gets their OWN row (so attendance condonation stays
    per-student), the rows are linked by a shared `bulk_group_id`, and they are
    auto-approved - the staff member raising it IS the approving authority.
    """

    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    # leave (personal) vs od (official duty) - drives the attendance impact.
    request_type: Mapped[LeaveRequestType] = mapped_column(
        Enum(LeaveRequestType, name="leave_request_type"),
        nullable=False,
        index=True,
    )
    # Sub-category within the type (e.g. "medical" for leave, "fest" for OD).
    # Kept as a short string (validated in the router) so new event types can be
    # added without a database migration.
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # The student this request is FOR.
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this request belongs to - inherited from the student.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The student's section at apply time (denormalised for staff filtering).
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For OD: the event/competition name (e.g. "TechFest 2026").
    event_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Optional supporting proof (medical certificate / event invite link).
    proof_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[LeaveStatus] = mapped_column(
        Enum(LeaveStatus, name="leave_status"),
        nullable=False,
        default=LeaveStatus.pending,
        index=True,
    )
    # Who actually created the row (the student, or a staff coordinator for OD).
    applied_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The staff member who approved/rejected it.
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Groups the rows created together by one bulk-OD action (NULL for singles).
    bulk_group_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<LeaveRequest id={self.id} type={self.request_type} "
            f"student={self.student_id} status={self.status}>"
        )
