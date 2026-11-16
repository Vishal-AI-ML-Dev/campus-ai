"""Security helpers: password hashing (bcrypt), JWT handling, and RBAC deps.

  * hash_password / verify_password - bcrypt
  * create_access_token             - issue a signed JWT
  * get_current_user                - resolve the logged-in user from a token
  * get_current_tenant_id           - the tenant the logged-in user belongs to
  * require_roles(*roles)           - dependency factory for role-gated routes
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, set_current_tenant
from app.models import RefreshToken, User, UserRole

# auto_error=False so a MISSING Authorization header is not an instant 401 -
# get_current_user then falls back to the httpOnly access-token cookie.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# Names of the httpOnly auth cookies (set on login/refresh alongside the JSON
# body). The refresh cookie is path-scoped to the SUPPORTED versioned auth
# routes (/v1/auth/refresh, /v1/auth/logout - what the frontend calls) so it is
# never sent on ordinary API calls.
#
# Phase 5D compatibility change: cookie-based refresh/logout is supported on
# /v1/auth only. The deprecated unversioned /auth/* alias no longer receives
# the refresh cookie (body-token clients are unaffected). Cookies issued under
# the old Path=/auth are expired on every login/refresh/logout response - that
# only removes them from the browser; it does NOT revoke their server-side
# refresh_tokens rows (see clear_legacy_refresh_cookie).
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = f"{settings.API_V1_PREFIX}/auth"
LEGACY_REFRESH_COOKIE_PATH = "/auth"

_BCRYPT_MAX_BYTES = 72  # bcrypt only hashes the first 72 bytes of input


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash of the given plaintext password."""
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    pw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(pw, hashed.encode("utf-8"))


def create_access_token(subject: str) -> str:
    """Create a signed JWT whose `sub` claim identifies the user."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the logged-in user from the Bearer header OR the access cookie."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Bearer header wins; fall back to the httpOnly access-token cookie so a
    # browser client needs no Authorization header at all.
    if not token:
        token = request.cookies.get(ACCESS_COOKIE_NAME)
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        subject = payload.get("sub")
        if subject is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.get(User, int(subject))
    if user is None or not user.is_active:
        raise credentials_exception

    # Phase 4 (Postgres RLS): stamp the caller's institute onto THIS request's
    # DB session as early as possible. Almost every authenticated route depends
    # on get_current_user (directly or via require_roles), and FastAPI caches
    # Depends(get_db) per request, so this is the SAME session the route's
    # queries run on. Setting the GUC here - rather than per-router - means
    # every authenticated read/write (including aggregations like the institute
    # dashboard) satisfies FORCE ROW LEVEL SECURITY policies. No-op on
    # non-Postgres backends, and public/unauthenticated routes never reach here.
    set_current_tenant(db, user.tenant_id)
    return user


def get_current_tenant_id(current_user: User = Depends(get_current_user)) -> int:
    """Return the tenant id of the logged-in user.

    This is the single source of truth for "which institute is this request
    for?". Routes that read or write tenant-scoped data should depend on this
    and filter every query with ``WHERE tenant_id == <this value>`` so one
    institute can never see or touch another institute's data.

    Usage:
        @router.get("/skills")
        def list_skills(
            tenant_id: int = Depends(get_current_tenant_id),
            db: Session = Depends(get_db),
        ):
            return db.query(Skill).filter(Skill.tenant_id == tenant_id).all()
    """
    return current_user.tenant_id


def require_roles(*allowed_roles: UserRole):
    """Build a dependency that allows only users with one of `allowed_roles`.

    Usage:
        admin_only = require_roles(UserRole.admin)
        @router.post(..., dependencies=[Depends(admin_only)])
    """

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return role_checker


def get_scoped_user(
    db: Session,
    user_id: int | None,
    tenant_id: int,
    *,
    role: UserRole | None = None,
) -> User | None:
    """Fetch a user by id ONLY within the caller's tenant (else None).

    Why this exists (security): the ``users`` table deliberately has NO Postgres
    RLS - authentication must read a user by email BEFORE any tenant is known
    (login is a chicken-and-egg: you learn the tenant *from* the user row). The
    side effect is that a plain ``db.get(User, id)`` primary-key lookup happily
    returns a user from ANOTHER institute. Every authenticated, tenant-scoped
    route must therefore funnel user-by-id lookups through here, which enforces
    ``user.tenant_id == <caller's tenant>`` in the application layer so one
    institute can never read or mutate another institute's accounts.

    Returns None when the id is missing, the user does not exist, belongs to a
    different tenant, or (when ``role`` is given) does not have that role - so
    callers keep their existing 404 handling unchanged.
    """
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        return None
    if role is not None and user.role != role:
        return None
    return user


def apply_tenant_guc(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Publish the caller's tenant into the DB session for Postgres RLS (Phase 4).

    Add this as a router-level dependency on tenant-scoped routers
    (``APIRouter(..., dependencies=[Depends(apply_tenant_guc)])``) so every
    authenticated request stamps its institute onto the session via
    ``set_current_tenant`` *before* the route's queries run. FastAPI caches
    ``Depends(get_db)`` per request, so this runs on the SAME session the route
    uses - no contextvars, no threadpool-propagation pitfalls. No-op on
    non-Postgres backends, so it is safe everywhere.
    """
    set_current_tenant(db, current_user.tenant_id)


