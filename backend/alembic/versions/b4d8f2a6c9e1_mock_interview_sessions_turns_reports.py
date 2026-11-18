"""mock interview: sessions, turns, reports (+ RLS)

Revision ID: b4d8f2a6c9e1
Revises: a7c3e9d2b4f1
Create Date: 2026-11-18

AI Mock Interview, Phase 2. Three new tenant-scoped tables; no existing table,
column, policy or grant is changed.

  * mock_interview_sessions - one interview attempt per row
  * mock_interview_turns    - ordered question/answer pairs of a session
  * mock_interview_reports  - final evaluation, at most one per session

Turns and reports reference their session through the composite key
(session_id, tenant_id) -> mock_interview_sessions(id, tenant_id). Postgres
checks foreign keys without RLS, so this stops a row stamped for one tenant
from pointing at another tenant's session. Sessions likewise reference their
student through (student_id, tenant_id) -> users(id, tenant_id), using the
existing uq_users_id_tenant constraint (users itself is not changed).

Each table gets ENABLE + FORCE ROW LEVEL SECURITY and the standard
``tenant_isolation`` policy (same as a1b2c3d4e5f7). The RLS block is
Postgres-only; on SQLite it is skipped. The runtime role gets table and
sequence privileges through the owner's ALTER DEFAULT PRIVILEGES (see
scripts/app_role.template.sql), as for every earlier table.

Downgrade drops the three tables and every row in them (interview history).
Run it only after an explicit decision and a backup; turning the feature flag
off is the normal rollback and keeps the data.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4d8f2a6c9e1"
down_revision: Union[str, Sequence[str], None] = "a7c3e9d2b4f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = (
    "mock_interview_sessions",
    "mock_interview_turns",
    "mock_interview_reports",
)


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
        "mock_interview_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("interview_type", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("target_role", sa.String(length=150), nullable=True),
        sa.Column("company_name", sa.String(length=150), nullable=True),
        sa.Column("difficulty", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["student_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_sessions_student_tenant",
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_mock_interview_sessions_id_tenant"),
        sa.CheckConstraint(
            "interview_type IN ('hr', 'technical', 'coding', 'company', 'full')",
            name="ck_mock_interview_sessions_interview_type",
        ),
        sa.CheckConstraint(
            "mode IN ('text', 'voice', 'video')",
            name="ck_mock_interview_sessions_mode",
        ),
        sa.CheckConstraint(
            "status IN ('setup', 'in_progress', 'evaluating', 'completed', "
            "'abandoned', 'failed')",
            name="ck_mock_interview_sessions_status",
        ),
        sa.CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_mock_interview_sessions_difficulty",
        ),
        sa.CheckConstraint(
            "language IN ('en', 'hi', 'hinglish')",
            name="ck_mock_interview_sessions_language",
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 5 AND 120",
            name="ck_mock_interview_sessions_duration",
        ),
    )
    op.create_index(
        op.f("ix_mock_interview_sessions_tenant_id"),
        "mock_interview_sessions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_mock_interview_sessions_resume_id"),
        "mock_interview_sessions",
        ["resume_id"],
    )
    op.create_index(
        op.f("ix_mock_interview_sessions_campaign_id"),
        "mock_interview_sessions",
        ["campaign_id"],
    )
    op.create_index(
        "ix_mock_interview_sessions_student_created",
        "mock_interview_sessions",
        ["student_id", "created_at"],
    )

    op.create_table(
        "mock_interview_turns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "is_follow_up",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_source", sa.String(length=10), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turn_eval_json", sa.JSON(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_turns_session_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_mock_interview_turns_session_seq"),
        sa.CheckConstraint("seq >= 1", name="ck_mock_interview_turns_seq_positive"),
        sa.CheckConstraint(
            "answer_source IS NULL OR answer_source IN ('typed', 'stt')",
            name="ck_mock_interview_turns_answer_source",
        ),
    )
    op.create_index(
        op.f("ix_mock_interview_turns_tenant_id"),
        "mock_interview_turns",
        ["tenant_id"],
    )

    op.create_table(
        "mock_interview_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("category_scores_json", sa.JSON(), nullable=False),
        sa.Column("strengths_json", sa.JSON(), nullable=False),
        sa.Column("weaknesses_json", sa.JSON(), nullable=False),
        sa.Column("recommendations_json", sa.JSON(), nullable=False),
        sa.Column("rubric_version", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_reports_session_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_mock_interview_reports_session"),
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_mock_interview_reports_overall_score",
        ),
    )
    op.create_index(
        op.f("ix_mock_interview_reports_tenant_id"),
        "mock_interview_reports",
        ["tenant_id"],
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
    """Downgrade schema (drops all mock-interview data)."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        op.f("ix_mock_interview_reports_tenant_id"), table_name="mock_interview_reports"
    )
    op.drop_table("mock_interview_reports")

    op.drop_index(
        op.f("ix_mock_interview_turns_tenant_id"), table_name="mock_interview_turns"
    )
    op.drop_table("mock_interview_turns")

    op.drop_index(
        "ix_mock_interview_sessions_student_created",
        table_name="mock_interview_sessions",
    )
    op.drop_index(
        op.f("ix_mock_interview_sessions_campaign_id"),
        table_name="mock_interview_sessions",
    )
    op.drop_index(
        op.f("ix_mock_interview_sessions_resume_id"),
        table_name="mock_interview_sessions",
    )
    op.drop_index(
        op.f("ix_mock_interview_sessions_tenant_id"),
        table_name="mock_interview_sessions",
    )
    op.drop_table("mock_interview_sessions")
