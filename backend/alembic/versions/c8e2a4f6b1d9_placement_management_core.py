"""placement management core v2 (+ RLS)

Revision ID: c8e2a4f6b1d9
Revises: b6d8f0a2c4e3
Create Date: 2026-11-24

Phase 9.1 (migration P9-A; spec docs/mock-interview/specs/
phase-9-placement-institute-management.md section 5.1). Four new
tenant-scoped tables; no existing table, column, policy or grant is changed.

  * student_placement_profiles   - TPO/admin-entered academic/placement data
  * placement_drive_settings     - 1:1 drive extension: lifecycle, window,
                                   extra eligibility criteria
  * placement_drive_departments  - departments a drive is open to
  * placement_application_events - append-only application status history

Single-column FKs to drives/departments/applications cannot enforce the
same-institute rule (Postgres checks FKs without RLS), so the application
enforces it. ENABLE + FORCE ROW LEVEL SECURITY and the standard
``tenant_isolation`` policy (Postgres-only block). Turning
PLACEMENT_MANAGEMENT_ENABLED off is the normal rollback and keeps the data;
downgrade drops the four tables and their rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e2a4f6b1d9"
down_revision: Union[str, Sequence[str], None] = "b6d8f0a2c4e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILES = "student_placement_profiles"
_SETTINGS = "placement_drive_settings"
_DEPTS = "placement_drive_departments"
_EVENTS = "placement_application_events"
_RLS_TABLES = (_PROFILES, _SETTINGS, _DEPTS, _EVENTS)

_PROFILE_STATUSES = ("active", "opted_out", "debarred")
_LIFECYCLES = ("draft", "published", "closed", "completed", "cancelled")
_APP_STATUSES = ("applied", "shortlisted", "selected", "rejected")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _PROFILES,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("roll_number", sa.String(length=40), nullable=True),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("tenth_percent", sa.Float(), nullable=True),
        sa.Column("twelfth_percent", sa.Float(), nullable=True),
        sa.Column("diploma_percent", sa.Float(), nullable=True),
        sa.Column("active_backlogs", sa.Integer(), nullable=True),
        sa.Column("placement_status", sa.String(length=20), nullable=False),
        sa.Column("status_note", sa.String(length=300), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["student_id", "tenant_id"], ["users.id", "users.tenant_id"], ondelete="CASCADE",
            name="fk_student_placement_profiles_student_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_student_placement_profiles_updated_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", name="uq_student_placement_profiles_student"),
        sa.CheckConstraint(
            _in_list("placement_status", _PROFILE_STATUSES), name="ck_student_placement_profiles_status"
        ),
        sa.CheckConstraint(
            "graduation_year IS NULL OR (graduation_year >= 2000 AND graduation_year <= 2100)",
            name="ck_student_placement_profiles_grad_year",
        ),
        sa.CheckConstraint(
            "(tenth_percent IS NULL OR (tenth_percent >= 0 AND tenth_percent <= 100))"
            " AND (twelfth_percent IS NULL OR (twelfth_percent >= 0 AND twelfth_percent <= 100))"
            " AND (diploma_percent IS NULL OR (diploma_percent >= 0 AND diploma_percent <= 100))",
            name="ck_student_placement_profiles_percents",
        ),
        sa.CheckConstraint(
            "active_backlogs IS NULL OR (active_backlogs >= 0 AND active_backlogs <= 99)",
            name="ck_student_placement_profiles_backlogs",
        ),
    )
    op.create_index(op.f("ix_student_placement_profiles_tenant_id"), _PROFILES, ["tenant_id"])
    op.create_index(
        "ix_student_placement_profiles_tenant_roll", _PROFILES, ["tenant_id", "roll_number"], unique=True,
        postgresql_where=sa.text("roll_number IS NOT NULL"),
        sqlite_where=sa.text("roll_number IS NOT NULL"),
    )
    op.create_index(
        "ix_student_placement_profiles_tenant_grad", _PROFILES, ["tenant_id", "graduation_year"]
    )

    op.create_table(
        _SETTINGS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("drive_id", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(length=20), nullable=False),
        sa.Column("applications_open_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applications_close_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("academic_year", sa.String(length=9), nullable=True),
        sa.Column("graduation_years_json", sa.JSON(), nullable=True),
        sa.Column("min_tenth_percent", sa.Float(), nullable=True),
        sa.Column("min_twelfth_percent", sa.Float(), nullable=True),
        sa.Column("max_active_backlogs", sa.Integer(), nullable=True),
        sa.Column("exclude_placed", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["drive_id"], ["drives.id"], ondelete="CASCADE", name="fk_placement_drive_settings_drive"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_placement_drive_settings_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_placement_drive_settings_updated_by",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_id", name="uq_placement_drive_settings_drive"),
        sa.CheckConstraint(_in_list("lifecycle", _LIFECYCLES), name="ck_placement_drive_settings_lifecycle"),
        sa.CheckConstraint(
            "applications_open_at IS NULL OR applications_close_at IS NULL"
            " OR applications_open_at < applications_close_at",
            name="ck_placement_drive_settings_window",
        ),
        sa.CheckConstraint(
            "(min_tenth_percent IS NULL OR (min_tenth_percent >= 0 AND min_tenth_percent <= 100))"
            " AND (min_twelfth_percent IS NULL OR (min_twelfth_percent >= 0 AND min_twelfth_percent <= 100))",
            name="ck_placement_drive_settings_percents",
        ),
        sa.CheckConstraint(
            "max_active_backlogs IS NULL OR (max_active_backlogs >= 0 AND max_active_backlogs <= 99)",
            name="ck_placement_drive_settings_backlogs",
        ),
    )
    op.create_index(op.f("ix_placement_drive_settings_tenant_id"), _SETTINGS, ["tenant_id"])
    op.create_index(op.f("ix_placement_drive_settings_academic_year"), _SETTINGS, ["academic_year"])

    op.create_table(
        _DEPTS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("drive_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["drive_id"], ["drives.id"], ondelete="CASCADE", name="fk_placement_drive_departments_drive"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE",
            name="fk_placement_drive_departments_department",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_id", "department_id", name="uq_placement_drive_departments"),
    )
    op.create_index(op.f("ix_placement_drive_departments_tenant_id"), _DEPTS, ["tenant_id"])
    op.create_index(op.f("ix_placement_drive_departments_drive_id"), _DEPTS, ["drive_id"])
    op.create_index(op.f("ix_placement_drive_departments_department_id"), _DEPTS, ["department_id"])

    op.create_table(
        _EVENTS,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="CASCADE",
            name="fk_placement_application_events_application",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], ondelete="SET NULL", name="fk_placement_application_events_actor"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("to_status", _APP_STATUSES), name="ck_placement_application_events_to"),
        sa.CheckConstraint(
            "from_status IS NULL OR " + _in_list("from_status", _APP_STATUSES),
            name="ck_placement_application_events_from",
        ),
    )
    op.create_index(op.f("ix_placement_application_events_tenant_id"), _EVENTS, ["tenant_id"])
    op.create_index(op.f("ix_placement_application_events_application_id"), _EVENTS, ["application_id"])

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
    """Downgrade schema (drops the four tables and their rows)."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(op.f("ix_placement_application_events_application_id"), table_name=_EVENTS)
    op.drop_index(op.f("ix_placement_application_events_tenant_id"), table_name=_EVENTS)
    op.drop_table(_EVENTS)
    op.drop_index(op.f("ix_placement_drive_departments_department_id"), table_name=_DEPTS)
    op.drop_index(op.f("ix_placement_drive_departments_drive_id"), table_name=_DEPTS)
    op.drop_index(op.f("ix_placement_drive_departments_tenant_id"), table_name=_DEPTS)
    op.drop_table(_DEPTS)
    op.drop_index(op.f("ix_placement_drive_settings_academic_year"), table_name=_SETTINGS)
    op.drop_index(op.f("ix_placement_drive_settings_tenant_id"), table_name=_SETTINGS)
    op.drop_table(_SETTINGS)
    op.drop_index("ix_student_placement_profiles_tenant_grad", table_name=_PROFILES)
    op.drop_index("ix_student_placement_profiles_tenant_roll", table_name=_PROFILES)
    op.drop_index(op.f("ix_student_placement_profiles_tenant_id"), table_name=_PROFILES)
    op.drop_table(_PROFILES)
