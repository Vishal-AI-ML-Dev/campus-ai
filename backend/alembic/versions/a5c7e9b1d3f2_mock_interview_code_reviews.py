"""mock interview coding feedback: code reviews (+ RLS)

Revision ID: a5c7e9b1d3f2
Revises: e7a1c4d9b2f6
Create Date: 2026-11-23

AI Mock Interview, Phase 8.3 (decision O1 = option A, approved 2026-11-23).
One new tenant-scoped table; no existing table, column, policy or grant is
changed. In particular ``mock_interview_reports`` (and its NOT NULL
``overall_score``) is untouched: review-only coding feedback never uses it.

  * mock_interview_code_reviews - the coding feedback for one problem (attempt)
    of a coding session: rubric version, AI estimates (code_quality, reasoning,
    testing; 0-5, NULL = not assessed), evidence and the review status.

Review-only rows (rubric ``coding-v1-unverified``) can never carry a score:
a CHECK constraint forces ``verification = 'not_verified'`` and
``problem_score IS NULL`` for them (decision P8-D7 rules 6-8). The nullable
``problem_score`` column is the future path for verified ``coding-v1``
results (8.6); nothing writes it before the sandbox gates pass.

References its session through (session_id, tenant_id) and its attempt through
(attempt_id, tenant_id), like migration A, so no row can point at another
institute's data. ENABLE + FORCE ROW LEVEL SECURITY and the standard
``tenant_isolation`` policy (Postgres-only block).

Downgrade drops the table and every stored coding feedback. Turning the
feature flag off is the normal rollback and keeps the data.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a5c7e9b1d3f2"
down_revision: Union[str, Sequence[str], None] = "e7a1c4d9b2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mock_interview_code_reviews"
_RUBRICS = ("coding-v1-unverified", "coding-v1")
_STATUSES = ("ai_estimated", "not_submitted")
_VERIFICATION = ("not_verified", "verified")
_LANGUAGES = ("python313", "cpp17", "java21", "c11")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def _score_check(column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} IS NULL OR ({column} >= 0 AND {column} <= 5)",
        name=f"ck_{_TABLE}_{column}",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("rubric_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("verification", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("code_quality_score", sa.Integer(), nullable=True),
        sa.Column("reasoning_score", sa.Integer(), nullable=True),
        sa.Column("testing_score", sa.Integer(), nullable=True),
        sa.Column("problem_score", sa.Float(), nullable=True),
        sa.Column("estimates_json", sa.JSON(), nullable=True),
        sa.Column("feedback_json", sa.JSON(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["mock_interview_sessions.id", "mock_interview_sessions.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_reviews_session_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["mock_interview_code_attempts.id", "mock_interview_code_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_mock_interview_code_reviews_attempt_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", name="uq_mock_interview_code_reviews_attempt"),
        sa.CheckConstraint(
            _in_list("rubric_version", _RUBRICS), name="ck_mock_interview_code_reviews_rubric"
        ),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_mock_interview_code_reviews_status"
        ),
        sa.CheckConstraint(
            _in_list("verification", _VERIFICATION),
            name="ck_mock_interview_code_reviews_verification",
        ),
        sa.CheckConstraint(
            "language IS NULL OR " + _in_list("language", _LANGUAGES),
            name="ck_mock_interview_code_reviews_language",
        ),
        # Review-only feedback is never verified and never scored (P8-D7).
        sa.CheckConstraint(
            "rubric_version <> 'coding-v1-unverified' OR "
            "(verification = 'not_verified' AND problem_score IS NULL)",
            name="ck_mock_interview_code_reviews_unverified_unscored",
        ),
        sa.CheckConstraint(
            "problem_score IS NULL OR (verification = 'verified' "
            "AND problem_score >= 0 AND problem_score <= 100)",
            name="ck_mock_interview_code_reviews_problem_score",
        ),
        _score_check("code_quality_score"),
        _score_check("reasoning_score"),
        _score_check("testing_score"),
    )
    op.create_index(op.f("ix_mock_interview_code_reviews_tenant_id"), _TABLE, ["tenant_id"])
    op.create_index(op.f("ix_mock_interview_code_reviews_session_id"), _TABLE, ["session_id"])

    # --- Postgres Row-Level Security (defense-in-depth) ---------------------
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {_TABLE}
            FOR ALL
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id())
        """
    )


def downgrade() -> None:
    """Downgrade schema (drops every stored coding feedback)."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_mock_interview_code_reviews_session_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_mock_interview_code_reviews_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
