"""Placement management v2 routes (Phase 9.1) - mounted under ``/placement``.

Feature-flagged: every route 404s unless PLACEMENT_MANAGEMENT_ENABLED is on
AND the caller's institute is in PLACEMENT_MANAGEMENT_TENANT_IDS (empty list =
nobody). Recruiters get 403 on every route here. Objects of another institute
are 404. Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §5.

Roles: TPO writes drives/settings/lifecycle/status; TPO and admin read and
write student placement profiles; students read their own profile and
eligibility, apply, and read their own application history. Teachers have no
access to anything here except the status probe (Phase 9.3 adds campaigns).

Phase 9.2 adds: status probe, staff drive list, v2 applicant list, bulk status
(dry-run preview + per-record compare-and-set), student withdrawal (P9-D8),
student opportunities and applications, academic spreadsheet import.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import exists, func, or_, select
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
    ApplicantV2Out,
    BulkStatusIn,
    BulkStatusOut,
    ImportCommitOut,
    ImportPreviewOut,
    ImportRowOut,
    MyApplicationV2Out,
    PlacementStatusOut,
    StaffDriveListItemOut,
    StudentDriveOut,
    WithdrawIn,
)
from app.services import placement_import as importer
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


# =============================================================================
# Phase 9.2
# =============================================================================
@router.get("/status", response_model=PlacementStatusOut)
def placement_status(user: User = Depends(get_current_user)) -> PlacementStatusOut:
    """Frontend probe. Reaching it means the gate is open for this institute."""
    return PlacementStatusOut(enabled=True, role=user.role.value)


_STATUS_RANK = {
    ApplicationStatus.selected: 0,
    ApplicationStatus.shortlisted: 1,
    ApplicationStatus.applied: 2,
    ApplicationStatus.rejected: 3,
    ApplicationStatus.withdrawn: 4,
}


@router.get("/drives", response_model=list[StaffDriveListItemOut])
def list_drives_v2(
    response: Response,
    lifecycle: str | None = Query(default=None, pattern="^(draft|published|closed|completed|cancelled)$"),
    academic_year: str | None = Query(default=None, max_length=9),
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
    page: Pagination = Depends(pagination_params),
) -> list[StaffDriveListItemOut]:
    """All drives of the institute with lifecycle and application counts."""
    tenant_id = staff.tenant_id
    stmt = select(Drive).where(Drive.tenant_id == tenant_id)
    if q and q.strip():
        pattern = f"%{svc.escape_like(q.strip().lower())}%"
        stmt = stmt.where(
            or_(Drive.company_name.ilike(pattern, escape="\\"), Drive.role_title.ilike(pattern, escape="\\"))
        )
    drives = list(db.scalars(stmt.order_by(Drive.created_at.desc(), Drive.id.desc())))
    ctxs = svc.drive_contexts(db, drives)
    if lifecycle is not None:
        drives = [d for d in drives if svc.effective_lifecycle(d, ctxs[d.id].cfg) == lifecycle]
    if academic_year:
        drives = [d for d in drives if ctxs[d.id].cfg is not None and ctxs[d.id].cfg.academic_year == academic_year]
    counts: dict[int, dict[str, int]] = {d.id: {} for d in drives}
    if drives:
        for drive_id, st, n in db.execute(
            select(Application.drive_id, Application.status, func.count(Application.id))
            .where(Application.tenant_id == tenant_id, Application.drive_id.in_([d.id for d in drives]))
            .group_by(Application.drive_id, Application.status)
        ).all():
            counts[drive_id][st.value] = n
    rows = []
    for d in drives:
        cfg = ctxs[d.id].cfg
        rows.append(
            StaffDriveListItemOut(
                id=d.id,
                company_name=d.company_name,
                role_title=d.role_title,
                location=d.location,
                package_lpa=d.package_lpa,
                is_open=d.is_open,
                deadline=d.deadline,
                created_at=d.created_at,
                lifecycle=svc.effective_lifecycle(d, cfg),
                configured=cfg is not None,
                academic_year=cfg.academic_year if cfg else None,
                applications_open_at=svc._aware(cfg.applications_open_at) if cfg else None,
                applications_close_at=svc._aware(cfg.applications_close_at) if cfg else None,
                window=svc.window_state(cfg),
                applications_total=sum(counts[d.id].values()),
                status_counts=counts[d.id],
            )
        )
    return paginate_list(response, rows, page)


@router.get("/drives/{drive_id}/applicants", response_model=list[ApplicantV2Out])
def drive_applicants_v2(
    drive_id: int,
    response: Response,
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    eligible: bool | None = None,
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
    page: Pagination = Depends(pagination_params),
) -> list[ApplicantV2Out]:
    """Applicants with CURRENT eligibility v2 (batched; constant queries).

    ``X-Status-Counts`` = JSON-free "status:n" pairs for the whole drive."""
    tenant_id = staff.tenant_id
    drive = svc.require_drive(db, drive_id, tenant_id)
    ctx = svc.drive_context(db, drive)
    rows = db.execute(
        select(Application, User)
        .join(User, Application.student_id == User.id)
        .where(
            Application.tenant_id == tenant_id,
            Application.drive_id == drive.id,
            User.tenant_id == tenant_id,
        )
    ).all()
    all_counts: dict[str, int] = {}
    for a, _u in rows:
        all_counts[a.status.value] = all_counts.get(a.status.value, 0) + 1
    ids = [u.id for _a, u in rows]
    metrics = svc.bulk_metrics(db, tenant_id, ids)
    profiles = svc.profiles_for(db, tenant_id, ids)
    depts = svc.departments_for(db, tenant_id, [u for _a, u in rows])
    placed_ids = svc.placed_student_ids(db, tenant_id, ids)
    offers = svc.offer_statuses(db, tenant_id, [a.id for a, _u in rows])
    needle = q.strip().lower() if q and q.strip() else None
    out: list[ApplicantV2Out] = []
    for a, u in rows:
        if status_filter is not None and a.status != status_filter:
            continue
        profile = profiles.get(u.id)
        if needle and not (
            needle in u.full_name.lower()
            or needle in u.email.lower()
            or (profile is not None and profile.roll_number and needle in profile.roll_number.lower())
        ):
            continue
        m = metrics.get(u.id) or svc.empty_metrics()
        ok, reasons = svc.evaluate(ctx, m, profile, depts[u.id], u.id in placed_ids)
        if eligible is not None and ok != eligible:
            continue
        out.append(
            ApplicantV2Out(
                application_id=a.id,
                student_id=u.id,
                full_name=u.full_name,
                roll_number=profile.roll_number if profile else None,
                department_name=depts[u.id][1],
                graduation_year=profile.graduation_year if profile else None,
                status=a.status,
                eligible=ok,
                failed_criteria=svc.failed_criteria(reasons),
                cgpa=m["cgpa"],
                attendance=m["attendance"],
                verified_skills=len(m["verified_skills"]),
                verified_projects=m["verified_projects"],
                placed=u.id in placed_ids,
                note=a.note,
                offer_status=offers.get(a.id),
                contact_revealed=a.contact_revealed,
                applied_at=a.created_at,
                decided_at=a.decided_at,
            )
        )
    out.sort(key=lambda r: (_STATUS_RANK.get(r.status, 9), -r.cgpa, r.application_id))
    response.headers["X-Status-Counts"] = ",".join(f"{k}:{v}" for k, v in sorted(all_counts.items()))
    return paginate_list(response, out, page)


@router.post("/drives/{drive_id}/applications/bulk-status", response_model=BulkStatusOut)
def bulk_status(
    drive_id: int,
    payload: BulkStatusIn,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> BulkStatusOut:
    """Bulk shortlist / reject (<= 200). ``dry_run`` (default true) previews."""
    drive = svc.require_drive(db, drive_id, tpo.tenant_id)
    to_status = ApplicationStatus(payload.to_status)
    results = svc.bulk_change_status(
        db,
        tpo,
        drive,
        to_status,
        [(i.application_id, i.expected_status) for i in payload.items],
        payload.note,
        payload.dry_run,
    )
    ok = sum(1 for r in results if r["ok"])
    return BulkStatusOut(
        dry_run=payload.dry_run, to_status=to_status, succeeded=ok, failed=len(results) - ok, results=results
    )


@router.post("/applications/{application_id}/withdraw", response_model=ApplicationOut)
def withdraw(
    application_id: int,
    payload: WithdrawIn | None = None,
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
) -> Application:
    """P9-D8: withdraw your own application (only applied/shortlisted; final)."""
    return svc.withdraw_application(
        db, student, application_id, payload.expected_status if payload is not None else None
    )


def _student_visible(drive: Drive, cfg) -> bool:
    return svc.effective_lifecycle(drive, cfg) != "draft"


@router.get("/me/drives", response_model=list[StudentDriveOut])
def my_drives(
    response: Response,
    scope: str = Query(default="open", pattern="^(open|all)$"),
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
    page: Pagination = Depends(pagination_params),
) -> list[StudentDriveOut]:
    """Opportunities for the student with their own eligibility per drive.

    ``open`` = published drives; ``all`` = every non-draft drive."""
    tenant_id = student.tenant_id
    drives = list(
        db.scalars(select(Drive).where(Drive.tenant_id == tenant_id).order_by(Drive.created_at.desc(), Drive.id.desc()))
    )
    ctxs = svc.drive_contexts(db, drives)
    visible = [
        d
        for d in drives
        if _student_visible(d, ctxs[d.id].cfg)
        and (scope == "all" or svc.effective_lifecycle(d, ctxs[d.id].cfg) == "published")
    ]
    metrics = svc.bulk_metrics(db, tenant_id, [student.id]).get(student.id, svc.empty_metrics())
    profile = svc.get_profile(db, tenant_id, student.id)
    dept = svc.departments_for(db, tenant_id, [student])[student.id]
    placed = student.id in svc.placed_student_ids(db, tenant_id, [student.id])
    mine = {
        drive_id: st
        for drive_id, st in db.execute(
            select(Application.drive_id, Application.status).where(
                Application.tenant_id == tenant_id, Application.student_id == student.id
            )
        ).all()
    }
    out: list[StudentDriveOut] = []
    for d in visible:
        ctx = ctxs[d.id]
        ok, reasons = svc.evaluate(ctx, metrics, profile, dept, placed)
        applied = d.id in mine
        cfg = ctx.cfg
        out.append(
            StudentDriveOut(
                id=d.id,
                company_name=d.company_name,
                role_title=d.role_title,
                description=d.description,
                location=d.location,
                package_lpa=d.package_lpa,
                required_skills=d.required_skills,
                lifecycle=svc.effective_lifecycle(d, cfg),
                window=svc.window_state(cfg),
                applications_open_at=svc._aware(cfg.applications_open_at) if cfg else None,
                applications_close_at=svc._aware(cfg.applications_close_at) if cfg else None,
                eligible=ok,
                failed_criteria=svc.failed_criteria(reasons),
                already_applied=applied,
                application_status=mine.get(d.id),
                can_apply=ok and not applied and not svc.apply_blockers(ctx),
            )
        )
    return paginate_list(response, out, page)


@router.get("/me/applications", response_model=list[MyApplicationV2Out])
def my_applications_v2(
    response: Response,
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
    page: Pagination = Depends(pagination_params),
) -> list[MyApplicationV2Out]:
    """The student's own applications with withdraw eligibility."""
    tenant_id = student.tenant_id
    rows = db.execute(
        select(Application, Drive)
        .join(Drive, Application.drive_id == Drive.id)
        .where(
            Application.tenant_id == tenant_id,
            Drive.tenant_id == tenant_id,
            Application.student_id == student.id,
        )
        .order_by(Application.created_at.desc(), Application.id.desc())
    ).all()
    ctxs = svc.drive_contexts(db, [d for _a, d in rows])
    offers = svc.offer_statuses(db, tenant_id, [a.id for a, _d in rows])
    placed = student.id in svc.placed_student_ids(db, tenant_id, [student.id])
    out = []
    for a, d in rows:
        lifecycle = svc.effective_lifecycle(d, ctxs[d.id].cfg)
        blocker = svc.withdraw_blocker(a, offers.get(a.id), placed, lifecycle)
        out.append(
            MyApplicationV2Out(
                application_id=a.id,
                drive_id=d.id,
                company_name=d.company_name,
                role_title=d.role_title,
                lifecycle=lifecycle,
                status=a.status,
                note=a.note,
                offer_status=offers.get(a.id),
                applied_at=a.created_at,
                decided_at=a.decided_at,
                can_withdraw=blocker is None,
                withdraw_blocker=blocker,
            )
        )
    return paginate_list(response, out, page)


