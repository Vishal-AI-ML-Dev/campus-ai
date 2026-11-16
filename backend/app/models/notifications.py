"""Notification ORM model - the in-app notification feed.

One row = one notification delivered to one recipient, created as a side effect
of a domain event (an announcement aimed at you, a TPO decision on your
application, a new answer to your doubt, a graded submission, ...). The
recipient reads their OWN feed (newest first), sees an unread badge count, and
marks items read. Tenant-scoped like every other feature table (FORCE RLS in
the migration), so one institute can never see another's notifications.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Notification(Base):
    """A single in-app notification for one user (its recipient)."""

    __tablename__ = "notifications"
    __table_args__ = (
        # Hot path: "my unread feed" / badge count filters by (user, is_read).
        Index("ix_notifications_user_unread", "user_id", "is_read"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The user this notification is FOR (its recipient).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Short machine category (see NotificationType). Stored as a plain string -
    # not a DB Enum - so new event types need no migration (mirrors
    # announcements.audience).
    type: Mapped[str] = mapped_column(
        String(40), default="generic", nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional in-app deep link the frontend routes to, e.g. "/announcements".
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Who/what triggered it (optional; SET NULL so deleting the actor keeps the
    # notification). System-generated events may leave this null.
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user={self.user_id} "
            f"type={self.type!r} read={self.is_read}>"
        )
