"""AI governance: spending and abuse protection for EVERY AI path (Phase 10.3).

Owner decisions: D4 (Rs 0/month, free tier only, stop new AI calls when the
free quota is exhausted, non-AI features keep working) and OQ-2 (OFF in code,
REQUIRED in production - ``config.ai_governance_issues`` refuses to boot a
production backend without it).

Two layers:

* Kill switches - ALWAYS active (they default to "nothing killed", so they
  change nothing until an operator sets them): ``AI_KILL_SWITCH`` refuses every
  new AI call; ``AI_DISABLED_FEATURES`` refuses the listed features.

* Governance - only while ``AI_GOVERNANCE_ENABLED`` is true:
    - provider-quota cooldown: once the provider reports its quota/rate limit
      as exhausted, new calls are refused (no provider call) for
      ``AI_QUOTA_COOLDOWN_SECONDS`` and an ``ai_quota_exhausted`` alert fires;
    - institute and per-user daily request caps, counted from the
      RLS-protected ``ai_usage_events`` ledger (calls that reached the worker,
      including failed ones);
    - platform daily request + token caps in ``ai_platform_usage_daily``
      (durable, shared by every process). A request is RESERVED atomically
      before the worker call, so a failed, timed-out or retried provider call
      still counts - it can never bypass the cap;
    - a process-wide concurrency cap with a short wait, and one in-flight
      mentor/resume/ATS call per user;
    - Idempotency-Key replay for mentor/resume/ATS (``idempotency_*``).

Fail closed: if the platform counter cannot be read or reserved, the call is
refused. Refusals never reach the worker or the provider.

Privacy: refusals, alerts and metrics carry feature names, reasons, tenant ids
and counts only - never prompts, answers, resumes, names or e-mails.

Multi-process note: the concurrency cap, the per-user in-flight guard, the
provider cooldown and the idempotency cache are per process (the free-tier
deployment runs one backend process). The daily caps are durable and global.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, time as dt_time, timezone
from typing import Any, Callable, Iterator

from fastapi import HTTPException, status
from sqlalchemy import func, select, text

from app.core.config import AI_FEATURES, positive_int_or_none, settings
from app.core.db import SessionLocal, set_current_tenant
from app.models import AIUsageEvent
from app.observability import ai_alerts, ai_metrics

logger = logging.getLogger("campus_ai.ai_governance")

FEATURE_MOCK_INTERVIEW, FEATURE_MENTOR, FEATURE_RESUME, FEATURE_ATS, FEATURE_PROOF_SCORING = AI_FEATURES
# Features whose calls count against AI_USER_DAILY_REQUEST_MAX (interviews keep
# their own per-student limits; proof scoring is triggered by staff workflows).
USER_CAPPED_FEATURES = (FEATURE_MENTOR, FEATURE_RESUME, FEATURE_ATS)
# Ledger outcomes that did NOT reach the worker (not counted against caps).
NOT_A_CALL = ("circuit_open", "quota_blocked")

_MESSAGES = {
    "kill_switch": "AI features are switched off right now. Everything else keeps working.",
    "feature_disabled": "This AI feature is switched off right now. Everything else keeps working.",
    "provider_quota": "The free AI quota is used up for now. Please try again later; "
                      "everything else keeps working.",
    "platform_requests": "The daily AI limit for the platform has been reached. Please try again "
                         "tomorrow; everything else keeps working.",
    "platform_tokens": "The daily AI limit for the platform has been reached. Please try again "
                       "tomorrow; everything else keeps working.",
    "platform_unavailable": "AI features are temporarily unavailable. Please try again shortly.",
    "tenant_requests": "Your institute's daily AI limit has been reached. Please try again tomorrow.",
    "user_requests": "You have reached your daily AI limit. Please try again tomorrow.",
    "user_busy": "You already have an AI request running. Please wait for it to finish.",
    "busy": "The AI service is busy. Please try again in a few seconds.",
    "circuit_open": "The AI service is having trouble right now. Please try again in a minute.",
}
_STATUS_429 = frozenset({"tenant_requests", "user_requests", "user_busy"})


class AIRefused(Exception):
    """A new AI call was refused before reaching the worker/provider."""

    def __init__(self, feature: str, reason: str, *, retry_after: int | None = None) -> None:
        super().__init__(f"{feature}: {reason}")
        self.feature = feature
        self.reason = reason
        self.retry_after = retry_after

    @property
    def status_code(self) -> int:
        return 429 if self.reason in _STATUS_429 else 503

    @property
    def message(self) -> str:
        return _MESSAGES.get(self.reason, _MESSAGES["platform_unavailable"])


def enabled() -> bool:
    return bool(settings.AI_GOVERNANCE_ENABLED)


def disabled_features() -> frozenset[str]:
    return frozenset(
        f for f in (x.strip().lower() for x in settings.AI_DISABLED_FEATURES.split(",")) if f
    )


def _seconds_to_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    return max(60, 86400 - (now.hour * 3600 + now.minute * 60 + now.second))


def _refuse(feature: str, reason: str, *, retry_after: int | None = None, **alert_fields: Any) -> AIRefused:
    ai_metrics.governance_refusal(feature, reason)
    if reason in ("platform_requests", "platform_tokens"):
        ai_alerts.alert(
            "ai_platform_quota_reached",
            "The platform daily AI cap was reached; new AI calls are refused until 00:00 UTC.",
            severity="error", key=reason, cap=reason,
        )
    elif reason == "tenant_requests":
        ai_alerts.alert(
            "ai_tenant_quota_reached",
            "An institute reached its daily AI request cap; its new AI calls are refused until 00:00 UTC.",
            severity="warning", key=alert_fields.get("tenant_id"), **alert_fields,
        )
    elif reason == "platform_unavailable":
        ai_alerts.alert(
            "ai_governance_unavailable",
            "The AI usage counter could not be checked; AI calls are refused (fail closed).",
            severity="error", key="counter",
        )
    return AIRefused(feature, reason, retry_after=retry_after)


# --------------------------------------------------------------------------- caller

_caller: ContextVar[tuple[int | None, int | None] | None] = ContextVar("ai_caller", default=None)


@contextmanager
def caller(tenant_id: int | None, user_id: int | None) -> Iterator[None]:
    """Who the AI calls in this block are for (routes set it; ``ai_client``
    reads it), so the existing client function signatures stay unchanged."""
    token = _caller.set((tenant_id, user_id))
    try:
        yield
    finally:
        _caller.reset(token)


def current_caller() -> tuple[int | None, int | None]:
    return _caller.get() or (None, None)


# --------------------------------------------------------------------------- switches


def check_switches(feature: str) -> None:
    """Kill switches (always active). Raises ``AIRefused``."""
    if settings.AI_KILL_SWITCH:
        raise _refuse(feature, "kill_switch")
    if feature in disabled_features():
        raise _refuse(feature, "feature_disabled")


# --------------------------------------------------------------------------- provider cooldown

_cooldown_lock = threading.Lock()
_cooldown_until = 0.0
_clock: Callable[[], float] = time.monotonic


def cooldown_remaining() -> int:
    with _cooldown_lock:
        return max(0, int(round(_cooldown_until - _clock())))


def mark_provider_quota_exhausted(feature: str) -> None:
    """The provider said its quota/rate limit is exhausted: stop new calls."""
    global _cooldown_until
    if not enabled():
        return
    with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, _clock() + settings.AI_QUOTA_COOLDOWN_SECONDS)
    ai_metrics.governance_refusal(feature, "provider_quota_signal")
    ai_alerts.alert(
        "ai_quota_exhausted",
        "The AI provider reported its free quota/rate limit as exhausted; new AI calls are "
        f"refused for {settings.AI_QUOTA_COOLDOWN_SECONDS}s.",
        severity="error", key="provider", feature=feature,
    )


def is_quota_signal(http_status: int | None, usage: dict | None) -> bool:
    """Did this worker reply report provider quota exhaustion?"""
    if http_status == 429:
        return True
    return isinstance(usage, dict) and usage.get("quota_exhausted") is True


# --------------------------------------------------------------------------- daily counts


def _day_start() -> datetime:
    return datetime.combine(datetime.now(timezone.utc).date(), dt_time.min, tzinfo=timezone.utc)


def _ledger_count(tenant_id: int, *, user_id: int | None = None,
                  features: tuple[str, ...] | None = None) -> int:
    db = SessionLocal()
    try:
        set_current_tenant(db, tenant_id)
        query = select(func.count(AIUsageEvent.id)).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.created_at >= _day_start(),
            AIUsageEvent.outcome.not_in(NOT_A_CALL),
        )
        if user_id is not None:
            query = query.where(AIUsageEvent.user_id == user_id)
        if features is not None:
            query = query.where(AIUsageEvent.feature.in_(features))
        return int(db.scalar(query) or 0)
    finally:
        db.close()


def _platform_guc(db) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.ai_platform_counter', 'on', true)"))


def _ensure_platform_row(db, day) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    from app.models import AIPlatformUsageDaily

    db.execute(
        dialect_insert(AIPlatformUsageDaily)
        .values(day=day, requests=0, tokens=0, updated_at=datetime.now(timezone.utc))
        .on_conflict_do_nothing(index_elements=["day"])
    )


def platform_usage_today() -> dict[str, int]:
    """Today's platform counters (admin/drill visibility). Raises on DB errors."""
    from app.models import AIPlatformUsageDaily

    db = SessionLocal()
    try:
        _platform_guc(db)
        row = db.get(AIPlatformUsageDaily, datetime.now(timezone.utc).date())
        return {"requests": row.requests if row else 0, "tokens": row.tokens if row else 0}
    finally:
        db.close()


