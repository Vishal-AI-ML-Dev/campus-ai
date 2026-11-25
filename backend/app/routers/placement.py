"""Placement routes - drives + the verified-data eligibility engine.

This is where the 'verified data moat' turns into placement value: the TPO
posts a drive with criteria, and the engine decides which students qualify
based ONLY on their VERIFIED profile:
  * CGPA (credit-weighted, from results)
  * attendance % ((present + late) / total)
  * count of VERIFIED skills matching the drive's required skills
  * count of VERIFIED project contributions

Every verdict is explainable (per-criterion pass/fail reasons), so the TPO can
trust and defend it.

Role model:
  * TPO posts drives, opens/closes them, and runs eligibility.
  * Any logged-in student can browse open drives and check their own
    eligibility breakdown.

(Applications + shortlisting are the next step; this step is drives +
eligibility.)

Mounted under the `/drives` prefix by `main.py`.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from pagination import Pagination, paginate, paginate_list, pagination_params
from app.services.notifications import notify
from app.models import (
    Application,
    ApplicationStatus,
    AttendanceRecord,
    AttendanceStatus,
    Drive,
    Internship,
    NotificationType,
    ProjectMember,
    Result,
    Skill,
    SkillStatus,
    Subject,
    User,
    UserRole,
)
from app.schemas import (
    ApplicantOut,
    ApplicationOut,
    ApplicationStatusUpdate,
    DriveCreate,
    DriveOut,
    DriveStatusUpdate,
    MyApplicationOut,
    MyEligibilityOut,
    StudentEligibilityOut,
)
from app.core.security import (
    get_current_tenant_id,
    get_current_user,
    require_roles,
)

router = APIRouter(prefix="/drives", tags=["placement"])

# Drives are the TPO's domain; admins may also list them.
tpo_only = require_roles(UserRole.tpo)
staff_only = require_roles(UserRole.tpo, UserRole.admin)
# Phase 5J: only students may apply to a drive.
student_only = require_roles(UserRole.student)


def no_recruiters(current_user: User = Depends(get_current_user)) -> User:
    """Block recruiter accounts from the student/staff drive routes.

    Recruiters only ever see drives linked to their own company, through
    the dedicated /recruiters/me/drives and /recruiters/me/candidates routes;
    the institute-wide browse/detail/eligibility routes would otherwise show
    other companies' drives (roles, packages, criteria).
    """
    if current_user.role == UserRole.recruiter:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recruiters can only view their linked drives via /recruiters/me/drives",
        )
    return current_user


# --- Eligibility engine (pure-ish helpers) ---------------------------------
def _parse_required_skills(raw: str | None) -> list[str]:
    """Split the comma-separated required-skills string into clean names."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _student_profile(db: Session, student_id: int) -> dict:
    """Compute one student's VERIFIED profile metrics used for eligibility."""
    # CGPA - credit-weighted average of grade points.
    gp_rows = db.execute(
        select(Result.grade_point, Subject.credits).join(
            Subject, Result.subject_id == Subject.id
        ).where(Result.student_id == student_id)
    ).all()
    total_credits = sum(credits for _, credits in gp_rows)
    cgpa = (
        round(sum(gp * credits for gp, credits in gp_rows) / total_credits, 2)
        if total_credits
        else 0.0
    )

    # Attendance % - (present + late) / total.
    statuses = list(
        db.scalars(
            select(AttendanceRecord.status).where(
                AttendanceRecord.student_id == student_id
            )
        )
    )
    total = len(statuses)
    attended = sum(
        1 for s in statuses if s in (AttendanceStatus.present, AttendanceStatus.late)
    )
    attendance = round(attended / total * 100, 1) if total else 0.0

    # Verified skills (names) and verified project-contribution count.
    verified_skills = list(
        db.scalars(
            select(Skill.name).where(
                Skill.student_id == student_id, Skill.status == SkillStatus.verified
            )
        )
    )
    verified_projects = (
        db.scalar(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.student_id == student_id,
                ProjectMember.status == SkillStatus.verified,
            )
        )
        or 0
    )

    # Verified internships / OJT (real work experience) as "Role @ Org".
    intern_rows = db.execute(
        select(Internship.role_title, Internship.organization).where(
            Internship.student_id == student_id,
            Internship.status == SkillStatus.verified,
        )
    ).all()
    verified_internships = [f"{role} @ {org}" for role, org in intern_rows]

    return {
        "cgpa": cgpa,
        "attendance": attendance,
        "verified_skills": verified_skills,
        "verified_projects": verified_projects,
        "verified_internships": verified_internships,
    }


