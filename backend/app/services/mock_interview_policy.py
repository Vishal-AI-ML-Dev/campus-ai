"""Institute controls and quotas for AI Mock Interview (Phase 7.6; R6, R7, R8).

Effective policy = environment ceiling INTERSECT the institute's row:
  * environment flags/settings are the upper bound; an institute row can only
    narrow them (pause, turn voice/video off, set LOWER limits) - R6, R7;
  * everything here applies only when ``AI_MOCK_INTERVIEW_OPS_ENABLED`` is on
    (and the master gate allows the institute); otherwise nothing beyond the
    existing per-student limits is enforced;
  * limits are checked ONLY when a session is created (``enforce_create``).
    Start, answer, next, finish and report never consult them, so an interview
    already in progress always finishes and gets its report - R8.

Limits (all optional; None = no limit at that level):
  student_sessions_per_day  - <= the built-in 10 per student per 24 h
  tenant_sessions_per_month - <= AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX
  tenant_tokens_per_month   - <= AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX
Months are calendar months in UTC. Token budgets count provider-reported
tokens from the usage ledger; calls whose usage is unknown count as 0 (the
ledger keeps them as NULL, so this is visible, not hidden).

Concurrency: institute caps are checked under a row lock on the institute
(``SELECT ... FOR NO KEY UPDATE`` on Postgres; it does not block other
requests' foreign-key checks), taken after the per-student lock that
``create_session`` already holds, so parallel creates in one institute cannot
both pass the last free slot. SQLite (tests) has no row locks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AIFeatureControl, AIUsageEvent, MockInterviewSession, Tenant, User
from app.observability import ai_alerts, ai_metrics
from app.observability.observability import new_correlation_id, request_id_ctx
from app.services import ai_usage
from app.services.mock_interview_usage import NOT_A_CALL

logger = logging.getLogger("campus_ai.mock_interview_policy")

FEATURE = ai_usage.FEATURE_MOCK_INTERVIEW
STUDENT_DAILY_CEILING = 10  # = mock_interview.MAX_SESSIONS_PER_DAY (built-in limit)
LIMIT_KEYS = ("student_sessions_per_day", "tenant_sessions_per_month", "tenant_tokens_per_month")
# Hard upper bounds for values an institute may store when no env ceiling is set.
_ABSOLUTE_MAX = {
    "student_sessions_per_day": STUDENT_DAILY_CEILING,
    "tenant_sessions_per_month": 10_000_000,
    "tenant_tokens_per_month": 10**13,
}
SOFT_LIMIT_RATIO = 0.8

MSG_PAUSED = "Practice interviews are paused for your institute right now. Please try again later."
MSG_TENANT_SESSIONS = "Your institute has reached its practice interview limit for this month."
MSG_TENANT_TOKENS = "Your institute has reached its AI usage limit for this month."


def _positive_int(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isascii() and raw.isdecimal() and int(raw) > 0:
        return int(raw)
    logger.warning("ignoring invalid mock-interview quota ceiling (must be a positive integer)")
    return None


def ceilings() -> dict[str, int | None]:
    """Platform upper bounds from the environment (None = uncapped)."""
    return {
        "student_sessions_per_day": STUDENT_DAILY_CEILING,
        "tenant_sessions_per_month": _positive_int(settings.AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX),
        "tenant_tokens_per_month": _positive_int(settings.AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX),
    }


def ops_enabled_for(tenant_id: int | None) -> bool:
    """Institute operations on for this institute (env master gate + ops flag)."""
    if not (settings.AI_MOCK_INTERVIEW_ENABLED and settings.AI_MOCK_INTERVIEW_OPS_ENABLED):
        return False
    from app.routers.mock_interview import _allowed_tenant_ids  # avoid an import cycle

    allowed = _allowed_tenant_ids(settings.AI_MOCK_INTERVIEW_TENANT_IDS)
    return allowed is None or tenant_id in allowed


@dataclass
class Effective:
    paused: bool = False
    voice_allowed: bool = True
    video_allowed: bool = True
    limits: dict[str, int | None] = field(default_factory=dict)
    configured: bool = False  # an institute row exists


def load_controls(db: Session, tenant_id: int) -> AIFeatureControl | None:
    return db.scalar(
        select(AIFeatureControl).where(
            AIFeatureControl.tenant_id == tenant_id, AIFeatureControl.feature == FEATURE
        )
    )


def _narrow(stored: object, ceiling: int | None) -> int | None:
    value = stored if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0 else None
    if value is None:
        return ceiling
    return value if ceiling is None else min(value, ceiling)


def effective(db: Session, tenant_id: int) -> Effective:
    """What applies to this institute right now (env ceiling AND row)."""
    caps = ceilings()
    if not ops_enabled_for(tenant_id):
        return Effective(limits={"student_sessions_per_day": STUDENT_DAILY_CEILING,
                                 "tenant_sessions_per_month": None, "tenant_tokens_per_month": None})
    row = load_controls(db, tenant_id)
    stored = (row.limits_json if row is not None and isinstance(row.limits_json, dict) else {})
    return Effective(
        paused=bool(row.paused) if row is not None else False,
        voice_allowed=bool(row.voice_allowed) if row is not None else True,
        video_allowed=bool(row.video_allowed) if row is not None else True,
        limits={k: _narrow(stored.get(k), caps[k]) for k in LIMIT_KEYS},
        configured=row is not None,
    )


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def student_sessions_last_day(db: Session, user: User) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    created = db.scalars(
        select(MockInterviewSession.created_at).where(
            MockInterviewSession.tenant_id == user.tenant_id,
            MockInterviewSession.student_id == user.id,
        ).order_by(MockInterviewSession.id.desc()).limit(STUDENT_DAILY_CEILING + 1)
    ).all()
    return sum(1 for c in created if c is not None and _aware(c) > since)


def tenant_sessions_this_month(db: Session, tenant_id: int) -> int:
    return int(db.scalar(
        select(func.count(MockInterviewSession.id)).where(
            MockInterviewSession.tenant_id == tenant_id,
            MockInterviewSession.created_at >= month_start(),
        )
    ) or 0)


def tenant_tokens_this_month(db: Session, tenant_id: int) -> int:
    total = db.scalar(
        select(
            func.coalesce(func.sum(func.coalesce(AIUsageEvent.input_tokens, 0)
                                   + func.coalesce(AIUsageEvent.output_tokens, 0)), 0)
        ).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.feature == FEATURE,
            AIUsageEvent.created_at >= month_start(),
        )
    )
    return int(total or 0)


def tenant_unreported_calls_this_month(db: Session, tenant_id: int) -> tuple[int, int]:
    """(AI calls with unknown or partial token usage, all AI calls) this month.

    Budgets count reported tokens only (spec 15.12, option a); this measures
    how much of the month that leaves uncounted. Not a token estimate.
    """
    unreported = (
        (AIUsageEvent.tokens_unreported > 0)
        | (AIUsageEvent.input_tokens.is_(None) & AIUsageEvent.output_tokens.is_(None))
    )
    row = db.execute(
        select(
            func.count(AIUsageEvent.id),
            func.coalesce(func.sum(case((unreported, 1), else_=0)), 0),
        ).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.feature == FEATURE,
            AIUsageEvent.created_at >= month_start(),
            AIUsageEvent.outcome.notin_(NOT_A_CALL),
        )
    ).one()
    return int(row[1] or 0), int(row[0] or 0)


def _unreported_alert(db: Session, tenant_id: int) -> None:
    """Warn (never block) when much of the month's usage is missing from the budget."""
    percent = settings.AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT
    if percent <= 0:
        return
    unreported, calls = tenant_unreported_calls_this_month(db, tenant_id)
    if calls < settings.AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS:
        return
    share = int(100 * unreported / calls)
    if unreported and share >= percent:
        ai_alerts.alert(
            "mock_interview_token_usage_unreported",
            f"{share}% of an institute's AI calls this month had unknown token usage; "
            "its token budget counts only reported tokens.",
            severity="warning",
            key=f"{tenant_id}:{month_start().date()}",
            tenant_id=tenant_id,
            percent=share,
            calls=calls,
        )


