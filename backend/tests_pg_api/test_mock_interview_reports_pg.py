"""Phase 4 AI Mock Interview reports on real Postgres (RLS, NOBYPASSRLS ``app_user``).

Proves what SQLite cannot:
  * report + history isolation (other student / other institute 404, staff 403)
    and RLS visibility of the stored report (owning tenant only, default deny);
  * the POST /report path (lease commit, then worker call, then insert) works
    under RLS: the tenant GUC survives the mid-request commits;
  * parallel report requests make exactly ONE worker call and store ONE row.

The worker is replaced by a thread-safe fake; all data is synthetic and removed
by the ``campus`` fixture (tenant cascade).
"""

from __future__ import annotations

import threading
import time

import pytest

from flowkit import _d
from test_mock_interview_pg import BASE, FakeEngine, _fresh_actor, _parallel, _started


@pytest.fixture
def mi_flag(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


@pytest.fixture
def fake(monkeypatch):
    import app.services.mock_interview_ai as engine

    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f

HR = ["relevance", "structure", "specificity", "communication", "self_awareness"]
WEIGHTS = [0.25, 0.20, 0.20, 0.20, 0.15]


class FakeReporter:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.delay = 0.0

    def __call__(self, payload):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        questions = []
        for t in payload["transcript"]:
            if t["answer"] is None:
                questions.append({"seq": t["seq"], "status": "no_answer", "feedback": "No answer given.",
                                  "scores": {k: {"score": 0, "evidence": ""} for k in HR}})
            else:
                questions.append({"seq": t["seq"], "status": "scored", "feedback": "Ok.",
                                  "scores": {k: {"score": 4, "evidence": t["answer"][:15]} for k in HR}})
        return {"rubric_version": "hr-v1", "overall_score": 0.0,  # recomputed by the backend
                "categories": [{"key": k, "label": k, "weight": w, "description": "", "score": 0.0,
                                "assessed": 0} for k, w in zip(HR, WEIGHTS)],
                "questions": questions, "strengths": [], "weaknesses": [],
                "recommendations": ["Practise STAR."], "provider": "fake"}


def _completed(actor):
    sid = _started(actor)
    r = actor.post(f"{BASE}/sessions/{sid}/answer",
                   json={"turn_seq": 1, "answer": "I am a final year CSE student."})
    assert r.status_code == 200, _d(r)
    done = actor.post(f"{BASE}/sessions/{sid}/finish")
    assert done.json()["status"] == "completed", _d(done)
    return sid


def test_pg_mi_report_isolation_and_rls(campus, sql, mi_flag, fake, monkeypatch):
    import app.services.mock_interview_ai as engine

    reporter = FakeReporter()
    monkeypatch.setattr(engine, "generate_report", reporter)
    a, b = campus.a, campus.b
    sid = _completed(a.student)

    r = a.student.post(f"{BASE}/sessions/{sid}/report")
    assert r.status_code == 200, _d(r)
    # 1 of 2 asked questions answered at 4/5 -> 40.0 per category, recomputed server-side.
    assert r.json()["overall_score"] == 40.0
    assert a.student.get(f"{BASE}/sessions/{sid}/report").json() == r.json()
    hist = a.student.get(f"{BASE}/history").json()
    assert hist[0]["id"] == sid and hist[0]["report"]["overall_score"] == 40.0

    for intruder in (a.student2, b.student):
        assert intruder.get(f"{BASE}/sessions/{sid}/report").status_code == 404
        assert intruder.post(f"{BASE}/sessions/{sid}/report").status_code == 404
        assert intruder.get(f"{BASE}/history").json() == []
    assert a.teacher.get(f"{BASE}/history").status_code == 403
    assert reporter.calls == 1

    q = "SELECT count(*) AS n FROM mock_interview_reports WHERE session_id = :i"
    assert sql(q, {"i": sid}, tenant=a.tenant) == [{"n": 1}]
    assert sql(q, {"i": sid}, tenant=b.tenant) == [{"n": 0}]
    assert sql(q, {"i": sid}) == [{"n": 0}]  # no tenant GUC: default deny
    row = sql("SELECT tenant_id, overall_score, rubric_version FROM mock_interview_reports "
              "WHERE session_id = :i", {"i": sid}, tenant=a.tenant)[0]
    assert row == {"tenant_id": a.tenant, "overall_score": 40.0, "rubric_version": "hr-v1"}
    lease = sql("SELECT (plan_json::jsonb -> 'lease') IS NOT NULL AS has "
                "FROM mock_interview_sessions WHERE id = :i", {"i": sid}, tenant=a.tenant)
    assert lease == [{"has": False}]  # released in the same commit as the insert


def test_pg_mi_parallel_reports_make_one_call(campus, sql, mi_flag, fake, monkeypatch):
    import app.services.mock_interview_ai as engine

    reporter = FakeReporter()
    reporter.delay = 0.7  # keep the first generation in flight while the others arrive
    monkeypatch.setattr(engine, "generate_report", reporter)
    a = campus.a
    sid = _completed(a.student)
    actors = [_fresh_actor(campus, a.student, f"r{i}") for i in range(6)]
    codes = _parallel(6, lambda i: actors[i].post(f"{BASE}/sessions/{sid}/report").status_code)
    assert reporter.calls == 1, (reporter.calls, codes)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}, codes
    assert sql("SELECT count(*) AS n FROM mock_interview_reports WHERE session_id = :i",
               {"i": sid}, tenant=a.tenant) == [{"n": 1}]
    # Everyone now gets the same stored report, without another worker call.
    assert a.student.post(f"{BASE}/sessions/{sid}/report").status_code == 200
    assert reporter.calls == 1


