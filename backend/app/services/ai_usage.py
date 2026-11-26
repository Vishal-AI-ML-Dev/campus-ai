"""AI usage ledger writer (Mock Interview Phase 7.3; requirements R3, R4, R5).

One ``ai_usage_events`` row per AI invocation, keyed by the invocation id the
backend generated BEFORE calling the worker (Phase 7.1). The write:

  * runs in its OWN short session (tenant GUC set for RLS) after the worker
    call, so it never holds or touches the request's transaction;
  * is idempotent - ``INSERT ... ON CONFLICT (event_id) DO NOTHING`` - so a
    retry (in-process or RQ) can never create a second row for one call;
  * never raises into the caller (R3): a failed write is re-queued through the
    existing job queue when ``REDIS_URL`` is set (``record_event_job``, RQ
    retry policy from ``jobs.py``), otherwise retried once in-process, then
    counted as lost (``ai_usage_ledger_writes_total{result="lost"}``).

Content-free (R5): only ids, counts, classes, timings and statuses are
accepted - ``build_event`` copies a fixed set of fields and drops the rest.

Callers set who/what the call is for with ``usage_context(...)`` (a context
variable), so the existing engine functions keep their signatures. Without a
context nothing is written (``result="skipped"``).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

import jobs
from app.core.db import SessionLocal, set_current_tenant
from app.models import AIUsageEvent
from app.observability import ai_metrics
from app.observability.observability import safe_correlation_id
from app.services import ai_pricing

logger = logging.getLogger("campus_ai.ai_usage")

FEATURE_MOCK_INTERVIEW = "mock_interview"


@dataclass(frozen=True)
class UsageContext:
    tenant_id: int
    user_id: int | None
    feature: str = FEATURE_MOCK_INTERVIEW
    subject_type: str | None = None
    subject_id: str | None = None


_context: ContextVar[UsageContext | None] = ContextVar("ai_usage_context", default=None)


@contextmanager
def usage_context(
    tenant_id: int,
    user_id: int | None,
    *,
    feature: str = FEATURE_MOCK_INTERVIEW,
    subject_type: str | None = None,
    subject_id: int | str | None = None,
) -> Iterator[UsageContext]:
    ctx = UsageContext(
        tenant_id=int(tenant_id),
        user_id=user_id,
        feature=feature[:40],
        subject_type=(subject_type or None) and subject_type[:40],
        subject_id=None if subject_id is None else str(subject_id)[:40],
    )
    token = _context.set(ctx)
    try:
        yield ctx
    finally:
        _context.reset(token)


def current_context() -> UsageContext | None:
    return _context.get()


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if 0 <= number < 2**31 else None


def _short(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def build_event(
    *,
    ctx: UsageContext,
    event_id: str,
    step: str,
    outcome: str,
    latency_ms: float,
    http_status: int | None = None,
    error_class: str | None = None,
    usage: dict | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """A content-free ledger row as a plain dict (RQ-serializable)."""
    usage = usage if isinstance(usage, dict) else {}
    provider = _short(usage.get("provider"), 40)
    model = _short(usage.get("model"), 80)
    input_tokens = _count(usage.get("input_tokens"))
    output_tokens = _count(usage.get("output_tokens"))
    tokens_unreported = _count(usage.get("tokens_unreported")) if usage else None
    # Some LLM calls in this request reported no usage: the token sums are a
    # lower bound, so the request's cost is unknown (never an undercount
    # presented as an estimate - P7-D9). Raw token sums are still stored.
    incomplete = bool(tokens_unreported)
    cost = ai_pricing.estimate(provider, model,
                               None if incomplete else input_tokens,
                               None if incomplete else output_tokens)
    fallback = usage.get("fallback_used")
    return {
        "event_id": event_id[:64],
        "tenant_id": ctx.tenant_id,
        "user_id": ctx.user_id,
        "feature": ctx.feature,
        "step": step[:40],
        "subject_type": ctx.subject_type,
        "subject_id": ctx.subject_id,
        "outcome": outcome[:30],
        "http_status": http_status if isinstance(http_status, int) else None,
        "error_class": _short(error_class, 80),
        "provider": provider,
        "model": model,
        "llm_calls": _count(usage.get("llm_calls")) if usage else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_unreported": tokens_unreported,
        "fallback_used": fallback if isinstance(fallback, bool) else None,
        "latency_ms": max(0, int(round(latency_ms))),
        "llm_latency_ms": _count(usage.get("llm_latency_ms")),
        "cost_micros": cost.cost_micros,
        "cost_currency": cost.currency,
        "price_version": cost.price_version if cost.cost_micros is not None else None,
        "request_id": safe_correlation_id(request_id),
        "created_at": datetime.now(timezone.utc),
    }


def record_event(event: dict[str, Any]) -> bool:
    """Insert one row; True if inserted, False if ``event_id`` already exists.

    Raises on database errors (callers decide what to do).
    """
    db = SessionLocal()
    try:
        set_current_tenant(db, event["tenant_id"])
        dialect = db.get_bind().dialect.name
        if dialect in ("postgresql", "sqlite"):
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            stmt = (
                dialect_insert(AIUsageEvent)
                .values(**event)
                .on_conflict_do_nothing(index_elements=["event_id"])
                # Explicit RETURNING: a row back = inserted, none = conflict
                # skipped. (rowcount is -1 on Postgres when SQLAlchemy adds an
                # implicit RETURNING, so it cannot tell the two apart.)
                .returning(AIUsageEvent.id)
            )
            inserted = db.execute(stmt).first() is not None
            db.commit()
            return inserted
        try:  # other dialects: plain insert, duplicate = integrity error
            db.execute(insert(AIUsageEvent).values(**event))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False
    finally:
        db.close()


def record_event_job(event: dict[str, Any]) -> bool:
    """RQ entry point (``app.services.ai_usage.record_event_job``).

    Raises on failure so RQ's retry policy applies; idempotent on success.
    """
    return record_event(event)


def _enqueue_retry(event: dict[str, Any]) -> bool:
    queue = jobs.get_queue()
    if queue is None:
        return False
    try:
        options: dict[str, Any] = {"job_timeout": 60, "result_ttl": 600, "failure_ttl": jobs.FAILURE_TTL}
        retry = jobs._retry()
        if retry is not None:
            options["retry"] = retry
        queue.enqueue(record_event_job, args=(event,), **options)
        return True
    except Exception as exc:  # noqa: BLE001 - broker trouble: fall through
        logger.warning("ai usage: enqueue failed (%s)", type(exc).__name__)
        return False


def record(event: dict[str, Any]) -> str:
    """Write a ledger row without ever raising. Returns the result label."""
    try:
        result = "ok" if record_event(event) else "duplicate"
    except Exception as exc:  # noqa: BLE001 - R3: never fail the student's request
        logger.warning("ai usage: ledger write failed (%s)", type(exc).__name__)
        if _enqueue_retry(event):
            result = "queued"
        else:
            try:
                result = "ok" if record_event(event) else "duplicate"
            except Exception as retry_exc:  # noqa: BLE001
                logger.warning("ai usage: ledger write lost (%s)", type(retry_exc).__name__)
                result = "lost"
    ai_metrics.ledger_write(result)
    return result


def record_call(**fields: Any) -> str:
    """Build and record an event for the current ``usage_context``.

    Never raises. Without a context (e.g. a caller outside the interview
    services) nothing is written.
    """
    try:
        ctx = current_context()
        if ctx is None:
            ai_metrics.ledger_write("skipped")
            return "skipped"
        return record(build_event(ctx=ctx, **fields))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai usage: event not built (%s)", type(exc).__name__)
        ai_metrics.ledger_write("lost")
        return "lost"