def _reserve_platform_request(feature: str) -> None:
    """Atomically count one request against the platform caps, or refuse."""
    request_cap = positive_int_or_none(settings.AI_PLATFORM_DAILY_REQUEST_MAX)
    token_cap = positive_int_or_none(settings.AI_PLATFORM_DAILY_TOKEN_MAX)
    day = datetime.now(timezone.utc).date()
    db = SessionLocal()
    try:
        _platform_guc(db)
        _ensure_platform_row(db, day)
        reserved = db.execute(
            text(
                "UPDATE ai_platform_usage_daily SET requests = requests + 1, updated_at = :now "
                "WHERE day = :day AND (CAST(:rcap AS BIGINT) IS NULL OR requests < CAST(:rcap AS BIGINT)) "
                "AND (CAST(:tcap AS BIGINT) IS NULL OR tokens < CAST(:tcap AS BIGINT)) RETURNING requests"
            ),
            {"now": datetime.now(timezone.utc), "day": day, "rcap": request_cap, "tcap": token_cap},
        ).first()
        if reserved is None:
            row = db.execute(
                text("SELECT requests, tokens FROM ai_platform_usage_daily WHERE day = :day"),
                {"day": day},
            ).first()
            db.rollback()
            tokens_hit = token_cap is not None and row is not None and row.tokens >= token_cap
            raise _refuse(feature, "platform_tokens" if tokens_hit else "platform_requests",
                          retry_after=_seconds_to_utc_midnight())
        db.commit()
    except AIRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed on counter trouble
        db.rollback()
        logger.warning("ai governance: platform counter unavailable (%s)", type(exc).__name__)
        raise _refuse(feature, "platform_unavailable", retry_after=60) from exc
    finally:
        db.close()


