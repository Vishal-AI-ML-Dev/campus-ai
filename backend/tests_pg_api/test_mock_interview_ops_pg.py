"""Phase 7 (operations) on real Postgres as the NOBYPASSRLS ``app_user``.

Proves what SQLite cannot:
  * RLS is ENABLED + FORCED with a tenant_isolation policy on both new tables
    (``ai_usage_events``, ``ai_feature_controls``);
  * ledger rows are invisible across institutes and without a tenant, a
    cross-tenant INSERT is rejected by WITH CHECK, and ON CONFLICT (event_id)
    DO NOTHING keeps writes idempotent;
  * the institute quota row lock: parallel creates from many students of one
    institute cannot exceed the monthly cap;
  * the admin API under RLS (own institute only, TPO read-only);
  * the retention purge works per institute under RLS and audits each one.

Same safety model as the rest of tests_pg_api (synthetic institutes, tenant
cascade cleanup). Skipped unless RLS_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from flowkit import _d

BASE = "/v1/mock-interview"
ADMIN = f"{BASE}/admin"


@pytest.fixture
def ops_flags(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX", "")
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", False)


def _event(tenant_id, user_id, event_id, *, days_old=0, tokens=(10, 5)):
    return {
        "event_id": event_id, "tenant_id": tenant_id, "user_id": user_id, "feature": "mock_interview",
        "step": "turn", "subject_type": None, "subject_id": None, "outcome": "ok", "http_status": 200,
        "error_class": None, "provider": "groq", "model": "m", "llm_calls": 1,
        "input_tokens": tokens[0], "output_tokens": tokens[1], "tokens_unreported": 0,
        "fallback_used": False, "latency_ms": 5, "llm_latency_ms": 4, "cost_micros": None,
        "cost_currency": None, "price_version": None, "request_id": "pg-req",
        "created_at": datetime.now(timezone.utc) - timedelta(days=days_old),
    }


def test_pg_rls_is_forced_on_the_new_tables(sql):
    rows = sql(
        "SELECT c.relname AS t, c.relrowsecurity AS on_, c.relforcerowsecurity AS forced, "
        "(SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname "
        " AND p.policyname = 'tenant_isolation') AS policies "
        "FROM pg_class c WHERE c.relname IN ('ai_usage_events', 'ai_feature_controls') ORDER BY 1"
    )
    assert rows == [
        {"t": "ai_feature_controls", "on_": True, "forced": True, "policies": 1},
        {"t": "ai_usage_events", "on_": True, "forced": True, "policies": 1},
    ]


def test_pg_ledger_isolation_check_and_idempotency(campus, sql):
    from app.services import ai_usage

    a, b = campus.a, campus.b
    event_id = f"pg-{uuid.uuid4().hex}"
    event = _event(a.tenant, a.student.id, event_id)
    assert ai_usage.record(event) == "ok"          # app engine, app_user, tenant GUC set
    assert ai_usage.record(event) == "duplicate"   # ON CONFLICT (event_id) DO NOTHING
    q = "SELECT count(*) AS n FROM ai_usage_events WHERE event_id = :e"
    assert sql(q, {"e": event_id}, tenant=a.tenant) == [{"n": 1}]
    assert sql(q, {"e": event_id}, tenant=b.tenant) == [{"n": 0}]
    assert sql(q, {"e": event_id}) == [{"n": 0}]   # no tenant: default deny
    with pytest.raises(Exception) as err:          # WITH CHECK: B cannot write A's row
        sql("INSERT INTO ai_usage_events (event_id, tenant_id, feature, step, outcome, latency_ms) "
            "VALUES (:e, :t, 'mock_interview', 'turn', 'ok', 1)",
            {"e": f"pg-x-{uuid.uuid4().hex}", "t": a.tenant}, tenant=b.tenant)
    assert "row-level security" in str(err.value).lower()


def _make_students(campus, n, label):
    admin = campus.a.admin
    out = []
    for i in range(n):
        email = f"{label}{i}-{campus.run}@zz-api-iso.example.com"
        pw = secrets.token_urlsafe(16)
        r = admin.post("/v1/admin/users", json={"email": email, "full_name": f"Synthetic {label}{i}",
                                                 "password": pw, "role": "student"})
        assert r.status_code == 201, _d(r)
        out.append(campus.login(email, pw, f"{label}{i}"))
    return out


def test_pg_institute_cap_holds_under_parallel_creates(campus, sql, ops_flags, monkeypatch):
    from app.services import mock_interview_policy as policy

    a = campus.a
    r = a.admin.put(f"{ADMIN}/controls", json={"limits": {"tenant_sessions_per_month": 3}})
    assert r.status_code == 200, _d(r)
    real = policy.tenant_sessions_this_month

    def slow_count(db, tenant_id):
        n = real(db, tenant_id)
        time.sleep(0.4)  # widen check-then-insert; only the institute row lock keeps the cap
        return n

    monkeypatch.setattr(policy, "tenant_sessions_this_month", slow_count)
    students = _make_students(campus, 6, "cap")
    barrier = threading.Barrier(len(students))
    codes: list = [None] * len(students)

    def create(i):
        barrier.wait()
        codes[i] = students[i].post(f"{BASE}/sessions", json={
            "interview_type": "hr", "target_role": "Engineer", "duration_minutes": 15}).status_code

    threads = [threading.Thread(target=create, args=(i,)) for i in range(len(students))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(90)
    assert sorted(codes) == [201, 201, 201, 429, 429, 429], codes
    n = sql("SELECT count(*) AS n FROM mock_interview_sessions WHERE tenant_id = :t", {"t": a.tenant},
            tenant=a.tenant)
    assert n == [{"n": 3}]
    blocked = sql("SELECT count(*) AS n FROM ai_usage_events WHERE tenant_id = :t AND outcome = 'quota_blocked'",
                  {"t": a.tenant}, tenant=a.tenant)
    assert blocked == [{"n": 3}]


def test_pg_admin_api_under_rls(campus, sql, ops_flags):
    from app.services import ai_usage

    a, b = campus.a, campus.b
    sid_b = sql("INSERT INTO mock_interview_sessions (tenant_id, student_id, interview_type, mode, status, "
                "difficulty, language, duration_minutes) VALUES (:t, :s, 'hr', 'text', 'setup', 'medium', "
                "'en', 15) RETURNING id", {"t": b.tenant, "s": b.student.id}, tenant=b.tenant)[0]["id"]
    ai_usage.record(_event(b.tenant, b.student.id, f"pg-b-{uuid.uuid4().hex}", tokens=(999, 999)))
    ai_usage.record(_event(a.tenant, a.student.id, f"pg-a-{uuid.uuid4().hex}", tokens=(7, 3)))
    usage = a.admin.get(f"{ADMIN}/usage?days=7")
    assert usage.status_code == 200, _d(usage)
    assert (usage.json()["totals"]["input_tokens"], usage.json()["totals"]["output_tokens"]) == (7, 3)
    assert a.tpo.get(f"{ADMIN}/usage").status_code == 200
    assert a.teacher.get(f"{ADMIN}/usage").status_code == 403
    assert a.admin.get(f"{ADMIN}/sessions/{sid_b}/diagnostics").status_code == 404
    assert a.tpo.put(f"{ADMIN}/controls", json={"paused": True}).status_code == 403
    assert a.admin.put(f"{ADMIN}/controls", json={"paused": True}).status_code == 200
    assert b.admin.get(f"{ADMIN}/controls").json()["paused"] is False
    audit = sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND action = "
                "'mock_interview.controls_update'", {"t": a.tenant}, tenant=a.tenant)
    assert audit == [{"n": 1}]


def test_pg_retention_purge_under_rls(campus, sql):
    from app.core.db import SessionLocal
    from app.services import ai_usage
    from scripts import purge_ai_usage as purge

    a, b = campus.a, campus.b
    for t, u in ((a.tenant, a.student.id), (b.tenant, b.student.id)):
        ai_usage.record(_event(t, u, f"pg-old-{uuid.uuid4().hex}", days_old=401))
        ai_usage.record(_event(t, u, f"pg-new-{uuid.uuid4().hex}", days_old=1))
    db = SessionLocal()
    try:
        cutoff = purge.cutoff_for(400)
        rows = purge.plan(db, cutoff)
        mine = [r for r in rows if r.tenant_id in (a.tenant, b.tenant)]
        assert sorted((r.tenant_id, r.rows) for r in mine) == sorted([(a.tenant, 1), (b.tenant, 1)])
        assert purge.apply(db, cutoff, mine, 400) == 2
    finally:
        db.close()
    for t in (a.tenant, b.tenant):
        assert sql("SELECT count(*) AS n FROM ai_usage_events WHERE tenant_id = :t", {"t": t}, tenant=t) == [{"n": 1}]
        assert sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND action = 'ai_usage.purge'",
                   {"t": t}, tenant=t) == [{"n": 1}]


def test_pg_unreported_usage_summary_and_alert_under_rls(campus, sql, ops_flags, monkeypatch):
    """Spec 15.12 decision (a) on Postgres: the new count query and summary field
    run under RLS, count only the caller's institute, never change the budget sum,
    and the warning never blocks a create."""
    from app.core.config import settings
    from app.observability import ai_alerts
    from app.services import ai_usage

    a, b = campus.a, campus.b
    mixed = [  # (outcome, input, output, tokens_unreported)
        ("ok", 100, 50, 0), ("ok", 40, 10, 2), ("ok", None, None, 1), ("timeout", None, None, None),
        ("circuit_open", None, None, None),
    ]
    for i, (outcome, inp, out, unrep) in enumerate(mixed):
        ai_usage.record({**_event(a.tenant, a.student.id, f"pg-u{i}-{uuid.uuid4().hex}"),
                         "outcome": outcome, "input_tokens": inp, "output_tokens": out,
                         "tokens_unreported": unrep})
    for i in range(3):  # another institute's unknown usage must never be counted for A
        ai_usage.record({**_event(b.tenant, b.student.id, f"pg-bu{i}-{uuid.uuid4().hex}"),
                         "input_tokens": None, "output_tokens": None, "tokens_unreported": 1})

    totals = a.admin.get(f"{ADMIN}/usage?days=7").json()["totals"]
    assert (totals["input_tokens"], totals["output_tokens"]) == (140, 60)
    assert (totals["calls_with_unknown_tokens"], totals["calls_with_partial_tokens"]) == (2, 1)

    sent = []
    monkeypatch.setattr(ai_alerts, "alert", lambda name, msg, **kw: sent.append((name, msg, kw)) or True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS", 4)
    r = a.admin.put(f"{ADMIN}/controls", json={"limits": {"tenant_tokens_per_month": 1_000_000}})
    assert r.status_code == 200, _d(r)
    r = a.student.post(f"{BASE}/sessions", json={
        "interview_type": "hr", "target_role": "Engineer", "duration_minutes": 15})
    assert r.status_code == 201, _d(r)  # the warning never blocks
    [(_, msg, kw)] = [s for s in sent if s[0] == "mock_interview_token_usage_unreported"]
    assert (kw["tenant_id"], kw["calls"], kw["percent"]) == (a.tenant, 4, 75)  # 3 of A's 4 calls
    assert set(kw) == {"severity", "key", "tenant_id", "percent", "calls"} and "@" not in msg