def _block(db: Session, user: User, code: int, detail: str, reason: str) -> HTTPException:
    """Refuse a new session: release the locks, record it, return the error."""
    db.rollback()  # release the user/tenant row locks BEFORE the ledger write
    ai_metrics.quota_block(reason)
    with ai_usage.usage_context(user.tenant_id, user.id):
        ai_usage.record_call(
            event_id=new_correlation_id(), step="create", outcome="quota_blocked",
            latency_ms=0, error_class=reason, request_id=request_id_ctx.get(),
        )
    return HTTPException(status_code=code, detail=detail)


def _soft_alert(tenant_id: int, kind: str, used: int, limit: int) -> None:
    if used >= SOFT_LIMIT_RATIO * limit:
        ai_alerts.alert(
            "mock_interview_quota_near_limit",
            f"An institute has used {int(100 * used / limit)}% of its monthly {kind} limit.",
            severity="warning",
            key=f"{tenant_id}:{kind}:{month_start().date()}",
            tenant_id=tenant_id,
            kind=kind,
            percent=int(100 * used / limit),
        )


def enforce_create(db: Session, user: User, mode: str) -> None:
    """Raise 403/422/429 if the institute's controls refuse a NEW session.

    Called by ``create_session`` after its own checks and its per-student lock.
    No-op unless institute operations are on for this institute.
    """
    if not ops_enabled_for(user.tenant_id):
        return
    eff = effective(db, user.tenant_id)
    if eff.paused:
        raise _block(db, user, status.HTTP_403_FORBIDDEN, MSG_PAUSED, "paused")
    if mode == "voice" and not eff.voice_allowed:
        raise _block(db, user, 422, "Voice interviews are not available", "voice_disabled")
    if mode == "video" and not eff.video_allowed:
        raise _block(db, user, 422, "Video interviews are not available", "video_disabled")
    daily = eff.limits.get("student_sessions_per_day")
    if daily is not None and daily < STUDENT_DAILY_CEILING:
        if student_sessions_last_day(db, user) >= daily:
            raise _block(db, user, status.HTTP_429_TOO_MANY_REQUESTS,
                         f"You can start up to {daily} practice interviews per day.",
                         "student_daily_limit")
    month_sessions = eff.limits.get("tenant_sessions_per_month")
    month_tokens = eff.limits.get("tenant_tokens_per_month")
    if month_sessions is None and month_tokens is None:
        return
    # Serialize creates per institute for the count below (Postgres row lock).
    db.execute(select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update(key_share=True))
    if month_sessions is not None:
        used = tenant_sessions_this_month(db, user.tenant_id)
        if used >= month_sessions:
            raise _block(db, user, status.HTTP_429_TOO_MANY_REQUESTS, MSG_TENANT_SESSIONS,
                         "institute_monthly_sessions")
        _soft_alert(user.tenant_id, "sessions", used + 1, month_sessions)
    if month_tokens is not None:
        used = tenant_tokens_this_month(db, user.tenant_id)
        if used >= month_tokens:
            raise _block(db, user, status.HTTP_429_TOO_MANY_REQUESTS, MSG_TENANT_TOKENS,
                         "institute_monthly_tokens")
        _soft_alert(user.tenant_id, "tokens", used, month_tokens)
        _unreported_alert(db, user.tenant_id)


