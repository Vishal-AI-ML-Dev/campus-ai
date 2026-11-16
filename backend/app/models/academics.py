"""Academics (departments, sections, subjects, results, attendance) ORM models."""

from datetime import date, datetime
from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import AttendanceStatus


class Department(Base):
    """A top-level academic unit, e.g. 'Computer Science' (code 'CSE').

    Name + code are unique *within an institute* (tenant), so two institutes
    can each have their own 'Computer Science' / 'CSE'.
    """

    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_department_tenant_name"),
        UniqueConstraint("tenant_id", "code", name="uq_department_tenant_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sections: Mapped[list["Section"]] = relationship(
        back_populates="department", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Department id={self.id} code={self.code!r}>"


class Section(Base):
    """A class/section within a department, e.g. name 'A', year 3."""

    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("department_id", "name", name="uq_section_dept_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    department: Mapped["Department"] = relationship(back_populates="sections")

    def __repr__(self) -> str:
        return f"<Section id={self.id} name={self.name!r} dept={self.department_id}>"


class AttendanceRecord(Base):
    """One attendance entry: a student's status in a section on a date.

    A student can have at most one record per section per date (enforced by a
    unique constraint), so re-marking simply updates the existing row.
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "section_id",
            "date",
            name="uq_attendance_student_section_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="attendance_status"), nullable=False
    )
    marked_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Attendance student={self.student_id} section={self.section_id} "
            f"date={self.date} status={self.status.value}>"
        )


class FaceEnrollment(Base):
    """A student's enrolled reference face (one row per student).

    The 512-dim face embedding itself lives in Qdrant, keyed by the student's
    id; this row records WHO is enrolled, who enrolled them, the detection
    confidence, and when - so staff rosters can show enrollment status without
    querying the vector store per student. Deleting the user cascades here.
    """

    __tablename__ = "face_enrollments"

    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enrolled_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    det_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    enrolled_at: Mapped[datetime] = mapped_column(
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
            f"<FaceEnrollment student={self.student_id} "
            f"det_score={self.det_score}>"
        )


class Subject(Base):
    """A subject/course within a department, tied to a semester.

    Subject code is unique *within* a department, so two departments can reuse
    the same code.
    """

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_subject_dept_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    semester: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Subject id={self.id} code={self.code!r} sem={self.semester}>"


class Result(Base):
    """A student's result for one subject: marks + a 0-10 grade point.

    `grade_point` is derived from the percentage by the academics module, so
    SGPA/CGPA can be computed as a credit-weighted average.
    One result per student per subject (unique constraint).
    """

    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", name="uq_result_student_subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tenant (institute) this result belongs to. Mirrors the student's tenant,
    # stored on the row so every query can filter by it directly (future RLS).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marks_obtained: Mapped[float] = mapped_column(Float, nullable=False)
    max_marks: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    grade_point: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Result student={self.student_id} subject={self.subject_id} "
            f"gp={self.grade_point}>"
        )
