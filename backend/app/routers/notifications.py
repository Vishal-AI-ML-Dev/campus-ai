"""In-app notifications: each user's own notification feed.

Notifications are created as a side effect of domain events by
``app.services.notifications``. Here the recipient reads THEIR OWN feed, sees an
unread badge count, and marks items read. Every query is scoped to the caller
(``user_id == me``) and their institute (``tenant_id``), with Postgres RLS
underneath. Mounted under the ``/notifications`` prefix by ``main.py``.

Phase 5L: recruiters are external accounts and must not see internal
institute announcements. Announcement-type notifications that reached a
recruiter before that rule existed are HIDDEN from them (list, unread count,
mark-read, mark-all-read) - never deleted and never mutated.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import apply_tenant_guc, get_current_user
from app.models import Notification, NotificationType, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import MarkReadResult, NotificationOut, UnreadCountOut

def _visible_to(user: User) -> list:
    """Base filter for the caller's OWN, VISIBLE notifications."""
    conds = [Notification.tenant_id == user.tenant_id, Notification.user_id == user.id]
    if user.role == UserRole.recruiter:
        conds.append(Notification.type != NotificationType.announcement.value)
    return conds


router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    # Stamp the caller's tenant onto the DB session so the notifications RLS
    # policy is enforced (no-op on SQLite).
    dependencies=[Depends(apply_tenant_guc)],
)


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    response: Response,
    unread_only: bool = Query(
        False, description="Only return unread notifications."
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination_params),
) -> list[Notification]:
    """List the caller's own notifications, newest first (X-Total-Count set)."""
    stmt = (
        select(Notification)
        .where(*_visible_to(current_user))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return list(paginate(db, response, stmt, page))


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UnreadCountOut:
    """Return the caller's unread notification count (for the bell badge)."""
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(*_visible_to(current_user), Notification.is_read.is_(False))
    )
    return UnreadCountOut(unread=int(count or 0))


@router.post("/read-all", response_model=MarkReadResult)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MarkReadResult:
    """Mark ALL of the caller's unread notifications as read."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Notification)
        .where(*_visible_to(current_user), Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    db.commit()
    return MarkReadResult(updated=int(result.rowcount or 0))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Notification:
    """Mark a single notification (the caller's own) as read."""
    note = db.get(Notification, notification_id)
    if (
        note is None
        or note.tenant_id != current_user.tenant_id
        or note.user_id != current_user.id
        # Hidden from this caller (see _visible_to): 404, row left untouched.
        or (
            current_user.role == UserRole.recruiter
            and note.type == NotificationType.announcement.value
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    if not note.is_read:
        note.is_read = True
        note.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(note)
    return note
