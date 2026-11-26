"""AI Mock Interview company profiles (Phase 8.4; decisions P8-D9..D11).

Mounted under ``/mock-interview`` (at /v1 and the deprecated unversioned path,
like every feature router). Every route 404s unless the master switch, the
master allow-list and ``AI_MOCK_INTERVIEW_COMPANY_ENABLED`` admit the
caller's institute. Then by role:

  students + admin/TPO:
    GET  /company/role-templates                  generic role templates
  students (own institute, PUBLISHED only; anything else 404):
    GET  /company/profiles                        published profiles
    GET  /company/profiles/{id}                   one published profile
    POST /company/jd-skills                       JD -> skill map (5/min)
  admin + TPO (own institute; every write audited, content-free):
    GET  /company/profiles/admin                  all profiles (any status)
    POST /company/profiles/admin                  create a draft
    PUT  /company/profiles/{id}                   replace a draft/published profile
    POST /company/profiles/{id}/publish           needs >= 1 source record
    POST /company/profiles/{id}/archive           archive (never delete)
    POST /company/profiles/from-drive/{drive_id}  draft from an OPEN drive

Teachers and recruiters get 403 on every route. URLs in source records are
never fetched; students see them as plain-text label + domain.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.rate_limit import limiter
from app.core.security import apply_tenant_guc, get_current_user
from app.models import User, UserRole
from app.routers.mock_interview import _allowed_tenant_ids, _caller_key
from app.schemas.mock_interview_company import (
    CompanyProfileBriefOut,
    CompanyProfileIn,
    CompanyProfileStaffOut,
    CompanyProfileStudentOut,
    JDSkillsIn,
    JDSkillsOut,
    RoleTemplateOut,
)
from app.services import mock_interview_company as company
from app.services import role_templates

router = APIRouter(
    prefix="/mock-interview",
    tags=["mock-interview"],
    dependencies=[Depends(apply_tenant_guc)],
)

_STAFF = frozenset({UserRole.admin, UserRole.tpo})
_WRITE_LIMIT = "20/minute"
_JD_LIMIT = "5/minute"


def company_enabled_for_tenant(tenant_id: int) -> bool:
    """Master switch + master allow-list + the company sub-flag (role-free)."""
    if not (settings.AI_MOCK_INTERVIEW_ENABLED and settings.AI_MOCK_INTERVIEW_COMPANY_ENABLED):
        return False
    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_TENANT_IDS)
    return allowed is None or tenant_id in allowed


def company_enabled_for(user: User) -> bool:
    """Students may use company context (create/picker) when this is True."""
    return user.role == UserRole.student and company_enabled_for_tenant(user.tenant_id)


def _gate(current_user: User = Depends(get_current_user)) -> User:
    if not company_enabled_for_tenant(current_user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return current_user


def require_company_staff(user: User = Depends(_gate)) -> User:
    if user.role not in _STAFF:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company profiles are managed by institute admins and TPOs only",
        )
    return user


def require_company_student(user: User = Depends(_gate)) -> User:
    if user.role != UserRole.student:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Available to students only")
    return user


def require_company_reader(user: User = Depends(_gate)) -> User:
    if user.role != UserRole.student and user.role not in _STAFF:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not available for your role")
    return user


# --- Shared -------------------------------------------------------------------------------


@router.get("/company/role-templates", response_model=list[RoleTemplateOut])
def list_role_templates(_user: User = Depends(require_company_reader)) -> list[dict]:
    """Generic role templates (written by us; no company content)."""
    return role_templates.public_list()


# --- Staff (admin + TPO) ----------------------------------------------------------------------


@router.get("/company/profiles/admin", response_model=list[CompanyProfileStaffOut])
def staff_list_profiles(
    db: Session = Depends(get_db), user: User = Depends(require_company_staff)
) -> list[dict]:
    """Every profile of my institute (draft, published and archived)."""
    return [company.staff_view(p) for p in company.list_staff(db, user.tenant_id)]


@router.post(
    "/company/profiles/admin",
    response_model=CompanyProfileStaffOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(_WRITE_LIMIT, key_func=_caller_key)
def staff_create_profile(
    request: Request,
    response: Response,
    payload: CompanyProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_staff),
) -> dict:
    """Create a draft profile (audited)."""
    return company.staff_view(company.create(db, user, payload))


@router.post(
    "/company/profiles/from-drive/{drive_id}",
    response_model=CompanyProfileStaffOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(_WRITE_LIMIT, key_func=_caller_key)
def staff_profile_from_drive(
    request: Request,
    response: Response,
    drive_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_staff),
) -> dict:
    """A draft pre-filled from an OPEN drive of my institute (else 404). Audited."""
    return company.staff_view(company.create_from_drive(db, user, drive_id))


@router.put("/company/profiles/{profile_id}", response_model=CompanyProfileStaffOut)
@limiter.limit(_WRITE_LIMIT, key_func=_caller_key)
def staff_update_profile(
    request: Request,
    response: Response,
    profile_id: int,
    payload: CompanyProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_staff),
) -> dict:
    """Replace a draft or published profile (409 when archived). Audited.
    Sessions already created keep their frozen copy."""
    p = company.get_profile(db, user.tenant_id, profile_id)
    return company.staff_view(company.update(db, user, p, payload))


@router.post("/company/profiles/{profile_id}/publish", response_model=CompanyProfileStaffOut)
@limiter.limit(_WRITE_LIMIT, key_func=_caller_key)
def staff_publish_profile(
    request: Request,
    response: Response,
    profile_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_staff),
) -> dict:
    """Publish (needs >= 1 source record; 422 otherwise). Idempotent. Audited."""
    p = company.get_profile(db, user.tenant_id, profile_id)
    return company.staff_view(company.publish(db, user, p))


@router.post("/company/profiles/{profile_id}/archive", response_model=CompanyProfileStaffOut)
@limiter.limit(_WRITE_LIMIT, key_func=_caller_key)
def staff_archive_profile(
    request: Request,
    response: Response,
    profile_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_staff),
) -> dict:
    """Archive (profiles are never deleted). Idempotent. Audited."""
    p = company.get_profile(db, user.tenant_id, profile_id)
    return company.staff_view(company.archive(db, user, p))


# --- Students -------------------------------------------------------------------------------


@router.get("/company/profiles", response_model=list[CompanyProfileBriefOut])
def student_list_profiles(
    db: Session = Depends(get_db), user: User = Depends(require_company_student)
) -> list[dict]:
    """Published profiles of my institute."""
    return [company.brief_view(p) for p in company.list_published(db, user.tenant_id)]


@router.get("/company/profiles/{profile_id}", response_model=CompanyProfileStudentOut)
def student_get_profile(
    profile_id: int, db: Session = Depends(get_db), user: User = Depends(require_company_student)
) -> dict:
    """One published profile of my institute (drafts, archived, other institutes: 404)."""
    return company.student_view(company.published_profile(db, user.tenant_id, profile_id))


@router.post("/company/jd-skills", response_model=JDSkillsOut)
@limiter.limit(_JD_LIMIT, key_func=_caller_key)
def student_jd_skills(
    request: Request,
    response: Response,
    payload: JDSkillsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_company_student),
) -> dict:
    """Skills in a job description (each quoting the JD), matched against my
    verified skills. Nothing is stored. 503 when the AI is unavailable."""
    return company.jd_skills(db, user, payload)