def _evaluate(profile: dict, drive: Drive) -> tuple[bool, list[dict]]:
    """Check a profile against a drive's criteria. Returns (eligible, reasons)."""
    required = _parse_required_skills(drive.required_skills)
    verified_lower = {name.lower() for name in profile["verified_skills"]}
    missing = [name for name in required if name.lower() not in verified_lower]

    checks = [
        (
            "CGPA",
            profile["cgpa"] >= drive.min_cgpa,
            f"{profile['cgpa']} (need >= {drive.min_cgpa})",
        ),
        (
            "Attendance",
            profile["attendance"] >= drive.min_attendance,
            f"{profile['attendance']}% (need >= {drive.min_attendance}%)",
        ),
        (
            "Verified projects",
            profile["verified_projects"] >= drive.min_verified_projects,
            f"{profile['verified_projects']} (need >= {drive.min_verified_projects})",
        ),
        (
            "Required skills",
            len(missing) == 0,
            "all present" if not missing else f"missing: {', '.join(missing)}",
        ),
    ]
    reasons = [
        {"criterion": c, "passed": passed, "detail": detail}
        for c, passed, detail in checks
    ]
    eligible = all(passed for _, passed, _ in checks)
    return eligible, reasons


def _v2(user: User):
    """Phase 9.1 (decision P9-D6): the placement-management service when the
    caller's institute has it enabled, else None (legacy behaviour).

    Imported lazily: the service imports this module for ``_evaluate``.
    """
    from app.services import placement_management as pm

    return pm if pm.enabled_for(user) else None


def _hide_draft_from_student(db: Session, drive: Drive, user: User) -> None:
    """Phase 9.1: a draft drive is invisible to students (flagged institutes)."""
    pm = _v2(user)
    if pm is None or user.role != UserRole.student:
        return
    if pm.effective_lifecycle(drive, pm.get_settings(db, drive)) == "draft":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Drive not found"
        )


def _require_drive(
    db: Session, drive_id: int, tenant_id: int | None = None
) -> Drive:
    drive = db.get(Drive, drive_id)
    # When a tenant is given, a drive from another institute is hidden (404).
    if drive is None or (tenant_id is not None and drive.tenant_id != tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Drive not found"
        )
    return drive


