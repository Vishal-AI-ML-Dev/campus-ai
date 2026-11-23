"""Phase 7.7: institute operations API - RBAC, tenant isolation, content-free output.

P7-D3: admin + TPO view their OWN institute; only admin edits controls (TPO
403); teachers/students/recruiters 403; other institutes' data 404/invisible;
404 for everyone while AI_MOCK_INTERVIEW_OPS_ENABLED is off. Acceptance
criterion 7: planted interview text never appears in any admin response.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import (
    AIUsageEvent,
    AuditLog,
    MockInterviewSession,
    MockInterviewTurn,
    UserRole,
)
from app.services import ai_circuit

ADMIN = "/v1/mock-interview/admin"
SENTINEL = "ANSWER-SENTINEL-c0de flux capacitor"
RESUME_SENTINEL = "RESUME-SENTINEL-7f3a basket weaving"


@pytest.fixture
def ops(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_OPS_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX", "100")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX", "")
    ai_circuit._reset()


@pytest.fixture
def world(make_user, make_tenant, token_header):
    """Institute A (default) with every role, institute B with an admin and data."""
    other = make_tenant("inst-b", name="Institute B")
    users = {
        "admin": make_user("admin@a.edu", role=UserRole.admin, full_name="Admin A"),
        "tpo": make_user("tpo@a.edu", role=UserRole.tpo),
        "teacher": make_user("teacher@a.edu", role=UserRole.teacher),
        "student": make_user("student@a.edu", full_name="Student A"),
        "recruiter": make_user("recruiter@a.edu", role=UserRole.recruiter),
        "admin_b": make_user("admin@b.edu", role=UserRole.admin, tenant=other),
        "student_b": make_user("student@b.edu", tenant=other, full_name="Student B"),
    }
    headers = {k: token_header(u.email) for k, u in users.items()}
    sessions = {"a": _seed(users["student"]), "b": _seed(users["student_b"])}
    return users, headers, sessions


def _seed(student) -> int:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        s = MockInterviewSession(
            tenant_id=student.tenant_id, student_id=student.id, interview_type="hr", mode="voice",
            status="completed", difficulty="medium", language="en", duration_minutes=15,
            target_role="Engineer", job_description=f"JD {RESUME_SENTINEL}",
            plan_json={"plan": {"topics": [{"name": RESUME_SENTINEL}]}, "end_reason": "time_up"},
            created_at=now - timedelta(minutes=30), started_at=now - timedelta(minutes=29),
            completed_at=now - timedelta(minutes=10), updated_at=now,
        )
        db.add(s)
        db.flush()
        db.add(MockInterviewTurn(tenant_id=student.tenant_id, session_id=s.id, seq=1,
                                 question=f"Q {RESUME_SENTINEL}", answer_text=SENTINEL))
        for i, (step, outcome, inp, out) in enumerate(
            [("plan", "ok", 100, 50), ("turn", "fallback", 80, 20), ("turn", "provider_error", None, None),
             ("report", "timeout", None, None)]
        ):
            db.add(AIUsageEvent(
                event_id=f"{s.id}-{i}", tenant_id=student.tenant_id, user_id=student.id,
                feature="mock_interview", step=step, outcome=outcome,
                subject_type="mock_interview_session", subject_id=str(s.id),
                provider="groq", model="m", llm_calls=1, input_tokens=inp, output_tokens=out,
                latency_ms=100 * (i + 1), request_id=f"req-{s.id}-{i}",
                error_class="HTTPStatusError" if outcome != "ok" else None,
                created_at=now - timedelta(minutes=20 - i),
            ))
        db.commit()
        return s.id


# --- Access control -------------------------------------------------------------------
GETS = ["/usage", "/usage/students", "/controls", "/health"]


def test_everything_is_404_while_ops_is_off(client, world, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    _, h, s = world
    for path in GETS + [f"/sessions/{s['a']}/diagnostics"]:
        assert client.get(ADMIN + path, headers=h["admin"]).status_code == 404
    assert client.put(ADMIN + "/controls", json={}, headers=h["admin"]).status_code == 404


def test_role_matrix(client, world, ops):
    _, h, s = world
    for path in GETS + [f"/sessions/{s['a']}/diagnostics"]:
        assert client.get(ADMIN + path, headers=h["admin"]).status_code == 200, path
        assert client.get(ADMIN + path, headers=h["tpo"]).status_code == 200, path
        for role in ("teacher", "student", "recruiter"):
            assert client.get(ADMIN + path, headers=h[role]).status_code == 403, (role, path)
    client.cookies.clear()
    assert client.get(ADMIN + "/usage").status_code == 401


def test_tpo_cannot_edit_and_admin_can(client, world, ops):
    users, h, _ = world
    assert client.put(ADMIN + "/controls", json={"paused": True}, headers=h["tpo"]).status_code == 403
    assert client.get(ADMIN + "/controls", headers=h["tpo"]).json()["can_edit"] is False
    r = client.put(ADMIN + "/controls", headers=h["admin"],
                   json={"paused": True, "limits": {"student_sessions_per_day": 3}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paused"] is True and body["limits"]["student_sessions_per_day"] == 3
    assert body["effective_limits"]["student_sessions_per_day"] == 3 and body["can_edit"] is True
    with SessionLocal() as db:
        [audit] = db.scalars(select(AuditLog).where(AuditLog.action == "mock_interview.controls_update")).all()
    assert audit.tenant_id == users["admin"].tenant_id and audit.actor_id == users["admin"].id
    assert "paused: False -> True" in audit.summary and "student_sessions_per_day: None -> 3" in audit.summary
    # the other institute is untouched
    assert client.get(ADMIN + "/controls", headers=h["admin_b"]).json()["paused"] is False


@pytest.mark.parametrize(
    "payload",
    [{"limits": {"student_sessions_per_day": 11}}, {"limits": {"tenant_sessions_per_month": 101}},
     {"limits": {"student_sessions_per_day": 0}}, {"paused": "yes please"}, {"tenant_id": 999},
     {"limits": {"unknown": 1}}],
)
def test_controls_reject_loosening_and_unknown_fields(client, world, ops, payload):
    _, h, _ = world
    assert client.put(ADMIN + "/controls", json=payload, headers=h["admin"]).status_code == 422


# --- Tenant isolation and content -------------------------------------------------------
def test_usage_summary_is_own_institute_only(client, world, ops):
    _, h, _ = world
    body = client.get(ADMIN + "/usage?days=7", headers=h["admin"]).json()
    t = body["totals"]
    assert (t["sessions_created"], t["sessions_completed"]) == (1, 1)  # B's session not counted
    assert (t["ai_calls"], t["ai_failures"]) == (4, 2)
    assert (t["input_tokens"], t["output_tokens"], t["calls_with_unknown_tokens"]) == (180, 70, 2)
    assert t["calls_with_partial_tokens"] == 0
    assert body["modes"] == {"voice": 1}
    assert body["by_outcome"] == {"ok": 1, "fallback": 1, "provider_error": 1, "timeout": 1}
    assert {r["key"] for r in body["by_step"]} == {"plan", "turn", "report"}
    assert body["cost"]["status"] == "pricing_not_configured" and body["cost"]["total_micros"] is None
    assert len(body["daily"]) == 7 and sum(d["ai_calls"] for d in body["daily"]) == 4


def test_students_page_counts_only_and_pagination(client, world, ops):
    users, h, _ = world
    r = client.get(ADMIN + "/usage/students?limit=1", headers=h["tpo"])
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "1"
    [row] = r.json()
    assert row["student_id"] == users["student"].id and row["full_name"] == "Student A"
    assert set(row) == {"student_id", "full_name", "sessions_created", "sessions_completed",
                        "ai_calls", "ai_failures", "input_tokens", "output_tokens", "last_activity"}
    assert client.get(ADMIN + "/usage/students?offset=5", headers=h["tpo"]).json() == []


def test_diagnostics_are_own_institute_and_content_free(client, world, ops):
    _, h, s = world
    r = client.get(ADMIN + f"/sessions/{s['a']}/diagnostics", headers=h["admin"])
    assert r.status_code == 200
    d = r.json()
    assert (d["turns"], d["answered"], d["report_generated"]) == (1, 1, False)
    assert [e["step"] for e in d["events"]] == ["plan", "turn", "turn", "report"]
    assert d["events"][0]["request_id"] == f"req-{s['a']}-0"
    assert client.get(ADMIN + f"/sessions/{s['b']}/diagnostics", headers=h["admin"]).status_code == 404
    assert client.get(ADMIN + "/sessions/999999/diagnostics", headers=h["admin"]).status_code == 404


def test_no_interview_content_in_any_admin_response(client, world, ops):
    _, h, s = world
    texts = [client.get(ADMIN + p, headers=h["admin"]).text
             for p in GETS + [f"/sessions/{s['a']}/diagnostics"]]
    for text in texts:
        for marker in ("ANSWER-SENTINEL", "RESUME-SENTINEL", "flux", "basket"):
            assert marker not in text


def test_health_shows_breaker_and_own_error_rate(client, world, ops, monkeypatch):
    _, h, _ = world
    body = client.get(ADMIN + "/health", headers=h["tpo"]).json()
    turn = next(x for x in body["steps"] if x["step"] == "turn")
    assert body["circuit_enabled"] is True and turn["state"] == "closed"
    assert (turn["last_hour_calls"], turn["last_hour_failures"], turn["last_hour_error_rate"]) == (2, 1, 0.5)
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", False)
    assert client.get(ADMIN + "/health", headers=h["tpo"]).json()["steps"][0]["state"] == "disabled"


# --- Audit filters (additive) ------------------------------------------------------------
def test_audit_filters(client, world, ops):
    users, h, _ = world
    client.put(ADMIN + "/controls", json={"paused": True}, headers=h["admin"])
    client.put(ADMIN + "/controls", json={"paused": True}, headers=h["admin_b"])
    base = "/v1/audit?target_type=ai_feature_controls"
    rows = client.get(base, headers=h["admin"]).json()
    assert len(rows) == 1 and rows[0]["action"] == "mock_interview.controls_update"  # own tenant only
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.get(base, params={"since": future}, headers=h["admin"]).json() == []
    assert len(client.get(base, params={"since": past, "until": future}, headers=h["admin"]).json()) == 1
    assert client.get("/v1/audit", params={"since": future, "until": past}, headers=h["admin"]).status_code == 422
    assert client.get("/v1/audit", headers=h["tpo"]).status_code == 403  # unchanged: admin only
