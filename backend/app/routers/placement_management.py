"""Placement management v2 routes (Phase 9.1) - mounted under ``/placement``.

Feature-flagged: every route 404s unless PLACEMENT_MANAGEMENT_ENABLED is on
AND the caller's institute is in PLACEMENT_MANAGEMENT_TENANT_IDS (empty list =
nobody). Recruiters get 403 on every route here. Objects of another institute
are 404. Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §5.

Roles: TPO writes drives/settings/lifecycle/status; TPO and admin read and
write student placement profiles; students read their own profile and
eligibility, apply, and read their own application history. Teachers have no
access in 9.1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import apply_tenant_guc, get_current_user, require_roles
from app.models import (
    Application,
    ApplicationStatus,
    Drive,
    Offer,
    OfferStatus,
    Section,
    StudentPlacementProfile,
    User,
    UserRole,
)
from app.schemas.placement import ApplicationOut, DriveOut
from app.schemas.placement_management import (
    ApplicationEventOut,
    ApplicationStatusIn,
    ApplicationSummaryOut,
    DraftDriveIn,
    DriveFieldsIn,
    DriveSettingsIn,
    DriveSettingsOut,
    DriveWithSettingsOut,
    EligibilityRowOut,
    LifecycleIn,
    MyEligibilityV2Out,
    MyPlacementProfileOut,
    StudentDetailOut,
    StudentListItemOut,
    StudentProfileIn,
    StudentProfileOut,
    VerifiedMetricsOut,
)
from app.services import placement_management as svc
from pagination import Pagination, paginate_list, paginate_rows, pagination_params


def require_placement_management(current_user: User = Depends(get_current_user)) -> User:
    """Gate for every route here: 404 when off for the caller's institute."""
    if not svc.enabled_for(current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if current_user.role == UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action",
        )
    return current_user


router = APIRouter(
    prefix="/placement",
    tags=["placement-management"],
    dependencies=[Depends(apply_tenant_guc), Depends(require_placement_management)],
)

tpo_only = require_roles(UserRole.tpo)
staff_only = require_roles(UserRole.tpo, UserRole.admin)
student_only = require_roles(UserRole.student)


# --- Helpers ------------------------------------------------------------------
def _profile_out(profile: StudentPlacementProfile | None) -> StudentProfileOut:
    if profile is None:
        return StudentProfileOut(recorded=False)
    return StudentProfileOut(
        roll_number=profile.roll_number,
        graduation_year=profile.graduation_year,
        tenth_percent=profile.tenth_percent,
        twelfth_percent=profile.twelfth_percent,
        diploma_percent=profile.diploma_percent,
        active_backlogs=profile.active_backlogs,
        placement_status=profile.placement_status,
        status_note=profile.status_note,
        recorded=True,
        updated_at=profile.updated_at,
    )


def _metrics_out(metrics: dict) -> VerifiedMetricsOut:
    return VerifiedMetricsOut(
        cgpa=metrics["cgpa"],
        attendance=metrics["attendance"],
        verified_skills=list(metrics["verified_skills"]),
        verified_projects=metrics["verified_projects"],
    )


def _settings_out(db: Session, drive: Drive) -> DriveSettingsOut:
    cfg = svc.get_settings(db, drive)
    return DriveSettingsOut(
        drive_id=drive.id,
        configured=cfg is not None,
        lifecycle=svc.effective_lifecycle(drive, cfg),
        is_open=drive.is_open,
        applications_open_at=svc._aware(cfg.applications_open_at) if cfg else None,
        applications_close_at=svc._aware(cfg.applications_close_at) if cfg else None,
        academic_year=cfg.academic_year if cfg else None,
        graduation_years=list(cfg.graduation_years_json or []) if cfg else [],
        department_ids=svc.drive_department_ids(db, drive) if cfg else [],
        min_tenth_percent=cfg.min_tenth_percent if cfg else None,
        min_twelfth_percent=cfg.min_twelfth_percent if cfg else None,
        max_active_backlogs=cfg.max_active_backlogs if cfg else None,
        exclude_placed=cfg.exclude_placed if cfg else False,
    )