# --- TPO: manage drives ----------------------------------------------------
@router.post("", response_model=DriveOut, status_code=status.HTTP_201_CREATED)
def create_drive(
    payload: DriveCreate,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> Drive:
    """Post a new placement drive with its eligibility criteria."""
    drive = Drive(
        tenant_id=tpo.tenant_id,
        company_name=payload.company_name,
        role_title=payload.role_title,
        description=payload.description,
        location=payload.location,
        package_lpa=payload.package_lpa,
        min_cgpa=payload.min_cgpa,
        min_attendance=payload.min_attendance,
        min_verified_projects=payload.min_verified_projects,
        required_skills=payload.required_skills,
        deadline=payload.deadline,
        created_by_id=tpo.id,
    )
    db.add(drive)
    db.commit()
    db.refresh(drive)
    return drive


@router.get("", response_model=list[DriveOut], dependencies=[Depends(staff_only)])
def list_drives(
    response: Response,
    open_only: bool = False,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    page: Pagination = Depends(pagination_params),
) -> list[Drive]:
    """List all drives (TPO/admin). Optionally only the open ones.

    Tenant-scoped: staff only see their own institute's drives.
    """
    stmt = select(Drive).where(Drive.tenant_id == tenant_id)
    if open_only:
        stmt = stmt.where(Drive.is_open.is_(True))
    return list(paginate(db, response, stmt.order_by(Drive.created_at.desc()), page))


@router.get("/open", response_model=list[DriveOut])
def open_drives(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(no_recruiters),
    page: Pagination = Depends(pagination_params),
) -> list[Drive]:
    """Browse currently open drives (any logged-in user).

    Tenant-scoped: a student only sees drives from their own institute.
    """
    stmt = (
        select(Drive)
        .where(
            Drive.is_open.is_(True),
            Drive.tenant_id == user.tenant_id,
        )
        .order_by(Drive.created_at.desc())
    )
    return list(paginate(db, response, stmt, page))


@router.get("/{drive_id}", response_model=DriveOut)
def get_drive(
    drive_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(no_recruiters),
) -> Drive:
    """Fetch a single drive by id (only within your own institute)."""
    drive = _require_drive(db, drive_id, user.tenant_id)
    _hide_draft_from_student(db, drive, user)
    return drive


@router.patch("/{drive_id}/status", response_model=DriveOut)
def set_drive_status(
    drive_id: int,
    payload: DriveStatusUpdate,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> Drive:
    """Open or close a drive."""
    drive = _require_drive(db, drive_id, tpo.tenant_id)
    pm = _v2(tpo)
    if pm is not None:
        # Flagged institute: open/close follows the drive lifecycle rules.
        pm.legacy_open_close(db, tpo, drive, payload.is_open)
        db.refresh(drive)
        return drive
    drive.is_open = payload.is_open
    db.commit()
    db.refresh(drive)
    return drive


# --- Eligibility ------------------------------------------------------------
@router.get(
    "/{drive_id}/eligibility",
    response_model=list[StudentEligibilityOut],
    dependencies=[Depends(tpo_only)],
)
def drive_eligibility(
    drive_id: int,
    response: Response,
    eligible_only: bool = False,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    page: Pagination = Depends(pagination_params),
    current_user: User = Depends(get_current_user),
) -> list[StudentEligibilityOut]:
    """Run the eligibility engine across all active students for this drive.

    Returns each student's verdict with explainable per-criterion reasons,
    eligible students first (then by CGPA). Tenant-scoped: only this
    institute's students are evaluated.
    """
    drive = _require_drive(db, drive_id, tenant_id)
    pm = _v2(current_user)
    if pm is not None:
        # Flagged institute: the same v2 verdicts the apply route enforces.
        return paginate_list(response, _v2_eligibility(pm, db, drive, eligible_only), page)
    students = list(
        db.scalars(
            select(User).where(
                User.role == UserRole.student,
                User.is_active.is_(True),
                User.tenant_id == tenant_id,
            )
        )
    )
    results: list[StudentEligibilityOut] = []
    for student in students:
        profile = _student_profile(db, student.id)
        eligible, reasons = _evaluate(profile, drive)
        if eligible_only and not eligible:
            continue
        results.append(
            StudentEligibilityOut(
                student_id=student.id,
                full_name=student.full_name,
                eligible=eligible,
                cgpa=profile["cgpa"],
                attendance=profile["attendance"],
                verified_skills=len(profile["verified_skills"]),
                verified_projects=profile["verified_projects"],
                reasons=reasons,
            )
        )
    results.sort(key=lambda r: (not r.eligible, -r.cgpa))
    return paginate_list(response, results, page)


def _v2_eligibility(pm, db: Session, drive: Drive, eligible_only: bool) -> list[StudentEligibilityOut]:
    """Legacy response shape filled from eligibility v2 (batched queries)."""
    tenant_id = drive.tenant_id
    ctx = pm.drive_context(db, drive)
    students = list(
        db.scalars(
            select(User).where(
                User.role == UserRole.student,
                User.is_active.is_(True),
                User.tenant_id == tenant_id,
            )
        )
    )
    metrics = pm.bulk_metrics(db, tenant_id, None)
    profiles = pm.profiles_for(db, tenant_id, None)
    depts = pm.departments_for(db, tenant_id, students)
    placed_ids = pm.placed_student_ids(db, tenant_id)
    results: list[StudentEligibilityOut] = []
    for student in students:
        m = metrics.get(student.id) or pm.empty_metrics()
        eligible, reasons = pm.evaluate(
            ctx, m, profiles.get(student.id), depts[student.id], student.id in placed_ids
        )
        if eligible_only and not eligible:
            continue
        results.append(
            StudentEligibilityOut(
                student_id=student.id,
                full_name=student.full_name,
                eligible=eligible,
                cgpa=m["cgpa"],
                attendance=m["attendance"],
                verified_skills=len(m["verified_skills"]),
                verified_projects=m["verified_projects"],
                reasons=reasons,
            )
        )
    results.sort(key=lambda r: (not r.eligible, -r.cgpa))
    return results


@router.get("/{drive_id}/my-eligibility", response_model=MyEligibilityOut)
def my_eligibility(
    drive_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(no_recruiters),
) -> MyEligibilityOut:
    """Check YOUR own eligibility for a drive, with per-criterion reasons."""
    drive = _require_drive(db, drive_id, current_user.tenant_id)
    _hide_draft_from_student(db, drive, current_user)
    pm = _v2(current_user)
    if pm is not None:
        eligible, reasons, m, _placed, _profile = pm.evaluate_student(
            db, pm.drive_context(db, drive), current_user
        )
        return MyEligibilityOut(
            drive_id=drive_id,
            eligible=eligible,
            cgpa=m["cgpa"],
            attendance=m["attendance"],
            verified_skills=len(m["verified_skills"]),
            verified_projects=m["verified_projects"],
            reasons=reasons,
        )
    profile = _student_profile(db, current_user.id)
    eligible, reasons = _evaluate(profile, drive)
    return MyEligibilityOut(
        drive_id=drive_id,
        eligible=eligible,
        cgpa=profile["cgpa"],
        attendance=profile["attendance"],
        verified_skills=len(profile["verified_skills"]),
        verified_projects=profile["verified_projects"],
        reasons=reasons,
    )


# --- Applications + shortlisting -------------------------------------------
@router.post(
    "/{drive_id}/apply",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
)
def apply_to_drive(
    drive_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(student_only),
) -> Application:
    """Apply to a drive. Only open drives and eligible students are allowed.

    Eligibility is re-checked here against the student's VERIFIED data, so an
    application can only exist if the moat criteria are genuinely met.
    """
    pm = _v2(current_user)
    if pm is not None:
        # Flagged institute (P9-D6): lifecycle, window and v2 criteria apply.
        return pm.apply(db, current_user, drive_id)
    drive = _require_drive(db, drive_id, current_user.tenant_id)
    if not drive.is_open:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This drive is closed for applications.",
        )

    existing = db.scalar(
        select(Application).where(
            Application.drive_id == drive_id,
            Application.student_id == current_user.id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already applied to this drive.",
        )

    profile = _student_profile(db, current_user.id)
    eligible, reasons = _evaluate(profile, drive)
    if not eligible:
        unmet = [r["detail"] for r in reasons if not r["passed"]]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not eligible for this drive: " + "; ".join(unmet),
        )

    application = Application(
        drive_id=drive_id,
        student_id=current_user.id,
        tenant_id=drive.tenant_id,
    )
    db.add(application)
    try:
        db.commit()
    except IntegrityError:
        # Phase 9.1 hardening: two concurrent applies race past the check
        # above; the unique constraint wins and the loser gets 409, not 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already applied to this drive.",
        )
    db.refresh(application)
    return application


