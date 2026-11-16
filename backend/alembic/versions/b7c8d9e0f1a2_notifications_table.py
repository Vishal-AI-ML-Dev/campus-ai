"""notifications table (in-app notification feed + RLS)

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f7
Create Date: 2026-07-06

Batch 19a: in-app notifications. One tenant-scoped table ``notifications`` (one
row per recipient+event). Goes under FORCE ROW LEVEL SECURITY with the standard
``tenant_isolation`` policy (reads ``app.current_tenant_id()``, set per-request
by db.set_current_tenant), so one institute can never see another's
notifications. The RLS block is Postgres-only (guarded on the dialect); the
SQLite test DB skips it and app-layer WHERE tenant_id filtering still applies.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RLS_TABLES = ("notifications",)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notifications_tenant_id"), "notifications", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_notifications_user_id"), "notifications", ["user_id"]
    )
    op.create_index(op.f("ix_notifications_type"), "notifications", ["type"])
    op.create_index(
        op.f("ix_notifications_is_read"), "notifications", ["is_read"]
    )
    op.create_index(
        op.f("ix_notifications_created_at"), "notifications", ["created_at"]
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "is_read"],
    )

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
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index(
        op.f("ix_notifications_created_at"), table_name="notifications"
    )
    op.drop_index(op.f("ix_notifications_is_read"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(
        op.f("ix_notifications_tenant_id"), table_name="notifications"
    )
    op.drop_table("notifications")