def _list_item(user: User, dept: tuple, profile: StudentPlacementProfile | None, placed: bool) -> dict:
    dept_id, dept_name, section_name = dept
    return dict(
        student_id=user.id,
        full_name=user.full_name,
        email=user.email,
        section_id=user.section_id,
        section_name=section_name,
        department_id=dept_id,
        department_name=dept_name,
        roll_number=profile.roll_number if profile else None,
        graduation_year=profile.graduation_year if profile else None,
        placement_status=profile.placement_status if profile else "active",
        profile_recorded=profile is not None,
        placed=placed,
    )


def _placed_clause(tenant_id: int):
    return or_(
        exists().where(
            Application.student_id == User.id,
            Application.tenant_id == tenant_id,
            Application.status == ApplicationStatus.selected,
        ),
        exists().where(
            Offer.student_id == User.id,
            Offer.tenant_id == tenant_id,
            Offer.status == OfferStatus.accepted,
        ),
    )


# --- Student placement profiles (TPO/admin) ------------------------------------
@router.get("/students", response_model=list[StudentListItemOut])
def list_students(
    response: Response,
    department_id: int | None = Query(default=None, ge=1),
    section_id: int | None = Query(default=None, ge=1),
    graduation_year: int | None = Query(default=None, ge=2000, le=2100),
    placement_status: str | None = Query(default=None, pattern="^(active|opted_out|debarred)$"),
    placed: bool | None = None,
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
    page: Pagination = Depends(pagination_params),
) -> list[StudentListItemOut]:
    """Paged student directory of the caller's institute (placement view)."""
    tenant_id = staff.tenant_id
    stmt = (
        select(User, StudentPlacementProfile)
        .outerjoin(
            StudentPlacementProfile,
            (StudentPlacementProfile.student_id == User.id) & (StudentPlacementProfile.tenant_id == tenant_id),
        )
        .where(User.tenant_id == tenant_id, User.role == UserRole.student, User.is_active.is_(True))
    )
    if section_id is not None:
        stmt = stmt.where(User.section_id == section_id)
    if department_id is not None:
        stmt = stmt.where(
            User.section_id.in_(
                select(Section.id).where(Section.department_id == department_id, Section.tenant_id == tenant_id)
            )
        )
    if graduation_year is not None:
        stmt = stmt.where(StudentPlacementProfile.graduation_year == graduation_year)
    if placement_status == "active":
        stmt = stmt.where(
            or_(StudentPlacementProfile.id.is_(None), StudentPlacementProfile.placement_status == "active")
        )
    elif placement_status is not None:
        stmt = stmt.where(StudentPlacementProfile.placement_status == placement_status)
    if placed is not None:
        clause = _placed_clause(tenant_id)
        stmt = stmt.where(clause if placed else ~clause)
    if q and q.strip():
        pattern = f"%{svc.escape_like(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                StudentPlacementProfile.roll_number.ilike(pattern, escape="\\"),
            )
        )
    rows = paginate_rows(db, response, stmt.order_by(User.full_name, User.id), page)
    users = [row[0] for row in rows]
    depts = svc.departments_for(db, tenant_id, users)
    placed_ids = svc.placed_student_ids(db, tenant_id, [u.id for u in users])
    return [
        StudentListItemOut(**_list_item(user, depts[user.id], profile, user.id in placed_ids))
        for user, profile in rows
    ]


