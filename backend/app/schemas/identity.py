"""Identity / auth / tenant schemas."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import UserRole, InviteStatus


class UserCreate(BaseModel):
    """Payload for public self-registration.

    SECURITY: self-registration ALWAYS creates a `student` account. Staff roles
    (teacher/tpo/admin) and recruiters are provisioned only by an admin (or via
    the recruiter invite flow) - never by this public endpoint. The `role` field
    is intentionally NOT accepted here so it cannot be elevated by the caller.

    Multi-tenancy: `tenant_slug` selects WHICH institute the student joins; the
    new account is scoped to that tenant. The slug is the institute's public
    signup handle (e.g. "iit-delhi").
    """

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    tenant_slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")


class UserOut(BaseModel):
    """Safe, public view of a user (never includes the password hash)."""

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    section_id: int | None = None  # a student's class/section (None for staff)

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    """JWT returned on successful login."""

    access_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    """Optional body for /auth/refresh & /auth/logout.

    Browsers rely on the httpOnly refresh cookie and send nothing here; only
    non-cookie API clients need to pass the raw refresh token explicitly.
    """

    refresh_token: str | None = None


class AdminUserCreate(BaseModel):
    """Admin-created account. Unlike self-signup, role is set deliberately."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = UserRole.student


class UserRoleUpdate(BaseModel):
    role: UserRole


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserSectionUpdate(BaseModel):
    """Assign a student to a section (or clear it with section_id=null)."""

    section_id: int | None = None


class TenantCreate(BaseModel):
    """Platform-admin payload to onboard a new institute (tenant)."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")


class TenantSignup(BaseModel):
    """Public self-serve onboarding: create an institute + first admin."""

    institution_name: str = Field(min_length=1, max_length=255)
    institution_slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9-]+$")
    admin_full_name: str = Field(min_length=1, max_length=255)
    admin_email: EmailStr
    admin_password: str = Field(min_length=6, max_length=128)


class TenantOut(BaseModel):
    """Public view of a tenant (institute)."""

    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TenantInviteCreate(BaseModel):
    """Institute-admin payload: invite a user into THIS tenant with a role."""

    email: EmailStr
    role: UserRole = UserRole.student
    expires_in_days: int = Field(default=14, ge=1, le=90)


class TenantInviteOut(BaseModel):
    """Public view of a tenant invite (never exposes the raw token)."""

    id: int
    tenant_id: int
    email: EmailStr
    role: UserRole
    status: InviteStatus
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TenantInviteCreated(BaseModel):
    """Returned right after creating an invite (includes the one-time token)."""

    invite: TenantInviteOut
    tenant: TenantOut
    token: str
    accept_path: str


class TenantAcceptInvite(BaseModel):
    """Payload a user submits to accept a tenant invite & create their account."""

    token: str = Field(min_length=10, max_length=64)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=6, max_length=128)
