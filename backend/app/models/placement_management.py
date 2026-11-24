"""Placement management v2 (Phase 9.1, migration c8e2a4f6b1d9).

Feature-flagged (``PLACEMENT_MANAGEMENT_ENABLED`` + tenant allow-list). Four
additive tables next to the existing placement core (``drives``,
``applications`` are NOT altered):

* ``student_placement_profiles`` - academic/placement data entered by the
  TPO/admin (decision P9-D2; fields limited by P9-D3).
* ``placement_drive_settings`` - 1:1 extension of a drive: lifecycle,
  application window and the extra eligibility criteria.
* ``placement_drive_departments`` - departments a drive is open to.
* ``placement_application_events`` - append-only application status history.

All are tenant-scoped and protected by Postgres RLS (``tenant_isolation``);
the app also filters every query by ``tenant_id``. Single-column FKs to
``drives``/``departments`` cannot enforce "same institute", so that rule is
checked in the application (as for ``mock_interview_company_profiles``).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mock_interview import _in_list

PROFILE_PLACEMENT_STATUS_VALUES = ("active", "opted_out", "debarred")
DRIVE_LIFECYCLE_VALUES = ("draft", "published", "closed", "completed", "cancelled")
APPLICATION_STATUS_VALUES = ("applied", "shortlisted", "selected", "rejected", "withdrawn")


class StudentPlacementProfile(Base):
    __tablename__ = "student_placement_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_student_placement_profiles_student_tenant",
        ),
        UniqueConstraint("student_id", name="uq_student_placement_profiles_student"),
        CheckConstraint(
            _in_list("placement_status", PROFILE_PLACEMENT_STATUS_VALUES),
            name="ck_student_placement_profiles_status",
        ),
        CheckConstraint(
            "graduation_year IS NULL OR (graduation_year >= 2000 AND graduation_year <= 2100)",
            name="ck_student_placement_profiles_grad_year",
        ),
        CheckConstraint(
            "(tenth_percent IS NULL OR (tenth_percent >= 0 AND tenth_percent <= 100))"
            " AND (twelfth_percent IS NULL OR (twelfth_percent >= 0 AND twelfth_percent <= 100))"
            " AND (diploma_percent IS NULL OR (diploma_percent >= 0 AND diploma_percent <= 100))",
            name="ck_student_placement_profiles_percents",
        ),
        CheckConstraint(
            "active_backlogs IS NULL OR (active_backlogs >= 0 AND active_backlogs <= 99)",
            name="ck_student_placement_profiles_backlogs",
        ),
        Index(
            "ix_student_placement_profiles_tenant_roll",
            "tenant_id",
            "roll_number",
            unique=True,
            postgresql_where=text("roll_number IS NOT NULL"),
            sqlite_where=text("roll_number IS NOT NULL"),
        ),
        Index("ix_student_placement_profiles_tenant_grad", "tenant_id", "graduation_year"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    roll_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tenth_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    twelfth_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    diploma_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_backlogs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placement_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    status_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_student_placement_profiles_updated_by"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlacementDriveSettings(Base):
    __tablename__ = "placement_drive_settings"
    __table_args__ = (
        UniqueConstraint("drive_id", name="uq_placement_drive_settings_drive"),
        CheckConstraint(
            _in_list("lifecycle", DRIVE_LIFECYCLE_VALUES),
            name="ck_placement_drive_settings_lifecycle",
        ),
        CheckConstraint(
            "applications_open_at IS NULL OR applications_close_at IS NULL"
            " OR applications_open_at < applications_close_at",
            name="ck_placement_drive_settings_window",
        ),
        CheckConstraint(
            "(min_tenth_percent IS NULL OR (min_tenth_percent >= 0 AND min_tenth_percent <= 100))"
            " AND (min_twelfth_percent IS NULL OR (min_twelfth_percent >= 0 AND min_twelfth_percent <= 100))",
            name="ck_placement_drive_settings_percents",
        ),
        CheckConstraint(
            "max_active_backlogs IS NULL OR (max_active_backlogs >= 0 AND max_active_backlogs <= 99)",
            name="ck_placement_drive_settings_backlogs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drive_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drives.id", ondelete="CASCADE", name="fk_placement_drive_settings_drive"),
        nullable=False,
    )
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    applications_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applications_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    academic_year: Mapped[str | None] = mapped_column(String(9), nullable=True, index=True)
    graduation_years_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    min_tenth_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_twelfth_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_active_backlogs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclude_placed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_placement_drive_settings_created_by"),
        nullable=True,
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_placement_drive_settings_updated_by"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlacementDriveDepartment(Base):
    __tablename__ = "placement_drive_departments"
    __table_args__ = (
        UniqueConstraint("drive_id", "department_id", name="uq_placement_drive_departments"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drive_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drives.id", ondelete="CASCADE", name="fk_placement_drive_departments_drive"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="CASCADE", name="fk_placement_drive_departments_department"),
        nullable=False,
        index=True,
    )


class PlacementApplicationEvent(Base):
    """Append-only: no route updates or deletes a row."""

    __tablename__ = "placement_application_events"
    __table_args__ = (
        CheckConstraint(
            _in_list("to_status", APPLICATION_STATUS_VALUES),
            name="ck_placement_application_events_to",
        ),
        CheckConstraint(
            "from_status IS NULL OR " + _in_list("from_status", APPLICATION_STATUS_VALUES),
            name="ck_placement_application_events_from",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("applications.id", ondelete="CASCADE", name="fk_placement_application_events_application"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_placement_application_events_actor"),
        nullable=True,
    )
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
