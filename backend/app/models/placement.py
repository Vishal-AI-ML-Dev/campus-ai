"""Placement & recruitment ORM models."""

from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import ApplicationStatus, RecruiterDecision, OfferStatus, RecruiterStatus, InviteStatus


class Drive(Base):
    """A placement/recruitment drive posted by the TPO.

    Eligibility criteria live as columns; the engine checks each student's
    VERIFIED profile (CGPA, attendance, verified skills, verified projects)
    against them. Only verified data counts - the moat reaches placement too.
    """

    __tablename__ = "drives"
    __table_args__ = (
        # Phase 5B: a drive may only link a company of its own institute.
        # NO ACTION (not SET NULL: that would also null tenant_id); deleting the
        # institute still cascades both sides.
        ForeignKeyConstraint(
            ["recruiter_id", "tenant_id"],
            ["recruiters.id", "recruiters.tenant_id"],
            name="fk_drives_recruiter_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant (institute) that posted this drive (the TPO's institute). Students
    # only ever see and apply to drives from their own institute.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # CTC in lakhs per annum (LPA).
    package_lpa: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- Eligibility criteria (checked against each student's VERIFIED data) ---
    min_cgpa: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    min_attendance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    min_verified_projects: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # Comma-separated required skill names, matched against verified skills.
    required_skills: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Optional link to a recruiting company (Step 27.2). When set, that
    # company's HR can view this drive's shortlisted/selected candidates.
    # Phase 5B: (recruiter_id, tenant_id) must reference a company of the SAME
    # institute - see __table_args__ (fk_drives_recruiter_tenant).
    recruiter_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    applications: Mapped[list["Application"]] = relationship(
        back_populates="drive", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Drive id={self.id} company={self.company_name!r} "
            f"open={self.is_open}>"
        )


class Application(Base):
    """A student's application to a drive, plus the TPO's decision.

    A student can apply to a drive at most once (unique constraint). Only
    students who pass the drive's eligibility check at apply time can create
    one - the verified-data moat gates the application itself.
    """

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "drive_id", "student_id", name="uq_application_drive_student"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drive_id: Mapped[int] = mapped_column(
        ForeignKey("drives.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"),
        default=ApplicationStatus.applied,
        nullable=False,
        index=True,
    )
    # Optional TPO note attached when shortlisting/selecting/rejecting.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Privacy gate (Step 27.2): a recruiter sees the candidate's contact
    # (email) only after the TPO explicitly reveals it for this application.
    contact_revealed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Recruiter's own (non-binding) call on this candidate (Step 27.3). The
    # TPO still owns `status`; this is the company's signal + offer precursor.
    recruiter_decision: Mapped[RecruiterDecision] = mapped_column(
        Enum(RecruiterDecision, name="recruiter_decision"),
        default=RecruiterDecision.pending,
        nullable=False,
    )
    recruiter_decision_note: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    recruiter_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    drive: Mapped["Drive"] = relationship(back_populates="applications")
    offer: Mapped["Offer | None"] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Application drive={self.drive_id} student={self.student_id} "
            f"status={self.status.value}>"
        )


class Offer(Base):
    """A formal offer a recruiter extends to a candidate (Step 27.3).

    One offer per application (unique). The recruiter sets the terms (role,
    package, location, joining + expiry dates); the student accepts or declines.
    The recruiter may withdraw it while it is still `extended`. A withdrawn or
    declined offer's row is reused if the recruiter later re-extends.
    """

    __tablename__ = "offers"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_offer_application"),
        # Phase 5B: an offer's company must belong to the offer's institute.
        ForeignKeyConstraint(
            ["recruiter_id", "tenant_id"],
            ["recruiters.id", "recruiters.tenant_id"],
            ondelete="CASCADE",
            name="fk_offers_recruiter_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Phase 5B: same-institute composite FK, see __table_args__.
    recruiter_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    drive_id: Mapped[int] = mapped_column(
        ForeignKey("drives.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    role_title: Mapped[str] = mapped_column(String(200), nullable=False)
    package_lpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus, name="offer_status"),
        default=OfferStatus.extended,
        nullable=False,
        index=True,
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    student_response_note: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    application: Mapped["Application"] = relationship(back_populates="offer")

    def __repr__(self) -> str:
        return (
            f"<Offer id={self.id} application={self.application_id} "
            f"status={self.status.value}>"
        )


class Recruiter(Base):
    """A recruiting company onboarded by the TPO (one row per company).

    External companies that hire from the institute. The TPO creates the row
    when inviting the company's first HR; it flips to `active` once an invite
    is accepted. All recruiter logins are scoped to exactly one company.
    """

    __tablename__ = "recruiters"
    __table_args__ = (
        # Target of the same-institute composite FKs from children.
        UniqueConstraint("id", "tenant_id", name="uq_recruiters_id_tenant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Phase 5B: owned by exactly one institute (RLS tenant_isolation).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    about: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RecruiterStatus] = mapped_column(
        Enum(RecruiterStatus, name="recruiter_status"),
        default=RecruiterStatus.pending,
        nullable=False,
        index=True,
    )
    # The TPO/admin who onboarded this company (kept for the audit trail).
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list["RecruiterUser"]] = relationship(
        back_populates="recruiter", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Recruiter id={self.id} company={self.company_name!r} "
            f"status={self.status.value}>"
        )


class RecruiterUser(Base):
    """Links a login account (role=recruiter) to its company (multi-HR ready).

    One account belongs to exactly one company (unique on user_id). `is_primary`
    marks the first/lead HR. Deleting either side cascades this link away.
    """

    __tablename__ = "recruiter_users"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_recruiter_user_account"),
        # Phase 5B: the company AND the login account must both belong to this
        # row's institute.
        ForeignKeyConstraint(
            ["recruiter_id", "tenant_id"],
            ["recruiters.id", "recruiters.tenant_id"],
            ondelete="CASCADE",
            name="fk_recruiter_users_recruiter_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            ondelete="CASCADE",
            name="fk_recruiter_users_user_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Phase 5B: owned by exactly one institute (RLS tenant_isolation).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recruiter_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # The HR's designation at the company (e.g. "Talent Lead").
    title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    recruiter: Mapped["Recruiter"] = relationship(back_populates="members")

    def __repr__(self) -> str:
        return (
            f"<RecruiterUser user={self.user_id} "
            f"recruiter={self.recruiter_id} primary={self.is_primary}>"
        )


class RecruiterInvite(Base):
    """A single-use invite token the TPO sends so a recruiter can self-onboard.

    Created `pending`; becomes `accepted` when the recruiter sets up their
    account, `expired` past `expires_at`, or `revoked` if the TPO cancels it.
    The token is unique and unguessable (secrets.token_urlsafe); only its
    SHA-256 digest is stored (``token_hash``, Phase 7).
    """

    __tablename__ = "recruiter_invites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["recruiter_id", "tenant_id"],
            ["recruiters.id", "recruiters.tenant_id"],
            ondelete="CASCADE",
            name="fk_recruiter_invites_recruiter_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Phase 5B: owned by exactly one institute (RLS tenant_isolation).
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recruiter_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
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
    # Optional designation to pre-fill on the new HR's account.
    title: Mapped[str | None] = mapped_column(String(150), nullable=True)
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
            f"<RecruiterInvite id={self.id} email={self.email!r} "
            f"status={self.status.value}>"
        )
