"""Phase 8.3 + 8.4 on real Postgres as the NOBYPASSRLS ``app_user``.

Proves what SQLite cannot:
  * coding feedback is single-flight under REAL parallel requests: one AI
    review per problem and one stored row set (session lease);
  * "Done" is single-flight: one explanation question, one AI call;
  * feedback rows and company profiles are invisible to another institute at
    the SQL level (RLS) and 404 through the API;
  * company profile RBAC (admin/TPO write, teacher 403), one audit row per
    write, and a session's frozen company copy under RLS.

The AI worker is stubbed (the suite blocks real worker calls). Nothing is
executed. Skipped unless RLS_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import threading
import time

import pytest

from flowkit import _d

BASE = "/v1/mock-interview"
SRC = "import sys\nnums = list(map(int, sys.stdin.read().split()))\nprint(sum(nums[1:]))\n"


@pytest.fixture
def flags(monkeypatch, campus):
    from app.core.config import settings
    from app.core.rate_limit import limiter

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", f"{campus.a.tenant},{campus.b.tenant}")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_AI_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(limiter, "enabled", False)


class SlowAI:
    """Thread-safe stub for the coding worker calls; slow enough to overlap."""

    def __init__(self):
        self.lock = threading.Lock()
        self.review = 0
        self.followup = 0

    def coding_review(self, payload):
        with self.lock:
            self.review += 1
        time.sleep(0.5)
        return {
            "rubric_version": "coding-v1-unverified",
            "code_quality": {"score": 3, "assessed": True, "rationale": "Readable.",
                             "evidence": [{"line": 3, "quote": "print(sum(nums[1:]))"}]},
            "reasoning": {"score": 0, "assessed": False, "evidence": [], "rationale": ""},
            "testing": {"score": 0, "assessed": False, "evidence": [], "rationale": ""},
            "complexity": {"time": "O(n)", "space": "O(n)", "justification": "", "quote": None},
            "observations": [], "consider_cases": [], "testing_notes": [], "provider": "fake",
        }

    def coding_followup(self, payload):
        with self.lock:
            self.followup += 1
        time.sleep(0.5)
        return {"action": "ask", "question": "Why is it linear?", "provider": "fake"}


@pytest.fixture
def ai(monkeypatch):
    import app.services.mock_interview_ai as engine

    stub = SlowAI()
    monkeypatch.setattr(engine, "coding_review", stub.coding_review)
    monkeypatch.setattr(engine, "coding_followup", stub.coding_followup)
    return stub


def _coding_round(student):
    r = student.post(f"{BASE}/sessions", json={
        "interview_type": "coding", "target_role": "Engineer", "duration_minutes": 30, "difficulty": "easy"})
    assert r.status_code == 201, _d(r)
    sid = r.json()["id"]
    assert student.post(f"{BASE}/sessions/{sid}/start").status_code == 200
    r = student.post(f"{BASE}/sessions/{sid}/coding/attempts/1/submissions",
                     json={"language": "python313", "source": SRC, "idempotency_key": "b2-key-0001"})
    assert r.status_code == 202, _d(r)
    return sid


def _parallel(clients, fn):
    barrier = threading.Barrier(len(clients))
    codes: list = [None] * len(clients)

    def go(i):
        barrier.wait()
        codes[i] = fn(clients[i]).status_code

    threads = [threading.Thread(target=go, args=(i,)) for i in range(len(clients))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return codes


def test_pg_feedback_is_single_flight_and_isolated(campus, sql, flags, ai):
    st = campus.a.student
    sid = _coding_round(st)
    assert st.post(f"{BASE}/sessions/{sid}/finish").json()["status"] == "completed"
    clients = [campus.login(st.email, st.password, f"fb{i}") for i in range(6)]
    codes = _parallel(clients, lambda c: c.post(f"{BASE}/sessions/{sid}/coding/feedback"))
    assert 200 in codes and set(codes) <= {200, 409}, codes
    assert ai.review == 1, ai.review
    rows = sql("SELECT status, verification, problem_score, rubric_version FROM mock_interview_code_reviews "
               "WHERE session_id = :s", {"s": sid}, tenant=campus.a.tenant)
    assert rows == [{"status": "ai_estimated", "verification": "not_verified", "problem_score": None,
                     "rubric_version": "coding-v1-unverified"}]
    assert sql("SELECT count(*) AS n FROM mock_interview_code_reviews WHERE session_id = :s",
               {"s": sid}, tenant=campus.b.tenant) == [{"n": 0}]
    assert sql("SELECT count(*) AS n FROM mock_interview_reports WHERE session_id = :s",
               {"s": sid}, tenant=campus.a.tenant) == [{"n": 0}]
    other = campus.b.student
    assert other.get(f"{BASE}/sessions/{sid}/coding/feedback").status_code == 404
    assert other.post(f"{BASE}/sessions/{sid}/coding/feedback").status_code == 404
    assert campus.a.student2.get(f"{BASE}/sessions/{sid}/coding/feedback").status_code == 404


def test_pg_done_asks_exactly_one_question(campus, sql, flags, ai):
    st = campus.a.student
    sid = _coding_round(st)
    clients = [campus.login(st.email, st.password, f"done{i}") for i in range(6)]
    codes = _parallel(clients, lambda c: c.post(f"{BASE}/sessions/{sid}/coding/attempts/1/done"))
    assert set(codes) == {200}, codes
    assert ai.followup == 1
    turns = sql("SELECT topic FROM mock_interview_turns WHERE session_id = :s", {"s": sid},
                tenant=campus.a.tenant)
    assert turns == [{"topic": "coding:1:explain"}]


def _profile_body(name="Acme"):
    return {"name": name, "role_title": "Backend Developer", "role_template_key": "backend_developer",
            "summary": "Notes from the placement cell.", "focus_skills": [{"name": "SQL", "weight": 2}],
            "competencies": ["Ownership"], "sources": [{"kind": "jd", "label": "JD from the TPO office"}]}


def test_pg_company_profiles_rbac_audit_and_isolation(campus, sql, flags, monkeypatch):
    a, b = campus.a, campus.b
    r = a.admin.post(f"{BASE}/company/profiles/admin", json=_profile_body())
    assert r.status_code == 201, _d(r)
    pid = r.json()["id"]
    assert a.teacher.post(f"{BASE}/company/profiles/admin", json=_profile_body()).status_code == 403
    assert a.teacher.get(f"{BASE}/company/profiles/admin").status_code == 403
    assert b.admin.put(f"{BASE}/company/profiles/{pid}", json=_profile_body("Other Co")).status_code == 404
    assert b.admin.post(f"{BASE}/company/profiles/{pid}/publish").status_code == 404
    assert a.tpo.post(f"{BASE}/company/profiles/{pid}/publish").json()["status"] == "published"
    assert [p["id"] for p in a.student.get(f"{BASE}/company/profiles").json()] == [pid]
    assert b.student.get(f"{BASE}/company/profiles").json() == []
    assert b.student.get(f"{BASE}/company/profiles/{pid}").status_code == 404
    # SQL level: invisible to institute B; audit rows are content-free.
    assert sql("SELECT count(*) AS n FROM mock_interview_company_profiles WHERE id = :i",
               {"i": pid}, tenant=b.tenant) == [{"n": 0}]
    audit = sql("SELECT action, summary FROM audit_logs WHERE target_type = 'mock_interview_company_profile' "
                "AND target_id = :i ORDER BY id", {"i": str(pid)}, tenant=a.tenant)
    assert [x["action"] for x in audit] == ["mock_interview.company_profile_create",
                                            "mock_interview.company_profile_publish"]
    assert all("Acme" not in x["summary"] and "placement cell" not in x["summary"] for x in audit)

    # A session keeps a frozen copy (under RLS) and generation gets the context.
    import app.services.mock_interview_ai as engine

    seen = {}

    def plan(payload):
        seen["plan"] = payload
        return {"plan": {"version": 1, "topics": [{"name": "Introduction", "source": "intro"},
                                                  {"name": "SQL", "source": "role"}],
                         "question_budget": 3, "planner": "llm"}, "provider": "fake"}

    def turn(payload):
        seen["turn"] = payload
        return {"action": "opening", "level": "medium", "provider": "fake",
                "question": {"seq": 1, "topic": "Introduction", "is_follow_up": False, "text": "Hi?"}}

    monkeypatch.setattr(engine, "plan_interview", plan)
    monkeypatch.setattr(engine, "interview_turn", turn)
    r = a.student.post(f"{BASE}/sessions", json={"interview_type": "technical", "target_role": "Backend Developer",
                                                 "duration_minutes": 15, "company_profile_id": pid})
    assert r.status_code == 201, _d(r)
    sid = r.json()["id"]
    assert r.json()["company_context"]["profile_id"] == pid
    assert a.student.post(f"{BASE}/sessions/{sid}/start").status_code == 200
    assert seen["plan"]["company_context"]["name"] == "Acme"
    a.admin.put(f"{BASE}/company/profiles/{pid}", json=_profile_body("Renamed"))
    snap = sql("SELECT plan_json FROM mock_interview_sessions WHERE id = :s", {"s": sid}, tenant=a.tenant)
    assert snap[0]["plan_json"]["company"]["name"] == "Acme"
