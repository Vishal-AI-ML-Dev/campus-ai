"""Manual, audited retention purge for the AI usage ledger (Phase 7.10, P7-D5).

Deletes ``ai_usage_events`` rows older than the retention period (400 days by
default; a shorter period is refused). Nothing else is ever touched: no
interview session, turn, report, user or audit row. It is never scheduled -
an operator runs it by hand.

    # 1. Dry run (default): per-institute counts, nothing changes
    uv run python -m scripts.purge_ai_usage
    # 2. Apply: repeat the TOTAL from the dry run as --expected-count
    uv run python -m scripts.purge_ai_usage --apply --expected-count 1234

Options: ``--days N`` (>= 400), ``--tenant-id ID`` (one institute only).

Safety:
  * dry run unless ``--apply`` AND ``--expected-count`` equal to the number of
    rows that would be deleted right now (stops a stale or mistyped command);
  * per institute, the delete and one ``audit_logs`` row
    (action ``ai_usage.purge``, actor ``system:purge_ai_usage``, counts and the
    cutoff date only) are committed in ONE transaction;
  * works under Postgres RLS: the tenant GUC is set for each institute, so the
    normal runtime role is enough.

Run it only against the database you intend to clean; it reads DATABASE_URL
like the app. Never run it against production without an approved change.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, set_current_tenant
from app.models import AIUsageEvent, AuditLog, Tenant

RETENTION_DAYS = 400
ACTION = "ai_usage.purge"
ACTOR = "system:purge_ai_usage"


@dataclass(frozen=True)
class PlanRow:
    tenant_id: int
    tenant_name: str
    rows: int


def cutoff_for(days: int, now: datetime | None = None) -> datetime:
    if days < RETENTION_DAYS:
        raise ValueError(f"--days must be at least {RETENTION_DAYS} (retention policy P7-D5)")
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


def plan(db: Session, cutoff: datetime, tenant_id: int | None = None) -> list[PlanRow]:
    """Rows older than ``cutoff`` per institute (only institutes with rows)."""
    stmt = select(Tenant.id, Tenant.name).order_by(Tenant.id)
    if tenant_id is not None:
        stmt = stmt.where(Tenant.id == tenant_id)
    tenants = db.execute(stmt).all()
    out: list[PlanRow] = []
    for tid, name in tenants:
        set_current_tenant(db, tid)
        count = db.scalar(
            select(func.count(AIUsageEvent.id)).where(
                AIUsageEvent.tenant_id == tid, AIUsageEvent.created_at < cutoff
            )
        ) or 0
        db.commit()
        if count:
            out.append(PlanRow(tid, name, int(count)))
    return out


def apply(db: Session, cutoff: datetime, rows: list[PlanRow], days: int) -> int:
    """Delete per institute, with its audit row in the same transaction."""
    deleted_total = 0
    for row in rows:
        set_current_tenant(db, row.tenant_id)
        result = db.execute(
            delete(AIUsageEvent).where(
                AIUsageEvent.tenant_id == row.tenant_id, AIUsageEvent.created_at < cutoff
            )
        )
        deleted = int(result.rowcount or 0)
        db.add(AuditLog(
            tenant_id=row.tenant_id,
            actor_id=None,
            actor_email=ACTOR,
            action=ACTION,
            target_type="ai_usage_events",
            target_id=None,
            summary=(
                f"Deleted {deleted} AI usage events created before "
                f"{cutoff.date().isoformat()} (retention {days} days)."
            ),
        ))
        db.commit()
        deleted_total += deleted
    return deleted_total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purge AI usage events past the retention period.")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--tenant-id", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument("--expected-count", type=int, default=None,
                        help="required with --apply: the TOTAL printed by the dry run")
    args = parser.parse_args(argv)
    try:
        cutoff = cutoff_for(args.days)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        rows = plan(db, cutoff, args.tenant_id)
        total = sum(r.rows for r in rows)
        print(f"AI usage events older than {cutoff.isoformat()} ({args.days} days):")
        for r in rows:
            print(f"  tenant {r.tenant_id} ({r.tenant_name}): {r.rows}")
        print(f"TOTAL: {total}")
        if not args.apply:
            print("Dry run - nothing deleted. Re-run with --apply --expected-count", total)
            return 0
        if args.expected_count is None or args.expected_count != total:
            print(f"error: --expected-count must equal the current total ({total}); nothing deleted.",
                  file=sys.stderr)
            return 3
        deleted = apply(db, cutoff, rows, args.days)
        print(f"Deleted {deleted} rows; one audit_logs row ({ACTION}) per institute.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
