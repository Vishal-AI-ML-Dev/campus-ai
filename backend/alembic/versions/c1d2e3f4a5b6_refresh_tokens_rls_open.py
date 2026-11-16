"""refresh_tokens: RLS enabled but open (auth runs before tenant is known)

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-07-06

Fix: a bulk RLS sweep left refresh_tokens with RLS ENABLED but ZERO policies,
which is Postgres default-deny -> every INSERT/SELECT by the NOBYPASSRLS
app_user role is rejected ("new row violates row-level security policy").

/auth/login and /auth/refresh must read & write refresh_tokens BEFORE any
tenant context (app.current_tenant_id GUC) is set, so a tenant_isolation policy
cannot work here. Rows are already protected by an unguessable token hash, so we
attach a permissive ("open") policy: RLS stays enabled for a consistent posture
and survives any future re-enable sweep, but the auth flow can operate.

Postgres-only: guarded so the SQLite test DB is skipped cleanly.
"""
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS refresh_tokens_open ON refresh_tokens")
    op.execute(
        "CREATE POLICY refresh_tokens_open ON refresh_tokens "
        "FOR ALL USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS refresh_tokens_open ON refresh_tokens")