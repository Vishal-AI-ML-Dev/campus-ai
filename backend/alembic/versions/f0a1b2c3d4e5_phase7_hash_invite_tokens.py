"""phase7: store invite tokens as SHA-256 digests, never in plain text

Revision ID: f0a1b2c3d4e5
Revises: 5e1f0a7b9c3d
Create Date: 2026-11-16

Why: tenant_invites.token and recruiter_invites.token held the raw one-time
secret. Anyone who could read those rows (DB leak, backup, log of a query,
an operator) could redeem a still-pending invite and create an account in
that institute with the pre-assigned role (e.g. admin/tpo). From now on only
SHA-256(token) is stored; the raw token exists only in the one-time create
response (and the link the inviter sends).

What upgrade does (single transaction on Postgres - all or nothing)
  1. RENAME COLUMN token -> token_hash on both tables (same VARCHAR(64)).
     Renamed deliberately: code that still expects `token` fails loudly
     instead of silently comparing raw tokens against digests.
  2. Converts EVERY existing row in place:
         token_hash = encode(sha256(convert_to(token_hash, 'UTF8')), 'hex')
     This is byte-for-byte the digest app.core.security.hash_invite_token
     computes, so EXISTING PENDING INVITES KEEP WORKING - the link the user
     already received is accepted unchanged. Accepted / expired / revoked
     rows are hashed too (the raw value has no further use). No row is
     deleted and no status changes. Uniqueness is preserved (distinct
     inputs -> distinct SHA-256 outputs).
     Rows that are ALREADY a 64-char lowercase hex digest are skipped. The
     app only ever issued secrets.token_urlsafe(32) tokens (43 chars of
     base64url, never 64 hex), so this only matters after a downgrade:
     re-upgrading does not double-hash, and invites issued before the
     downgrade work again.
     recruiter_invites is under FORCE ROW LEVEL SECURITY, so the owner role
     would see zero rows; FORCE is lifted only for this UPDATE and restored
     in the same transaction.
  3. Renames the unique indexes ix_*_token -> ix_*_token_hash.
  4. Adds CHECK (token_hash ~ '^[0-9a-f]{64}$') on both tables so a raw
     token can never be written again (by old code or by hand).
  5. Recreates the invite_token_lookup RLS policy on recruiter_invites to
     match token_hash against the GUC app.invite_token_hash (the app now
     sends the digest; the raw token never reaches the database).

Downgrade (operator decision only - documented, NOT lossless)
  Reverses 5 -> 1 structurally (policy back to `token` /
  app.invite_token, CHECKs dropped, indexes + columns renamed back). The
  stored values STAY digests: a raw token cannot be recovered from its
  hash. Consequence: invites that are still PENDING at downgrade time can
  no longer be accepted by the old code ("Invalid or already-used invite");
  admins / TPOs must revoke and re-issue them. Nothing is deleted. Prefer
  rolling FORWARD (fix code) over downgrading.

Recovery if upgrade fails midway: Postgres DDL is transactional and
alembic/env.py runs the whole upgrade inside one begin_transaction(), so a
failure rolls the revision back and the DB stays at 5e1f0a7b9c3d with the
original tokens. Take a pg_dump before applying anyway (see release plan).
"""

import hashlib
import re

from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "5e1f0a7b9c3d"
branch_labels = None
depends_on = None

TABLES = ("tenant_invites", "recruiter_invites")
_HEX64 = "^[0-9a-f]{64}$"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_pg():
        _upgrade_generic()
        return

    for t in TABLES:
        op.execute(f"ALTER TABLE {t} RENAME COLUMN token TO token_hash")

    # recruiter_invites: FORCE RLS would hide every row from the owner.
    op.execute("ALTER TABLE recruiter_invites NO FORCE ROW LEVEL SECURITY")
    for t in TABLES:
        op.execute(
            f"UPDATE {t} SET token_hash = "
            "encode(sha256(convert_to(token_hash, 'UTF8')), 'hex') "
            f"WHERE token_hash !~ '{_HEX64}'"
        )
    op.execute("ALTER TABLE recruiter_invites FORCE ROW LEVEL SECURITY")

    for t in TABLES:
        op.execute(f"ALTER INDEX ix_{t}_token RENAME TO ix_{t}_token_hash")
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT ck_{t}_token_hash_sha256 "
            f"CHECK (token_hash ~ '{_HEX64}')"
        )

    op.execute("DROP POLICY invite_token_lookup ON recruiter_invites")
    op.execute(
        """
        CREATE POLICY invite_token_lookup ON recruiter_invites
            FOR SELECT
            USING (
                status = 'pending'::invite_status
                AND token_hash = NULLIF(current_setting('app.invite_token_hash', true), '')
            )
        """
    )


def downgrade() -> None:
    if not _is_pg():
        _downgrade_generic()
        return

    op.execute("DROP POLICY invite_token_lookup ON recruiter_invites")
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT ck_{t}_token_hash_sha256")
        op.execute(f"ALTER INDEX ix_{t}_token_hash RENAME TO ix_{t}_token")
        # Values stay SHA-256 digests (irreversible) - see module docstring.
        op.execute(f"ALTER TABLE {t} RENAME COLUMN token_hash TO token")
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


# --- non-Postgres (SQLite dev/test DBs built by Alembic) ---------------------
def _upgrade_generic() -> None:
    bind = op.get_bind()
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.alter_column("token", new_column_name="token_hash")
        rows = bind.execute(sa.text(f"SELECT id, token_hash FROM {t}")).all()
        for rid, raw in rows:
            if re.fullmatch("[0-9a-f]{64}", raw or ""):
                continue
            bind.execute(
                sa.text(f"UPDATE {t} SET token_hash = :h WHERE id = :i"),
                {"h": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "i": rid},
            )


def _downgrade_generic() -> None:
    for t in TABLES:
        with op.batch_alter_table(t) as b:
            b.alter_column("token_hash", new_column_name="token")
