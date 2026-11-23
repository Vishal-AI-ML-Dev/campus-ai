"""Audit log: append-only trail of governance actions.

`record_audit()` is a small helper that other routers call after a sensitive
change (role/status change, structure edits, etc.). It writes one `audit_logs`
row describing who did what. Reads are admin-only via `GET /audit`.

The helper only *adds* the row to the caller's session (it does not commit), so
it must be called just before the caller's own `db.commit()`. That way the
audit row and the change it records are saved atomically in one transaction:
if the caller rolls back, the audit entry is discarded too, keeping the trail
consistent with what actually happened.

Mounted under the `/audit` prefix by `main.py`.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import AuditLog, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import AuditLogOut
from app.core.security import require_roles

router = APIRouter(prefix="/audit", tags=["audit"])

# Only admins may read the governance trail.
admin_only = require_roles(UserRole.admin)


def record_audit(
    db: Session,
    actor: User | None,
    action: str,
    summary: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
) -> None:
    """Add one audit row to the current transaction (the caller commits).

    Call this *before* the caller's `db.commit()` so the audit row and the
    change it describes are persisted together.
    """
    db.add(
        AuditLog(
            actor_id=actor.id if actor is not None else None,
            # Tenant scope, denormalised from the actor so the trail survives
            # the actor being deleted (actor_id -> NULL) later.
            tenant_id=actor.tenant_id if actor is not None else None,
            actor_email=actor.email if actor is not None else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            summary=summary,
        )
    )


@router.get("", response_model=list[AuditLogOut])
def list_audit(
    response: Response,
    action: str | None = None,
    target_type: str | None = Query(None, max_length=40),
    since: datetime | None = None,
    until: datetime | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
    page: Pagination = Depends(pagination_params),
) -> list[AuditLog]:
    """List recent audit entries, newest first (admin only).

    Optional ?action= filters by exact action key (e.g. "user.role_change").
    Phase 7.7 (additive): ?target_type= exact match, ?since= / ?until=
    ISO-8601 bounds on created_at (inclusive / exclusive; naive = UTC).
    Use ?limit= / ?offset= to page (X-Total-Count header carries the total).
    """
    since, until = _utc(since), _utc(until)
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail="'since' must not be after 'until'")
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == admin.tenant_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at < until)
    return list(paginate(db, response, stmt, page))


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
