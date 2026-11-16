"""Notification creation helpers - call these from domain event sites.

Notifications are a SIDE EFFECT of a primary action (shortlisting a candidate,
answering a doubt, grading a submission, ...). These helpers create the
notification row(s) in the SAME session/transaction as that primary write, so
the route's existing ``db.commit()`` persists both atomically (pass
``commit=True`` only when there is no later commit). Keep call sites tiny.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models import Notification


def notify(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    actor_id: int | None = None,
    commit: bool = False,
) -> Notification:
    """Create ONE notification for ``user_id`` (the recipient).

    Returns the (unflushed unless ``commit``) Notification. Titles are clipped
    to the column limit so a long event title can never raise.
    """
    note = Notification(
        tenant_id=tenant_id,
        user_id=user_id,
        type=type,
        title=title[:200],
        body=body,
        link=link,
        actor_id=actor_id,
    )
    db.add(note)
    if commit:
        db.commit()
        db.refresh(note)
    return note


def notify_many(
    db: Session,
    *,
    tenant_id: int,
    user_ids: Iterable[int],
    type: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    actor_id: int | None = None,
    commit: bool = False,
) -> list[Notification]:
    """Fan a notification out to many recipients (deduped, skips falsy ids)."""
    notes: list[Notification] = []
    seen: set[int] = set()
    for uid in user_ids:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        notes.append(
            notify(
                db,
                tenant_id=tenant_id,
                user_id=uid,
                type=type,
                title=title,
                body=body,
                link=link,
                actor_id=actor_id,
            )
        )
    if commit and notes:
        db.commit()
    return notes
