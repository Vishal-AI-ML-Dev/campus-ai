"""Authentication routes: register, login (JWT), and current-user lookup.

Mounted under the `/auth` prefix by `main.py`.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Tenant, User, UserRole
from app.schemas import Token, TokenRefreshRequest, UserCreate, UserOut
from app.core.security import (
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    create_access_token,
    get_current_user,
    hash_password,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    set_auth_cookies,
    verify_password,
)

from app.core.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("15/minute")
def register(
    request: Request,
    response: Response,
    payload: UserCreate,
    db: Session = Depends(get_db),
) -> User:
    """Create a new account. Fails if the email is already taken.

    SECURITY: public self-registration always creates a STUDENT account. Staff
    and recruiter roles are provisioned by an admin or via the recruiter invite
    flow - the caller cannot choose their own role here.

    Multi-tenancy: the student joins the institute identified by
    `payload.tenant_slug`; the account is scoped to that tenant. An unknown or
    inactive slug is rejected so we never create orphan (tenant-less) users.
    """
    tenant = db.scalar(select(Tenant).where(Tenant.slug == payload.tenant_slug))
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown or inactive institute",
        )

    exists = db.scalar(select(User).where(User.email == payload.email))
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.student,
        tenant_id=tenant.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
@limiter.limit("30/minute")
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    """Verify credentials and return a JWT access token.

    Note: the OAuth2 form field is called `username`; we use the email there.

    A deactivated account gets the SAME generic 401 (checked after the
    password, so the response does not reveal account state) and no cookies
    or refresh-token row are created.
    """
    user = db.scalar(select(User).where(User.email == form.username))
    if (
        user is None
        or not verify_password(form.password, user.hashed_password)
        or not user.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(subject=str(user.id))
    # Issue a rotating refresh token and set BOTH tokens as httpOnly cookies.
    # The access token is still returned in the body too, so existing Bearer
    # clients (and the test-suite) keep working unchanged.
    refresh_token = issue_refresh_token(
        db, user, user_agent=request.headers.get("user-agent")
    )
    set_auth_cookies(response, access_token, refresh_token)
    return Token(access_token=access_token)


@router.post("/refresh", response_model=Token)
@limiter.limit("60/minute")
def refresh(
    request: Request,
    response: Response,
    payload: TokenRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> Token:
    """Rotate the refresh token and mint a fresh access token.

    The refresh token is read from the httpOnly cookie (browser) or, for
    non-cookie API clients, from the optional JSON body. Rotation is single-use:
    reusing an already-rotated token revokes the entire session family (theft
    defence). New access + refresh cookies are set on success.
    """
    raw = (payload.refresh_token if payload else None) or request.cookies.get(
        REFRESH_COOKIE_NAME
    )
    user, new_refresh = rotate_refresh_token(
        db, raw, user_agent=request.headers.get("user-agent")
    )
    access_token = create_access_token(subject=str(user.id))
    set_auth_cookies(response, access_token, new_refresh)
    return Token(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    payload: TokenRefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> None:
    """Log out: revoke the current refresh-token family and clear the cookies."""
    raw = (payload.refresh_token if payload else None) or request.cookies.get(
        REFRESH_COOKIE_NAME
    )
    revoke_refresh_token(db, raw)
    clear_auth_cookies(response)
    return None


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the currently authenticated user (requires a valid token)."""
    return current_user
