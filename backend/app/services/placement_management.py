"""Placement management v2 (Phase 9.1) - eligibility, apply, status, lifecycle.

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §5.

* Gate: ``enabled_for(user)`` = PLACEMENT_MANAGEMENT_ENABLED and the user's
  institute in PLACEMENT_MANAGEMENT_TENANT_IDS (empty/invalid = nobody).
* Eligibility v2 = the legacy four criteria (computed with the SAME formulas
  and the legacy ``_evaluate`` for identical reasons) + department, graduation
  year, 10th/12th %, active backlogs, placement status and the drive's
  one-offer policy. Metrics are computed in a constant number of queries for
  any number of students (no N+1).
* Writes are race-safe: the applications unique constraint (duplicate apply
  -> 409, never 500) and compare-and-set UPDATEs for application status and
  drive lifecycle (a stale writer gets 409).
* Audit summaries carry ids, statuses and field NAMES only - never marks,
  notes or student names.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.models import (
    Application,
    ApplicationStatus,
    AttendanceRecord,
    AttendanceStatus,
    Department,
    Drive,
    NotificationType,
    Offer,
    OfferStatus,
    PlacementApplicationEvent,
    PlacementDriveDepartment,
    PlacementDriveSettings,
    ProjectMember,
    Result,
    Section,
    Skill,
    SkillStatus,
    StudentPlacementProfile,
    Subject,
    User,
    UserRole,
)
from app.routers.audit import record_audit
from app.routers.mock_interview import _allowed_tenant_ids
from app.routers.placement import _evaluate as legacy_evaluate
from app.services.notifications import notify

MSG_DRIVE_NOT_FOUND = "Drive not found"
MSG_APPLICATION_NOT_FOUND = "Application not found"
MSG_STUDENT_NOT_FOUND = "Student not found"

# Drive lifecycle (spec §5.4). completed / cancelled are terminal.
DRIVE_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published", "cancelled"}),
    "published": frozenset({"closed", "cancelled"}),
    "closed": frozenset({"published", "completed", "cancelled"}),
}
# Application status (spec §5.4). Corrections need a note.
APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.applied: frozenset(
        {ApplicationStatus.shortlisted, ApplicationStatus.rejected, ApplicationStatus.selected}
    ),
    ApplicationStatus.shortlisted: frozenset({ApplicationStatus.selected, ApplicationStatus.rejected}),
    ApplicationStatus.rejected: frozenset({ApplicationStatus.shortlisted}),
    ApplicationStatus.selected: frozenset({ApplicationStatus.rejected}),
}
NOTE_REQUIRED_FROM = frozenset({ApplicationStatus.rejected, ApplicationStatus.selected})

PROFILE_FIELDS = (
    "roll_number",
    "graduation_year",
    "tenth_percent",
    "twelfth_percent",
    "diploma_percent",
    "active_backlogs",
    "placement_status",
    "status_note",
)


# --- Gate ---------------------------------------------------------------------
def enabled_for(user: User) -> bool:
    """Fail closed: master switch AND institute explicitly allow-listed."""
    if not app_settings.PLACEMENT_MANAGEMENT_ENABLED:
        return False
    allowed = _allowed_tenant_ids(app_settings.PLACEMENT_MANAGEMENT_TENANT_IDS)
    # Unlike the mock-interview master list, EMPTY means nobody (P9-D1).
    return allowed is not None and user.tenant_id in allowed


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone=True columns; they are UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# --- Lookups ------------------------------------------------------------------
def require_drive(db: Session, drive_id: int, tenant_id: int) -> Drive:
    drive = db.get(Drive, drive_id)
    if drive is None or drive.tenant_id != tenant_id:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_DRIVE_NOT_FOUND)
    return drive


def require_student(db: Session, student_id: int, tenant_id: int) -> User:
    user = db.get(User, student_id)
    if user is None or user.tenant_id != tenant_id or user.role != UserRole.student:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_STUDENT_NOT_FOUND)
    return user


def get_settings(db: Session, drive: Drive) -> PlacementDriveSettings | None:
    return db.scalar(
        select(PlacementDriveSettings).where(
            PlacementDriveSettings.drive_id == drive.id,
            PlacementDriveSettings.tenant_id == drive.tenant_id,
        )
    )


def drive_department_ids(db: Session, drive: Drive) -> list[int]:
    return sorted(
        db.scalars(
            select(PlacementDriveDepartment.department_id).where(
                PlacementDriveDepartment.drive_id == drive.id,
                PlacementDriveDepartment.tenant_id == drive.tenant_id,
            )
        )
    )


def effective_lifecycle(drive: Drive, cfg: PlacementDriveSettings | None) -> str:
    """Legacy drives (no settings row) are published when open, else closed."""
    if cfg is None:
        return "published" if drive.is_open else "closed"
    return cfg.lifecycle


def window_state(cfg: PlacementDriveSettings | None, now: datetime | None = None) -> str:
    if cfg is None or (cfg.applications_open_at is None and cfg.applications_close_at is None):
        return "no_window"
    now = now or _now()
    opens, closes = _aware(cfg.applications_open_at), _aware(cfg.applications_close_at)
    if opens is not None and now < opens:
        return "not_open_yet"
    if closes is not None and now >= closes:
        return "closed"
    return "open"


def get_profile(db: Session, tenant_id: int, student_id: int) -> StudentPlacementProfile | None:
    return db.scalar(
        select(StudentPlacementProfile).where(
            StudentPlacementProfile.tenant_id == tenant_id,
            StudentPlacementProfile.student_id == student_id,
        )
    )


def profiles_for(db: Session, tenant_id: int, student_ids: list[int] | None) -> dict[int, StudentPlacementProfile]:
    stmt = select(StudentPlacementProfile).where(StudentPlacementProfile.tenant_id == tenant_id)
    if student_ids is not None:
        if not student_ids:
            return {}
        stmt = stmt.where(StudentPlacementProfile.student_id.in_(student_ids))
    return {p.student_id: p for p in db.scalars(stmt)}


def departments_for(db: Session, tenant_id: int, students: list[User]) -> dict[int, tuple[int | None, str | None, str | None]]:
    """student_id -> (department_id, department_name, section_name)."""
    section_ids = {s.section_id for s in students if s.section_id is not None}
    by_section: dict[int, tuple[int, str, str]] = {}
    if section_ids:
        rows = db.execute(
            select(Section.id, Section.name, Department.id, Department.name)
            .join(Department, Section.department_id == Department.id)
            .where(Section.id.in_(section_ids), Section.tenant_id == tenant_id, Department.tenant_id == tenant_id)
        ).all()
        by_section = {sid: (did, dname, sname) for sid, sname, did, dname in rows}
    out: dict[int, tuple[int | None, str | None, str | None]] = {}
    for s in students:
        hit = by_section.get(s.section_id) if s.section_id is not None else None
        out[s.id] = hit if hit is not None else (None, None, None)
    return out


def placed_student_ids(db: Session, tenant_id: int, student_ids: list[int] | None = None) -> set[int]:
    """Placed = a `selected` application OR an `accepted` offer (P9-D4)."""
    sel = select(Application.student_id).where(
        Application.tenant_id == tenant_id, Application.status == ApplicationStatus.selected
    )
    acc = select(Offer.student_id).where(Offer.tenant_id == tenant_id, Offer.status == OfferStatus.accepted)
    if student_ids is not None:
        if not student_ids:
            return set()
        sel = sel.where(Application.student_id.in_(student_ids))
        acc = acc.where(Offer.student_id.in_(student_ids))
    return set(db.scalars(sel)) | set(db.scalars(acc))


# --- Verified metrics (batched; same formulas as routers/placement.py) -------
def bulk_metrics(db: Session, tenant_id: int, student_ids: list[int] | None) -> dict[int, dict]:
    """Per-student verified metrics in a constant number of queries.

    ``student_ids=None`` = every student of the institute. Formulas match
    ``routers.placement._student_profile`` (credit-weighted CGPA rounded to 2;
    attendance = (present + late) / total rounded to 1; verified skills and
    verified project contributions). Every query is tenant-filtered.
    """
    if student_ids is not None and not student_ids:
        return {}

    def scope(stmt, column):
        stmt = stmt.where(column.in_(student_ids)) if student_ids is not None else stmt
        return stmt

    gp = db.execute(
        scope(
            select(Result.student_id, Result.grade_point, Subject.credits)
            .join(Subject, Result.subject_id == Subject.id)
            .where(Result.tenant_id == tenant_id)
            .order_by(Result.id),
            Result.student_id,
        )
    ).all()
    weighted: dict[int, float] = defaultdict(float)
    credits_total: dict[int, int] = defaultdict(int)
    for sid, grade_point, credits in gp:
        weighted[sid] += grade_point * credits
        credits_total[sid] += credits

    att = db.execute(
        scope(
            select(AttendanceRecord.student_id, AttendanceRecord.status, func.count(AttendanceRecord.id))
            .where(AttendanceRecord.tenant_id == tenant_id)
            .group_by(AttendanceRecord.student_id, AttendanceRecord.status),
            AttendanceRecord.student_id,
        )
    ).all()
    att_total: dict[int, int] = defaultdict(int)
    att_ok: dict[int, int] = defaultdict(int)
    for sid, st, n in att:
        att_total[sid] += n
        if st in (AttendanceStatus.present, AttendanceStatus.late):
            att_ok[sid] += n

    skills: dict[int, list[str]] = defaultdict(list)
    for sid, name in db.execute(
        scope(
            select(Skill.student_id, Skill.name)
            .where(Skill.tenant_id == tenant_id, Skill.status == SkillStatus.verified)
            .order_by(Skill.id),
            Skill.student_id,
        )
    ).all():
        skills[sid].append(name)

    projects = dict(
        db.execute(
            scope(
                select(ProjectMember.student_id, func.count(ProjectMember.id))
                .where(ProjectMember.tenant_id == tenant_id, ProjectMember.status == SkillStatus.verified)
                .group_by(ProjectMember.student_id),
                ProjectMember.student_id,
            )
        ).all()
    )

    ids = student_ids if student_ids is not None else sorted(
        set(credits_total) | set(att_total) | set(skills) | set(projects)
    )
    out: dict[int, dict] = {}
    for sid in ids:
        total_credits = credits_total.get(sid, 0)
        total_att = att_total.get(sid, 0)
        out[sid] = {
            "cgpa": round(weighted[sid] / total_credits, 2) if total_credits else 0.0,
            "attendance": round(att_ok.get(sid, 0) / total_att * 100, 1) if total_att else 0.0,
            "verified_skills": list(skills.get(sid, [])),
            "verified_projects": int(projects.get(sid, 0)),
        }
    return out


def empty_metrics() -> dict:
    return {"cgpa": 0.0, "attendance": 0.0, "verified_skills": [], "verified_projects": 0}


# --- Eligibility v2 -----------------------------------------------------------
@dataclass(frozen=True)
class DriveContext:
    drive: Drive
    cfg: PlacementDriveSettings | None
    department_ids: tuple[int, ...]
    department_names: dict[int, str]


def drive_context(db: Session, drive: Drive) -> DriveContext:
    cfg = get_settings(db, drive)
    dept_ids = tuple(drive_department_ids(db, drive)) if cfg is not None else ()
    names: dict[int, str] = {}
    if dept_ids:
        names = dict(
            db.execute(
                select(Department.id, Department.name).where(
                    Department.id.in_(dept_ids), Department.tenant_id == drive.tenant_id
                )
            ).all()
        )
    return DriveContext(drive=drive, cfg=cfg, department_ids=dept_ids, department_names=names)


def _reason(criterion: str, passed: bool, detail: str) -> dict:
    return {"criterion": criterion, "passed": passed, "detail": detail}


def _fmt(value: float) -> str:
    return f"{value:g}"


def evaluate(
    ctx: DriveContext,
    metrics: dict,
    profile: StudentPlacementProfile | None,
    department: tuple[int | None, str | None, str | None],
    placed: bool,
) -> tuple[bool, list[dict]]:
    """Legacy reasons (unchanged) followed by the v2 criteria. A criterion the
    drive does not set is skipped; a required value missing from the profile
    fails ("not recorded")."""
    _ok, reasons = legacy_evaluate(metrics, ctx.drive)
    cfg = ctx.cfg

    pstatus = profile.placement_status if profile is not None else "active"
    reasons.append(
        _reason(
            "Placement status",
            pstatus == "active",
            "active" if pstatus == "active" else pstatus.replace("_", " "),
        )
    )

    if cfg is not None:
        if cfg.exclude_placed:
            reasons.append(
                _reason("Not yet placed", not placed, "already placed" if placed else "not placed yet")
            )
        if ctx.department_ids:
            dept_id, dept_name, _section = department
            allowed = ", ".join(sorted(ctx.department_names.get(d, f"#{d}") for d in ctx.department_ids))
            reasons.append(
                _reason(
                    "Department",
                    dept_id is not None and dept_id in ctx.department_ids,
                    f"{dept_name or 'not assigned'} (open to: {allowed})",
                )
            )
        years = list(cfg.graduation_years_json or [])
        if years:
            gy = profile.graduation_year if profile is not None else None
            reasons.append(
                _reason(
                    "Graduation year",
                    gy is not None and gy in years,
                    f"{gy if gy is not None else 'not recorded'} (need {', '.join(map(str, years))})",
                )
            )
        if cfg.min_tenth_percent is not None:
            v = profile.tenth_percent if profile is not None else None
            reasons.append(
                _reason(
                    "10th %",
                    v is not None and v >= cfg.min_tenth_percent,
                    f"{_fmt(v) + '%' if v is not None else 'not recorded'} (need >= {_fmt(cfg.min_tenth_percent)}%)",
                )
            )
        if cfg.min_twelfth_percent is not None:
            twelfth = profile.twelfth_percent if profile is not None else None
            diploma = profile.diploma_percent if profile is not None else None
            if twelfth is not None:
                passed, shown = twelfth >= cfg.min_twelfth_percent, f"{_fmt(twelfth)}%"
            elif diploma is not None:
                passed, shown = diploma >= cfg.min_twelfth_percent, f"diploma {_fmt(diploma)}%"
            else:
                passed, shown = False, "not recorded"
            reasons.append(
                _reason("12th / diploma %", passed, f"{shown} (need >= {_fmt(cfg.min_twelfth_percent)}%)")
            )
        if cfg.max_active_backlogs is not None:
            b = profile.active_backlogs if profile is not None else None
            reasons.append(
                _reason(
                    "Active backlogs",
                    b is not None and b <= cfg.max_active_backlogs,
                    f"{b if b is not None else 'not recorded'} (max {cfg.max_active_backlogs})",
                )
            )
    return all(r["passed"] for r in reasons), reasons


def evaluate_student(db: Session, ctx: DriveContext, student: User) -> tuple[bool, list[dict], dict, bool, StudentPlacementProfile | None]:
    tenant_id = ctx.drive.tenant_id
    metrics = bulk_metrics(db, tenant_id, [student.id]).get(student.id, empty_metrics())
    profile = get_profile(db, tenant_id, student.id)
    dept = departments_for(db, tenant_id, [student])[student.id]
    placed = student.id in placed_student_ids(db, tenant_id, [student.id])
    eligible, reasons = evaluate(ctx, metrics, profile, dept, placed)
    return eligible, reasons, metrics, placed, profile


# --- Apply --------------------------------------------------------------------
def _add_event(
    db: Session,
    application: Application,
    from_status: ApplicationStatus | None,
    to_status: ApplicationStatus,
    actor: User,
    note: str | None,
) -> None:
    db.add(
        PlacementApplicationEvent(
            tenant_id=application.tenant_id,
            application_id=application.id,
            from_status=from_status.value if from_status is not None else None,
            to_status=to_status.value,
            actor_id=actor.id,
            actor_role=actor.role.value,
            note=note,
        )
    )


def apply_blockers(ctx: DriveContext, now: datetime | None = None) -> list[str]:
    """Drive-level reasons the drive cannot take applications right now."""
    lifecycle = effective_lifecycle(ctx.drive, ctx.cfg)
    if lifecycle != "published":
        return ["This drive is not accepting applications."]
    state = window_state(ctx.cfg, now)
    if state == "not_open_yet":
        return ["Applications have not opened yet."]
    if state == "closed":
        return ["The application window has closed."]
    return []


def apply(db: Session, student: User, drive_id: int) -> Application:
    drive = require_drive(db, drive_id, student.tenant_id)
    ctx = drive_context(db, drive)
    if effective_lifecycle(drive, ctx.cfg) == "draft":
        # Drafts are invisible to students (no existence oracle).
        raise _http(status.HTTP_404_NOT_FOUND, MSG_DRIVE_NOT_FOUND)
    blockers = apply_blockers(ctx)
    if blockers:
        raise _http(status.HTTP_400_BAD_REQUEST, blockers[0])

    existing = db.scalar(
        select(Application.id).where(
            Application.tenant_id == drive.tenant_id,
            Application.drive_id == drive.id,
            Application.student_id == student.id,
        )
    )
    if existing is not None:
        raise _http(status.HTTP_409_CONFLICT, "You have already applied to this drive.")

    eligible, reasons, _m, _p, _prof = evaluate_student(db, ctx, student)
    if not eligible:
        unmet = [f"{r['criterion']}: {r['detail']}" for r in reasons if not r["passed"]]
        raise _http(status.HTTP_403_FORBIDDEN, "Not eligible for this drive: " + "; ".join(unmet))

    application = Application(drive_id=drive.id, student_id=student.id, tenant_id=drive.tenant_id)
    db.add(application)
    try:
        db.flush()
        _add_event(db, application, None, ApplicationStatus.applied, student, None)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "You have already applied to this drive.")
    db.refresh(application)
    return application


# --- Application status -------------------------------------------------------
def require_application(db: Session, application_id: int, tenant_id: int) -> Application:
    application = db.get(Application, application_id)
    if application is None or application.tenant_id != tenant_id:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_APPLICATION_NOT_FOUND)
    drive = db.get(Drive, application.drive_id)
    if drive is None or drive.tenant_id != tenant_id:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_APPLICATION_NOT_FOUND)
    return application


def change_application_status(
    db: Session,
    actor: User,
    application_id: int,
    to_status: ApplicationStatus,
    note: str | None,
    expected_status: ApplicationStatus | None = None,
) -> Application:
    note = (note or "").strip() or None  # legacy bodies are not stripped
    application = require_application(db, application_id, actor.tenant_id)
    drive = db.get(Drive, application.drive_id)
    cfg = get_settings(db, drive)
    if cfg is not None and cfg.lifecycle == "cancelled":
        raise _http(status.HTTP_409_CONFLICT, "This drive was cancelled; its applications are frozen.")

    current = application.status
    if expected_status is not None and expected_status != current:
        raise _http(status.HTTP_409_CONFLICT, "This application was changed by someone else. Reload and try again.")
    if to_status == ApplicationStatus.applied:
        raise _http(status.HTTP_400_BAD_REQUEST, "Cannot move an application back to 'applied'.")
    if to_status == current:
        raise _http(status.HTTP_409_CONFLICT, f"The application is already '{current.value}'.")
    if to_status not in APPLICATION_TRANSITIONS.get(current, frozenset()):
        raise _http(
            status.HTTP_409_CONFLICT,
            f"Cannot change an application from '{current.value}' to '{to_status.value}'.",
        )
    if current in NOTE_REQUIRED_FROM and not note:
        raise _http(status.HTTP_400_BAD_REQUEST, "A note is required to correct a final decision.")

    # Compare-and-set: only one concurrent writer can move it from `current`.
    result = db.execute(
        update(Application)
        .where(
            Application.id == application.id,
            Application.tenant_id == actor.tenant_id,
            Application.status == current,
        )
        .values(status=to_status, note=note, decided_by_id=actor.id, decided_at=_now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "This application was changed by someone else. Reload and try again.")

    _add_event(db, application, current, to_status, actor, note)
    record_audit(
        db,
        actor,
        "placement.application_status",
        f"Application {application.id} (drive {drive.id}): {current.value} -> {to_status.value}",
        target_type="application",
        target_id=application.id,
    )
    notify(
        db,
        tenant_id=actor.tenant_id,
        user_id=application.student_id,
        type=NotificationType.application_status.value,
        title=f"Application update: {to_status.value}",
        body=note or None,
        link="/placement/applications",
        actor_id=actor.id,
    )
    db.commit()
    db.refresh(application)
    return application


def application_history(db: Session, application: Application) -> list[PlacementApplicationEvent]:
    return list(
        db.scalars(
            select(PlacementApplicationEvent)
            .where(
                PlacementApplicationEvent.application_id == application.id,
                PlacementApplicationEvent.tenant_id == application.tenant_id,
            )
            .order_by(PlacementApplicationEvent.created_at, PlacementApplicationEvent.id)
        )
    )


# --- Drive settings + lifecycle ----------------------------------------------
def _validate_departments(db: Session, tenant_id: int, department_ids: list[int]) -> None:
    if not department_ids:
        return
    found = set(
        db.scalars(select(Department.id).where(Department.id.in_(department_ids), Department.tenant_id == tenant_id))
    )
    if found != set(department_ids):
        # Same message for "missing" and "another institute's" (no oracle).
        raise _http(422, "Unknown department id")


def _new_settings(drive: Drive, actor: User, lifecycle: str) -> PlacementDriveSettings:
    return PlacementDriveSettings(
        tenant_id=drive.tenant_id,
        drive_id=drive.id,
        lifecycle=lifecycle,
        graduation_years_json=[],
        exclude_placed=False,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )


def _apply_settings_fields(cfg: PlacementDriveSettings, data) -> None:
    cfg.applications_open_at = data.applications_open_at
    cfg.applications_close_at = data.applications_close_at
    cfg.academic_year = data.academic_year
    cfg.graduation_years_json = list(data.graduation_years)
    cfg.min_tenth_percent = data.min_tenth_percent
    cfg.min_twelfth_percent = data.min_twelfth_percent
    cfg.max_active_backlogs = data.max_active_backlogs
    cfg.exclude_placed = data.exclude_placed


def _replace_departments(db: Session, drive: Drive, department_ids: list[int]) -> None:
    current = set(drive_department_ids(db, drive))
    wanted = set(department_ids)
    for row in db.scalars(
        select(PlacementDriveDepartment).where(
            PlacementDriveDepartment.drive_id == drive.id,
            PlacementDriveDepartment.tenant_id == drive.tenant_id,
            PlacementDriveDepartment.department_id.in_(current - wanted),
        )
    ):
        db.delete(row)
    for dept_id in sorted(wanted - current):
        db.add(PlacementDriveDepartment(tenant_id=drive.tenant_id, drive_id=drive.id, department_id=dept_id))


def upsert_settings(db: Session, actor: User, drive: Drive, data) -> PlacementDriveSettings:
    cfg = get_settings(db, drive)
    if cfg is not None and cfg.lifecycle in ("completed", "cancelled"):
        raise _http(status.HTTP_409_CONFLICT, f"A {cfg.lifecycle} drive cannot be edited.")
    _validate_departments(db, drive.tenant_id, data.department_ids)
    created = cfg is None
    if created:
        cfg = _new_settings(drive, actor, effective_lifecycle(drive, None))
        db.add(cfg)
    _apply_settings_fields(cfg, data)
    cfg.updated_by_id = actor.id
    _replace_departments(db, drive, data.department_ids)
    record_audit(
        db,
        actor,
        "placement.drive_settings",
        f"Drive {drive.id} placement settings {'created' if created else 'updated'}",
        target_type="drive",
        target_id=drive.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "The drive settings were changed at the same time. Try again.")
    db.refresh(cfg)
    return cfg


def create_draft_drive(db: Session, actor: User, drive_fields: dict, data) -> tuple[Drive, PlacementDriveSettings]:
    _validate_departments(db, actor.tenant_id, data.department_ids)
    drive = Drive(tenant_id=actor.tenant_id, created_by_id=actor.id, is_open=False, **drive_fields)
    db.add(drive)
    db.flush()
    cfg = _new_settings(drive, actor, "draft")
    _apply_settings_fields(cfg, data)
    db.add(cfg)
    for dept_id in data.department_ids:
        db.add(PlacementDriveDepartment(tenant_id=drive.tenant_id, drive_id=drive.id, department_id=dept_id))
    record_audit(db, actor, "placement.drive_create", f"Drive {drive.id} created as draft", "drive", drive.id)
    db.commit()
    db.refresh(drive)
    db.refresh(cfg)
    return drive, cfg


def update_drive_fields(db: Session, actor: User, drive: Drive, drive_fields: dict) -> Drive:
    cfg = get_settings(db, drive)
    lifecycle = effective_lifecycle(drive, cfg)
    if lifecycle in ("completed", "cancelled"):
        raise _http(status.HTTP_409_CONFLICT, f"A {lifecycle} drive cannot be edited.")
    changed = sorted(k for k, v in drive_fields.items() if getattr(drive, k) != v)
    for key, value in drive_fields.items():
        setattr(drive, key, value)
    record_audit(
        db,
        actor,
        "placement.drive_update",
        f"Drive {drive.id} fields changed: {', '.join(changed) or 'none'}",
        "drive",
        drive.id,
    )
    db.commit()
    db.refresh(drive)
    return drive


def transition_drive(db: Session, actor: User, drive: Drive, to: str) -> PlacementDriveSettings:
    cfg = get_settings(db, drive)
    current = effective_lifecycle(drive, cfg)
    if to not in DRIVE_TRANSITIONS.get(current, frozenset()):
        raise _http(status.HTTP_409_CONFLICT, f"Cannot move a {current} drive to {to}.")
    if to == "published" and cfg is not None:
        closes = _aware(cfg.applications_close_at)
        if closes is not None and closes <= _now():
            raise _http(
                status.HTTP_409_CONFLICT,
                "The application window has already closed. Update the drive settings first.",
            )

    if cfg is None:
        cfg = _new_settings(drive, actor, to)
        db.add(cfg)
    else:
        result = db.execute(
            update(PlacementDriveSettings)
            .where(
                PlacementDriveSettings.id == cfg.id,
                PlacementDriveSettings.tenant_id == drive.tenant_id,
                PlacementDriveSettings.lifecycle == current,
            )
            .values(lifecycle=to, updated_by_id=actor.id, updated_at=_now())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            raise _http(status.HTTP_409_CONFLICT, "The drive was changed by someone else. Reload and try again.")
    drive.is_open = to == "published"
    record_audit(
        db,
        actor,
        "placement.drive_lifecycle",
        f"Drive {drive.id}: {current} -> {to}",
        "drive",
        drive.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _http(status.HTTP_409_CONFLICT, "The drive was changed by someone else. Reload and try again.")
    db.refresh(cfg)
    db.refresh(drive)
    return cfg


def legacy_open_close(db: Session, actor: User, drive: Drive, is_open: bool) -> None:
    """P9-D6: the legacy open/close route maps onto the lifecycle when the
    institute is flagged. Re-sending the current state is a no-op (legacy
    behaviour); anything else follows the lifecycle rules."""
    cfg = get_settings(db, drive)
    current = effective_lifecycle(drive, cfg)
    target = "published" if is_open else "closed"
    if current == target:
        return
    transition_drive(db, actor, drive, target)


# --- Student profiles ---------------------------------------------------------
def upsert_profile(db: Session, actor: User, student: User, data) -> StudentPlacementProfile:
    profile = get_profile(db, student.tenant_id, student.id)
    if data.roll_number is not None:
        taken = db.scalar(
            select(StudentPlacementProfile.id).where(
                StudentPlacementProfile.tenant_id == student.tenant_id,
                StudentPlacementProfile.roll_number == data.roll_number,
                StudentPlacementProfile.student_id != student.id,
            )
        )
        if taken is not None:
            raise _http(status.HTTP_409_CONFLICT, "Another student of this institute already has that roll number.")
    created = profile is None
    if created:
        profile = StudentPlacementProfile(tenant_id=student.tenant_id, student_id=student.id)
        db.add(profile)
    changed = []
    for field in PROFILE_FIELDS:
        old = None if created else getattr(profile, field)
        new = getattr(data, field)
        if old != new:
            changed.append(field)
        setattr(profile, field, new)
    profile.updated_by_id = actor.id
    record_audit(
        db,
        actor,
        "placement.profile_update",
        f"Student {student.id} placement profile {'created' if created else 'updated'}; "
        f"fields: {', '.join(changed) or 'none'}",
        target_type="user",
        target_id=student.id,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent write won the race (same student or same roll number).
        raise _http(status.HTTP_409_CONFLICT, "The profile was changed at the same time. Reload and try again.")
    db.refresh(profile)
    return profile


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
