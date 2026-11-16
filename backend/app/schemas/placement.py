"""Placement & recruitment schemas."""

from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import ApplicationStatus, RecruiterDecision, OfferStatus, RecruiterStatus, InviteStatus
from app.schemas.identity import UserOut


class DriveCreate(BaseModel):
    """TPO posts a drive with its eligibility criteria.

    All criteria are checked against each student's VERIFIED data. Leave a
    criterion at its default (0 / empty) to skip it.
    """

    company_name: str = Field(min_length=1, max_length=200, examples=["Acme Corp"])
    role_title: str = Field(min_length=1, max_length=200, examples=["Backend Engineer"])
    description: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=150, examples=["Bangalore"])
    package_lpa: float | None = Field(default=None, ge=0, examples=[12.0])
    min_cgpa: float = Field(default=0.0, ge=0, le=10, examples=[7.0])
    min_attendance: float = Field(default=0.0, ge=0, le=100, examples=[75])
    min_verified_projects: int = Field(default=0, ge=0, examples=[1])
    required_skills: str | None = Field(
        default=None,
        max_length=500,
        examples=["FastAPI, SQL, Docker"],
        description="Comma-separated skill names; matched against verified skills.",
    )
    deadline: date | None = None


class DriveOut(BaseModel):
    id: int
    company_name: str
    role_title: str
    description: str | None
    location: str | None
    package_lpa: float | None
    min_cgpa: float
    min_attendance: float
    min_verified_projects: int
    required_skills: str | None
    is_open: bool
    deadline: date | None
    recruiter_id: int | None = None

    model_config = ConfigDict(from_attributes=True)


class DriveStatusUpdate(BaseModel):
    """Open or close a drive for the eligibility/application window."""

    is_open: bool


class EligibilityReason(BaseModel):
    """Why one criterion passed or failed (explainable eligibility)."""

    criterion: str
    passed: bool
    detail: str


class StudentEligibilityOut(BaseModel):
    """One student's eligibility verdict for a drive (TPO view)."""

    student_id: int
    full_name: str
    eligible: bool
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    reasons: list[EligibilityReason]


class MyEligibilityOut(BaseModel):
    """The logged-in student's own eligibility breakdown for a drive."""

    drive_id: int
    eligible: bool
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    reasons: list[EligibilityReason]


class ApplicationOut(BaseModel):
    """A student's application record."""

    id: int
    drive_id: int
    student_id: int
    status: ApplicationStatus
    note: str | None

    model_config = ConfigDict(from_attributes=True)


class ApplicationStatusUpdate(BaseModel):
    """TPO decision on an application (shortlist / select / reject)."""

    status: ApplicationStatus = Field(examples=["shortlisted"])
    note: str | None = Field(default=None, max_length=500)


class DriveBriefOut(BaseModel):
    """Compact drive view embedded in a student's application list."""

    id: int
    company_name: str
    role_title: str
    package_lpa: float | None
    is_open: bool

    model_config = ConfigDict(from_attributes=True)


class MyApplicationOut(BaseModel):
    """One of the logged-in student's applications, with drive context."""

    id: int
    status: ApplicationStatus
    note: str | None
    drive: DriveBriefOut

    model_config = ConfigDict(from_attributes=True)


class ApplicantOut(BaseModel):
    """An applicant on a drive (TPO view), with a verified-data snapshot."""

    application_id: int
    student_id: int
    full_name: str
    status: ApplicationStatus
    eligible: bool
    cgpa: float
    attendance: float
    verified_skills: int
    verified_projects: int
    note: str | None
    # Whether the TPO has revealed this candidate's contact to the recruiter.
    contact_revealed: bool = False


class RecruiterInviteCreate(BaseModel):
    """TPO payload: onboard a company and invite its first HR."""

    company_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    website: str | None = Field(default=None, max_length=300)
    about: str | None = None
    title: str | None = Field(default=None, max_length=150)
    expires_in_days: int = Field(default=14, ge=1, le=90)


