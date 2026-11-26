"""mock interview coding: attempts + submissions (+ RLS)

Revision ID: e7a1c4d9b2f6
Revises: d6f0b4c8e3a5
Create Date: 2026-11-23

AI Mock Interview, Phase 8.1 (migration A). Two new tenant-scoped tables; no
existing table, column, policy or grant is changed.

  * mock_interview_code_attempts    - one row per problem in a coding session
  * mock_interview_code_submissions - code submitted for an attempt

Both reference their session through the composite key (session_id, tenant_id)
-> mock_interview_sessions(id, tenant_id); submissions also reference their
attempt through (attempt_id, tenant_id) -> mock_interview_code_attempts(id,
tenant_id). Postgres checks foreign keys without RLS, so this stops a row
stamped for one institute from pointing at another institute's data.

No hidden test data, expected output or reference solution is stored in either
table (spec section 3.2). Allowed values are CHECK constraints on strings
(dialect-neutral, works on SQLite).

ENABLE + FORCE ROW LEVEL SECURITY and the standard ``tenant_isolation`` policy
(same as a1b2c3d4e5f7); Postgres-only block. The runtime role gets table and
sequence privileges through the owner's ALTER DEFAULT PRIVILEGES, as for every
earlier table.

Downgrade drops both tables and every row in them (students' coding drafts and
submissions). Run it only after an explicit decision and a backup; turning the
feature flag off is the normal rollback and keeps the data.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7a1c4d9b2f6"
down_revision: Union[str, Sequence[str], None] = "d6f0b4c8e3a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ATTEMPTS = "mock_interview_code_attempts"
_SUBMISSIONS = "mock_interview_code_submissions"
_RLS_TABLES = (_ATTEMPTS, _SUBMISSIONS)

_LANGUAGES = ("python313", "cpp17", "java21", "c11")
_ATTEMPT_STATUSES = ("pending", "working", "explaining", "auto_submitted", "closed", "abandoned")
_KINDS = ("visible", "custom", "graded", "auto")
_SUB_STATUSES = ("queued", "running", "completed", "error", "not_executed", "rejected")
_VERDICTS = (
    "accepted",
    "wrong_answer",
    "time_limit",
    "memory_limit",
    "runtime_error",
    "compile_error",
    "output_limit",
    "sandbox_error",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _ATTEMPTS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("problem_key", sa.String(length=80), nullable=False),
        sa.Column("problem_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("draft_source", sa.Text(), nullable=True),
        sa.Column("draft_rev", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hints_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("runs_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("submissions_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_flight_submission_id", sa.Integer(), nullable=True),
        sa.Column("best_submission_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_attempts_session_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_mock_interview_code_attempts_id_tenant"),
        sa.UniqueConstraint(
            "session_id", "seq", name="uq_mock_interview_code_attempts_session_seq"
        ),
        sa.CheckConstraint("seq >= 1", name="ck_mock_interview_code_attempts_seq_positive"),
        sa.CheckConstraint(
            _in_list("status", _ATTEMPT_STATUSES), name="ck_mock_interview_code_attempts_status"
        ),
        sa.CheckConstraint(
            "language IS NULL OR " + _in_list("language", _LANGUAGES),
            name="ck_mock_interview_code_attempts_language",
        ),
        sa.CheckConstraint(
            "draft_rev >= 0 AND hints_used >= 0 AND runs_used >= 0 AND submissions_used >= 0",
            name="ck_mock_interview_code_attempts_counters",
        ),
    )
    op.create_index(op.f("ix_mock_interview_code_attempts_tenant_id"), _ATTEMPTS, ["tenant_id"])

    op.create_table(
        _SUBMISSIONS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("custom_input", sa.Text(), nullable=True),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("group_results_json", sa.JSON(), nullable=True),
        sa.Column("first_failure_json", sa.JSON(), nullable=True),
        sa.Column("visible_results_json", sa.JSON(), nullable=True),
        sa.Column("compile_message", sa.Text(), nullable=True),
        sa.Column("max_time_ms", sa.Integer(), nullable=True),
        sa.Column("max_memory_kb", sa.Integer(), nullable=True),
        sa.Column("pack_digest", sa.String(length=80), nullable=True),
        sa.Column("runner_version", sa.String(length=40), nullable=True),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        _created_at(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_submissions_session_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            [f"{_ATTEMPTS}.id", f"{_ATTEMPTS}.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_submissions_attempt_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_mock_interview_code_submissions_execution"),
        sa.UniqueConstraint(
            "attempt_id",
            "idempotency_key",
            name="uq_mock_interview_code_submissions_attempt_idem",
        ),
        sa.CheckConstraint(_in_list("kind", _KINDS), name="ck_mock_interview_code_submissions_kind"),
        sa.CheckConstraint(
            _in_list("language", _LANGUAGES), name="ck_mock_interview_code_submissions_language"
        ),
        sa.CheckConstraint(
            _in_list("status", _SUB_STATUSES), name="ck_mock_interview_code_submissions_status"
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR " + _in_list("verdict", _VERDICTS),
            name="ck_mock_interview_code_submissions_verdict",
        ),
    )
    op.create_index(
        op.f("ix_mock_interview_code_submissions_tenant_id"), _SUBMISSIONS, ["tenant_id"]
    )
    op.create_index(
        op.f("ix_mock_interview_code_submissions_session_id"), _SUBMISSIONS, ["session_id"]
    )
    op.create_index(
        "ix_mock_interview_code_submissions_tenant_created", _SUBMISSIONS, ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_mock_interview_code_submissions_attempt_created",
        _SUBMISSIONS,
        ["attempt_id", "created_at"],
    )
    op.create_index(
        "ix_mock_interview_code_submissions_status_created", _SUBMISSIONS, ["status", "created_at"]
    )

    # --- Postgres Row-Level Security (defense-in-depth) ---------------------
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                FOR ALL
                USING (tenant_id = app.current_tenant_id())
                WITH CHECK (tenant_id = app.current_tenant_id())
            """
        )


def downgrade() -> None:
    """Downgrade schema (drops coding attempts and submissions, with their data)."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for name in (
        "ix_mock_interview_code_submissions_status_created",
        "ix_mock_interview_code_submissions_attempt_created",
        "ix_mock_interview_code_submissions_tenant_created",
        op.f("ix_mock_interview_code_submissions_session_id"),
        op.f("ix_mock_interview_code_submissions_tenant_id"),
    ):
        op.drop_index(name, table_name=_SUBMISSIONS)
    op.drop_table(_SUBMISSIONS)
    op.drop_index(op.f("ix_mock_interview_code_attempts_tenant_id"), table_name=_ATTEMPTS)
    op.drop_table(_ATTEMPTS)
