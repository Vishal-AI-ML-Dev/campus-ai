"""ai usage events: content-free AI usage ledger (+ RLS)

Revision ID: c5e9a3b7d2f4
Revises: b4d8f2a6c9e1
Create Date: 2026-11-21

AI Mock Interview, Phase 7.3. One new tenant-scoped, append-only table; no
existing table, column, policy or grant is changed.

  * ai_usage_events - one row per AI invocation (plan / turn / report step,
    circuit-open short-circuit, quota block). ``event_id`` is the invocation id
    generated before the worker call and is UNIQUE, so retried writes are
    idempotent. Holds ids, counts, classes, timings and statuses only - never
    interview content. Token counts are NULL when unknown; ``cost_micros`` is
    NULL unless an approved price table priced the call.

ENABLE + FORCE ROW LEVEL SECURITY with the standard ``tenant_isolation`` policy
(same as a1b2c3d4e5f7 / b4d8f2a6c9e1); the RLS block is Postgres-only. The
runtime role gets table and sequence privileges through the owner's ALTER
DEFAULT PRIVILEGES (scripts/app_role.template.sql), as for every earlier table.

Downgrade drops the ledger (usage history only; no interview content). Run it
only after an explicit decision; retention deletes go through
scripts/purge_ai_usage.py (manual, audited, 400 days - decision P7-D5).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5e9a3b7d2f4"
down_revision: Union[str, Sequence[str], None] = "b4d8f2a6c9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_usage_events"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feature", sa.String(length=40), nullable=False),
        sa.Column("step", sa.String(length=40), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=True),
        sa.Column("subject_id", sa.String(length=40), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tokens_unreported", sa.Integer(), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("llm_latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("price_version", sa.String(length=40), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_ai_usage_events_event_id"),
    )
    op.create_index(op.f("ix_ai_usage_events_tenant_id"), _TABLE, ["tenant_id"])
    op.create_index("ix_ai_usage_events_tenant_created", _TABLE, ["tenant_id", "created_at"])
    op.create_index(
        "ix_ai_usage_events_tenant_user_created", _TABLE, ["tenant_id", "user_id", "created_at"]
    )
    op.create_index(
        "ix_ai_usage_events_tenant_subject", _TABLE, ["tenant_id", "subject_type", "subject_id"]
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
    """Downgrade schema (drops the AI usage ledger)."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
        op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_ai_usage_events_tenant_subject", table_name=_TABLE)
    op.drop_index("ix_ai_usage_events_tenant_user_created", table_name=_TABLE)
    op.drop_index("ix_ai_usage_events_tenant_created", table_name=_TABLE)
    op.drop_index(op.f("ix_ai_usage_events_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