class RecruiterOut(BaseModel):
    """Public view of a recruiting company."""

    id: int
    company_name: str
    website: str | None = None
    about: str | None = None
    status: RecruiterStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecruiterInviteOut(BaseModel):
    """Public view of a recruiter invite (never exposes the raw token)."""

    id: int
    recruiter_id: int
    email: EmailStr
    status: InviteStatus
    title: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecruiterInviteCreated(BaseModel):
    """Returned right after creating an invite (includes the one-time token)."""

    invite: RecruiterInviteOut
    recruiter: RecruiterOut
    token: str
    accept_path: str


class RecruiterAcceptInvite(BaseModel):
    """Payload a recruiter submits to accept an invite & create their account."""

    token: str = Field(min_length=10, max_length=64)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=6, max_length=128)


class RecruiterMeOut(BaseModel):
    """A recruiter's own profile: their account + their company."""

    user: UserOut
    recruiter: RecruiterOut
    title: str | None = None
    is_primary: bool


class RecruiterDriveOut(BaseModel):
    """A drive linked to the recruiter's company (recruiter view)."""

    id: int
    company_name: str
    role_title: str
    location: str | None = None
    package_lpa: float | None = None
    is_open: bool
    deadline: date | None = None
    shortlisted_count: int
    selected_count: int


class RecruiterCandidateOut(BaseModel):
    """A shortlisted/selected candidate as seen by a recruiter.

    The verified-data snapshot is always visible (the moat's value); contact
    (email) appears only after the TPO reveals it for this application.
    """

    application_id: int
    drive_id: int
    drive_role: str
    status: ApplicationStatus
    full_name: str
    cgpa: float
    attendance: float
    verified_skills: list[str]
    verified_projects: int
    verified_internships: list[str] = []
    contact_revealed: bool
    email: str | None = None
    # Recruiter's own (non-binding) call on this candidate (Step 27.3).
    recruiter_decision: RecruiterDecision = RecruiterDecision.pending
    recruiter_decision_note: str | None = None
    # Whether this candidate already has a live offer (extended/accepted).
    has_active_offer: bool = False


class RecruiterDecisionUpdate(BaseModel):
    """Recruiter records a non-binding call on a visible candidate (27.3).

    `pending` is not allowed here - use interested / on_hold / rejected.
    """

    decision: RecruiterDecision = Field(examples=["interested"])
    note: str | None = Field(default=None, max_length=500)


class OfferCreate(BaseModel):
    """Recruiter extends an offer to a candidate (Step 27.3).

    `role_title`, `package_lpa` and `location` default to the drive's values
    when omitted, so a quick offer just needs the application id.
    """

    application_id: int = Field(gt=0)
    role_title: str | None = Field(default=None, max_length=200)
    package_lpa: float | None = Field(default=None, ge=0, examples=[8.5])
    location: str | None = Field(default=None, max_length=150)
    joining_date: date | None = None
    expires_on: date | None = None
    note: str | None = Field(default=None, max_length=1000)


class OfferRespond(BaseModel):
    """Student's response to an offer: accept or decline (Step 27.3)."""

    accept: bool
    note: str | None = Field(default=None, max_length=500)


class OfferOut(BaseModel):
    """A full offer record, enriched with drive/company/student context."""

    id: int
    application_id: int
    drive_id: int
    drive_role: str
    company_name: str
    student_id: int
    student_name: str
    role_title: str
    package_lpa: float | None = None
    location: str | None = None
    joining_date: date | None = None
    expires_on: date | None = None
    status: OfferStatus
    note: str | None = None
    student_response_note: str | None = None
    created_at: datetime
    responded_at: datetime | None = None


class DriveRecruiterLink(BaseModel):
    """TPO payload to link (or unlink) a drive to a recruiting company."""

    recruiter_id: int | None = Field(
        default=None, description="Company id to link; null to unlink."
    )


class ContactRevealUpdate(BaseModel):
    """TPO toggle to reveal/hide a candidate's contact to the recruiter."""

    revealed: bool
