"""AI Mock Interview endpoints (Placement Preparation). Feature-flagged.

Phase 1 ships only the isolation layer:

  * GET /mock-interview/status  -> {"enabled": bool} for the signed-in user.
    Always available (authenticated) so the frontend can decide whether to
    show the feature; it never reveals anything else.
  * GET /mock-interview/catalog -> static setup choices. Gated: 404 while the
    feature is off for the caller's institute.

Every future mock-interview route must depend on ``require_mock_interview``
so it disappears (404) whenever the flag is off.

Flags (app/core/config.py, both default OFF / empty):
  AI_MOCK_INTERVIEW_ENABLED     master switch
  AI_MOCK_INTERVIEW_TENANT_IDS  optional comma-separated pilot allow-list

Mounted under the `/mock-interview` prefix by `main.py` (at /v1 and the
deprecated unversioned path, like every feature router).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.security import apply_tenant_guc, get_current_user
from app.models import User, UserRole
from app.schemas.mock_interview import MockInterviewCatalogOut, MockInterviewStatusOut

router = APIRouter(
    prefix="/mock-interview",
    tags=["mock-interview"],
    # Same tenant/RLS stamping as every tenant-scoped router.
    dependencies=[Depends(apply_tenant_guc)],
)

# Roles that may use the module. Recruiters (external company users) never can.
_ALLOWED_ROLES = frozenset(
    {UserRole.student, UserRole.teacher, UserRole.tpo, UserRole.admin}
)


def _allowed_tenant_ids(raw: str) -> frozenset[int] | None:
    """Parse the pilot allow-list. None = no restriction (all tenants).

    Fail closed: a non-empty value with no valid integer enables nobody.
    """
    if not raw.strip():
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        # ASCII 0-9 only: str.isdigit() also accepts e.g. "²", which int()
        # rejects, and that would turn every request into a 500.
        if part.isascii() and part.isdecimal():
            ids.add(int(part))
    return frozenset(ids)


def mock_interview_enabled_for(user: User) -> bool:
    """True when the feature is on for this user's institute and role.

    Reads settings at call time so the flag can be flipped (or patched in
    tests) without re-importing anything.
    """
    if not settings.AI_MOCK_INTERVIEW_ENABLED:
        return False
    if user.role not in _ALLOWED_ROLES:
        return False
    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_TENANT_IDS)
    return allowed is None or user.tenant_id in allowed


def require_mock_interview(
    current_user: User = Depends(get_current_user),
) -> User:
    """Gate for every mock-interview route: 404 unless enabled for the caller.

    404 (not 403) so, to a signed-in user, a disabled feature looks like a
    route that does not exist. Unauthenticated callers get 401 first (the
    router-level auth dependency runs before this gate), and /status itself is
    always present by design, so the module's existence is not secret.
    """
    if not mock_interview_enabled_for(current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return current_user


@router.get("/status", response_model=MockInterviewStatusOut)
def mock_interview_status(
    current_user: User = Depends(get_current_user),
) -> MockInterviewStatusOut:
    """Is the AI Mock Interview available to me? (Used to show/hide the UI.)"""
    return MockInterviewStatusOut(enabled=mock_interview_enabled_for(current_user))


@router.get("/catalog", response_model=MockInterviewCatalogOut)
def mock_interview_catalog(
    _user: User = Depends(require_mock_interview),
) -> MockInterviewCatalogOut:
    """Setup choices for a new interview (static; no database access)."""
    return MockInterviewCatalogOut(
        interview_types=["hr", "technical", "coding", "company", "full"],
        modes=["text", "voice", "video"],
        difficulties=["easy", "medium", "hard"],
        languages=["en", "hi", "hinglish"],
        durations_minutes=[10, 20, 30, 45],
    )
