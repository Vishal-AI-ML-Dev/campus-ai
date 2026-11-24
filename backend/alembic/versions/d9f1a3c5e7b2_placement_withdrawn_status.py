"""placement: application status 'withdrawn' (Phase 9.2, decision P9-D8)

Revision ID: d9f1a3c5e7b2
Revises: c8e2a4f6b1d9
Create Date: 2026-11-24

Additive only:

  * Postgres: ``ALTER TYPE application_status ADD VALUE 'withdrawn'``
    (outside a transaction block; ``IF NOT EXISTS`` so a re-upgrade is safe).
    SQLite stores the enum as a plain string (no CHECK), nothing to do.
  * ``placement_application_events``: the two CHECK constraints on
    ``from_status`` / ``to_status`` are widened to accept 'withdrawn'.

No row is changed. Only the student withdraw route of the flagged
placement-management API can set the new value; legacy routes reject it.

Downgrade restores the narrow CHECKs. It REFUSES (raises) while any
application or history event uses 'withdrawn', so data is never silently
rewritten. Postgres cannot drop an enum value; the value stays (unused) after
a downgrade, which is harmless.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9f1a3c5e7b2"
down_revision: Union[str, Sequence[str], None] = "c8e2a4f6b1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EVENTS = "placement_application_events"
_OLD = ("applied", "shortlisted", "selected", "rejected")
_NEW = _OLD + ("withdrawn",)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


def _replace_checks(values: tuple[str, ...]) -> None:
    with op.batch_alter_table(_EVENTS) as batch:
        batch.drop_constraint("ck_placement_application_events_to", type_="check")
        batch.drop_constraint("ck_placement_application_events_from", type_="check")
        batch.create_check_constraint("ck_placement_application_events_to", _in_list("to_status", values))
        batch.create_check_constraint(
            "ck_placement_application_events_from", "from_status IS NULL OR " + _in_list("from_status", values)
        )


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        # ADD VALUE must commit before the value can be used; run it on its own.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE application_status ADD VALUE IF NOT EXISTS 'withdrawn'")
    _replace_checks(_NEW)


def downgrade() -> None:
    """Downgrade schema (refuses while 'withdrawn' data exists)."""
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text(
            f"SELECT (SELECT COUNT(*) FROM applications WHERE CAST(status AS VARCHAR) = 'withdrawn')"
            f" + (SELECT COUNT(*) FROM {_EVENTS} WHERE to_status = 'withdrawn' OR from_status = 'withdrawn')"
        )
    ).scalar()
    if in_use:
        raise RuntimeError(
            "Cannot downgrade d9f1a3c5e7b2: withdrawn applications or history events exist. "
            "Turn PLACEMENT_MANAGEMENT_ENABLED off instead (the normal rollback keeps the data)."
        )
    _replace_checks(_OLD)
