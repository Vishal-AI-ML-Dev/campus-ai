"""Faculty interview-preparation campaigns (Phase 9.3).

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §13.

* Who: TPO and admin manage every campaign of their institute; a teacher
  manages only campaigns they created and may assign only active students of
  sections they teach (``timetable_entries.teacher_id``). Students see their
  own assignments. Anything outside these rules is 404 (no existence oracle).
* Completion is derived READ-ONLY from ``mock_interview_sessions``: a
  ``completed`` session of the student whose ``completed_at`` falls in
  [starts_at, due_at] (on time) or after due_at (late), with the campaign's
  interview type when one is set. The ONLY columns read from that table are
  ``student_id``, ``status``, ``interview_type``, ``completed_at`` and
  ``tenant_id`` - never scores, transcripts, reports or answers (P9-D7).
* Status: draft -> active|cancelled; active -> closed|cancelled;
  closed -> active|archived; cancelled -> archived; archived is terminal.
  Assignment changes are allowed while draft or active.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationStatus,
    Department,
    Drive,
    MockInterviewSession,
    NotificationType,
    PlacementCampaign,
    PlacementCampaignStudent,
    Section,
    StudentPlacementProfile,
    TimetableEntry,
    User,
    UserRole,
)
from app.routers.audit import record_audit
from app.services.notifications import notify

MSG_NOT_FOUND = "Campaign not found"
MAX_ASSIGNED = 2000
CAMPAIGN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "cancelled"}),
    "active": frozenset({"closed", "cancelled"}),
    "closed": frozenset({"active", "archived"}),
    "cancelled": frozenset({"archived"}),
}
EDITABLE = frozenset({"draft", "active"})
STAFF = (UserRole.tpo, UserRole.admin)


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    """Store/compare as UTC (SQLite keeps no offset; Postgres timestamptz is fine)."""
    return _utc(dt)


# --- Access -------------------------------------------------------------------------
def teacher_section_ids(db: Session, teacher: User) -> set[int]:
    return set(
        db.scalars(
            select(TimetableEntry.section_id).where(
                TimetableEntry.tenant_id == teacher.tenant_id, TimetableEntry.teacher_id == teacher.id
            )
        )
    )


def _visible_clause(user: User):
    clause = PlacementCampaign.tenant_id == user.tenant_id
    if user.role == UserRole.teacher:
        clause = and_(clause, PlacementCampaign.created_by_id == user.id)
    return clause


def require_campaign(db: Session, user: User, campaign_id: int) -> PlacementCampaign:
    c = db.get(PlacementCampaign, campaign_id)
    if c is None or c.tenant_id != user.tenant_id:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_FOUND)
    if user.role == UserRole.teacher and c.created_by_id != user.id:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_FOUND)
    if user.role not in (UserRole.teacher, *STAFF):
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_FOUND)
    return c


def _validate_drive(db: Session, user: User, drive_id: int | None) -> None:
    if drive_id is None:
        return
    if user.role == UserRole.teacher:
        # Placement drives are TPO/admin data (P9-D2 role matrix).
        raise _http(status.HTTP_403_FORBIDDEN, "Only the placement office can link a campaign to a drive.")
    drive = db.get(Drive, drive_id)
    if drive is None or drive.tenant_id != user.tenant_id:
        raise _http(422, "Unknown drive id")


# --- Students a user may assign ------------------------------------------------------
def assignable_students_stmt(db: Session, user: User):
    stmt = select(User).where(
        User.tenant_id == user.tenant_id, User.role == UserRole.student, User.is_active.is_(True)
    )
    if user.role == UserRole.teacher:
        sections = teacher_section_ids(db, user)
        stmt = stmt.where(User.section_id.in_(sections or {-1}))
    return stmt


def candidates_stmt(
    db: Session,
    user: User,
    *,
    section_id: int | None,
    department_id: int | None,
    graduation_year: int | None,
    drive_id: int | None,
    application_status: ApplicationStatus | None,
    q: str | None,
):
    stmt = assignable_students_stmt(db, user)
    tenant_id = user.tenant_id
    if section_id is not None:
        stmt = stmt.where(User.section_id == section_id)
    if department_id is not None:
        stmt = stmt.where(
            User.section_id.in_(
                select(Section.id).where(Section.department_id == department_id, Section.tenant_id == tenant_id)
            )
        )
    if graduation_year is not None or drive_id is not None:
        if user.role == UserRole.teacher:
            raise _http(status.HTTP_403_FORBIDDEN, "These filters are available to the placement office only.")
    if graduation_year is not None:
        stmt = stmt.where(
            User.id.in_(
                select(StudentPlacementProfile.student_id).where(
                    StudentPlacementProfile.tenant_id == tenant_id,
                    StudentPlacementProfile.graduation_year == graduation_year,
                )
            )
        )
    if drive_id is not None:
        drive = db.get(Drive, drive_id)
        if drive is None or drive.tenant_id != tenant_id:
            raise _http(status.HTTP_404_NOT_FOUND, "Drive not found")
        sub = select(Application.student_id).where(
            Application.tenant_id == tenant_id, Application.drive_id == drive_id
        )
        if application_status is not None:
            sub = sub.where(Application.status == application_status)
        stmt = stmt.where(User.id.in_(sub))
    if q and q.strip():
        from app.services.placement_management import escape_like

        pattern = f"%{escape_like(q.strip().lower())}%"
        stmt = stmt.where(or_(User.full_name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\")))
    return stmt.order_by(User.full_name, User.id)


def section_info(db: Session, tenant_id: int, section_ids: set[int]) -> dict[int, tuple[str, str | None]]:
    if not section_ids:
        return {}
    rows = db.execute(
        select(Section.id, Section.name, Department.name)
        .outerjoin(Department, and_(Section.department_id == Department.id, Department.tenant_id == tenant_id))
        .where(Section.id.in_(section_ids), Section.tenant_id == tenant_id)
    ).all()
    return {sid: (sname, dname) for sid, sname, dname in rows}


def assigned_ids(db: Session, campaign: PlacementCampaign) -> set[int]:
    return set(
        db.scalars(
            select(PlacementCampaignStudent.student_id).where(
                PlacementCampaignStudent.tenant_id == campaign.tenant_id,
                PlacementCampaignStudent.campaign_id == campaign.id,
            )
        )
    )


# --- Completion (read-only, completion columns only) ---------------------------------
def completion_map(db: Session, campaign: PlacementCampaign, student_ids: list[int]) -> dict[int, datetime]:
    """student_id -> earliest qualifying completion time (>= starts_at)."""
    if not student_ids:
        return {}
    stmt = (
        select(MockInterviewSession.student_id, func.min(MockInterviewSession.completed_at))
        .where(
            MockInterviewSession.tenant_id == campaign.tenant_id,
            MockInterviewSession.student_id.in_(student_ids),
            MockInterviewSession.status == "completed",
            MockInterviewSession.completed_at.is_not(None),
            MockInterviewSession.completed_at >= _naive_utc(campaign.starts_at),
        )
        .group_by(MockInterviewSession.student_id)
    )
    if campaign.interview_type:
        stmt = stmt.where(MockInterviewSession.interview_type == campaign.interview_type)
    return {sid: _utc(at) for sid, at in db.execute(stmt).all()}


def completion_of(campaign: PlacementCampaign, at: datetime | None) -> str:
    if at is None:
        return "pending"
    return "completed" if at <= _utc(campaign.due_at) else "completed_late"


def counts_for(db: Session, campaigns: list[PlacementCampaign]) -> dict[int, tuple[int, int, int]]:
    """campaign_id -> (assigned, completed on time, completed late).

    Exactly two queries for any number of campaigns (Phase 9 closure L4; it
    used to be one completion query per campaign): the assignments, then the
    assigned students' completed sessions since the earliest campaign start.
    The per-campaign rule of ``completion_map`` is then applied in memory:
    earliest ``completed`` session with ``completed_at >= starts_at``, matching
    the campaign's interview type when set. Only the completion columns of
    ``mock_interview_sessions`` are read.
    """
    out: dict[int, tuple[int, int, int]] = {}
    if not campaigns:
        return out
    tenant_id = campaigns[0].tenant_id
    by_campaign: dict[int, list[int]] = {c.id: [] for c in campaigns}
    for cid, sid in db.execute(
        select(PlacementCampaignStudent.campaign_id, PlacementCampaignStudent.student_id).where(
            PlacementCampaignStudent.tenant_id == tenant_id,
            PlacementCampaignStudent.campaign_id.in_(list(by_campaign)),
        )
    ).all():
        by_campaign[cid].append(sid)
    student_ids = sorted({sid for sids in by_campaign.values() for sid in sids})
    sessions: dict[int, list[tuple[str, datetime]]] = {}
    if student_ids:
        earliest = min(_naive_utc(c.starts_at) for c in campaigns)
        for sid, itype, at in db.execute(
            select(
                MockInterviewSession.student_id,
                MockInterviewSession.interview_type,
                MockInterviewSession.completed_at,
            ).where(
                MockInterviewSession.tenant_id == tenant_id,
                MockInterviewSession.student_id.in_(student_ids),
                MockInterviewSession.status == "completed",
                MockInterviewSession.completed_at.is_not(None),
                MockInterviewSession.completed_at >= earliest,
            )
        ).all():
            sessions.setdefault(sid, []).append((itype, _utc(at)))
    for c in campaigns:
        starts = _utc(c.starts_at)
        on_time = late = 0
        for sid in by_campaign[c.id]:
            first = min(
                (
                    at for itype, at in sessions.get(sid, ())
                    if at >= starts and (not c.interview_type or itype == c.interview_type)
                ),
                default=None,
            )
            if first is None:
                continue
            if completion_of(c, first) == "completed":
                on_time += 1
            else:
                late += 1
        out[c.id] = (len(by_campaign[c.id]), on_time, late)
    return out


# --- Writes --------------------------------------------------------------------------
def create(db: Session, user: User, data) -> PlacementCampaign:
    _validate_drive(db, user, data.drive_id)
    c = PlacementCampaign(
        tenant_id=user.tenant_id,
        title=data.title,
        description=data.description,
        status="draft",
        interview_type=data.interview_type,
        target_role=data.target_role,
        drive_id=data.drive_id,
        starts_at=_naive_utc(data.starts_at),
        due_at=_naive_utc(data.due_at),
        created_by_id=user.id,
        created_by_role=user.role.value,
    )
    db.add(c)
    db.flush()
    record_audit(db, user, "placement.campaign_create", f"Campaign {c.id} created (draft)", "campaign", c.id)
    db.commit()
    db.refresh(c)
    return c


def edit(db: Session, user: User, c: PlacementCampaign, data) -> PlacementCampaign:
    if c.status not in EDITABLE:
        raise _http(status.HTTP_409_CONFLICT, f"A {c.status} campaign cannot be edited.")
    if data.drive_id != c.drive_id:
        _validate_drive(db, user, data.drive_id)
    fields = dict(
        title=data.title,
        description=data.description,
        interview_type=data.interview_type,
        target_role=data.target_role,
        drive_id=data.drive_id,
        starts_at=_naive_utc(data.starts_at),
        due_at=_naive_utc(data.due_at),
    )
    changed = sorted(
        k for k, v in fields.items()
        if (_utc(getattr(c, k)) if k in ("starts_at", "due_at") else getattr(c, k)) != v
    )
    for k, v in fields.items():
        setattr(c, k, v)
    record_audit(
        db, user, "placement.campaign_update",
        f"Campaign {c.id} fields changed: {', '.join(changed) or 'none'}", "campaign", c.id,
    )
    db.commit()
    db.refresh(c)
    return c


def _notify_assigned(db: Session, user: User, c: PlacementCampaign, student_ids: list[int]) -> None:
    for sid in student_ids[:MAX_ASSIGNED]:
        notify(
            db,
            tenant_id=c.tenant_id,
            user_id=sid,
            type=NotificationType.campaign_assigned.value,
            title=f"Interview practice assigned: {c.title}"[:200],
            body=f"Complete a mock interview by {_utc(c.due_at).strftime('%d %b %Y %H:%M UTC')}.",
            link="/dashboard/placement-hub",
            actor_id=user.id,
        )


def transition(db: Session, user: User, c: PlacementCampaign, to: str) -> PlacementCampaign:
    current = c.status
    if to not in CAMPAIGN_TRANSITIONS.get(current, frozenset()):
        raise _http(status.HTTP_409_CONFLICT, f"Cannot move a {current} campaign to {to}.")
    if to == "active" and _utc(c.due_at) <= datetime.now(timezone.utc):
        raise _http(status.HTTP_409_CONFLICT, "The due date has passed. Move the due date first.")
    result = db.execute(
        update(PlacementCampaign)
        .where(
            PlacementCampaign.id == c.id,
            PlacementCampaign.tenant_id == c.tenant_id,
            PlacementCampaign.status == current,
        )
        .values(status=to, updated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "The campaign was changed by someone else. Reload and try again.")
    notified = 0
    if current == "draft" and to == "active":
        ids = sorted(assigned_ids(db, c))
        _notify_assigned(db, user, c, ids)
        notified = len(ids)
    record_audit(
        db, user, "placement.campaign_status",
        f"Campaign {c.id}: {current} -> {to}" + (f"; {notified} students notified" if notified else ""),
        "campaign", c.id,
    )
    db.commit()
    db.refresh(c)
    return c


def assign(db: Session, user: User, c: PlacementCampaign, student_ids: list[int], section_ids: list[int]) -> dict:
    if c.status not in EDITABLE:
        raise _http(status.HTTP_409_CONFLICT, f"Students cannot be assigned to a {c.status} campaign.")
    allowed = assignable_students_stmt(db, user)
    wanted: set[int] = set()
    if student_ids:
        found = set(db.scalars(allowed.with_only_columns(User.id).where(User.id.in_(student_ids))))
        if found != set(student_ids):
            # Same answer for unknown, other-institute and not-your-section ids.
            raise _http(422, "Unknown student id")
        wanted |= found
    if section_ids:
        ok_sections = set(
            db.scalars(select(Section.id).where(Section.id.in_(section_ids), Section.tenant_id == user.tenant_id))
        )
        if user.role == UserRole.teacher:
            ok_sections &= teacher_section_ids(db, user)
        if ok_sections != set(section_ids):
            raise _http(422, "Unknown section id")
        wanted |= set(db.scalars(allowed.with_only_columns(User.id).where(User.section_id.in_(section_ids))))
    existing = assigned_ids(db, c)
    new = sorted(wanted - existing)
    if len(existing) + len(new) > MAX_ASSIGNED:
        raise _http(422, f"A campaign can have at most {MAX_ASSIGNED} students.")
    for sid in new:
        db.add(
            PlacementCampaignStudent(tenant_id=c.tenant_id, campaign_id=c.id, student_id=sid, assigned_by_id=user.id)
        )
    if c.status == "active" and new:
        _notify_assigned(db, user, c, new)
    record_audit(
        db, user, "placement.campaign_assign",
        f"Campaign {c.id}: {len(new)} students assigned ({len(wanted) - len(new)} already assigned)",
        "campaign", c.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "Assignments changed at the same time. Reload and try again.")
    return dict(added=len(new), already_assigned=len(wanted) - len(new), total_assigned=len(existing) + len(new))


def unassign(db: Session, user: User, c: PlacementCampaign, student_id: int) -> None:
    if c.status not in EDITABLE:
        raise _http(status.HTTP_409_CONFLICT, f"Students cannot be removed from a {c.status} campaign.")
    row = db.scalar(
        select(PlacementCampaignStudent).where(
            PlacementCampaignStudent.tenant_id == c.tenant_id,
            PlacementCampaignStudent.campaign_id == c.id,
            PlacementCampaignStudent.student_id == student_id,
        )
    )
    if row is None:
        raise _http(status.HTTP_404_NOT_FOUND, "Student is not assigned to this campaign")
    db.delete(row)
    record_audit(
        db, user, "placement.campaign_unassign", f"Campaign {c.id}: student {student_id} removed", "campaign", c.id
    )
    db.commit()


def drive_labels(db: Session, tenant_id: int, drive_ids: set[int]) -> dict[int, str]:
    if not drive_ids:
        return {}
    return {
        did: f"{company} — {role}"
        for did, company, role in db.execute(
            select(Drive.id, Drive.company_name, Drive.role_title).where(
                Drive.id.in_(drive_ids), Drive.tenant_id == tenant_id
            )
        ).all()
    }