# ---------------------------------------------------------------------------
# Refresh tokens (httpOnly cookie rotation) - Batch 7
# ---------------------------------------------------------------------------

_refresh_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired refresh token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _hash_refresh_token(raw: str) -> str:
    """SHA-256 hex digest used to store/look up a refresh token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_invite_token(raw: str) -> str:
    """SHA-256 hex digest under which an invite token is stored / looked up.

    Phase 7: tenant and recruiter invite tokens are never stored in plain
    text. The raw token (secrets.token_urlsafe(32), ~256 bits) is returned to
    the inviter exactly once; the DB keeps only this digest, so a leaked DB
    row or backup cannot be redeemed. An unsalted fast hash is sufficient
    because the input is a high-entropy random secret, not a password.
    Migration f0a1b2c3d4e5 converts pre-existing rows with the same digest
    (Postgres sha256() over the UTF-8 bytes), so old pending links keep working.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    """Treat naive timestamps (SQLite) as UTC so comparisons never crash."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def issue_refresh_token(
    db: Session,
    user: User,
    *,
    family_id: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create + persist a new refresh-token row; return the RAW token (once)."""
    raw = secrets.token_urlsafe(48)
    row = RefreshToken(
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_hash=_hash_refresh_token(raw),
        family_id=family_id or secrets.token_hex(16),
        user_agent=((user_agent or "")[:255] or None),
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(row)
    db.commit()
    return raw


def _revoke_family(db: Session, family_id: str) -> None:
    """Revoke every still-active token in a rotation family (reuse response)."""
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        row.revoked_at = now
    db.commit()


def rotate_refresh_token(
    db: Session, raw_token: str | None, *, user_agent: str | None = None
) -> tuple[User, str]:
    """Validate a refresh token, rotate it, return (user, new_raw_token).

    Rotation rules (single-use tokens):
      * unknown hash     -> 401
      * already revoked  -> REUSE: revoke the whole family, then 401
      * expired          -> 401
      * inactive user    -> 401
      * otherwise        -> consume this row, mint a successor in-family
    """
    if not raw_token:
        raise _refresh_error
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh_token(raw_token)
        )
    )
    if row is None:
        raise _refresh_error

    now = datetime.now(timezone.utc)

    # Reuse detection: a previously-rotated (revoked) token was presented again.
    if row.revoked_at is not None:
        _revoke_family(db, row.family_id)
        raise _refresh_error

    if _aware(row.expires_at) <= now:
        raise _refresh_error

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise _refresh_error

    # Consume this token and mint its successor within the same family.
    new_raw = secrets.token_urlsafe(48)
    successor = RefreshToken(
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_hash=_hash_refresh_token(new_raw),
        family_id=row.family_id,
        user_agent=((user_agent or row.user_agent or "")[:255] or None),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(successor)
    db.flush()
    row.revoked_at = now
    row.replaced_by_id = successor.id
    db.commit()
    return user, new_raw


def revoke_refresh_token(db: Session, raw_token: str | None) -> None:
    """Best-effort logout: revoke the presented token's whole family."""
    if not raw_token:
        return
    row = db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh_token(raw_token)
        )
    )
    if row is not None:
        _revoke_family(db, row.family_id)


def set_auth_cookies(
    response: Response, access_token: str, refresh_token: str
) -> None:
    """Set the httpOnly access + refresh cookies (in addition to the JSON body)."""
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite
    domain = settings.COOKIE_DOMAIN or None
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path=REFRESH_COOKIE_PATH,
    )
    clear_legacy_refresh_cookie(response)


def clear_legacy_refresh_cookie(response: Response) -> None:
    """Expire a refresh cookie left over from the old ``Path=/auth`` scope.

    Browser-side cleanup only. A browser never sends that cookie to /v1/auth,
    so the server cannot see (or revoke) its token here: the matching
    refresh_tokens row stays valid until it expires or its family is revoked.
    Skipped if the versioned path ever equals the legacy one.
    """
    if LEGACY_REFRESH_COOKIE_PATH == REFRESH_COOKIE_PATH:
        return
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=LEGACY_REFRESH_COOKIE_PATH,
        domain=settings.COOKIE_DOMAIN or None,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def clear_auth_cookies(response: Response) -> None:
    """Remove the auth cookies (logout), including any legacy /auth-path one."""
    domain = settings.COOKIE_DOMAIN or None
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite
    response.delete_cookie(
        ACCESS_COOKIE_NAME,
        path="/",
        domain=domain,
        secure=secure,
        httponly=True,
        samesite=samesite,
    )
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        domain=domain,
        secure=secure,
        httponly=True,
        samesite=samesite,
    )
    clear_legacy_refresh_cookie(response)
