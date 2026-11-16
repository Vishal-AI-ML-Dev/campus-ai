"""phase5b: recruiter companies/invites/accounts are owned by one institute

Revision ID: e7f8a9b0c1d2
Revises: c1d2e3f4a5b6
Create Date: 2026-11-15

Product decision (Phase 5B, "Option A"): each institute owns and manages its
own recruiter companies and invites; the same real company may have separate
rows at different institutes; a recruiter login belongs to the institute of
its invite. This supersedes the earlier "recruiters are platform-global" note
(c4d5e6f7a8b9 / d4e5f6a7b8c9), which the code never actually supported.

Schema
  * tenant_id NOT NULL (FK tenants ON DELETE CASCADE) on recruiters,
    recruiter_invites, recruiter_users.
  * UNIQUE (id, tenant_id) on recruiters and users, so children can reference
    (id, tenant_id) pairs: composite FKs make cross-institute links impossible
    at the database level (invites, recruiter accounts, drives, offers).
  * FORCE RLS + the standard tenant_isolation policy on the three tables.
  * recruiter_invites gets one extra, narrow, SELECT-only policy for the
    public accept-invite flow (no tenant is known before login): a row is
    visible iff it is still 'pending' AND its token equals the
    transaction-local GUC app.invite_token. Possession of the secret token is
    already the authority to accept that invite; the policy reveals nothing
    beyond that one row, and the tenant is then read FROM the row - the client
    never supplies a tenant id. No BYPASSRLS role or SECURITY DEFINER needed.

Existing rows (explicit, fail-closed - never guessed, never deleted)
  A company's institute is derived from every piece of evidence we hold:
  creator, inviters, recruiter accounts (users.tenant_id), linked drives and
  offers (tenant_id). Exactly one distinct institute -> assigned. No evidence
  or conflicting evidence -> the migration RAISES listing the company ids and
  the whole (transactional) upgrade rolls back, leaving the DB unchanged for
  an operator decision. drives/offers are FORCE-RLS'd, so FORCE is lifted for
  the table owner inside this transaction only, to read that evidence, and
  restored before commit (a rollback restores it too).

Behaviour change: drives.recruiter_id now references (recruiters.id,
tenant_id) with NO ACTION instead of SET NULL - a company with linked drives
cannot be deleted directly (no API does this); deleting the institute still
cascades everything.

Postgres-only (SQLite tests build the schema from the models).
"""

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

OWNED = ("recruiters", "recruiter_invites", "recruiter_users")

