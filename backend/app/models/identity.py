"""Identity, tenancy & auth ORM models."""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.enums import UserRole, InviteStatus


class Tenant(Base):
    """A single tenant = one institute/college on the platform.

    Multi-tenancy backbone. Every tenant-scoped row will carry a `tenant_id`
    pointing here, so one institute can never see or touch another's data.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-readable institute name, e.g. "IIT Delhi".
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Short unique code used at signup / in a subdomain, e.g. "iitd".
    slug: Mapped[str] = mapped_column(
        String(63), unique=True, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"


class User(Base):
    """A single account. One row per person, regardless of role."""

    __tablename__ = "users"
    __table_args__ = (
        # Phase 5B: lets other tables reference (user, institute) pairs, e.g.
        # recruiter_users -> a recruiter login must be in the company's institute.
        UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        default=UserRole.student,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # True only for a self-registered student awaiting approval by an admin of
    # the same institute (POST /admin/users/{id}/approve). Keeps "pending
    # request" distinct from "disabled by an admin": both have is_active=False.
    # Every other account (and every pre-existing row) is False.
    approval_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    # A student's class/section - used to build attendance rosters & gradebook.
    # Nullable: staff accounts (teacher/tpo/admin) and not-yet-assigned students.
    section_id: Mapped[int | None] = mapped_column(
        ForeignKey("sections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Multi-tenancy: which institute this account belongs to.
    # NOT NULL (Phase 2d): every user belongs to exactly one tenant. All three
    # creation paths set it - invite-accept, tenant-scoped /auth/register, and
    # the Phase 1 backfill - so orphan (tenant-less) users are impossible.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role.value}>"


class TenantInvite(Base):
    """A single-use invite an institute admin sends so a user can self-onboard
    into that tenant with a pre-assigned role.

    Mirrors RecruiterInvite: created `pending`, becomes `accepted` once the
    user sets up their account, `expired` past `expires_at`, or `revoked` if
    the admin cancels it. The token is unique and unguessable; only its
    SHA-256 digest is stored (``token_hash``, Phase 7).
    """

    __tablename__ = "tenant_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Role the invited user will get on accept (student/teacher/tpo/admin).
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False
    )
    # SHA-256 hex of the one-time invite token (Phase 7). Never the raw token.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status"),
        default=InviteStatus.pending,
        nullable=False,
        index=True,
    )
    invited_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<TenantInvite tenant={self.tenant_id} email={self.email!r} "
            f"status={self.status.value}>"
        )


class RefreshToken(Base):
    """A single issued refresh token (hashed) for cookie-based auth rotation.

    Security model
    --------------
    * Only the SHA-256 HASH of the token is stored - the raw value lives solely
      in the client's httpOnly cookie, so a DB leak cannot be replayed.
    * Rotation: every /auth/refresh consumes one token and issues a new one in
      the SAME ``family_id``. The consumed row gets ``revoked_at`` set and
      ``replaced_by_id`` points at its successor.
    * Reuse detection: if an already-revoked token is presented again (a stolen,
      previously-rotated token), the WHOLE family is revoked - killing both the
      attacker's and the victim's sessions, which is the safe default.

    NOTE (multi-tenancy): this table intentionally has NO Postgres RLS. The
    /auth/refresh route runs BEFORE any user/tenant is known (there is no access
    token yet), so a tenant-scoped RLS policy would block the very lookup that
    identifies the session. Rows are found only by the unguessable token hash,
    and the successor's user/tenant are copied from the validated row.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex digest of the raw token (64 chars). Never store the raw token.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    # All tokens produced by one login share a family; rotation stays in-family
    # so reuse-detection can revoke the entire chain at once.
    family_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Successor set on rotation (this row was consumed to mint replaced_by).
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    # Optional context for session listing / debugging.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<RefreshToken id={self.id} user_id={self.user_id} "
            f"family={self.family_id[:8]!r}>"
        )
