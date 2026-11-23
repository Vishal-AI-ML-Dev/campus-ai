"""Campus AI ORM models — split by domain.

All model classes & enums are re-exported here so `from app.models import X`
and `import app.models as models` keep working, and importing this package
registers every table on Base.metadata."""

from app.core.db import Base
from app.models.enums import (
    UserRole,
    AttendanceStatus,
    SkillStatus,
    ApplicationStatus,
    RecruiterDecision,
    OfferStatus,
    ECACategory,
    InternshipType,
    SubmissionStatus,
    MaterialCategory,
    DoubtStatus,
    LeaveRequestType,
    LeaveStatus,
    RecruiterStatus,
    InviteStatus,
    NotificationType,
)
from app.models.identity import (
    Tenant,
    User,
    TenantInvite,
    RefreshToken,
)
from app.models.academics import (
    Department,
    Section,
    AttendanceRecord,
    FaceEnrollment,
    Subject,
    Result,
)
from app.models.careers import (
    Skill,
    Project,
    ProjectMember,
    ExtraCurricular,
    Internship,
    Resume,
)
from app.models.placement import (
    Drive,
    Application,
    Offer,
    Recruiter,
    RecruiterUser,
    RecruiterInvite,
)
from app.models.engagement import (
    Announcement,
    CalendarEvent,
    Assignment,
    Submission,
    Material,
    Doubt,
    DoubtAnswer,
    AnswerVote,
)
from app.models.operations import (
    Lead,
    Feedback,
    AuditLog,
    TimetableEntry,
    LeaveRequest,
)
from app.models.mentor import (
    MentorConversation,
    MentorMessage,
)
from app.models.notifications import (
    Notification,
)
from app.models.mock_interview import (
    MockInterviewSession,
    MockInterviewTurn,
    MockInterviewReport,
)
from app.models.ai_operations import AIFeatureControl, AIUsageEvent
from app.models.mock_interview_coding import (
    MockInterviewCodeAttempt,
    MockInterviewCodeSubmission,
)

__all__ = [
    "Base",
    "UserRole",
    "AttendanceStatus",
    "SkillStatus",
    "ApplicationStatus",
    "RecruiterDecision",
    "OfferStatus",
    "ECACategory",
    "InternshipType",
    "SubmissionStatus",
    "MaterialCategory",
    "DoubtStatus",
    "LeaveRequestType",
    "LeaveStatus",
    "RecruiterStatus",
    "InviteStatus",
    "NotificationType",
    "Tenant",
    "User",
    "TenantInvite",
    "RefreshToken",
    "Department",
    "Section",
    "AttendanceRecord",
    "FaceEnrollment",
    "Subject",
    "Result",
    "Skill",
    "Project",
    "ProjectMember",
    "ExtraCurricular",
    "Internship",
    "Resume",
    "Drive",
    "Application",
    "Offer",
    "Recruiter",
    "RecruiterUser",
    "RecruiterInvite",
    "Announcement",
    "CalendarEvent",
    "Assignment",
    "Submission",
    "Material",
    "Doubt",
    "DoubtAnswer",
    "AnswerVote",
    "Lead",
    "Feedback",
    "AuditLog",
    "TimetableEntry",
    "LeaveRequest",
    "MentorConversation",
    "MentorMessage",
    "Notification",
    "MockInterviewSession",
    "MockInterviewTurn",
    "MockInterviewReport",
    "MockInterviewCodeAttempt",
    "MockInterviewCodeSubmission",
    "AIUsageEvent",
    "AIFeatureControl",
]