@router.get("/me/applications", response_model=list[MyApplicationOut])
def my_applications(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(no_recruiters),
    page: Pagination = Depends(pagination_params),
) -> list[Application]:
    """List the logged-in student's own applications (with drive context)."""
    stmt = (
        select(Application)
        .where(
            Application.tenant_id == current_user.tenant_id,
            Application.student_id == current_user.id,
        )
        .order_by(Application.created_at.desc())
    )
    return list(paginate(db, response, stmt, page))


@router.get(
    "/{drive_id}/applications",
    response_model=list[ApplicantOut],
    dependencies=[Depends(tpo_only)],
)
def drive_applications(
    drive_id: int,
    response: Response,
    status_filter: ApplicationStatus | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    page: Pagination = Depends(pagination_params),
    current_user: User = Depends(get_current_user),
) -> list[ApplicantOut]:
    """List applicants on a drive (TPO), each with a verified-data snapshot.

    Selected/shortlisted candidates are surfaced first, then by CGPA.
    """
    drive = _require_drive(db, drive_id, tenant_id)
    pm = _v2(current_user)
    stmt = select(Application).where(
        Application.tenant_id == tenant_id,
        Application.drive_id == drive_id,
    )
    if status_filter is not None:
        stmt = stmt.where(Application.status == status_filter)
    applications = list(db.scalars(stmt))

    # Rank: selected, shortlisted, applied, rejected - then by CGPA desc.
    rank = {
        ApplicationStatus.selected: 0,
        ApplicationStatus.shortlisted: 1,
        ApplicationStatus.applied: 2,
        ApplicationStatus.rejected: 3,
        ApplicationStatus.withdrawn: 4,
    }
    # Phase 9.2: flagged institutes see the CURRENT eligibility v2 verdict
    # (same engine as apply); the response shape is unchanged.
    # Phase 9 closure L4: one tenant-scoped query for every applicant instead of
    # one `get_scoped_user` per row (same rule: id AND tenant must match, else
    # the row is skipped exactly as before).
    student_ids = list({a.student_id for a in applications})
    students = (
        {
            u.id: u
            for u in db.scalars(
                select(User).where(User.id.in_(student_ids), User.tenant_id == drive.tenant_id)
            )
        }
        if student_ids
        else {}
    )
    v2 = _V2Eligibility(pm, db, drive, list(students.values())) if pm else None
    out: list[ApplicantOut] = []
    for application in applications:
        student = students.get(application.student_id)
        if student is None:
            continue
        if v2 is not None:
            profile, eligible = v2.verdict(student)
        else:
            profile = _student_profile(db, student.id)
            eligible, _reasons = _evaluate(profile, drive)
        out.append(
            ApplicantOut(
                application_id=application.id,
                student_id=student.id,
                full_name=student.full_name,
                status=application.status,
                eligible=eligible,
                cgpa=profile["cgpa"],
                attendance=profile["attendance"],
                verified_skills=len(profile["verified_skills"]),
                verified_projects=profile["verified_projects"],
                note=application.note,
                contact_revealed=application.contact_revealed,
            )
        )
    out.sort(key=lambda a: (rank.get(a.status, 9), -a.cgpa))
    return paginate_list(response, out, page)


