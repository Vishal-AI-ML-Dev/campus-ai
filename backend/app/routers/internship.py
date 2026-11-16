"""Internship / OJT routes - the verifiable work-experience moat.

Same verified-data lifecycle as ECA (no AI proof-scoring; a human confirms):
  * STUDENT logs an internship/OJT with details + proof -> starts `pending`;
    can view/delete own.
  * TEACHER or TPO reviews the queue and verifies/flags each claim.
  * Only `verified` internships count toward the profile / recruiter view.

Captures real industry exposure (org, role, dates, mode) so a student's
profile shows verifiable work experience beyond academics. Mounted under the
`/internships` prefix by `main.py`.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Internship, SkillStatus, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import InternshipCreate, InternshipDecision, InternshipOut
from app.core.security import get_current_tenant_id, get_current_user, require_roles
from app.routers.audit import record_audit

router = APIRouter(prefix="/internships", tags=["internships"])

# Either a teacher (mentor) or the TPO may verify internship claims and read a
# student's internships (e.g. for eligibility / placement context).
reviewer_only = require_roles(UserRole.teacher, UserRole.tpo)
staff_only = require_roles(UserRole.teacher, UserRole.tpo)
# Phase 5P: internships are a STUDENT's verified-profile claim; only students
# log them (staff and external recruiter accounts get 403).
student_only = require_roles(UserRole.student)


# --- Student: log & view own internships -----------------------------------
@router.post(
    "", response_model=InternshipOut, status_code=status.HTTP_201_CREATED
)
def claim_internship(
    payload: InternshipCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(student_only),
) -> Internship:
    """Log an internship/OJT for yourself (starts `pending`).
    The (organization, role) pair must be unique per student."""
    if (
        payload.start_date is not None
        and payload.end_date is not None
        and payload.end_date < payload.start_date
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="End date cannot be before start date",
        )
    internship = Internship(
        student_id=current_user.id,
        tenant_id=current_user.tenant_id,
        organization=payload.organization,
        role_title=payload.role_title,
        internship_type=payload.internship_type,
        mode=payload.mode,
        location=payload.location,
        description=payload.description,
        skills_used=payload.skills_used,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_ongoing=payload.is_ongoing,
        certificate_url=payload.certificate_url,
        status=SkillStatus.pending,
    )
    db.add(internship)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already logged this role at this organization",
        )
    db.refresh(internship)
    return internship


@router.get("/me", response_model=list[InternshipOut])
def my_internships(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination_params),
) -> list[Internship]:
    """List the logged-in user's own internship claims (any status)."""
    stmt = (
        select(Internship)
        .where(
            Internship.student_id == current_user.id,
            Internship.tenant_id == current_user.tenant_id,
        )
        .order_by(Internship.created_at.desc())
    )
    return list(paginate(db, response, stmt, page))


@router.delete("/{internship_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_internship(
    internship_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete one of your own internship claims."""
    internship = db.get(Internship, internship_id)
    if internship is None or internship.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Internship not found"
        )
    # Phase 5P: a verified / flagged (or reviewed) internship is a decision
    # record - ONLY an institute admin may delete it, audited atomically with
    # the delete (same transaction). Undecided ones: the owner only.
    decided = (
        internship.status != SkillStatus.pending or internship.reviewed_at is not None
    )
    if decided:
        if current_user.role != UserRole.admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This internship has a verification decision and is kept as a "
                    "record; only an institute admin can delete it"
                ),
            )
        record_audit(
            db,
            current_user,
            action="internship.delete",
            summary=(
                f"Deleted internship #{internship.id} ({internship.status.value}) "
                f"of student #{internship.student_id} - decision record"
            ),
            target_type="internship",
            target_id=internship.id,
        )
    elif internship.student_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own internships",
        )
    db.delete(internship)
    db.commit()


# --- Reviewer (teacher/TPO): verification queue & decisions ----------------
@router.get(
    "/queue",
    response_model=list[InternshipOut],
    dependencies=[Depends(reviewer_only)],
)
def verification_queue(
    response: Response,
    status_filter: SkillStatus = SkillStatus.pending,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    page: Pagination = Depends(pagination_params),
) -> list[Internship]:
    """The reviewer's queue. Defaults to pending claims (oldest first).

    Tenant-scoped: a reviewer only ever sees claims from their own institute.
    """
    stmt = (
        select(Internship)
        .where(
            Internship.tenant_id == tenant_id,
            Internship.status == status_filter,
        )
        .order_by(Internship.created_at.asc())
    )
    return list(paginate(db, response, stmt, page))


@router.patch("/{internship_id}/decision", response_model=InternshipOut)
def decide_internship(
    internship_id: int,
    payload: InternshipDecision,
    db: Session = Depends(get_db),
    reviewer: User = Depends(reviewer_only),
) -> Internship:
    """Teacher/TPO verifies or flags an internship claim. `pending` is not a target."""
    if payload.status not in (SkillStatus.verified, SkillStatus.flagged):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Decision must be 'verified' or 'flagged'",
        )
    internship = db.get(Internship, internship_id)
    # Tenant guard: a reviewer can only act on claims from their own institute.
    # Treating a cross-tenant row as 404 also avoids leaking that the id exists.
    if internship is None or internship.tenant_id != reviewer.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Internship not found"
        )
    internship.status = payload.status
    internship.review_note = payload.review_note
    internship.reviewed_by_id = reviewer.id
    internship.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(internship)
    return internship


# --- Staff: read a student's internships ------------------------------------
@router.get(
    "/student/{student_id}",
    response_model=list[InternshipOut],
    dependencies=[Depends(staff_only)],
)
def student_internships(
    student_id: int,
    response: Response,
    status_filter: SkillStatus | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    page: Pagination = Depends(pagination_params),
) -> list[Internship]:
    """List a student's internships (teacher/TPO). Filter by status, e.g. verified.

    Tenant-scoped: staff can only read students within their own institute.
    """
    stmt = select(Internship).where(
        Internship.student_id == student_id,
        Internship.tenant_id == tenant_id,
    )
    if status_filter is not None:
        stmt = stmt.where(Internship.status == status_filter)
    return list(
        paginate(db, response, stmt.order_by(Internship.created_at.desc()), page)
    )
