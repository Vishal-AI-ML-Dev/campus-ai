"""Notification API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    """A single notification as returned to its recipient."""

    id: int
    type: str
    title: str
    body: str | None
    link: str | None
    actor_id: int | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnreadCountOut(BaseModel):
    """The caller's unread notification count (drives the bell badge)."""

    unread: int


class MarkReadResult(BaseModel):
    """How many notifications a mark-read action updated."""

    updated: int