def test_pg_mi_report_eligibility_retry_and_timeout(campus, sql, mi_flag, fake, monkeypatch):
    import app.services.mock_interview_ai as engine

    reporter = FakeReporter()
    monkeypatch.setattr(engine, "generate_report", reporter)
    a = campus.a
    q_rows = "SELECT count(*) AS n FROM mock_interview_reports WHERE session_id = :i"
    q_lease = ("SELECT (plan_json::jsonb -> 'lease') IS NOT NULL AS has "
               "FROM mock_interview_sessions WHERE id = :i")

    # Completed sessions only: setup / in_progress / abandoned -> 409, no worker call.
    sid = a.student.post(f"{BASE}/sessions", json={
        "interview_type": "hr", "target_role": "Software Engineer", "duration_minutes": 15}).json()["id"]
    assert a.student.post(f"{BASE}/sessions/{sid}/report").status_code == 409  # setup
    assert a.student.post(f"{BASE}/sessions/{sid}/start").status_code == 200
    r = a.student.post(f"{BASE}/sessions/{sid}/report")
    assert r.status_code == 409 and "Finish" in r.json()["detail"], _d(r)  # in progress
    assert a.student.get(f"{BASE}/sessions/{sid}/report").status_code == 404
    a.student.post(f"{BASE}/sessions/{sid}/abandon")
    r = a.student.post(f"{BASE}/sessions/{sid}/report")
    assert r.status_code == 409 and "Cancelled" in r.json()["detail"], _d(r)
    assert reporter.calls == 0 and sql(q_rows, {"i": sid}, tenant=a.tenant) == [{"n": 0}]

    # Worker failure: 503, nothing stored, lease released -> an immediate retry works.
    sid = _completed(a.student)
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise engine.InterviewEngineUnavailable("/interview/report", status_code=502)
        return reporter(payload)

    monkeypatch.setattr(engine, "generate_report", flaky)
    assert a.student.post(f"{BASE}/sessions/{sid}/report").status_code == 503
    assert sql(q_rows, {"i": sid}, tenant=a.tenant) == [{"n": 0}]
    assert sql(q_lease, {"i": sid}, tenant=a.tenant) == [{"has": False}]
    assert a.student.post(f"{BASE}/sessions/{sid}/report").status_code == 200
    assert sql(q_rows, {"i": sid}, tenant=a.tenant) == [{"n": 1}] and calls["n"] == 2

    # Read timeout: the worker may still be running -> lease kept, retry is 409, no 2nd call.
    sid2 = _completed(a.student)

    def timeout(payload):
        calls["n"] += 1
        raise engine.InterviewEngineUnavailable("/interview/report", timed_out=True)

    monkeypatch.setattr(engine, "generate_report", timeout)
    before = calls["n"]
    assert a.student.post(f"{BASE}/sessions/{sid2}/report").status_code == 503
    busy = a.student.post(f"{BASE}/sessions/{sid2}/report")
    assert busy.status_code == 409 and "being prepared" in busy.json()["detail"], _d(busy)
    assert calls["n"] == before + 1
    assert sql(q_lease, {"i": sid2}, tenant=a.tenant) == [{"has": True}]
    assert sql(q_rows, {"i": sid2}, tenant=a.tenant) == [{"n": 0}]
