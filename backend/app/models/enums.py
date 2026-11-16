"""Shared enums used across Campus AI ORM models."""

import enum


class UserRole(str, enum.Enum):
    """The core roles in Campus AI (drives RBAC across the product)."""

    student = "student"
    teacher = "teacher"
    tpo = "tpo"
    admin = "admin"
    # External-facing role: recruiters from companies that hire via the portal.
    recruiter = "recruiter"


class AttendanceStatus(str, enum.Enum):
    """Possible states when marking a student for a given day."""

    present = "present"
    absent = "absent"
    late = "late"


class SkillStatus(str, enum.Enum):
    """Shared verification lifecycle for proof-backed achievements.

    Used by both `skills` and `project_members` (the anti-fraud moat):
      pending  -> claimed, awaiting mentor review
      verified -> mentor confirmed the proof; counts toward resume/eligibility
      flagged  -> mentor rejected/doubted the proof; does NOT count
    """

    pending = "pending"
    verified = "verified"
    flagged = "flagged"


class ApplicationStatus(str, enum.Enum):
    """Lifecycle of a student's application to a placement drive.

      applied     -> student applied (was eligible at apply time)
      shortlisted -> TPO shortlisted the candidate for the next round
      selected    -> candidate was selected/placed
      rejected    -> TPO rejected the application
    """

    applied = "applied"
    shortlisted = "shortlisted"
    selected = "selected"
    rejected = "rejected"


class RecruiterDecision(str, enum.Enum):
    """A recruiter's NON-binding assessment of a candidate they can see.

    The TPO still owns the official `ApplicationStatus`; this is the company's
    signal back to the TPO (and the natural precursor to extending an offer):
      pending    -> recruiter hasn't acted yet (default)
      interested -> wants to move forward (likely to make an offer)
      on_hold    -> keeping the candidate in consideration for now
      rejected   -> not interested (the recruiter's own view, not the TPO's)
    """

    pending = "pending"
    interested = "interested"
    on_hold = "on_hold"
    rejected = "rejected"


class OfferStatus(str, enum.Enum):
    """Lifecycle of a formal offer a recruiter extends to a candidate.

      extended  -> recruiter made the offer; awaiting the student's response
      accepted  -> student accepted the offer
      declined  -> student declined the offer
      withdrawn -> recruiter pulled the offer back (only while still extended)
    """

    extended = "extended"
    accepted = "accepted"
    declined = "declined"
    withdrawn = "withdrawn"


class ECACategory(str, enum.Enum):
    """Buckets for extra-curricular activities (the 'well-rounded' picture)."""

    sports = "sports"
    cultural = "cultural"
    technical = "technical"
    volunteering = "volunteering"
    leadership = "leadership"
    other = "other"


class InternshipType(str, enum.Enum):
    """Kind of verifiable work-experience entry a student can log."""

    internship = "internship"
    ojt = "ojt"
    apprenticeship = "apprenticeship"
    training = "training"
    other = "other"


class SubmissionStatus(str, enum.Enum):
    """Lifecycle of a student's submission to an assignment.

      submitted -> student turned it in (re-submitting resets grading)
      graded    -> teacher assigned marks + (optional) feedback
    """

    submitted = "submitted"
    graded = "graded"


class MaterialCategory(str, enum.Enum):
    """How a Study Hub material is classified (for browsing/filtering)."""

    notes = "notes"
    slides = "slides"
    video = "video"
    link = "link"
    other = "other"


class DoubtStatus(str, enum.Enum):
    """Lifecycle of a question in the doubt forum.

      open     -> awaiting a satisfactory answer
      resolved -> the asker (or staff) accepted an answer as the solution
    """

    open = "open"
    resolved = "resolved"


class LeaveRequestType(str, enum.Enum):
    """Two kinds of planned absence, with different attendance impact.

    leave -> a personal absence (medical / personal / emergency). Counts
             against attendance unless explicitly excused.
    od    -> "on duty": the student is away on OFFICIAL college work (fest,
             hackathon, sports, paper presentation, NSS/NCC, industrial visit,
             placement interview, ...). Approved OD is condoned, so it must NOT
             pull the student's attendance percentage down.
    """

    leave = "leave"
    od = "od"


class LeaveStatus(str, enum.Enum):
    """Lifecycle of a leave/OD request (human-in-the-loop approval)."""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class RecruiterStatus(str, enum.Enum):
    """Lifecycle of a recruiting company in the placement portal."""

    pending = "pending"      # invited, awaiting first HR login
    active = "active"        # at least one HR has accepted & can log in
    suspended = "suspended"  # access revoked by the TPO


class InviteStatus(str, enum.Enum):
    """Lifecycle of a single-use recruiter invitation token."""

    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class NotificationType(str, enum.Enum):
    """Machine categories for in-app notifications.

    Stored as a short plain string on the model (not a DB Enum) so new event
    types need no migration - mirrors announcements.audience. These constants
    keep the event-site code typo-free.
    """

    announcement = "announcement"
    application_status = "application_status"
    doubt_answer = "doubt_answer"
    submission_graded = "submission_graded"
    generic = "generic"
