"""Response/request schemas for AI Mock Interview operations (Phase 7.6 / 7.7).

Every model is content-free by construction: ids, counts, statuses, classes,
timings and settings only. None of them has a field for prompts, questions,
answers, resumes, job descriptions, plans, reports or error messages.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class UsageMeOut(BaseModel):
    """GET /mock-interview/usage/me - the student's own availability."""

    available: bool
    reason: str | None = None  # paused | daily_limit | institute_limit
    daily_limit: int
    used_today: int
    remaining_today: int
    voice_allowed: bool
    video_allowed: bool


class ControlLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_sessions_per_day: int | None = Field(default=None, ge=1)
    tenant_sessions_per_month: int | None = Field(default=None, ge=1)
    tenant_tokens_per_month: int | None = Field(default=None, ge=1)


class ControlsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool = False
    voice_allowed: bool = True
    video_allowed: bool = True
    limits: ControlLimits = Field(default_factory=ControlLimits)


class ControlsOut(BaseModel):
    feature: str
    configured: bool
    paused: bool
    voice_allowed: bool
    video_allowed: bool
    limits: dict[str, int | None]
    effective_limits: dict[str, int | None]
    platform_limits: dict[str, int | None]
    platform_voice_enabled: bool
    platform_video_enabled: bool
    updated_at: datetime | None = None
    can_edit: bool = False


class CostOut(BaseModel):
    # pricing_not_configured | estimated | partial
    status: str
    currency: str | None = None
    total_micros: int | None = None
    priced_calls: int = 0
    unpriced_calls: int = 0
    price_versions: list[str] = Field(default_factory=list)


class UsageTotals(BaseModel):
    sessions_created: int = 0
    sessions_completed: int = 0
    sessions_abandoned: int = 0
    sessions_in_progress: int = 0
    ai_calls: int = 0
    ai_failures: int = 0
    circuit_open: int = 0
    quota_blocked: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    calls_with_unknown_tokens: int = 0
    calls_with_partial_tokens: int = 0
    avg_latency_ms: int | None = None
    p95_latency_ms: int | None = None


class DailyUsage(BaseModel):
    day: date
    sessions_created: int = 0
    ai_calls: int = 0
    ai_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class BreakdownRow(BaseModel):
    key: str
    ai_calls: int = 0
    ai_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    avg_latency_ms: int | None = None


class UsageSummaryOut(BaseModel):
    days: int
    since: datetime
    totals: UsageTotals
    daily: list[DailyUsage]
    by_step: list[BreakdownRow]
    by_model: list[BreakdownRow]
    by_outcome: dict[str, int]
    modes: dict[str, int]
    cost: CostOut


class StudentUsageOut(BaseModel):
    student_id: int
    full_name: str
    sessions_created: int
    sessions_completed: int
    ai_calls: int
    ai_failures: int
    input_tokens: int
    output_tokens: int
    last_activity: datetime | None = None


class DiagnosticEvent(BaseModel):
    event_id: str
    created_at: datetime
    step: str
    outcome: str
    http_status: int | None = None
    error_class: str | None = None
    latency_ms: int
    llm_latency_ms: int | None = None
    llm_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    fallback_used: bool | None = None
    provider: str | None = None
    model: str | None = None
    request_id: str | None = None


class SessionDiagnosticsOut(BaseModel):
    session_id: int
    student_id: int
    status: str
    mode: str
    interview_type: str
    duration_minutes: int
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    turns: int
    answered: int
    report_generated: bool
    events: list[DiagnosticEvent]


class StepHealth(BaseModel):
    step: str
    state: str
    consecutive_failures: int = 0
    retry_in_seconds: int | None = None
    last_hour_calls: int = 0
    last_hour_failures: int = 0
    last_hour_error_rate: float | None = None


class HealthOut(BaseModel):
    circuit_enabled: bool
    steps: list[StepHealth]
    note: str