# --- Academic spreadsheet import (TPO/admin) ------------------------------------
@router.get("/import/template")
def import_template(_staff: User = Depends(staff_only)) -> Response:
    return Response(
        content=importer.TEMPLATE_CSV,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=placement_academic_template.csv"},
    )


def _preview_out(plan) -> ImportPreviewOut:
    return ImportPreviewOut(
        file_sha256=plan.file_sha256,
        file_type=plan.file_type,
        total_rows=len(plan.rows),
        valid_rows=len(plan.rows) - plan.count("error"),
        invalid_rows=plan.count("error"),
        to_create=plan.count("create"),
        to_update=plan.count("update"),
        unchanged=plan.count("unchanged"),
        ignored_columns=plan.ignored_columns,
        rows=[ImportRowOut(**r.__dict__) for r in plan.rows],
    )


@router.post("/import/preview", response_model=ImportPreviewOut)
async def import_preview(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> ImportPreviewOut:
    """Parse and validate; nothing is written."""
    data, kind = await importer.read_upload(file)
    return _preview_out(importer.build_plan(db, staff.tenant_id, data, kind))


@router.post("/import/commit", response_model=ImportCommitOut)
async def import_commit(
    file: UploadFile = File(...),
    file_sha256: str = Form(..., min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> ImportCommitOut:
    """Re-validate the previewed file and save every valid row at once."""
    data, kind = await importer.read_upload(file)
    if importer.sha256(data) != file_sha256:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This file is not the one you previewed. Preview it again before importing.",
        )
    plan = importer.build_plan(db, staff.tenant_id, data, kind)
    return ImportCommitOut(**importer.commit_plan(db, staff, plan))