def modes_allowed(db: Session, tenant_id: int) -> tuple[bool, bool]:
    """(voice_allowed, video_allowed) at institute level (True when ops is off)."""
    eff = effective(db, tenant_id)
    return eff.voice_allowed, eff.video_allowed


def availability(db: Session, user: User) -> dict:
    """Remaining-quota view for one student (no other student's or institute numbers)."""
    eff = effective(db, user.tenant_id)
    daily = eff.limits.get("student_sessions_per_day") or STUDENT_DAILY_CEILING
    used = student_sessions_last_day(db, user)
    reason = None
    if eff.paused:
        reason = "paused"
    elif used >= daily:
        reason = "daily_limit"
    else:
        cap = eff.limits.get("tenant_sessions_per_month")
        if cap is not None and tenant_sessions_this_month(db, user.tenant_id) >= cap:
            reason = "institute_limit"
        cap = eff.limits.get("tenant_tokens_per_month")
        if reason is None and cap is not None and tenant_tokens_this_month(db, user.tenant_id) >= cap:
            reason = "institute_limit"
    return {
        "available": reason is None,
        "reason": reason,
        "daily_limit": daily,
        "used_today": min(used, daily),
        "remaining_today": max(0, daily - used),
        "voice_allowed": eff.voice_allowed,
        "video_allowed": eff.video_allowed,
    }


