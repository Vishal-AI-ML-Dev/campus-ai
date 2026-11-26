"""Faculty interview-preparation campaigns (Phase 9.3, migration e1a3c5b7d9f4).

A campaign asks a set of students to complete AI mock-interview practice
between ``starts_at`` and ``due_at``. It does NOT create interviews and has no
interview engine of its own: completion is derived read-only from the
student's existing ``mock_interview_sessions`` (status ``completed``,
``completed_at`` inside the window, matching ``interview_type`` when set).

Privacy (decision P9-D7, option A): faculty see only whether an assigned
student completed practice and when. Scores, transcripts, recordings, AI
feedback, reports and answers are never read by the campaign code.

Both tables are tenant-scoped with Postgres RLS (``tenant_isolation``).
``mock_interview_sessions.campaign_id`` is intentionally left untouched.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mock_interview import _in_list

CAMPAIGN_STATUS_VALUES = ("draft", "active", "closed", "cancelled", "archived")
CAMPAIGN_INTERVIEW_TYPES = ("hr", "technical", "coding")


class PlacementCampaign(Base):
    __tablename__ = "placement_campaigns"
    __table_args__ = (
        CheckConstraint(_in_list("status", CAMPAIGN_STATUS_VALUES), name="ck_placement_campaigns_status"),
        CheckConstraint(
            "interview_type IS NULL OR " + _in_list("interview_type", CAMPAIGN_INTERVIEW_TYPES),
            name="ck_placement_campaigns_type",
        ),
        CheckConstraint("starts_at < due_at", name="ck_placement_campaigns_window"),
        Index("ix_placement_campaigns_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    interview_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # Optional link to a placement drive of the same institute (checked in the app).
    drive_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("drives.id", ondelete="SET NULL", name="fk_placement_campaigns_drive"),
        nullable=True,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_placement_campaigns_created_by"),
        nullable=True,
        index=True,
    )
    created_by_role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlacementCampaignStudent(Base):
    __tablename__ = "placement_campaign_students"
    __table_args__ = (
        ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_placement_campaign_students_student_tenant",
        ),
        UniqueConstraint("campaign_id", "student_id", name="uq_placement_campaign_students"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("placement_campaigns.id", ondelete="CASCADE", name="fk_placement_campaign_students_campaign"),
        nullable=False,
        index=True,
    )
    student_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    assigned_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_placement_campaign_students_assigned_by"),
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
