"""Analytics & institute dashboards schemas."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel

from app.models import UserRole


class RiskFactorOut(BaseModel):
    """One explainable contributor to a student's risk score."""

    key: str
    label: str
    value: float | None = None
    risk: float | None = None
    weight: float
    available: bool


class StudentRiskOut(BaseModel):
    """A student's at-risk assessment with a transparent factor breakdown."""

    student_id: int
    student_name: str | None = None
    risk_score: float
    band: str  # high | medium | low
    attendance_pct: float | None = None
    cgpa: float | None = None
    submission_rate: float | None = None
    reasons: list[str] = []
    factors: list[RiskFactorOut] = []


class ClassAnalyticsOut(BaseModel):
    """Aggregate analytics for one section (teacher dashboard)."""

    section_id: int
    section_name: str | None = None
    student_count: int
    avg_attendance_pct: float | None = None
    avg_cgpa: float | None = None
    results_coverage: int = 0
    total_assignments: int = 0
    avg_submission_rate: float | None = None
    risk_high: int = 0
    risk_medium: int = 0
    risk_low: int = 0
    at_risk_count: int = 0


class PlacementFunnelOut(BaseModel):
    """Current status distribution across all applications."""

    applied: int = 0
    shortlisted: int = 0
    selected: int = 0
    rejected: int = 0


class DrivePerformanceOut(BaseModel):
    """Per-drive recruitment performance snapshot."""

    drive_id: int
    company_name: str
    role_title: str
    package_lpa: float | None = None
    is_open: bool
    applicants: int = 0
    shortlisted: int = 0
    selected: int = 0
    rejected: int = 0
    selection_rate: float | None = None


class CompanyStatOut(BaseModel):
    """Aggregated outcomes for one company across its drives."""

    company_name: str
    drives: int = 0
    applicants: int = 0
    selected: int = 0
    avg_package: float | None = None


class PlacementAnalyticsOut(BaseModel):
    """Whole-program placement analytics for the TPO dashboard."""

    total_drives: int = 0
    open_drives: int = 0
    closed_drives: int = 0
    total_applications: int = 0
    unique_applicants: int = 0
    placed_students: int = 0
    total_active_students: int = 0
    placement_rate: float | None = None
    applicant_conversion: float | None = None
    avg_package: float | None = None
    highest_package: float | None = None
    highest_package_company: str | None = None
    funnel: PlacementFunnelOut
    drives: list[DrivePerformanceOut] = []
    companies: list[CompanyStatOut] = []


class BulkImportRowResult(BaseModel):
    """Outcome of importing a single CSV row."""

    row: int
    email: str | None = None
    status: Literal["created", "skipped"]
    detail: str
    user_id: int | None = None
    role: UserRole | None = None
    temp_password: str | None = None  # set only when auto-generated


class BulkImportResult(BaseModel):
    """Summary of a bulk user-import run."""

    total_rows: int
    created: int
    skipped: int
    results: list[BulkImportRowResult]


class InstituteUsersKpi(BaseModel):
    """Account counts by role + activation + section coverage."""

    total: int = 0
    active: int = 0
    inactive: int = 0
    students: int = 0
    teachers: int = 0
    tpos: int = 0
    admins: int = 0
    recruiters: int = 0
    students_with_section: int = 0


class InstituteStructureKpi(BaseModel):
    """Org structure footprint."""

    departments: int = 0
    sections: int = 0
    subjects: int = 0


class InstituteMoatKpi(BaseModel):
    """Verified vs pending proof - the anti-fraud data moat at a glance."""

    skills_verified: int = 0
    skills_pending: int = 0
    projects_verified: int = 0
    projects_pending: int = 0
    eca_verified: int = 0
    eca_pending: int = 0
    internships_verified: int = 0
    internships_pending: int = 0


class InstituteAcademicsKpi(BaseModel):
    """Institute averages over sections (condonation-aware attendance)."""

    avg_attendance_pct: float | None = None
    students_with_results: int = 0
    avg_cgpa: float | None = None


class InstitutePlacementKpi(BaseModel):
    """Placement outcomes rolled up across all drives."""

    total_drives: int = 0
    open_drives: int = 0
    total_applications: int = 0
    placed_students: int = 0
    placement_rate: float | None = None
    avg_package: float | None = None
    highest_package: float | None = None
    recruiter_companies: int = 0


class InstituteEngagementKpi(BaseModel):
    """Day-to-day platform activity volumes."""

    assignments: int = 0
    submissions: int = 0
    materials: int = 0
    doubts_open: int = 0
    doubts_resolved: int = 0
    announcements: int = 0
    leave_pending: int = 0


class InstituteRiskKpi(BaseModel):
    """At-risk band distribution from the explainable risk model."""

    assessed_students: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class InstituteDashboardOut(BaseModel):
    """Whole-institute KPI snapshot for the admin dashboard."""

    generated_at: datetime
    users: InstituteUsersKpi
    structure: InstituteStructureKpi
    moat: InstituteMoatKpi
    academics: InstituteAcademicsKpi
    placement: InstitutePlacementKpi
    engagement: InstituteEngagementKpi
    risk: InstituteRiskKpi
