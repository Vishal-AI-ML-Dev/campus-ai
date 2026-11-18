"""AI Mock Interview ORM models (Phase 2: sessions, turns, reports).

Feature-flagged module (``AI_MOCK_INTERVIEW_ENABLED``); nothing reads or writes
these tables until the Phase 3 routes exist. All three tables are tenant-scoped
and protected by Postgres RLS (``tenant_isolation``) like every other
tenant-scoped table; the app layer still filters every query by ``tenant_id``.

Defense in depth: turns and reports reference their session through the
composite key ``(session_id, tenant_id) -> mock_interview_sessions(id,
tenant_id)``. Postgres checks foreign keys without RLS, so a single-column FK
would let a row stamped for tenant A point at tenant B's session; the composite
key makes that impossible at the database level. Sessions likewise reference
their student through ``(student_id, tenant_id) -> users(id, tenant_id)``
(``uq_users_id_tenant``), so a session can only belong to a student of the same
institute. ``resume_id`` stays a single-column FK (resumes has no such key);
Phase 3 must check the resume's owner and tenant in the app layer.

Allowed values live in the ``*_VALUES`` tuples below and are enforced by CHECK
constraints (dialect-neutral strings, no Postgres ENUM types), so they work on
SQLite and are cheap to extend with a later additive migration.
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
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

INTERVIEW_TYPE_VALUES = ("hr", "technical", "coding", "company", "full")
MODE_VALUES = ("text", "voice", "video")
STATUS_VALUES = (
    "setup",
    "in_progress",
    "evaluating",
    "completed",
    "abandoned",
    "failed",
)
DIFFICULTY_VALUES = ("easy", "medium", "hard")
LANGUAGE_VALUES = ("en", "hi", "hinglish")
ANSWER_SOURCE_VALUES = ("typed", "stt")
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 120


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """SQL text for ``column IN ('a', 'b')`` with standard SQL quoting."""
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


class MockInterviewSession(Base):
    """One mock-interview attempt by one student.

    ``campaign_id`` is a plain nullable integer for now; the campaigns table
    and its foreign key arrive with Phase 9. No audio or video is stored, only
    the text transcript in ``mock_interview_turns``.
    """

    __tablename__ = "mock_interview_sessions"
    __table_args__ = (
        # Target of the composite (session_id, tenant_id) FKs below.
        UniqueConstraint("id", "tenant_id", name="uq_mock_interview_sessions_id_tenant"),
        ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_sessions_student_tenant",
        ),
        CheckConstraint(
            _in_list("interview_type", INTERVIEW_TYPE_VALUES),
            name="ck_mock_interview_sessions_interview_type",
        ),
        CheckConstraint(_in_list("mode", MODE_VALUES), name="ck_mock_interview_sessions_mode"),
        CheckConstraint(
            _in_list("status", STATUS_VALUES), name="ck_mock_interview_sessions_status"
        ),
        CheckConstraint(
            _in_list("difficulty", DIFFICULTY_VALUES),
            name="ck_mock_interview_sessions_difficulty",
        ),
        CheckConstraint(
            _in_list("language", LANGUAGE_VALUES),
            name="ck_mock_interview_sessions_language",
        ),
        CheckConstraint(
            f"duration_minutes BETWEEN {MIN_DURATION_MINUTES} AND {MAX_DURATION_MINUTES}",
            name="ck_mock_interview_sessions_duration",
        ),
        # Student history list: newest first, per student.
        Index("ix_mock_interview_sessions_student_created", "student_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Indexed by ix_mock_interview_sessions_student_created (leading column).
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    interview_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="text")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="setup")
    target_role: Mapped[str | None] = mapped_column(String(150), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # The resume version the interview was grounded on; kept if it's deleted.
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Interview plan from the AI worker (topics, question budget, ...).
    plan_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # Faculty campaign (Phase 9); FK added when that table exists.
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    turns: Mapped[list["MockInterviewTurn"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MockInterviewTurn.seq",
    )
    report: Mapped["MockInterviewReport | None"] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MockInterviewSession id={self.id} student={self.student_id} "
            f"type={self.interview_type} status={self.status}>"
        )


class MockInterviewTurn(Base):
    """One question (and, once given, its answer) inside a session, in order."""

    __tablename__ = "mock_interview_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_turns_session_tenant",
        ),
        UniqueConstraint("session_id", "seq", name="uq_mock_interview_turns_session_seq"),
        CheckConstraint("seq >= 1", name="ck_mock_interview_turns_seq_positive"),
        CheckConstraint(
            "answer_source IS NULL OR " + _in_list("answer_source", ANSWER_SOURCE_VALUES),
            name="ck_mock_interview_turns_answer_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Indexed by uq_mock_interview_turns_session_seq (leading column).
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    is_follow_up: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Per-answer evaluation from the AI worker (scores + evidence quotes).
    turn_eval_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["MockInterviewSession"] = relationship(back_populates="turns")

    def __repr__(self) -> str:
        return f"<MockInterviewTurn id={self.id} session={self.session_id} seq={self.seq}>"


class MockInterviewReport(Base):
    """Final evaluation of a session (at most one per session)."""

    __tablename__ = "mock_interview_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_reports_session_tenant",
        ),
        UniqueConstraint("session_id", name="uq_mock_interview_reports_session"),
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_mock_interview_reports_overall_score",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 0-100.
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    category_scores_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    strengths_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    weaknesses_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    recommendations_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    rubric_version: Mapped[str] = mapped_column(String(20), nullable=False)
    # Which LLM produced the report.
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["MockInterviewSession"] = relationship(back_populates="report")

    def __repr__(self) -> str:
        return (
            f"<MockInterviewReport id={self.id} session={self.session_id} "
            f"score={self.overall_score}>"
        )
