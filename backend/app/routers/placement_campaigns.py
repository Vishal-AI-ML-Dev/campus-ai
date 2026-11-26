"""Faculty interview-preparation campaigns (Phase 9.3) - ``/placement/campaigns``.

Same gate as the rest of ``/placement`` (PLACEMENT_MANAGEMENT_ENABLED + the
institute allow-list; 404 when off; recruiters 403). Roles: teacher (own
campaigns, own sections), tpo and admin (all campaigns of the institute),
student (own assignments via ``/placement/me/campaigns``).

Privacy (P9-D7, option A): progress responses carry completion status only.
No route here reads or returns a score, transcript, recording, AI feedback,
report or answer; the service reads only completion columns.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import apply_tenant_guc, require_roles
from app.models import ApplicationStatus, PlacementCampaign, PlacementCampaignStudent, Section, User, UserRole
from app.routers.placement_management import require_placement_management
from app.schemas.placement_campaigns import (
    AssignIn,
    AssignOut,
    CampaignIn,
    CampaignOut,
    CampaignStatusIn,
    CandidateOut,
    MyCampaignOut,
    ProgressRowOut,
    TeacherSectionOut,
)
from app.services import placement_campaigns as svc
from pagination import Pagination, paginate_list, paginate_rows, pagination_params

router = APIRouter(
    prefix="/placement",
    tags=["placement-campaigns"],
    dependencies=[Depends(apply_tenant_guc), Depends(require_placement_management)],
)

managers = require_roles(UserRole.teacher, UserRole.tpo, UserRole.admin)
student_only = require_roles(UserRole.student)


def _out(c: PlacementCampaign, user: User, counts: tuple[int, int, int], labels: dict, names: dict) -> CampaignOut:
    assigned, on_time, late = counts
    return CampaignOut(
        id=c.id,
        title=c.title,
        description=c.description,
        status=c.status,
        interview_type=c.interview_type,
        target_role=c.target_role,
        drive_id=c.drive_id,
        drive_label=labels.get(c.drive_id) if c.drive_id else None,
        starts_at=svc._utc(c.starts_at),
        due_at=svc._utc(c.due_at),
        created_by_role=c.created_by_role,
        created_by_name=names.get(c.created_by_id),
        is_mine=c.created_by_id == user.id,
        assigned=assigned,
        completed=on_time,
        completed_late=late,
        created_at=c.created_at,
    )


def _render(db: Session, user: User, campaigns: list[PlacementCampaign]) -> list[CampaignOut]:
    counts = svc.counts_for(db, campaigns)
    labels = svc.drive_labels(db, user.tenant_id, {c.drive_id for c in campaigns if c.drive_id})
    creator_ids = {c.created_by_id for c in campaigns if c.created_by_id}
    names = (
        dict(
            db.execute(
                select(User.id, User.full_name).where(User.id.in_(creator_ids), User.tenant_id == user.tenant_id)
            ).all()
        )
        if creator_ids
        else {}
    )
    return [_out(c, user, counts.get(c.id, (0, 0, 0)), labels, names) for c in campaigns]


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(
    response: Response,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(draft|active|closed|cancelled|archived)$"),
    include_archived: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(managers),
    page: Pagination = Depends(pagination_params),
) -> list[CampaignOut]:
    stmt = select(PlacementCampaign).where(svc._visible_clause(user))
    if status_filter is not None:
        stmt = stmt.where(PlacementCampaign.status == status_filter)
    elif not include_archived:
        stmt = stmt.where(PlacementCampaign.status != "archived")
    rows = paginate_rows(
        db, response, stmt.order_by(PlacementCampaign.due_at.desc(), PlacementCampaign.id.desc()), page
    )
    return _render(db, user, [row[0] for row in rows])


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db), user: User = Depends(managers)) -> CampaignOut:
    return _render(db, user, [svc.create(db, user, payload)])[0]


@router.get("/campaigns/sections", response_model=list[TeacherSectionOut])
def my_sections(db: Session = Depends(get_db), user: User = Depends(managers)) -> list[TeacherSectionOut]:
    """Sections the caller may assign by section (teacher: sections they teach)."""
    stmt = select(Section).where(Section.tenant_id == user.tenant_id)
    if user.role == UserRole.teacher:
        stmt = stmt.where(Section.id.in_(svc.teacher_section_ids(db, user) or {-1}))
    sections = list(db.scalars(stmt.order_by(Section.name)))
    info = svc.section_info(db, user.tenant_id, {s.id for s in sections})
    return [TeacherSectionOut(id=s.id, name=s.name, department_name=info.get(s.id, (None, None))[1]) for s in sections]


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Session = Depends(get_db), user: User = Depends(managers)) -> CampaignOut:
    return _render(db, user, [svc.require_campaign(db, user, campaign_id)])[0]


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
def edit_campaign(
    campaign_id: int, payload: CampaignIn, db: Session = Depends(get_db), user: User = Depends(managers)
) -> CampaignOut:
    c = svc.require_campaign(db, user, campaign_id)
    return _render(db, user, [svc.edit(db, user, c, payload)])[0]


@router.post("/campaigns/{campaign_id}/status", response_model=CampaignOut)
def campaign_status(
    campaign_id: int, payload: CampaignStatusIn, db: Session = Depends(get_db), user: User = Depends(managers)
) -> CampaignOut:
    c = svc.require_campaign(db, user, campaign_id)
    return _render(db, user, [svc.transition(db, user, c, payload.to)])[0]


@router.get("/campaigns/{campaign_id}/candidates", response_model=list[CandidateOut])
def campaign_candidates(
    campaign_id: int,
    response: Response,
    section_id: int | None = Query(default=None, ge=1),
    department_id: int | None = Query(default=None, ge=1),
    graduation_year: int | None = Query(default=None, ge=2000, le=2100),
    drive_id: int | None = Query(default=None, ge=1),
    application_status: ApplicationStatus | None = None,
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(managers),
    page: Pagination = Depends(pagination_params),
) -> list[CandidateOut]:
    """Students the caller may assign (teachers: own sections only)."""
    c = svc.require_campaign(db, user, campaign_id)
    stmt = svc.candidates_stmt(
        db,
        user,
        section_id=section_id,
        department_id=department_id,
        graduation_year=graduation_year,
        drive_id=drive_id,
        application_status=application_status,
        q=q,
    )
    students = [row[0] for row in paginate_rows(db, response, stmt, page)]
    info = svc.section_info(db, user.tenant_id, {s.section_id for s in students if s.section_id})
    already = svc.assigned_ids(db, c)
    return [
        CandidateOut(
            student_id=s.id,
            full_name=s.full_name,
            section_id=s.section_id,
            section_name=info.get(s.section_id, (None, None))[0] if s.section_id else None,
            department_name=info.get(s.section_id, (None, None))[1] if s.section_id else None,
            assigned=s.id in already,
        )
        for s in students
    ]


@router.post("/campaigns/{campaign_id}/students", response_model=AssignOut)
def assign_students(
    campaign_id: int, payload: AssignIn, db: Session = Depends(get_db), user: User = Depends(managers)
) -> AssignOut:
    c = svc.require_campaign(db, user, campaign_id)
    return AssignOut(**svc.assign(db, user, c, payload.student_ids, payload.section_ids))


@router.delete("/campaigns/{campaign_id}/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_student(
    campaign_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(managers)
) -> Response:
    c = svc.require_campaign(db, user, campaign_id)
    svc.unassign(db, user, c, student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/campaigns/{campaign_id}/progress", response_model=list[ProgressRowOut])
def campaign_progress(
    campaign_id: int,
    response: Response,
    completion: str | None = Query(default=None, pattern="^(pending|completed|completed_late)$"),
    db: Session = Depends(get_db),
    user: User = Depends(managers),
    page: Pagination = Depends(pagination_params),
) -> list[ProgressRowOut]:
    """Completion status per assigned student - nothing else (P9-D7)."""
    c = svc.require_campaign(db, user, campaign_id)
    rows = db.execute(
        select(PlacementCampaignStudent.student_id, PlacementCampaignStudent.assigned_at, User.full_name, User.section_id)
        .join(User, PlacementCampaignStudent.student_id == User.id)
        .where(
            PlacementCampaignStudent.tenant_id == c.tenant_id,
            PlacementCampaignStudent.campaign_id == c.id,
            User.tenant_id == c.tenant_id,
        )
        .order_by(User.full_name, User.id)
    ).all()
    done = svc.completion_map(db, c, [r[0] for r in rows])
    info = svc.section_info(db, c.tenant_id, {r[3] for r in rows if r[3]})
    out = []
    for sid, assigned_at, name, section_id in rows:
        state = svc.completion_of(c, done.get(sid))
        if completion is not None and state != completion:
            continue
        out.append(
            ProgressRowOut(
                student_id=sid,
                full_name=name,
                section_name=info.get(section_id, (None, None))[0] if section_id else None,
                completion=state,
                completed_at=done.get(sid),
                assigned_at=assigned_at,
            )
        )
    response.headers["X-Completion-Counts"] = ",".join(
        f"{k}:{sum(1 for sid, *_r in rows if svc.completion_of(c, done.get(sid)) == k)}"
        for k in ("pending", "completed", "completed_late")
    )
    return paginate_list(response, out, page)


@router.get("/me/campaigns", response_model=list[MyCampaignOut])
def my_campaigns(
    response: Response,
    db: Session = Depends(get_db),
    student: User = Depends(student_only),
    page: Pagination = Depends(pagination_params),
) -> list[MyCampaignOut]:
    """The student's own active/closed assignments with their completion."""
    campaigns = list(
        db.scalars(
            select(PlacementCampaign)
            .join(
                PlacementCampaignStudent,
                (PlacementCampaignStudent.campaign_id == PlacementCampaign.id)
                & (PlacementCampaignStudent.tenant_id == student.tenant_id),
            )
            .where(
                PlacementCampaign.tenant_id == student.tenant_id,
                PlacementCampaignStudent.student_id == student.id,
                PlacementCampaign.status.in_(("active", "closed")),
            )
            .order_by(PlacementCampaign.due_at, PlacementCampaign.id)
        )
    )
    labels = svc.drive_labels(db, student.tenant_id, {c.drive_id for c in campaigns if c.drive_id})
    out = []
    for c in campaigns:
        at = svc.completion_map(db, c, [student.id]).get(student.id)
        out.append(
            MyCampaignOut(
                id=c.id,
                title=c.title,
                description=c.description,
                status=c.status,
                interview_type=c.interview_type,
                target_role=c.target_role,
                drive_id=c.drive_id,
                drive_label=labels.get(c.drive_id) if c.drive_id else None,
                starts_at=svc._utc(c.starts_at),
                due_at=svc._utc(c.due_at),
                completion=svc.completion_of(c, at),
                completed_at=at,
            )
        )
    return paginate_list(response, out, page)
