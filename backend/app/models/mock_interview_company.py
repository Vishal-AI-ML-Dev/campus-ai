"""AI Mock Interview company profiles (Phase 8.4, migration b6d8f0a2c4e3).

Feature-flagged (``AI_MOCK_INTERVIEW_COMPANY_ENABLED``). Institute-owned
profiles written by admins/TPOs (decision P8-D9); tenant-scoped and protected
by Postgres RLS (``tenant_isolation``); the app also filters every query by
``tenant_id``. ``drive_id`` is a single-column FK (SET NULL), so the
same-institute rule is checked in the application.

Content (summary, JD, competencies) is institute-authored and untrusted: it is
shown as "From your institute", never as verified fact, and reaches the AI
only as delimited data for question generation (P8-D10, P8-D11).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mock_interview import _in_list

PROFILE_STATUS_VALUES = ("draft", "published", "archived")


class MockInterviewCompanyProfile(Base):
    __tablename__ = "mock_interview_company_profiles"
    __table_args__ = (
        CheckConstraint(
            _in_list("status", PROFILE_STATUS_VALUES),
            name="ck_mock_interview_company_profiles_status",
        ),
        Index("ix_mock_interview_company_profiles_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    role_title: Mapped[str] = mapped_column(String(150), nullable=False)
    role_template_key: Mapped[str] = mapped_column(String(60), nullable=False)
    drive_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("drives.id", ondelete="SET NULL", name="fk_mock_interview_company_profiles_drive"),
        nullable=True,
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    focus_skills_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    competencies_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # Reserved for full simulations (8.7); not used in Phase 8.4.
    rounds_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    sources_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_mock_interview_company_profiles_created_by"),
        nullable=True,
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_mock_interview_company_profiles_updated_by"),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<MockInterviewCompanyProfile id={self.id} tenant={self.tenant_id} status={self.status}>"
