"""Phase 7.6: institute controls and quotas (SQLite, offline; engine stubbed).

R6 env flags are the ceiling; R7 institutes only tighten; R8 a running
interview is never interrupted; quota blocks are recorded in the ledger; the
student's availability view shows only their own numbers. Postgres-level
concurrency (two creates racing for the last slot) is covered by
``tests_pg_api/test_mock_interview_ops_pg.py`` (opt-in, needs Postgres).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import AIFeatureControl, AIUsageEvent
from app.observability import ai_alerts
from app.services import mock_interview_policy as policy
from app.services import mock_interview_usage as usage_service
from tests.test_mock_interview_reports import FakeReporter
from tests.test_mock_interview_sessions import BASE, FakeEngine

BODY = {"interview_type": "hr", "target_role": "Engineer", "duration_minutes": 15}


@pytest.fixture
def ops(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX", "")
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", False)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT", 10)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS", 20)


@pytest.fixture
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    monkeypatch.setattr(engine, "generate_report", FakeReporter())
    return f


def set_controls(tenant_id, **values):
    limits = {k: values.pop(k) for k in policy.LIMIT_KEYS if k in values}
    with SessionLocal() as db:
        row = db.scalar(select(AIFeatureControl).where(AIFeatureControl.tenant_id == tenant_id))
        if row is None:
            row = AIFeatureControl(tenant_id=tenant_id, feature="mock_interview")
            db.add(row)
        row.paused = values.get("paused", False)
        row.voice_allowed = values.get("voice_allowed", True)
        row.video_allowed = values.get("video_allowed", True)
        row.limits_json = limits
        db.commit()


def student(make_user, token_header, email, tenant=None):
    user = make_user(email, tenant=tenant)
    return user, token_header(email)


def create(client, h, **extra):
    return client.post(f"{BASE}/sessions", json={**BODY, **extra}, headers=h)


def finish(client, h, sid):
    assert client.post(f"{BASE}/sessions/{sid}/abandon", headers=h).status_code == 200


def ledger():
    with SessionLocal() as db:
        return [(e.step, e.outcome, e.error_class) for e in db.scalars(select(AIUsageEvent))]


# --- Default-off ---------------------------------------------------------------------
def test_ops_off_changes_nothing(client, make_user, token_header, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    user, h = student(make_user, token_header, "off@x.edu")
    set_controls(user.tenant_id, paused=True, student_sessions_per_day=1)
    assert client.get(f"{BASE}/usage/me", headers=h).status_code == 404
    assert create(client, h).status_code == 201  # the stored row is ignored
    assert ledger() == []


def test_ops_on_without_a_row_uses_platform_defaults(client, make_user, token_header, ops):
    _, h = student(make_user, token_header, "defaults@x.edu")
    me = client.get(f"{BASE}/usage/me", headers=h).json()
    assert me == {"available": True, "reason": None, "daily_limit": 10, "used_today": 0,
                  "remaining_today": 10, "voice_allowed": True, "video_allowed": True}
    assert create(client, h).status_code == 201


# --- Controls ------------------------------------------------------------------------
def test_pause_blocks_new_sessions_and_is_recorded(client, make_user, token_header, ops):
    user, h = student(make_user, token_header, "paused@x.edu")
    set_controls(user.tenant_id, paused=True)
    r = create(client, h)
    assert r.status_code == 403 and "paused" in r.json()["detail"]
    assert ledger() == [("create", "quota_blocked", "paused")]
    assert client.get(f"{BASE}/usage/me", headers=h).json()["reason"] == "paused"


def test_pause_never_interrupts_an_interview_in_progress(client, make_user, token_header, ops, fake):
    user, h = student(make_user, token_header, "r8@x.edu")
    sid = create(client, h).json()["id"]
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    set_controls(user.tenant_id, paused=True, tenant_sessions_per_month=1, tenant_tokens_per_month=1)
    r = client.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": "I built an API."}, headers=h)
    assert r.status_code == 200, r.text
    assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()["status"] == "completed"
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert rep.status_code == 200, rep.text


def test_institute_can_turn_voice_off_but_never_on(client, make_user, token_header, ops, monkeypatch):
    user, h = student(make_user, token_header, "voice@x.edu")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)
    set_controls(user.tenant_id, voice_allowed=False)
    modes = [m["value"] for m in client.get(f"{BASE}/options", headers=h).json()["modes"]]
    assert modes == ["text"]
    assert create(client, h, mode="voice").status_code == 422
    # env off + institute "allowed" => still off (env is the ceiling, R6)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", False)
    set_controls(user.tenant_id, voice_allowed=True)
    assert [m["value"] for m in client.get(f"{BASE}/options", headers=h).json()["modes"]] == ["text"]
    assert create(client, h, mode="voice").status_code == 422


def test_institute_can_turn_video_off(client, make_user, token_header, ops, monkeypatch):
    user, h = student(make_user, token_header, "video@x.edu")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(user.tenant_id))
    assert "video" in [m["value"] for m in client.get(f"{BASE}/options", headers=h).json()["modes"]]
    set_controls(user.tenant_id, video_allowed=False)
    assert "video" not in [m["value"] for m in client.get(f"{BASE}/options", headers=h).json()["modes"]]
    assert create(client, h, mode="video").status_code == 422


# --- Quotas --------------------------------------------------------------------------
def test_lower_student_daily_limit(client, make_user, token_header, ops):
    user, h = student(make_user, token_header, "daily@x.edu")
    set_controls(user.tenant_id, student_sessions_per_day=2)
    for _ in range(2):
        sid = create(client, h).json()["id"]
        finish(client, h, sid)
    r = create(client, h)
    assert r.status_code == 429 and "up to 2" in r.json()["detail"]
    me = client.get(f"{BASE}/usage/me", headers=h).json()
    assert me["available"] is False and me["reason"] == "daily_limit" and me["remaining_today"] == 0


def test_institute_monthly_session_cap_counts_every_student(client, make_user, token_header, ops, make_tenant):
    a, ha = student(make_user, token_header, "a@x.edu")
    _, hb = student(make_user, token_header, "b@x.edu")
    other_tenant = make_tenant("other")
    _, hc = student(make_user, token_header, "c@other.edu", tenant=other_tenant)
    set_controls(a.tenant_id, tenant_sessions_per_month=2)
    finish(client, ha, create(client, ha).json()["id"])
    finish(client, hb, create(client, hb).json()["id"])
    r = create(client, hb)
    assert r.status_code == 429 and "institute" in r.json()["detail"]
    me = client.get(f"{BASE}/usage/me", headers=ha).json()
    assert me["reason"] == "institute_limit" and "tenant" not in str(me)  # no totals leak
    assert create(client, hc).status_code == 201  # other institutes unaffected
    assert ("create", "quota_blocked", "institute_monthly_sessions") in ledger()


def test_institute_monthly_token_budget_with_soft_alert(client, make_user, token_header, ops, monkeypatch):
    user, h = student(make_user, token_header, "tokens@x.edu")
    sent = []
    monkeypatch.setattr(ai_alerts, "alert", lambda name, msg, **kw: sent.append((name, kw)) or True)
    set_controls(user.tenant_id, tenant_tokens_per_month=1000)
    with SessionLocal() as db:
        for i, (inp, out) in enumerate([(500, 300), (None, None)]):
            db.add(AIUsageEvent(event_id=f"t{i}", tenant_id=user.tenant_id, user_id=user.id,
                                feature="mock_interview", step="turn", outcome="ok", latency_ms=1,
                                input_tokens=inp, output_tokens=out,
                                created_at=datetime.now(timezone.utc)))
        db.commit()
    sid = create(client, h).json()["id"]  # 800 / 1000 used: allowed, 80 % alert
    assert [n for n, _ in sent] == ["mock_interview_quota_near_limit"]
    assert sent[0][1]["percent"] == 80 and "tokens" in sent[0][1]["kind"]
    finish(client, h, sid)
    with SessionLocal() as db:
        db.add(AIUsageEvent(event_id="t9", tenant_id=user.tenant_id, user_id=user.id,
                            feature="mock_interview", step="turn", outcome="ok", latency_ms=1,
                            input_tokens=150, output_tokens=50, created_at=datetime.now(timezone.utc)))
        db.commit()
    r = create(client, h)
    assert r.status_code == 429 and "AI usage limit" in r.json()["detail"]


# --- Unknown provider usage (spec 15.12, decision a: count reported tokens only) ----
def add_usage(user, rows):
    """rows: (outcome, input_tokens, output_tokens, tokens_unreported)."""
    with SessionLocal() as db:
        for i, (outcome, inp, out, unrep) in enumerate(rows):
            db.add(AIUsageEvent(event_id=f"u{user.id}-{i}", tenant_id=user.tenant_id, user_id=user.id,
                                feature="mock_interview", step="turn", outcome=outcome, latency_ms=1,
                                input_tokens=inp, output_tokens=out, tokens_unreported=unrep,
                                created_at=datetime.now(timezone.utc)))
        db.commit()


MIXED = [("ok", 100, 50, 0), ("ok", 40, 10, 2), ("ok", None, None, 1), ("timeout", None, None, None),
         ("circuit_open", None, None, None), ("quota_blocked", None, None, None)]


def test_budget_counts_reported_tokens_only_and_measures_the_gap(make_user, ops):
    user = make_user("gap@x.edu")
    add_usage(user, MIXED)
    with SessionLocal() as db:
        assert policy.tenant_tokens_this_month(db, user.tenant_id) == 200  # no estimate added
        # partial + unknown + failed of 4 real calls (breaker skips / quota blocks are not calls)
        assert policy.tenant_unreported_calls_this_month(db, user.tenant_id) == (3, 4)
        summary = usage_service.summary(db, user.tenant_id, 7)["totals"]
    assert (summary["calls_with_unknown_tokens"], summary["calls_with_partial_tokens"]) == (2, 1)


def test_unreported_usage_alerts_but_never_blocks(client, make_user, token_header, ops, monkeypatch):
    user, h = student(make_user, token_header, "unrep@x.edu")
    sent = []
    monkeypatch.setattr(ai_alerts, "alert", lambda name, msg, **kw: sent.append((name, msg, kw)) or True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS", 4)
    set_controls(user.tenant_id, tenant_tokens_per_month=10**6)
    add_usage(user, MIXED)
    assert create(client, h).status_code == 201
    [(name, msg, kw)] = [s for s in sent if s[0] == "mock_interview_token_usage_unreported"]
    assert kw["percent"] == 75 and kw["calls"] == 4 and kw["tenant_id"] == user.tenant_id
    assert kw["severity"] == "warning" and "@" not in msg  # no student data in the alert


@pytest.mark.parametrize("percent, min_calls, budget", [(0, 1, 10**6), (80, 1, 10**6), (10, 5, 10**6),
                                                        (10, 1, None)])
def test_unreported_alert_quiet_when_off_below_threshold_or_no_budget(
        client, make_user, token_header, ops, monkeypatch, percent, min_calls, budget):
    user, h = student(make_user, token_header, f"quiet{percent}-{min_calls}-{budget}@x.edu")
    sent = []
    monkeypatch.setattr(ai_alerts, "alert", lambda name, msg, **kw: sent.append(name) or True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT", percent)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS", min_calls)
    set_controls(user.tenant_id, tenant_tokens_per_month=budget)
    add_usage(user, MIXED)
    assert create(client, h).status_code == 201
    assert "mock_interview_token_usage_unreported" not in sent


def test_env_ceiling_caps_the_stored_value(make_user, ops, monkeypatch):
    user = make_user("ceiling@x.edu")
    set_controls(user.tenant_id, tenant_sessions_per_month=500)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "100")
    with SessionLocal() as db:
        assert policy.effective(db, user.tenant_id).limits["tenant_sessions_per_month"] == 100
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "lots")
    assert policy.ceilings()["tenant_sessions_per_month"] is None  # invalid: ignored, logged


@pytest.mark.parametrize(
    "limits, ok",
    [({"student_sessions_per_day": 3}, True), ({"student_sessions_per_day": 11}, False),
     ({"student_sessions_per_day": 0}, False), ({"tenant_sessions_per_month": 50}, True)],
)
def test_validation_only_allows_tightening(limits, ok, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "100")
    if ok:
        policy.validate_limits(limits)
    else:
        with pytest.raises(Exception) as err:
            policy.validate_limits(limits)
        assert getattr(err.value, "status_code", None) == 422
    with pytest.raises(Exception):
        policy.validate_limits({"tenant_sessions_per_month": 101})
