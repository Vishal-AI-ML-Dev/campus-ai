"""Operational usage queries for AI Mock Interview (Phase 7.7).

A pure, tenant-filtered query layer for the institute operations API. Every
function takes the caller's ``tenant_id`` and filters on it explicitly (RLS is
a second layer on Postgres). Results are plain dicts of counts, statuses,
ids, timings and costs - never interview content: no question, answer, plan,
resume, job description or report text is selected, not even internally
(turn counts are computed in SQL).

Usage (operational) analytics only. Performance analytics (scores by section,
placement outcomes) are Phase 9 and need their own consent design.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    AIUsageEvent,
    MockInterviewReport,
    MockInterviewSession,
    MockInterviewTurn,
    User,
)
from app.services import ai_circuit, ai_pricing
from app.services.ai_usage import FEATURE_MOCK_INTERVIEW

FAILURE_OUTCOMES = frozenset({"provider_error", "worker_error", "timeout", "unreachable"})
NOT_A_CALL = frozenset({"circuit_open", "quota_blocked"})
STEPS = ("plan", "turn", "report")
MAX_DAYS = 90


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def period_start(days: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct * (len(ordered) - 1)))))
    return ordered[index]


def _events(db: Session, tenant_id: int, since: datetime) -> list:
    return db.execute(
        select(
            AIUsageEvent.created_at,
            AIUsageEvent.user_id,
            AIUsageEvent.step,
            AIUsageEvent.outcome,
            AIUsageEvent.provider,
            AIUsageEvent.model,
            AIUsageEvent.llm_calls,
            AIUsageEvent.input_tokens,
            AIUsageEvent.output_tokens,
            AIUsageEvent.tokens_unreported,
            AIUsageEvent.latency_ms,
            AIUsageEvent.cost_micros,
            AIUsageEvent.cost_currency,
            AIUsageEvent.price_version,
        ).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.feature == FEATURE_MOCK_INTERVIEW,
            AIUsageEvent.created_at >= since,
        )
    ).all()


def _cost(events: list) -> dict:
    priced = [e for e in events if e.cost_micros is not None]
    unpriced = [e for e in events if e.cost_micros is None and e.outcome not in NOT_A_CALL]
    currencies = {e.cost_currency for e in priced}
    versions = sorted({e.price_version for e in priced if e.price_version})
    if not priced:
        status_ = (
            ai_pricing.STATUS_NOT_CONFIGURED
            if ai_pricing.active_table() is None
            else "no_priced_calls"
        )
        return {"status": status_, "currency": None, "total_micros": None,
                "priced_calls": 0, "unpriced_calls": len(unpriced), "price_versions": []}
    if len(currencies) > 1:
        return {"status": "mixed_currency", "currency": None, "total_micros": None,
                "priced_calls": len(priced), "unpriced_calls": len(unpriced),
                "price_versions": versions}
    return {
        "status": "estimated" if not unpriced else "partial",
        "currency": next(iter(currencies)),
        "total_micros": sum(e.cost_micros for e in priced),
        "priced_calls": len(priced),
        "unpriced_calls": len(unpriced),
        "price_versions": versions,
    }


def _breakdown(events: list, key) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for e in events:
        if e.outcome in NOT_A_CALL:
            continue
        groups[key(e)].append(e)
    rows = []
    for name, items in sorted(groups.items()):
        latencies = [e.latency_ms for e in items]
        rows.append({
            "key": name,
            "ai_calls": len(items),
            "ai_failures": sum(1 for e in items if e.outcome in FAILURE_OUTCOMES),
            "input_tokens": sum(e.input_tokens or 0 for e in items),
            "output_tokens": sum(e.output_tokens or 0 for e in items),
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        })
    return rows


def summary(db: Session, tenant_id: int, days: int) -> dict:
    since = period_start(days)
    sessions = db.execute(
        select(MockInterviewSession.created_at, MockInterviewSession.status, MockInterviewSession.mode)
        .where(MockInterviewSession.tenant_id == tenant_id, MockInterviewSession.created_at >= since)
    ).all()
    events = _events(db, tenant_id, since)
    calls = [e for e in events if e.outcome not in NOT_A_CALL]
    latencies = [e.latency_ms for e in calls]

    daily: dict = {}
    for i in range(days):
        day = (since + timedelta(days=i)).date()
        daily[day] = {"day": day, "sessions_created": 0, "ai_calls": 0, "ai_failures": 0,
                      "input_tokens": 0, "output_tokens": 0}
    for s in sessions:
        row = daily.get(_aware(s.created_at).date())
        if row is not None:
            row["sessions_created"] += 1
    for e in calls:
        row = daily.get(_aware(e.created_at).date())
        if row is None:
            continue
        row["ai_calls"] += 1
        row["ai_failures"] += e.outcome in FAILURE_OUTCOMES
        row["input_tokens"] += e.input_tokens or 0
        row["output_tokens"] += e.output_tokens or 0

    statuses = Counter(s.status for s in sessions)
    totals = {
        "sessions_created": len(sessions),
        "sessions_completed": statuses.get("completed", 0),
        "sessions_abandoned": statuses.get("abandoned", 0),
        "sessions_in_progress": statuses.get("in_progress", 0) + statuses.get("setup", 0),
        "ai_calls": len(calls),
        "ai_failures": sum(1 for e in calls if e.outcome in FAILURE_OUTCOMES),
        "circuit_open": sum(1 for e in events if e.outcome == "circuit_open"),
        "quota_blocked": sum(1 for e in events if e.outcome == "quota_blocked"),
        "llm_calls": sum(e.llm_calls or 0 for e in calls),
        "input_tokens": sum(e.input_tokens or 0 for e in calls),
        "output_tokens": sum(e.output_tokens or 0 for e in calls),
        "calls_with_unknown_tokens": sum(
            1 for e in calls if e.input_tokens is None and e.output_tokens is None
        ),
        # some LLM calls in the request reported usage, some did not (lower bound)
        "calls_with_partial_tokens": sum(
            1 for e in calls
            if (e.tokens_unreported or 0) > 0
            and not (e.input_tokens is None and e.output_tokens is None)
        ),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }
    return {
        "days": days,
        "since": since,
        "totals": totals,
        "daily": list(daily.values()),
        "by_step": _breakdown(events, lambda e: e.step),
        "by_model": _breakdown(events, lambda e: f"{e.provider or 'unknown'}/{e.model or 'unknown'}"),
        "by_outcome": dict(Counter(e.outcome for e in events)),
        "modes": dict(Counter(s.mode for s in sessions)),
        "cost": _cost(events),
    }


def students(db: Session, tenant_id: int, days: int, limit: int, offset: int) -> tuple[list[dict], int]:
    """Per-student counts (no content), most active first. Returns (page, total)."""
    since = period_start(days)
    session_rows = db.execute(
        select(
            MockInterviewSession.student_id,
            func.count(MockInterviewSession.id),
            func.sum(case((MockInterviewSession.status == "completed", 1), else_=0)),
            func.max(MockInterviewSession.created_at),
        )
        .where(MockInterviewSession.tenant_id == tenant_id, MockInterviewSession.created_at >= since)
        .group_by(MockInterviewSession.student_id)
    ).all()
    stats: dict[int, dict] = {
        sid: {"student_id": sid, "sessions_created": int(n), "sessions_completed": int(done or 0),
              "ai_calls": 0, "ai_failures": 0, "input_tokens": 0, "output_tokens": 0,
              "last_activity": last}
        for sid, n, done, last in session_rows
    }
    for e in _events(db, tenant_id, since):
        if e.user_id is None or e.outcome in NOT_A_CALL:
            continue
        row = stats.setdefault(e.user_id, {
            "student_id": e.user_id, "sessions_created": 0, "sessions_completed": 0,
            "ai_calls": 0, "ai_failures": 0, "input_tokens": 0, "output_tokens": 0,
            "last_activity": None})
        row["ai_calls"] += 1
        row["ai_failures"] += e.outcome in FAILURE_OUTCOMES
        row["input_tokens"] += e.input_tokens or 0
        row["output_tokens"] += e.output_tokens or 0
        if row["last_activity"] is None or _aware(e.created_at) > _aware(row["last_activity"]):
            row["last_activity"] = e.created_at
    if stats:
        names = dict(db.execute(
            select(User.id, User.full_name).where(User.tenant_id == tenant_id, User.id.in_(stats))
        ).all())
    else:
        names = {}
    rows = [{**r, "full_name": names[r["student_id"]]} for r in stats.values() if r["student_id"] in names]
    rows.sort(key=lambda r: (-r["sessions_created"], -r["ai_calls"], r["student_id"]))
    return rows[offset: offset + limit], len(rows)


def diagnostics(db: Session, tenant_id: int, session_id: int) -> dict:
    s = db.execute(
        select(
            MockInterviewSession.id, MockInterviewSession.student_id, MockInterviewSession.status,
            MockInterviewSession.mode, MockInterviewSession.interview_type,
            MockInterviewSession.duration_minutes, MockInterviewSession.created_at,
            MockInterviewSession.started_at, MockInterviewSession.completed_at,
        ).where(MockInterviewSession.id == session_id, MockInterviewSession.tenant_id == tenant_id)
    ).first()
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview not found")
    turns, answered = db.execute(
        select(
            func.count(MockInterviewTurn.id),
            func.sum(case((MockInterviewTurn.answer_text.is_not(None), 1), else_=0)),
        ).where(MockInterviewTurn.session_id == session_id, MockInterviewTurn.tenant_id == tenant_id)
    ).one()
    has_report = db.scalar(
        select(func.count(MockInterviewReport.id)).where(
            MockInterviewReport.session_id == session_id, MockInterviewReport.tenant_id == tenant_id)
    )
    events = db.scalars(
        select(AIUsageEvent).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.subject_type == "mock_interview_session",
            AIUsageEvent.subject_id == str(session_id),
        ).order_by(AIUsageEvent.created_at, AIUsageEvent.id)
    ).all()
    return {
        "session_id": s.id,
        "student_id": s.student_id,
        "status": s.status,
        "mode": s.mode,
        "interview_type": s.interview_type,
        "duration_minutes": s.duration_minutes,
        "created_at": s.created_at,
        "started_at": s.started_at,
        "completed_at": s.completed_at,
        "turns": int(turns or 0),
        "answered": int(answered or 0),
        "report_generated": bool(has_report),
        "events": [
            {"event_id": e.event_id, "created_at": e.created_at, "step": e.step, "outcome": e.outcome,
             "http_status": e.http_status, "error_class": e.error_class, "latency_ms": e.latency_ms,
             "llm_latency_ms": e.llm_latency_ms, "llm_calls": e.llm_calls,
             "input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
             "fallback_used": e.fallback_used, "provider": e.provider, "model": e.model,
             "request_id": e.request_id}
            for e in events
        ],
    }


def health(db: Session, tenant_id: int) -> dict:
    snap = ai_circuit.snapshot()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = db.execute(
        select(AIUsageEvent.step, AIUsageEvent.outcome).where(
            AIUsageEvent.tenant_id == tenant_id,
            AIUsageEvent.feature == FEATURE_MOCK_INTERVIEW,
            AIUsageEvent.created_at >= since,
        )
    ).all()
    steps = []
    for step in STEPS:
        calls = [o for st, o in recent if st == step and o not in NOT_A_CALL]
        failures = sum(1 for o in calls if o in FAILURE_OUTCOMES)
        state = snap.get(step, {"state": "closed", "consecutive_failures": 0, "retry_in_seconds": None})
        steps.append({
            "step": step,
            "state": state["state"] if ai_circuit.active() else "disabled",
            "consecutive_failures": state["consecutive_failures"],
            "retry_in_seconds": state["retry_in_seconds"],
            "last_hour_calls": len(calls),
            "last_hour_failures": failures,
            "last_hour_error_rate": round(failures / len(calls), 3) if calls else None,
        })
    return {
        "circuit_enabled": ai_circuit.active(),
        "steps": steps,
        "note": "Circuit state is per backend process; error rates are your institute's own calls.",
    }