# --- Admin updates --------------------------------------------------------------------
def validate_limits(limits: dict[str, int | None]) -> dict[str, int | None]:
    """Reject values above the environment ceiling (R7: institutes only tighten)."""
    caps = ceilings()
    clean: dict[str, int | None] = {}
    for key in LIMIT_KEYS:
        value = limits.get(key)
        if value is None:
            clean[key] = None
            continue
        upper = caps[key] if caps[key] is not None else _ABSOLUTE_MAX[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > upper:
            raise HTTPException(
                status_code=422,
                detail=f"{key} must be between 1 and {upper} (the platform limit).",
            )
        clean[key] = value
    return clean


def controls_view(db: Session, tenant_id: int) -> dict:
    row = load_controls(db, tenant_id)
    eff = effective(db, tenant_id)
    stored = row.limits_json if row is not None and isinstance(row.limits_json, dict) else {}
    return {
        "feature": FEATURE,
        "configured": row is not None,
        "paused": eff.paused,
        "voice_allowed": eff.voice_allowed,
        "video_allowed": eff.video_allowed,
        "limits": {k: stored.get(k) for k in LIMIT_KEYS},
        "effective_limits": eff.limits,
        "platform_limits": ceilings(),
        "platform_voice_enabled": bool(settings.AI_MOCK_INTERVIEW_VOICE_ENABLED),
        "platform_video_enabled": bool(settings.AI_MOCK_INTERVIEW_VIDEO_ENABLED),
        "updated_at": row.updated_at if row is not None else None,
    }


def update_controls(
    db: Session, actor: User, *, paused: bool, voice_allowed: bool, video_allowed: bool,
    limits: dict[str, int | None],
) -> tuple[AIFeatureControl, dict, dict]:
    """Upsert this institute's row. Returns (row, before, after) for the audit
    summary. The caller commits (audit row in the same transaction)."""
    clean = validate_limits(limits)
    row = load_controls(db, actor.tenant_id)
    before = _snapshot(row)
    now = datetime.now(timezone.utc)
    if row is None:
        row = AIFeatureControl(tenant_id=actor.tenant_id, feature=FEATURE, created_at=now)
        db.add(row)
    row.paused = bool(paused)
    row.voice_allowed = bool(voice_allowed)
    row.video_allowed = bool(video_allowed)
    row.limits_json = dict(clean)  # reassigned, never mutated in place
    row.updated_by_id = actor.id
    row.updated_at = now
    return row, before, _snapshot(row)


def _snapshot(row: AIFeatureControl | None) -> dict:
    if row is None:
        return {"paused": False, "voice_allowed": True, "video_allowed": True,
                **{k: None for k in LIMIT_KEYS}}
    stored = row.limits_json if isinstance(row.limits_json, dict) else {}
    return {"paused": bool(row.paused), "voice_allowed": bool(row.voice_allowed),
            "video_allowed": bool(row.video_allowed), **{k: stored.get(k) for k in LIMIT_KEYS}}
