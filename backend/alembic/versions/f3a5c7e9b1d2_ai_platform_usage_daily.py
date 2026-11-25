"""AI platform usage counter (Phase 10.3, owner decision D4)

Revision ID: f3a5c7e9b1d2
Revises: e1a3c5b7d9f4
Create Date: 2026-11-25

Additive. One new NON-tenant table, ``ai_platform_usage_daily`` (day,
requests, tokens, updated_at): the durable, cross-process counter behind the
platform daily AI caps. It holds two numbers per day and no tenant, user or
content data.

Postgres: ENABLE + FORCE ROW LEVEL SECURITY with policy ``ai_platform_counter``
that admits a session only when it set ``app.ai_platform_counter = 'on'`` for
its transaction (``app/services/ai_governance.py`` does). Other clients - e.g.
a Supabase Data API role - see and change nothing. Downgrade drops the table
(the counters are rebuildable; the caps then restart from zero for the day).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a5c7e9b1d2"
down_revision: Union[str, Sequence[str], None] = "e1a3c5b7d9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "ai_platform_usage_daily"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day"),
        sa.CheckConstraint("requests >= 0", name="ck_ai_platform_usage_daily_requests"),
        sa.CheckConstraint("tokens >= 0", name="ck_ai_platform_usage_daily_tokens"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY ai_platform_counter ON {_TABLE}
            USING (current_setting('app.ai_platform_counter', true) = 'on')
            WITH CHECK (current_setting('app.ai_platform_counter', true) = 'on')
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS ai_platform_counter ON {_TABLE}")
    op.drop_table(_TABLE)
