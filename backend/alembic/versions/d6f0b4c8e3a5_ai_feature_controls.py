"""ai feature controls: institute controls for AI features (+ RLS)

Revision ID: d6f0b4c8e3a5
Revises: c5e9a3b7d2f4
Create Date: 2026-11-21

AI Mock Interview, Phase 7.6. Additive only:

  * ai_feature_controls - at most one row per (tenant_id, feature). A missing
    row means platform defaults. Rows can only NARROW what the environment
    flags allow (pause, disable voice/video, lower limits); the app enforces
    that, the database just stores it.
  * index ix_mock_interview_sessions_tenant_created (tenant_id, created_at) on
    the existing mock_interview_sessions table, for the monthly institute
    quota count. No column or constraint of that table changes.

ENABLE + FORCE ROW LEVEL SECURITY with the standard ``tenant_isolation`` policy
on the new table; Postgres-only block.

Downgrade drops the controls (institutes fall back to platform defaults) and
the extra index.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6f0b4c8e3a5"
down_revision: Union[str, Sequence[str], None] = "c5e9a3b7d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_feature_controls"
_SESSIONS_INDEX = "ix_mock_interview_sessions_tenant_created"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("voice_allowed", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("video_allowed", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("limits_json", sa.JSON(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "feature", name="uq_ai_feature_controls_tenant_feature"),
    )
    op.create_index(op.f("ix_ai_feature_controls_tenant_id"), _TABLE, ["tenant_id"])
    op.create_index(_SESSIONS_INDEX, "mock_interview_sessions", ["tenant_id", "created_at"])

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
    """Downgrade schema (drops institute AI controls and the extra index)."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index(_SESSIONS_INDEX, table_name="mock_interview_sessions")
    op.drop_index(op.f("ix_ai_feature_controls_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
