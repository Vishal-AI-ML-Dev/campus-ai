"""Placement dashboard, analytics, CSV export, interview preparation (9.3/9.4).

Same gate as ``/placement`` (404 when off; recruiters 403). TPO and admin read
the dashboard, analytics and exports; teachers and students get 403. Students
read their own drive preparation. Exports are audited (kind, filter NAMES and
row count only) and every cell is formula-injection safe.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import apply_tenant_guc, require_roles
from app.models import ApplicationStatus, User, UserRole
from app.routers.audit import record_audit
from app.routers.placement_management import require_placement_management
from app.services import placement_insights as svc
from app.services import placement_management as pm

router = APIRouter(
    prefix="/placement",
    tags=["placement-insights"],
    dependencies=[Depends(apply_tenant_guc), Depends(require_placement_management)],
)

staff_only = require_roles(UserRole.tpo, UserRole.admin)
student_only = require_roles(UserRole.student)
_YEAR = "^20[0-9]{2}-[0-9]{2}$"


def _filters(
    academic_year: str | None = Query(default=None, pattern=_YEAR),
    department_id: int | None = Query(default=None, ge=1),
    graduation_year: int | None = Query(default=None, ge=2000, le=2100),
    company: str | None = Query(default=None, min_length=1, max_length=200),
    drive_id: int | None = Query(default=None, ge=1),
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
) -> svc.Filters:
    return svc.Filters(
        academic_year=academic_year,
        department_id=department_id,
        graduation_year=graduation_year,
        company=company.strip() if company else None,
        drive_id=drive_id,
        status=status_filter,
    )


def _check_refs(db: Session, staff: User, f: svc.Filters) -> None:
    """A department / drive id of another institute is 404 (no oracle)."""
    if f.drive_id is not None:
        pm.require_drive(db, f.drive_id, staff.tenant_id)
    if f.department_id is not None:
        known = {d["id"] for d in svc.departments_for_filter(db, staff.tenant_id)}
        if f.department_id not in known:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")


@router.get("/dashboard")
def tpo_dashboard(
    academic_year: str | None = Query(default=None, pattern=_YEAR),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> dict:
    """TPO home: drives, deadlines, applications, students, campaigns, actions."""
    return svc.dashboard(db, staff.tenant_id, academic_year)


@router.get("/analytics")
def placement_analytics_v2(
    f: svc.Filters = Depends(_filters),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> dict:
    _check_refs(db, staff, f)
    return svc.analytics(db, staff.tenant_id, f)


@router.get("/analytics/export")
def placement_export(
    kind: str = Query(pattern="^(summary|companies|departments|drives|applications)$"),
    f: svc.Filters = Depends(_filters),
    db: Session = Depends(get_db),
    staff: User = Depends(staff_only),
) -> Response:
    _check_refs(db, staff, f)
    header, rows = svc.export_rows(db, staff.tenant_id, f, kind)
    body = svc.to_csv(header, rows)
    record_audit(
        db,
        staff,
        "placement.export",
        f"Placement export '{kind}': {len(rows)} rows; filters: {', '.join(f.names()) or 'none'}",
        target_type="tenant",
        target_id=staff.tenant_id,
    )
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = re.sub(r"[^a-z0-9_.-]", "", f"placement_{kind}_{stamp}.csv")
    return Response(
        content="﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/analytics/options")
def analytics_options(db: Session = Depends(get_db), staff: User = Depends(staff_only)) -> dict:
    """Filter choices: departments, companies, drives, academic years."""
    from sqlalchemy import select

    from app.models import Drive, PlacementDriveSettings

    drives = list(db.scalars(select(Drive).where(Drive.tenant_id == staff.tenant_id).order_by(Drive.company_name)))
    years = sorted(
        {
            y
            for y in db.scalars(
                select(PlacementDriveSettings.academic_year).where(
                    PlacementDriveSettings.tenant_id == staff.tenant_id,
                    PlacementDriveSettings.academic_year.is_not(None),
                )
            )
        },
        reverse=True,
    )
    return dict(
        departments=svc.departments_for_filter(db, staff.tenant_id),
        companies=sorted({d.company_name.strip() for d in drives}, key=str.lower),
        drives=[dict(id=d.id, label=f"{d.company_name} — {d.role_title}") for d in drives],
        academic_years=years,
    )


@router.get("/drives/{drive_id}/preparation")
def drive_preparation(drive_id: int, db: Session = Depends(get_db), student: User = Depends(student_only)) -> dict:
    """Interview preparation for one drive, for the student (own data only)."""
    drive = pm.require_drive(db, drive_id, student.tenant_id)
    if pm.effective_lifecycle(drive, pm.get_settings(db, drive)) == "draft":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=pm.MSG_DRIVE_NOT_FOUND)
    return svc.preparation(db, student, drive)
