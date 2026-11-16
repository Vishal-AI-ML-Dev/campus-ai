"""Marketing & feedback routes: public lead capture + in-product feedback.

  * Lead     -> public, unauthenticated submissions from the marketing site's
                contact / demo-request form (POST /leads).
  * Feedback -> anonymous in-product feedback (POST /feedback).
  * Admins review both (GET /leads, GET /feedback) and mark a lead handled.

The public POST routes intentionally have NO auth dependency - prospects are
not users yet. Admin reads are gated by `require_roles(UserRole.admin)`.
This router uses explicit full paths (no prefix) so /leads and /feedback are
both top-level. Wired into the app by `main.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Feedback, Lead, User, UserRole
from pagination import Pagination, paginate, pagination_params
from app.schemas import FeedbackCreate, FeedbackOut, LeadCreate, LeadOut
from app.core.security import require_roles

from app.core.rate_limit import limiter

router = APIRouter(tags=["marketing"])

# Reusable dependency: only admins may review captured leads/feedback.
admin_only = require_roles(UserRole.admin)


# --- Public: capture a lead (marketing contact form) -----------------------
@router.post("/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_lead(
    request: Request,
    response: Response,
    payload: LeadCreate,
    db: Session = Depends(get_db),
) -> Lead:
    """Public demo-request/contact submission. No authentication required."""
    lead = Lead(
        name=payload.name,
        email=payload.email,
        institute=payload.institute,
        role=payload.role,
        message=payload.message,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


# --- Public: in-product feedback -------------------------------------------
@router.post(
    "/feedback", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED
)
@limiter.limit("20/minute")
def create_feedback(
    request: Request,
    response: Response,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
) -> Feedback:
    """Submit anonymous feedback. Stored without a user link."""
    entry = Feedback(
        message=payload.message,
        category=payload.category,
        rating=payload.rating,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# --- Admin: review captured leads ------------------------------------------
@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    response: Response,
    handled: bool | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
    page: Pagination = Depends(pagination_params),
) -> list[Lead]:
    """List the leads claimed into the admin's own institute.

    Phase 5R: institute admins only ever see / act on leads ALREADY claimed
    into their own institute. Unclaimed public leads (tenant_id IS NULL) are
    platform-level contact data: they are hidden from every institute admin.
    Managing them (triage, assigning to an institute) needs a future
    PLATFORM-OPERATOR workflow - no such role exists yet, so nobody can claim
    them through this API for now.
    """
    stmt = (
        select(Lead)
        .where(Lead.tenant_id == admin.tenant_id)
        .order_by(Lead.created_at.desc())
    )
    if handled is not None:
        stmt = stmt.where(Lead.handled == handled)
    return list(paginate(db, response, stmt, page))


@router.patch("/leads/{lead_id}/handled", response_model=LeadOut)
def mark_lead_handled(
    lead_id: int,
    handled: bool = True,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> Lead:
    """Mark a lead as handled / not handled (admin).

    Only leads already claimed into the admin's own institute (Phase 5R);
    unclaimed and other institutes' leads are 404.
    """
    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != admin.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )
    lead.handled = handled
    db.commit()
    db.refresh(lead)
    return lead


@router.patch("/leads/{lead_id}/claim", response_model=LeadOut)
def claim_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
) -> Lead:
    """Claim endpoint - institute admins can no longer claim unclaimed leads.

    Phase 5R: institute admins only ever see / act on leads ALREADY claimed
    into their own institute. Unclaimed public leads (tenant_id IS NULL) are
    platform-level contact data: they are hidden from every institute admin.
    Managing them (triage, assigning to an institute) needs a future
    PLATFORM-OPERATOR workflow - no such role exists yet, so nobody can claim
    them through this API for now.

    Behaviour kept for compatibility: a lead ALREADY in the admin's institute
    is returned unchanged (idempotent); anything else is 404 and unchanged.
    """
    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != admin.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )
    return lead


# --- Admin: review submitted feedback --------------------------------------
@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    response: Response,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_only),
    page: Pagination = Depends(pagination_params),
) -> list[Feedback]:
    """List submitted feedback, newest first (admin).

    Phase 5S (same policy as leads, Phase 5R): an institute admin sees ONLY
    feedback already claimed into their own institute. Public submissions
    land unclaimed (tenant_id IS NULL) and are hidden from every institute
    admin; triaging / assigning them needs a future PLATFORM-OPERATOR
    workflow. There is no claim or edit route for feedback, so admins cannot
    claim or change it either. Nothing is deleted.
    """
    stmt = (
        select(Feedback)
        .where(Feedback.tenant_id == admin.tenant_id)
        .order_by(Feedback.created_at.desc())
    )
    return list(paginate(db, response, stmt, page))
