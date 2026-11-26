"""AI Mock Interview - institute operations API (Phase 7.7; decision P7-D3).

Mounted at ``/mock-interview/admin`` (and ``/v1/...``) via FEATURE_ROUTERS.

Access:
  * 404 for everyone unless institute operations are on for the caller's
    institute (AI_MOCK_INTERVIEW_ENABLED + AI_MOCK_INTERVIEW_OPS_ENABLED + the
    master tenant allow-list) - a disabled feature looks like a missing route;
  * viewing (every GET) = institute ``admin`` and ``tpo``; teachers, students
    and recruiters get 403;
  * editing controls (PUT /controls) = ``admin`` only; TPO gets 403;
  * everything is scoped to the caller's own institute: every query filters
    on ``tenant_id`` (Postgres RLS is a second layer) and another institute's
    session id is a 404. There is no cross-institute view (P7-D2).

Content-free: counts, statuses, ids, timings, classes and costs only - never
questions, answers, plans, resumes, job descriptions or reports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.core.security import apply_tenant_guc, get_current_user
from app.models import User, UserRole
from app.routers.audit import record_audit
from app.routers.mock_interview import _caller_key
from app.schemas.ai_operations import (
    ControlsOut,
    ControlsUpdate,
    HealthOut,
    SessionDiagnosticsOut,
    StudentUsageOut,
    UsageSummaryOut,
)
from app.services import mock_interview_policy as policy
from app.services import mock_interview_usage as usage

router = APIRouter(
    prefix="/mock-interview/admin",
    tags=["mock-interview-admin"],
    dependencies=[Depends(apply_tenant_guc)],
)

_VIEW_ROLES = frozenset({UserRole.admin, UserRole.tpo})
_ADMIN_LIMIT = "60/minute"
AUDIT_ACTION = "mock_interview.controls_update"


def require_ops_viewer(current_user: User = Depends(get_current_user)) -> User:
    if not policy.ops_enabled_for(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if current_user.role not in _VIEW_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Interview operations are available to institute admins and TPOs only",
        )
    return current_user


def require_ops_editor(current_user: User = Depends(require_ops_viewer)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only institute admins can change interview controls",
        )
    return current_user


@router.get("/usage", response_model=UsageSummaryOut)
@limiter.limit(_ADMIN_LIMIT, key_func=_caller_key)
def admin_usage(
    request: Request,
    response: Response,
    days: int = Query(30, ge=1, le=usage.MAX_DAYS),
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_viewer),
) -> dict:
    """Institute usage for the last ``days`` days: sessions, AI calls, failures,
    tokens, latency, breakdowns by step/model/outcome, mode mix and cost."""
    return usage.summary(db, user.tenant_id, days)


@router.get("/usage/students", response_model=list[StudentUsageOut])
@limiter.limit(_ADMIN_LIMIT, key_func=_caller_key)
def admin_usage_students(
    request: Request,
    response: Response,
    days: int = Query(30, ge=1, le=usage.MAX_DAYS),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_viewer),
) -> list[dict]:
    """Per-student counts, most active first. ``X-Total-Count`` = all rows."""
    rows, total = usage.students(db, user.tenant_id, days, limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/controls", response_model=ControlsOut)
def admin_get_controls(
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_viewer),
) -> dict:
    """This institute's controls, the platform limits they sit under, and the
    effective values. ``can_edit`` tells the UI whether to offer editing."""
    return {**policy.controls_view(db, user.tenant_id), "can_edit": user.role == UserRole.admin}


@router.put("/controls", response_model=ControlsOut)
@limiter.limit("20/minute", key_func=_caller_key)
def admin_update_controls(
    request: Request,
    response: Response,
    payload: ControlsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_editor),
) -> dict:
    """Replace this institute's controls (admin only). Values can only narrow
    the platform settings: a limit above the platform limit is a 422. The
    change and the audit row are saved in one transaction."""
    _, before, after = policy.update_controls(
        db,
        user,
        paused=payload.paused,
        voice_allowed=payload.voice_allowed,
        video_allowed=payload.video_allowed,
        limits=payload.limits.model_dump(),
    )
    changes = [f"{k}: {before[k]} -> {after[k]}" for k in after if before.get(k) != after[k]]
    record_audit(
        db,
        user,
        AUDIT_ACTION,
        "Mock interview controls updated" + (f" ({'; '.join(changes)})" if changes else " (no change)"),
        target_type="ai_feature_controls",
        target_id=policy.FEATURE,
    )
    db.commit()
    return {**policy.controls_view(db, user.tenant_id), "can_edit": True}


@router.get("/sessions/{session_id}/diagnostics", response_model=SessionDiagnosticsOut)
@limiter.limit(_ADMIN_LIMIT, key_func=_caller_key)
def admin_session_diagnostics(
    request: Request,
    response: Response,
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_viewer),
) -> dict:
    """Content-free timeline of one interview's AI steps (404 if the interview
    is not in the caller's institute)."""
    return usage.diagnostics(db, user.tenant_id, session_id)


@router.get("/health", response_model=HealthOut)
def admin_health(
    db: Session = Depends(get_db),
    user: User = Depends(require_ops_viewer),
) -> dict:
    """Circuit breaker state per step (this backend process) and the last hour's
    error rate of this institute's own AI calls."""
    return usage.health(db, user.tenant_id)