class _V2Eligibility:
    """Batched eligibility v2 for a set of applicants (Phase 9.2)."""

    def __init__(self, pm, db: Session, drive: Drive, students: list[User]) -> None:
        tenant_id = drive.tenant_id
        student_ids = [s.id for s in students]
        self.pm = pm
        self.ctx = pm.drive_context(db, drive)
        self.metrics = pm.bulk_metrics(db, tenant_id, student_ids)
        self.profiles = pm.profiles_for(db, tenant_id, student_ids)
        self.placed = pm.placed_student_ids(db, tenant_id, student_ids)
        # Phase 9 closure L4: departments for all applicants in one query
        # (was one query per applicant).
        self.depts = pm.departments_for(db, tenant_id, students)

    def verdict(self, student: User) -> tuple[dict, bool]:
        m = self.metrics.get(student.id) or self.pm.empty_metrics()
        dept = self.depts.get(student.id, (None, None, None))
        ok, _r = self.pm.evaluate(
            self.ctx, m, self.profiles.get(student.id), dept, student.id in self.placed
        )
        return m, ok


@router.patch(
    "/applications/{application_id}/status", response_model=ApplicationOut
)
def set_application_status(
    application_id: int,
    payload: ApplicationStatusUpdate,
    db: Session = Depends(get_db),
    tpo: User = Depends(tpo_only),
) -> Application:
    """TPO decision on an application: shortlist, select, or reject."""
    pm = _v2(tpo)
    if pm is not None:
        # Flagged institute (P9-D6): transition rules, history and audit.
        return pm.change_application_status(
            db, tpo, application_id, payload.status, payload.note
        )
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application not found"
        )
    # Tenant guard: a TPO can only decide applications on their own drives.
    drive = db.get(Drive, application.drive_id)
    if drive is None or drive.tenant_id != tpo.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Application not found"
        )
    # Phase 9.2 (P9-D8): `withdrawn` is student-only and final on every path.
    from app.services.placement_management import guard_staff_status_change

    guard_staff_status_change(application.status, payload.status)
    if payload.status == ApplicationStatus.applied:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot move an application back to 'applied'.",
        )
    application.status = payload.status
    application.note = payload.note
    application.decided_by_id = tpo.id
    application.decided_at = datetime.now(timezone.utc)
    # Notify the student of the TPO decision (same transaction).
    notify(
        db,
        tenant_id=tpo.tenant_id,
        user_id=application.student_id,
        type=NotificationType.application_status.value,
        title=f"Application update: {payload.status.value}",
        body=payload.note or None,
        link="/placement/applications",
        actor_id=tpo.id,
    )
    db.commit()
    db.refresh(application)
    return application
