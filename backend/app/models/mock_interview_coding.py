"""AI Mock Interview coding round ORM models (Phase 8.1, migration e7a1c4d9b2f6).

Feature-flagged (``AI_MOCK_INTERVIEW_CODING_ENABLED`` + a fail-closed coding
allow-list). Two tenant-scoped tables, protected by Postgres RLS
(``tenant_isolation``) like every other tenant table; the app layer still
filters every query by ``tenant_id``:

  * ``mock_interview_code_attempts``    - one row per problem in a coding session
  * ``mock_interview_code_submissions`` - code the student submitted for a problem

Defense in depth (same pattern as turns/reports): attempts reference their
session through ``(session_id, tenant_id) -> mock_interview_sessions(id,
tenant_id)``; submissions reference both the session and the attempt through
composite keys, so no row can point at another institute's data even though
Postgres checks foreign keys without RLS.

No hidden test data, expected output or reference solution is ever stored here
(spec section 3.2): hidden-test results arrive later (8.6) only as per-group
pass counts. In Batch 1 no code is executed at all: graded submissions are
stored with ``status = "not_executed"`` (review-only mode, decision P8-D8).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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

CODE_LANGUAGE_VALUES = ("python313", "cpp17", "java21", "c11")
ATTEMPT_STATUS_VALUES = (
    "pending",
    "working",
    "explaining",
    "auto_submitted",
    "closed",
    "abandoned",
)
SUBMISSION_KIND_VALUES = ("visible", "custom", "graded", "auto")
SUBMISSION_STATUS_VALUES = (
    "queued",
    "running",
    "completed",
    "error",
    "not_executed",
    "rejected",
)
VERDICT_VALUES = (
    "accepted",
    "wrong_answer",
    "time_limit",
    "memory_limit",
    "runtime_error",
    "compile_error",
    "output_limit",
    "sandbox_error",
)


class MockInterviewCodeAttempt(Base):
    """One assigned problem inside a coding session, with its draft and counters."""

    __tablename__ = "mock_interview_code_attempts"
    __table_args__ = (
        # Target of the composite (attempt_id, tenant_id) FK on submissions.
        UniqueConstraint("id", "tenant_id", name="uq_mock_interview_code_attempts_id_tenant"),
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_attempts_session_tenant",
        ),
        UniqueConstraint("session_id", "seq", name="uq_mock_interview_code_attempts_session_seq"),
        CheckConstraint("seq >= 1", name="ck_mock_interview_code_attempts_seq_positive"),
        CheckConstraint(
            _in_list("status", ATTEMPT_STATUS_VALUES),
            name="ck_mock_interview_code_attempts_status",
        ),
        CheckConstraint(
            "language IS NULL OR " + _in_list("language", CODE_LANGUAGE_VALUES),
            name="ck_mock_interview_code_attempts_language",
        ),
        CheckConstraint(
            "draft_rev >= 0 AND hints_used >= 0 AND runs_used >= 0 AND submissions_used >= 0",
            name="ck_mock_interview_code_attempts_counters",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Indexed by uq_mock_interview_code_attempts_session_seq (leading column).
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    problem_key: Mapped[str] = mapped_column(String(80), nullable=False)
    problem_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    draft_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    runs_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    submissions_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # One submission in flight per attempt (compare-and-set; used from 8.6).
    in_flight_submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set by the backend from objective results only (decision P8-D14, 8.6).
    best_submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MockInterviewCodeAttempt id={self.id} session={self.session_id} "
            f"seq={self.seq} status={self.status}>"
        )


class MockInterviewCodeSubmission(Base):
    """Code a student submitted for one attempt. Content-bearing (source code)."""

    __tablename__ = "mock_interview_code_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_submissions_session_tenant",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["mock_interview_code_attempts.id", "mock_interview_code_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_submissions_attempt_tenant",
        ),
        UniqueConstraint("execution_id", name="uq_mock_interview_code_submissions_execution"),
        UniqueConstraint(
            "attempt_id",
            "idempotency_key",
            name="uq_mock_interview_code_submissions_attempt_idem",
        ),
        CheckConstraint(
            _in_list("kind", SUBMISSION_KIND_VALUES),
            name="ck_mock_interview_code_submissions_kind",
        ),
        CheckConstraint(
            _in_list("language", CODE_LANGUAGE_VALUES),
            name="ck_mock_interview_code_submissions_language",
        ),
        CheckConstraint(
            _in_list("status", SUBMISSION_STATUS_VALUES),
            name="ck_mock_interview_code_submissions_status",
        ),
        CheckConstraint(
            "verdict IS NULL OR " + _in_list("verdict", VERDICT_VALUES),
            name="ck_mock_interview_code_submissions_verdict",
        ),
        Index("ix_mock_interview_code_submissions_tenant_created", "tenant_id", "created_at"),
        Index("ix_mock_interview_code_submissions_attempt_created", "attempt_id", "created_at"),
        Index("ix_mock_interview_code_submissions_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    attempt_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    # Nulled for visible/custom runs once the report exists (decision P8-D17).
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    custom_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Per-group pass counts only, e.g. {"basic": [6, 6], "edge": [3, 5]} (8.6).
    group_results_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    first_failure_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    visible_results_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    compile_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_memory_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    runner_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MockInterviewCodeSubmission id={self.id} attempt={self.attempt_id} "
            f"kind={self.kind} status={self.status}>"
        )
