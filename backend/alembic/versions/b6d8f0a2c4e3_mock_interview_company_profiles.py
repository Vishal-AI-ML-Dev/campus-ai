"""mock interview company profiles (+ RLS)

Revision ID: b6d8f0a2c4e3
Revises: a5c7e9b1d3f2
Create Date: 2026-11-23

AI Mock Interview, Phase 8.4 (migration B; decisions P8-D9..D11, approved
2026-11-23). One new tenant-scoped table; no existing table, column, policy
or grant is changed.

  * mock_interview_company_profiles - institute-owned company/role profiles
    written by admins/TPOs, with mandatory source records to publish. Used only
    to steer question GENERATION; evaluation never sees them (P8-D11).

``drive_id`` optionally links the open placement drive a profile was created
from (FK drives, ON DELETE SET NULL). Postgres checks that FK without RLS, so
the SAME-INSTITUTE rule is enforced in the application (spec 6.2). Profiles
are archived, never deleted, by the application.

ENABLE + FORCE ROW LEVEL SECURITY and the standard ``tenant_isolation`` policy
(Postgres-only block). Downgrade drops the table and its profiles; turning
AI_MOCK_INTERVIEW_COMPANY_ENABLED off is the normal rollback and keeps them.
Sessions keep their own frozen copy in plan_json, so they are unaffected.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b6d8f0a2c4e3"
down_revision: Union[str, Sequence[str], None] = "a5c7e9b1d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mock_interview_company_profiles"
_STATUSES = ("draft", "published", "archived")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("role_title", sa.String(length=150), nullable=False),
        sa.Column("role_template_key", sa.String(length=60), nullable=False),
        sa.Column("drive_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=True),
        sa.Column("focus_skills_json", sa.JSON(), nullable=True),
        sa.Column("competencies_json", sa.JSON(), nullable=True),
        sa.Column("rounds_json", sa.JSON(), nullable=True),
        sa.Column("sources_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["drive_id"], ["drives.id"], ondelete="SET NULL",
            name="fk_mock_interview_company_profiles_drive",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_mock_interview_company_profiles_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_mock_interview_company_profiles_updated_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_mock_interview_company_profiles_status"
        ),
    )
    op.create_index(op.f("ix_mock_interview_company_profiles_tenant_id"), _TABLE, ["tenant_id"])
    op.create_index(
        "ix_mock_interview_company_profiles_tenant_status", _TABLE, ["tenant_id", "status"]
    )

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
    """Downgrade schema (drops every company profile)."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_mock_interview_company_profiles_tenant_status", table_name=_TABLE)
    op.drop_index(op.f("ix_mock_interview_company_profiles_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