def refund_platform_request() -> None:
    """Give back today's reservation for a call that never reached the worker.

    Used when the circuit breaker refuses AFTER admission: no provider call was
    made, so the platform budget must not shrink during an outage. Never raises.
    """
    if not enabled():
        return
    db = SessionLocal()
    try:
        _platform_guc(db)
        db.execute(
            text("UPDATE ai_platform_usage_daily SET requests = requests - 1, updated_at = :now "
                 "WHERE day = :day AND requests > 0"),
            {"now": datetime.now(timezone.utc), "day": datetime.now(timezone.utc).date()},
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - worst case the cap is slightly conservative
        db.rollback()
        logger.warning("ai governance: refund not recorded (%s)", type(exc).__name__)
    finally:
        db.close()


def add_platform_tokens(tokens: int | None) -> None:
    """Add provider-reported tokens to today's platform counter. Never raises."""
    if not enabled() or not isinstance(tokens, int) or tokens <= 0:
        return
    db = SessionLocal()
    try:
        _platform_guc(db)
        day = datetime.now(timezone.utc).date()
        _ensure_platform_row(db, day)
        db.execute(
            text("UPDATE ai_platform_usage_daily SET tokens = tokens + :n, updated_at = :now "
                 "WHERE day = :day"),
            {"n": int(tokens), "now": datetime.now(timezone.utc), "day": day},
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("ai governance: token count not recorded (%s)", type(exc).__name__)
    finally:
        db.close()


def usage_tokens(usage: dict | None) -> int | None:
    if not isinstance(usage, dict):
        return None
    total = 0
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total or None


# --------------------------------------------------------------------------- concurrency

_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0
_inflight_lock = threading.Lock()
_inflight_users: set[tuple[int, int]] = set()


def _semaphore() -> threading.BoundedSemaphore:
    global _slots, _slots_size
    with _slots_lock:
        if _slots is None or _slots_size != settings.AI_MAX_CONCURRENT_CALLS:
            _slots = threading.BoundedSemaphore(settings.AI_MAX_CONCURRENT_CALLS)
            _slots_size = settings.AI_MAX_CONCURRENT_CALLS
        return _slots


# --------------------------------------------------------------------------- admission


def precheck(feature: str, *, tenant_id: int | None, user_id: int | None) -> None:
    """Cheap checks a route runs BEFORE it writes anything (raises ``AIRefused``).

    Kill switches always; with governance on also the provider cooldown and the
    institute/user daily caps. Does not reserve anything - ``admit`` does.
    """
    check_switches(feature)
    if not enabled():
        return
    remaining = cooldown_remaining()
    if remaining > 0:
        raise _refuse(feature, "provider_quota", retry_after=remaining)
    if tenant_id is None:
        return
    try:
        tenant_cap = positive_int_or_none(settings.AI_TENANT_DAILY_REQUEST_MAX)
        if tenant_cap is not None and _ledger_count(tenant_id) >= tenant_cap:
            raise _refuse(feature, "tenant_requests", retry_after=_seconds_to_utc_midnight(),
                          tenant_id=tenant_id)
        user_cap = positive_int_or_none(settings.AI_USER_DAILY_REQUEST_MAX)
        if (user_id is not None and user_cap is not None and feature in USER_CAPPED_FEATURES
                and _ledger_count(tenant_id, user_id=user_id, features=USER_CAPPED_FEATURES) >= user_cap):
            raise _refuse(feature, "user_requests", retry_after=_seconds_to_utc_midnight())
    except AIRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.warning("ai governance: usage count unavailable (%s)", type(exc).__name__)
        raise _refuse(feature, "platform_unavailable", retry_after=60) from exc


@contextmanager
def admit(feature: str, *, tenant_id: int | None, user_id: int | None) -> Iterator[None]:
    """Admit ONE AI call (raises ``AIRefused`` before any worker call).

    Order: switches -> cooldown -> institute/user caps -> per-user in-flight ->
    concurrency slot (waits up to AI_CONCURRENCY_WAIT_SECONDS) -> platform
    reservation. The slot and the in-flight marker are released on exit, also
    when the caller is cancelled (e.g. a closed stream).
    """
    precheck(feature, tenant_id=tenant_id, user_id=user_id)
    if not enabled():
        yield
        return
    user_key = (int(tenant_id), int(user_id)) if (
        feature in USER_CAPPED_FEATURES and tenant_id is not None and user_id is not None) else None
    if user_key is not None:
        with _inflight_lock:
            if user_key in _inflight_users:
                raise _refuse(feature, "user_busy", retry_after=5)
            _inflight_users.add(user_key)
    slot = _semaphore()
    acquired = False
    try:
        acquired = slot.acquire(timeout=settings.AI_CONCURRENCY_WAIT_SECONDS)
        if not acquired:
            raise _refuse(feature, "busy", retry_after=5)
        _reserve_platform_request(feature)
        yield
    finally:
        if acquired:
            slot.release()
        if user_key is not None:
            with _inflight_lock:
                _inflight_users.discard(user_key)


# --------------------------------------------------------------------------- idempotency

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
_idem_lock = threading.Lock()
# (feature, user, key) -> (expires_at, request fingerprint, value or _PENDING).
# Only a SHA-256 fingerprint of the request is kept, never the body itself.
_idem: dict[tuple[str, int, str], tuple[float, str, Any]] = {}
_PENDING = object()
_REUSED_KEY = "This Idempotency-Key was already used for a different request."


def request_fingerprint(request: Any) -> str:
    """Deterministic SHA-256 of the canonical VALIDATED request (Phase 10 review #2).

    A pydantic model is dumped in JSON mode, then serialised with sorted keys
    and no whitespace, so the same validated body always gives the same hash.
    """
    data = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idem_key(feature: str, user_id: int, key: str | None) -> tuple[str, int, str] | None:
    if key is None or not enabled():
        return None
    if not _IDEMPOTENCY_KEY.match(key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Idempotency-Key must be 8-128 letters, digits, '-' or '_'.")
    return (feature, int(user_id), key)


def idempotency_begin(feature: str, user_id: int, key: str | None, *, request: Any) -> Any:
    """Return a stored response for a replayed key, else mark the key pending.

    No key, or governance off -> None (normal processing). Keys are scoped to
    the user and feature and bound to ``request_fingerprint(request)``:
    same key + same validated body -> the stored response (or 409 while the
    first request still runs); same key + a DIFFERENT body -> 409.
    """
    k = _idem_key(feature, user_id, key)
    if k is None:
        return None
    fingerprint = request_fingerprint(request)
    now = _clock()
    with _idem_lock:
        for stale in [x for x, (exp, _, _) in _idem.items() if exp <= now]:
            _idem.pop(stale, None)
        entry = _idem.get(k)
        if entry is not None:
            _, stored_fingerprint, value = entry
            if stored_fingerprint != fingerprint:
                ai_metrics.governance_refusal(feature, "idempotency_key_reused")
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_REUSED_KEY)
            if value is _PENDING:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="This request is already being processed.")
            ai_metrics.governance_refusal(feature, "idempotent_replay")
            return value
        _idem[k] = (now + settings.AI_IDEMPOTENCY_TTL_SECONDS, fingerprint, _PENDING)
    return None


def idempotency_finish(feature: str, user_id: int, key: str | None, value: Any) -> None:
    k = _idem_key(feature, user_id, key)
    if k is None:
        return
    with _idem_lock:
        entry = _idem.get(k)
        if entry is None or entry[2] is not _PENDING:
            return  # expired meanwhile, or not ours: store nothing unbound
        _idem[k] = (_clock() + settings.AI_IDEMPOTENCY_TTL_SECONDS, entry[1], value)


def idempotency_abort(feature: str, user_id: int, key: str | None) -> None:
    """Forget a pending key after a failure, so the client may retry it."""
    try:
        k = _idem_key(feature, user_id, key)
    except HTTPException:
        return
    if k is None:
        return
    with _idem_lock:
        entry = _idem.get(k)
        if entry is not None and entry[2] is _PENDING:
            _idem.pop(k, None)


def _reset() -> None:
    """Test hook: clear every in-process governance state."""
    global _cooldown_until, _slots, _slots_size
    with _cooldown_lock:
        _cooldown_until = 0.0
    with _slots_lock:
        _slots, _slots_size = None, 0
    with _inflight_lock:
        _inflight_users.clear()
    with _idem_lock:
        _idem.clear()