@router.get("/students/{student_id}", response_model=StudentDetailOut)
def get_student(
    student_id: int,
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> StudentDetailOut:
    student = svc.require_student(db, student_id, staff.tenant_id)
    tenant_id = staff.tenant_id
    profile = svc.get_profile(db, tenant_id, student.id)
    dept = svc.departments_for(db, tenant_id, [student])[student.id]
    placed = student.id in svc.placed_student_ids(db, tenant_id, [student.id])
    metrics = svc.bulk_metrics(db, tenant_id, [student.id]).get(student.id, svc.empty_metrics())
    apps = db.execute(
        select(Application, Drive)
        .join(Drive, Application.drive_id == Drive.id)
        .where(
            Application.tenant_id == tenant_id,
            Drive.tenant_id == tenant_id,
            Application.student_id == student.id,
        )
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(200)
    ).all()
    return StudentDetailOut(
        **_list_item(student, dept, profile, placed),
        profile=_profile_out(profile),
        metrics=_metrics_out(metrics),
        applications=[
            ApplicationSummaryOut(
                application_id=a.id,
                drive_id=d.id,
                company_name=d.company_name,
                role_title=d.role_title,
                status=a.status,
                created_at=a.created_at,
            )
            for a, d in apps
        ],
    )


@router.put("/students/{student_id}/profile", response_model=StudentProfileOut)
def put_student_profile(
    student_id: int,
    payload: StudentProfileIn,
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> StudentProfileOut:
    student = svc.require_student(db, student_id, staff.tenant_id)
    return _profile_out(svc.upsert_profile(db, staff, student, payload))


@router.get("/me/profile", response_model=MyPlacementProfileOut)
def my_profile(
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
) -> MyPlacementProfileOut:
    tenant_id = student.tenant_id
    profile = svc.get_profile(db, tenant_id, student.id)
    dept_id, dept_name, _section = svc.departments_for(db, tenant_id, [student])[student.id]
    metrics = svc.bulk_metrics(db, tenant_id, [student.id]).get(student.id, svc.empty_metrics())
    return MyPlacementProfileOut(
        profile=_profile_out(profile),
        metrics=_metrics_out(metrics),
        department_id=dept_id,
        department_name=dept_name,
        placed=student.id in svc.placed_student_ids(db, tenant_id, [student.id]),
    )


# --- Drives: create draft, edit, settings, lifecycle (TPO) ---------------------
@router.post("/drives", response_model=DriveWithSettingsOut, status_code=status.HTTP_201_CREATED)
def create_draft_drive(
    payload: DraftDriveIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> DriveWithSettingsOut:
    """Create a drive as a DRAFT with its settings (hidden from students)."""
    drive, _cfg = svc.create_draft_drive(db, tpo, payload.model_dump(exclude={"settings"}), payload.settings)
    return DriveWithSettingsOut(drive=DriveOut.model_validate(drive), settings=_settings_out(db, drive))


@router.put("/drives/{drive_id}", response_model=DriveOut)
def update_drive(
    drive_id: int,
    payload: DriveFieldsIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> Drive:
    drive = svc.require_drive(db, drive_id, tpo.tenant_id)
    return svc.update_drive_fields(db, tpo, drive, payload.model_dump())


@router.get("/drives/{drive_id}/settings", response_model=DriveSettingsOut)
def get_drive_settings(
    drive_id: int,
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> DriveSettingsOut:
    drive = svc.require_drive(db, drive_id, staff.tenant_id)
    return _settings_out(db, drive)


@router.put("/drives/{drive_id}/settings", response_model=DriveSettingsOut)
def put_drive_settings(
    drive_id: int,
    payload: DriveSettingsIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> DriveSettingsOut:
    drive = svc.require_drive(db, drive_id, tpo.tenant_id)
    svc.upsert_settings(db, tpo, drive, payload)
    return _settings_out(db, drive)


@router.post("/drives/{drive_id}/lifecycle", response_model=DriveSettingsOut)
def change_drive_lifecycle(
    drive_id: int,
    payload: LifecycleIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> DriveSettingsOut:
    drive = svc.require_drive(db, drive_id, tpo.tenant_id)
    svc.transition_drive(db, tpo, drive, payload.to)
    return _settings_out(db, drive)


# --- Eligibility ---------------------------------------------------------------
@router.get("/drives/{drive_id}/eligibility", response_model=list[EligibilityRowOut])
def drive_eligibility(
    drive_id: int,
    response: Response,
    eligible_only: bool = False,
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
    page: Pagination = Depends(pagination_params),
) -> list[EligibilityRowOut]:
    """Eligibility v2 across the institute's active students (batched).

    Eligible first, then by CGPA. ``X-Eligible-Count`` = eligible students in
    the whole result (not just this page).
    """
    tenant_id = staff.tenant_id
    drive = svc.require_drive(db, drive_id, tenant_id)
    ctx = svc.drive_context(db, drive)
    students = list(
        db.scalars(
            select(User)
            .where(User.tenant_id == tenant_id, User.role == UserRole.student, User.is_active.is_(True))
            .order_by(User.id)
        )
    )
    metrics = svc.bulk_metrics(db, tenant_id, None)
    profiles = svc.profiles_for(db, tenant_id, None)
    depts = svc.departments_for(db, tenant_id, students)
    placed_ids = svc.placed_student_ids(db, tenant_id)
    applied_ids = set(
        db.scalars(
            select(Application.student_id).where(
                Application.tenant_id == tenant_id, Application.drive_id == drive.id
            )
        )
    )
    rows: list[EligibilityRowOut] = []
    for student in students:
        m = metrics.get(student.id) or svc.empty_metrics()
        profile = profiles.get(student.id)
        placed = student.id in placed_ids
        eligible, reasons = svc.evaluate(ctx, m, profile, depts[student.id], placed)
        if eligible_only and not eligible:
            continue
        rows.append(
            EligibilityRowOut(
                student_id=student.id,
                full_name=student.full_name,
                department_name=depts[student.id][1],
                graduation_year=profile.graduation_year if profile else None,
                eligible=eligible,
                cgpa=m["cgpa"],
                attendance=m["attendance"],
                verified_skills=len(m["verified_skills"]),
                verified_projects=m["verified_projects"],
                placed=placed,
                placement_status=profile.placement_status if profile else "active",
                applied=student.id in applied_ids,
                reasons=reasons,
            )
        )
    rows.sort(key=lambda r: (not r.eligible, -r.cgpa, r.student_id))
    response.headers["X-Eligible-Count"] = str(sum(1 for r in rows if r.eligible))
    return paginate_list(response, rows, page)


@router.get("/drives/{drive_id}/my-eligibility", response_model=MyEligibilityV2Out)
def my_eligibility(
    drive_id: int,
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
) -> MyEligibilityV2Out:
    drive = svc.require_drive(db, drive_id, student.tenant_id)
    ctx = svc.drive_context(db, drive)
    lifecycle = svc.effective_lifecycle(drive, ctx.cfg)
    if lifecycle == "draft":
        # Students never see drafts.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=svc.MSG_DRIVE_NOT_FOUND)
    eligible, reasons, metrics, _placed, _profile = svc.evaluate_student(db, ctx, student)
    already = (
        db.scalar(
            select(Application.id).where(
                Application.tenant_id == student.tenant_id,
                Application.drive_id == drive.id,
                Application.student_id == student.id,
            )
        )
        is not None
    )
    blockers = svc.apply_blockers(ctx)
    if already:
        blockers.append("You have already applied to this drive.")
    if not eligible:
        blockers.append("You do not meet every eligibility criterion.")
    cfg = ctx.cfg
    return MyEligibilityV2Out(
        drive_id=drive.id,
        eligible=eligible,
        lifecycle=lifecycle,
        window=svc.window_state(cfg),
        applications_open_at=svc._aware(cfg.applications_open_at) if cfg else None,
        applications_close_at=svc._aware(cfg.applications_close_at) if cfg else None,
        already_applied=already,
        can_apply=not blockers,
        blockers=blockers,
        cgpa=metrics["cgpa"],
        attendance=metrics["attendance"],
        verified_skills=len(metrics["verified_skills"]),
        verified_projects=metrics["verified_projects"],
        reasons=reasons,
    )


# --- Applications ----------------------------------------------------------------
@router.post("/drives/{drive_id}/apply", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def apply_to_drive(
    drive_id: int,
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
) -> Application:
    return svc.apply(db, student, drive_id)


@router.patch("/applications/{application_id}/status", response_model=ApplicationOut)
def set_application_status(
    application_id: int,
    payload: ApplicationStatusIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> Application:
    return svc.change_application_status(
        db, tpo, application_id, payload.status, payload.note, payload.expected_status
    )


@router.get("/applications/{application_id}/history", response_model=list[ApplicationEventOut])
def application_history(
    application_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ApplicationEventOut]:
    if user.role not in (UserRole.tpo, UserRole.admin, UserRole.student):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action",
        )
    application = svc.require_application(db, application_id, user.tenant_id)
    if user.role == UserRole.student and application.student_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=svc.MSG_APPLICATION_NOT_FOUND)
    return [
        ApplicationEventOut(
            id=e.id,
            from_status=e.from_status,
            to_status=e.to_status,
            actor_role=e.actor_role,
            note=e.note,
            created_at=e.created_at,
        )
        for e in svc.application_history(db, application)
    ]
