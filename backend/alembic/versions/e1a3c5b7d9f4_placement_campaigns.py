"""placement campaigns (faculty interview preparation) + RLS

Revision ID: e1a3c5b7d9f4
Revises: d9f1a3c5e7b2
Create Date: 2026-11-24

Phase 9.3 (spec docs/mock-interview/specs/phase-9-placement-institute-management.md
section 13). Two new tenant-scoped tables; no existing table is changed
(``mock_interview_sessions.campaign_id`` stays a plain integer - completion is
derived read-only from completed sessions inside the campaign window).

  * placement_campaigns          - title, window, optional interview type /
                                   role / drive link, status
  * placement_campaign_students  - assigned students (composite FK keeps the
                                   student in the same institute)

ENABLE + FORCE ROW LEVEL SECURITY and the standard ``tenant_isolation`` policy
(Postgres-only block). Downgrade drops both tables and their rows; turning
PLACEMENT_MANAGEMENT_ENABLED off is the normal rollback and keeps the data.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1a3c5b7d9f4"
down_revision: Union[str, Sequence[str], None] = "d9f1a3c5e7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CAMPAIGNS = "placement_campaigns"
_STUDENTS = "placement_campaign_students"
_RLS_TABLES = (_CAMPAIGNS, _STUDENTS)
_STATUSES = ("draft", "active", "closed", "cancelled", "archived")
_TYPES = ("hr", "technical", "coding")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _CAMPAIGNS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("interview_type", sa.String(length=20), nullable=True),
        sa.Column("target_role", sa.String(length=150), nullable=True),
        sa.Column("drive_id", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["drive_id"], ["drives.id"], ondelete="SET NULL", name="fk_placement_campaigns_drive"),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL", name="fk_placement_campaigns_created_by"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="ck_placement_campaigns_status"),
        sa.CheckConstraint(
            "interview_type IS NULL OR " + _in_list("interview_type", _TYPES), name="ck_placement_campaigns_type"
        ),
        sa.CheckConstraint("starts_at < due_at", name="ck_placement_campaigns_window"),
    )
    op.create_index(op.f("ix_placement_campaigns_tenant_id"), _CAMPAIGNS, ["tenant_id"])
    op.create_index(op.f("ix_placement_campaigns_drive_id"), _CAMPAIGNS, ["drive_id"])
    op.create_index(op.f("ix_placement_campaigns_created_by_id"), _CAMPAIGNS, ["created_by_id"])
    op.create_index("ix_placement_campaigns_tenant_status", _CAMPAIGNS, ["tenant_id", "status"])

    op.create_table(
        _STUDENTS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_id", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], [f"{_CAMPAIGNS}.id"], ondelete="CASCADE", name="fk_placement_campaign_students_campaign"
        ),
        sa.ForeignKeyConstraint(
            ["student_id", "tenant_id"], ["users.id", "users.tenant_id"], ondelete="CASCADE",
            name="fk_placement_campaign_students_student_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_id"], ["users.id"], ondelete="SET NULL", name="fk_placement_campaign_students_assigned_by"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "student_id", name="uq_placement_campaign_students"),
    )
    op.create_index(op.f("ix_placement_campaign_students_tenant_id"), _STUDENTS, ["tenant_id"])
    op.create_index(op.f("ix_placement_campaign_students_campaign_id"), _STUDENTS, ["campaign_id"])
    op.create_index(op.f("ix_placement_campaign_students_student_id"), _STUDENTS, ["student_id"])

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
    """Downgrade schema (drops both tables and their rows)."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_placement_campaign_students_student_id"), table_name=_STUDENTS)
    op.drop_index(op.f("ix_placement_campaign_students_campaign_id"), table_name=_STUDENTS)
    op.drop_index(op.f("ix_placement_campaign_students_tenant_id"), table_name=_STUDENTS)
    op.drop_table(_STUDENTS)
    op.drop_index("ix_placement_campaigns_tenant_status", table_name=_CAMPAIGNS)
    op.drop_index(op.f("ix_placement_campaigns_created_by_id"), table_name=_CAMPAIGNS)
    op.drop_index(op.f("ix_placement_campaigns_drive_id"), table_name=_CAMPAIGNS)
    op.drop_index(op.f("ix_placement_campaigns_tenant_id"), table_name=_CAMPAIGNS)
    op.drop_table(_CAMPAIGNS)
