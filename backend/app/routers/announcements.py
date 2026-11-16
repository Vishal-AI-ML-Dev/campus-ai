"""Announcements: institute-wide broadcasts posted by admins.

Admins post an announcement targeted at everyone ("all") or a single role
("student" | "teacher" | "tpo"). Every authenticated user can list the
announcements meant for them (audience "all" or their own role), newest first.

Reads are open to any logged-in INTERNAL user; posting and deleting are
admin-only (governance). Recruiters are external company accounts: they never
see institute announcements ("all" means every internal role) and are not
notified of them (Phase 5L). Mounted under the `/announcements` prefix by
`main.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Announcement, NotificationType, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import AnnouncementCreate, AnnouncementOut
from app.core.security import apply_tenant_guc, get_current_user, require_roles
from app.services.notifications import notify_many

router = APIRouter(
    prefix="/announcements",
    tags=["announcements"],
    # Phase 4 RLS pilot: stamp the caller's tenant onto the DB session so a
    # future Postgres RLS policy on `announcements` is enforced. No-op until
    # the policy is enabled (Batch 3); harmless on SQLite.
    dependencies=[Depends(apply_tenant_guc)],
)

# Posting / removing announcements is an admin (governance) action.
admin_only = require_roles(UserRole.admin)


@router.post(
    "",
    response_model=AnnouncementOut,
    status_code=status.HTTP_201_CREATED,
)
def create_announcement(
    payload: AnnouncementCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> Announcement:
    """Post an announcement (admin). Targets everyone or a single role."""
    announcement = Announcement(
        tenant_id=admin.tenant_id,
        title=payload.title,
        body=payload.body,
        audience=payload.audience,
        author_id=admin.id,
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)

    # Fan-out: notify every user this announcement targets (audience
    # "all", or a single role) except the poster. Best-effort side effect.
    # Recruiters (external) are never recipients of internal announcements.
    recipient_stmt = select(User.id).where(
        User.tenant_id == admin.tenant_id,
        User.id != admin.id,
        User.role != UserRole.recruiter,
    )
    if announcement.audience != "all":
        recipient_stmt = recipient_stmt.where(
            User.role == UserRole(announcement.audience)
        )
    recipient_ids = list(db.scalars(recipient_stmt).all())
    notify_many(
        db,
        tenant_id=admin.tenant_id,
        user_ids=recipient_ids,
        type=NotificationType.announcement.value,
        title=announcement.title,
        body=announcement.body,
        link="/announcements",
        actor_id=admin.id,
        commit=True,
    )
    return announcement


@router.get("", response_model=list[AnnouncementOut])
def list_announcements(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination_params),
) -> list[Announcement]:
    """List announcements visible to the caller, newest first.

    Admins see every announcement (a governance / management view); everyone
    else sees only posts targeted at them (audience "all" or their own role).
    """
    # Tenant-scoped: everyone (admins included) only ever sees announcements
    # from their own institute.
    stmt = (
        select(Announcement)
        .where(Announcement.tenant_id == current_user.tenant_id)
        .order_by(Announcement.created_at.desc())
    )
    if current_user.role == UserRole.recruiter:
        # External accounts: no institute announcement is addressed to them.
        stmt = stmt.where(false())
    elif current_user.role != UserRole.admin:
        stmt = stmt.where(
            or_(
                Announcement.audience == "all",
                Announcement.audience == current_user.role.value,
            )
        )
    return list(paginate(db, response, stmt, page))


@router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_announcement(
    announcement_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> None:
    """Delete an announcement (admin)."""
    announcement = db.get(Announcement, announcement_id)
    # Tenant guard: an admin can only delete their own institute's posts.
    if announcement is None or announcement.tenant_id != admin.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )
    db.delete(announcement)
    db.commit()
