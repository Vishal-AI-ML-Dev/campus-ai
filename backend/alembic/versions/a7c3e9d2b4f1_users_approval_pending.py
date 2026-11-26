"""users.approval_pending: keep pending self-registrations distinct from disabled

Revision ID: a7c3e9d2b4f1
Revises: f0a1b2c3d4e5
Create Date: 2026-11-17

Why: public student self-registration (POST /auth/register) now creates an
INACTIVE account that an admin of the same institute must approve. With only
``is_active`` a pending request and an account an admin disabled look the
same, so an "approve" action could silently re-enable a disabled student.

What upgrade does
  ALTER TABLE users ADD COLUMN approval_pending BOOLEAN NOT NULL
      DEFAULT false
  Additive only. Every existing row gets ``false`` (not pending): existing
  accounts keep their current is_active value and behave exactly as before.
  On PostgreSQL 11+ a constant default is a catalog-only change (no table
  rewrite). No RLS policy, grant or index changes: ``users`` has no RLS, and
  the runtime role's existing table grants cover the new column.

Downgrade
  Drops the column. Accounts still pending at that time stay inactive
  (is_active=false) and would then look like disabled accounts; an admin
  could enable them with the old status toggle. Nothing else is lost.
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c3e9d2b4f1"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "approval_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "approval_pending")
