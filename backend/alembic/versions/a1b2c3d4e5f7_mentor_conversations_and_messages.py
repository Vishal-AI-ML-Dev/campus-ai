"""mentor conversations + messages (durable mentor memory + RLS)

Revision ID: a1b2c3d4e5f7
Revises: f9a1b2c3d4e5
Create Date: 2026-07-05 16:50:00.000000

Batch 10b: the AI Career Mentor gets durable memory. Two tenant-scoped tables:
  * mentor_conversations - one saved chat thread per student
  * mentor_messages      - the user/assistant turns inside a thread

Both go under FORCE ROW LEVEL SECURITY with a ``tenant_isolation`` policy
(reads ``app.current_tenant_id()``, set per-request by db.set_current_tenant),
so one institute can never see another's mentor chats - matching every other
tenant-scoped table. The RLS block is Postgres-only (guarded on the dialect),
so the SQLite test DB skips it cleanly and app-layer ``WHERE tenant_id``
filtering still applies there.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "f9a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_TABLES = ("mentor_conversations", "mentor_messages")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mentor_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
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
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mentor_conversations_tenant_id"),
        "mentor_conversations",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_mentor_conversations_student_id"),
        "mentor_conversations",
        ["student_id"],
    )

    op.create_table(
        "mentor_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["mentor_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mentor_messages_tenant_id"), "mentor_messages", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_mentor_messages_conversation_id"),
        "mentor_messages",
        ["conversation_id"],
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
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        for table in _RLS_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        op.f("ix_mentor_messages_conversation_id"), table_name="mentor_messages"
    )
    op.drop_index(
        op.f("ix_mentor_messages_tenant_id"), table_name="mentor_messages"
    )
    op.drop_table("mentor_messages")
    op.drop_index(
        op.f("ix_mentor_conversations_student_id"),
        table_name="mentor_conversations",
    )
    op.drop_index(
        op.f("ix_mentor_conversations_tenant_id"),
        table_name="mentor_conversations",
    )
    op.drop_table("mentor_conversations")