_EVIDENCE = """
    SELECT r.id AS recruiter_id,
           count(DISTINCT e.tenant_id) AS n_tenants,
           min(e.tenant_id) AS tenant_id
    FROM recruiters r
    LEFT JOIN LATERAL (
        SELECT u.tenant_id FROM users u WHERE u.id = r.created_by_id
        UNION ALL
        SELECT u.tenant_id FROM recruiter_invites i
            JOIN users u ON u.id = i.invited_by_id WHERE i.recruiter_id = r.id
        UNION ALL
        SELECT u.tenant_id FROM recruiter_users ru
            JOIN users u ON u.id = ru.user_id WHERE ru.recruiter_id = r.id
        UNION ALL
        SELECT d.tenant_id FROM drives d WHERE d.recruiter_id = r.id
        UNION ALL
        SELECT o.tenant_id FROM offers o WHERE o.recruiter_id = r.id
    ) e ON true
    GROUP BY r.id
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for t in OWNED:
        op.execute(f"ALTER TABLE {t} ADD COLUMN tenant_id integer")

    # --- Backfill existing rows from evidence (fail closed) -----------------
    op.execute("ALTER TABLE drives NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE offers NO FORCE ROW LEVEL SECURITY")
    rows = bind.exec_driver_sql(_EVIDENCE).fetchall()
    unresolved = sorted(
        (r.recruiter_id, "no evidence" if r.n_tenants == 0 else "conflicting institutes")
        for r in rows
        if r.n_tenants != 1
    )
    if unresolved:
        raise RuntimeError(
            "Phase 5B migration aborted (nothing changed): cannot determine the "
            "owning institute of recruiter companies "
            f"{unresolved}. Resolve each company's institute (or remove the "
            "stray link/row) explicitly, then re-run."
        )
    op.execute(
        f"UPDATE recruiters r SET tenant_id = ev.tenant_id "
        f"FROM ({_EVIDENCE}) ev WHERE ev.recruiter_id = r.id"
    )
    op.execute("ALTER TABLE drives FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE offers FORCE ROW LEVEL SECURITY")
    for t in ("recruiter_invites", "recruiter_users"):
        op.execute(
            f"UPDATE {t} c SET tenant_id = r.tenant_id "
            f"FROM recruiters r WHERE r.id = c.recruiter_id"
        )

    # --- Ownership columns ----------------------------------------------------
    for t in OWNED:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN tenant_id SET NOT NULL")
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT fk_{t}_tenant_id FOREIGN KEY "
            f"(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
        )
        op.execute(f"CREATE INDEX ix_{t}_tenant_id ON {t} (tenant_id)")

    # --- Same-institute integrity (composite FKs) -----------------------------
    op.execute(
        "ALTER TABLE recruiters ADD CONSTRAINT uq_recruiters_id_tenant "
        "UNIQUE (id, tenant_id)"
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT uq_users_id_tenant UNIQUE (id, tenant_id)"
    )
    _swap_fk("recruiter_invites", "recruiter_invites_recruiter_id_fkey",
             "fk_recruiter_invites_recruiter_tenant",
             "(recruiter_id, tenant_id) REFERENCES recruiters(id, tenant_id) "
             "ON DELETE CASCADE")
    _swap_fk("recruiter_users", "recruiter_users_recruiter_id_fkey",
             "fk_recruiter_users_recruiter_tenant",
             "(recruiter_id, tenant_id) REFERENCES recruiters(id, tenant_id) "
             "ON DELETE CASCADE")
    _swap_fk("recruiter_users", "recruiter_users_user_id_fkey",
             "fk_recruiter_users_user_tenant",
             "(user_id, tenant_id) REFERENCES users(id, tenant_id) ON DELETE CASCADE")
    _swap_fk("drives", "fk_drives_recruiter_id", "fk_drives_recruiter_tenant",
             "(recruiter_id, tenant_id) REFERENCES recruiters(id, tenant_id)")
    _swap_fk("offers", "offers_recruiter_id_fkey", "fk_offers_recruiter_tenant",
             "(recruiter_id, tenant_id) REFERENCES recruiters(id, tenant_id) "
             "ON DELETE CASCADE")

    # --- Row-Level Security -------------------------------------------------
    for t in OWNED:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {t}
                FOR ALL
                USING (tenant_id = app.current_tenant_id())
                WITH CHECK (tenant_id = app.current_tenant_id())
            """
        )
    op.execute(
        """
        CREATE POLICY invite_token_lookup ON recruiter_invites
            FOR SELECT
            USING (
                status = 'pending'::invite_status
                AND token = NULLIF(current_setting('app.invite_token', true), '')
            )
        """
    )


def _swap_fk(table: str, old: str, new: str, definition: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {old}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {new} FOREIGN KEY {definition}")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS invite_token_lookup ON recruiter_invites")
    for t in OWNED:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    # Restore the original single-column FKs (names + actions as before).
    _swap_fk("offers", "fk_offers_recruiter_tenant", "offers_recruiter_id_fkey",
             "(recruiter_id) REFERENCES recruiters(id) ON DELETE CASCADE")
    _swap_fk("drives", "fk_drives_recruiter_tenant", "fk_drives_recruiter_id",
             "(recruiter_id) REFERENCES recruiters(id) ON DELETE SET NULL")
    _swap_fk("recruiter_users", "fk_recruiter_users_user_tenant",
             "recruiter_users_user_id_fkey",
             "(user_id) REFERENCES users(id) ON DELETE CASCADE")
    _swap_fk("recruiter_users", "fk_recruiter_users_recruiter_tenant",
             "recruiter_users_recruiter_id_fkey",
             "(recruiter_id) REFERENCES recruiters(id) ON DELETE CASCADE")
    _swap_fk("recruiter_invites", "fk_recruiter_invites_recruiter_tenant",
             "recruiter_invites_recruiter_id_fkey",
             "(recruiter_id) REFERENCES recruiters(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE users DROP CONSTRAINT uq_users_id_tenant")
    op.execute("ALTER TABLE recruiters DROP CONSTRAINT uq_recruiters_id_tenant")
    for t in OWNED:
        op.execute(f"DROP INDEX IF EXISTS ix_{t}_tenant_id")
        op.execute(f"ALTER TABLE {t} DROP COLUMN tenant_id")
