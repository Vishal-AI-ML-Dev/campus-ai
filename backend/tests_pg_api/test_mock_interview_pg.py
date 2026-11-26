"""Phase 3 AI Mock Interview on real Postgres (RLS, NOBYPASSRLS ``app_user``).

Drives the real API against Postgres to prove what SQLite cannot:
  * tenant + owner isolation and resume ownership under RLS;
  * RLS default-deny / cross-tenant invisibility of the interview tables;
  * the per-student ``FOR UPDATE`` create lock (parallel creates);
  * the per-session lease (one AI step at a time, exact timestamptz CAS);
  * concurrent answer submission;
  * "finish" beating a late AI result.

Concurrency uses real threads, each with its own TestClient (own event loop and
own pooled DB connection). The AI engine is replaced by a thread-safe fake;
``ai_client._worker_request`` stays blocked by the suite's ``no_ai_network``.
All data is synthetic and removed by the ``campus`` fixture (tenant cascade).
"""

from __future__ import annotations

import threading
import time

import pytest

from flowkit import _d

BASE = "/v1/mock-interview"


@pytest.fixture
def mi_flag(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


class FakeEngine:
    """Thread-safe stand-in for the worker. ``gate`` can hold turn calls."""

    def __init__(self):
        self.lock = threading.Lock()
        self.plan_calls = 0
        self.turn_calls = 0
        self.fail_turn = False
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.delay = 0.0

    def plan(self, payload):
        with self.lock:
            self.plan_calls += 1
        return {"plan": {"version": 1, "question_budget": 6, "planner": "llm",
                         "topics": [{"name": "Introduction", "source": "intro"},
                                    {"name": "Teamwork", "source": "general"}]},
                "provider": "fake"}

    def turn(self, payload):
        with self.lock:
            self.turn_calls += 1
            fail = self.fail_turn
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(20), "test gate never released"
        if self.delay:
            time.sleep(self.delay)
        from app.services.mock_interview_ai import InterviewEngineUnavailable

        if fail:
            raise InterviewEngineUnavailable("turn")
        transcript = payload["transcript"]
        seq = len(transcript) + 1
        q = {"seq": seq, "topic": "Introduction" if seq == 1 else "Teamwork",
             "is_follow_up": False, "text": f"Question {seq}?"}
        if not transcript:
            return {"action": "opening", "level": "medium", "provider": "fake", "question": q}
        return {"action": "next_topic", "level": "medium", "provider": "fake",
                "evaluation": {"coverage": "full", "depth": 3}, "question": q}


@pytest.fixture
def fake(monkeypatch):
    import app.services.mock_interview_ai as engine

    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f


def _resume(sql, tenant, student_id, title="CV"):
    rows = sql(
        "INSERT INTO resumes (student_id, tenant_id, title, markdown, is_primary) "
        "VALUES (:s, :t, :ti, '# CV\n- Built a Flask API', false) RETURNING id",
        {"s": student_id, "t": tenant, "ti": title}, tenant=tenant)
    return rows[0]["id"]


def _create(actor, **kw):
    body = {"interview_type": "hr", "target_role": "Software Engineer", "duration_minutes": 15, **kw}
    return actor.post(f"{BASE}/sessions", json=body)


def _started(actor, **kw):
    r = _create(actor, **kw)
    assert r.status_code == 201, _d(r)
    sid = r.json()["id"]
    s = actor.post(f"{BASE}/sessions/{sid}/start")
    assert s.status_code == 200, _d(s)
    return sid


def _parallel(n, fn):
    """Run fn(i) in n threads released together; return results in order."""
    barrier = threading.Barrier(n)
    out: list = [None] * n

    def worker(i):
        barrier.wait()
        out[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return out


def _fresh_actor(campus, actor, label):
    return campus.login(actor.email, actor.password, label)


# ---------------------------------------------------------------------------
def test_pg_mi_flow_ownership_resume_and_rls(campus, sql, mi_flag, fake):
    a, b = campus.a, campus.b
    own_cv = _resume(sql, a.tenant, a.student.id)
    mate_cv = _resume(sql, a.tenant, a.student2.id, "mate")
    other_cv = _resume(sql, b.tenant, b.student.id, "other")

    # Resume ownership: classmate's and other institute's resumes -> 404.
    for rid in (mate_cv, other_cv):
        r = _create(a.student, resume_id=rid)
        assert r.status_code == 404 and r.json()["detail"] == "Resume not found", _d(r)
    opts = a.student.get(f"{BASE}/options").json()
    assert [x["id"] for x in opts["resumes"]] == [own_cv]

    sid = _started(a.student, resume_id=own_cv, job_description="Python, REST APIs")
    s = a.student.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": "I am a CSE student."})
    assert s.status_code == 200, _d(s)
    assert [t["seq"] for t in s.json()["turns"]] == [1, 2]

    # Other student (same institute) and other institute: 404 everywhere.
    for intruder in (a.student2, b.student):
        assert intruder.get(f"{BASE}/sessions/{sid}").status_code == 404
        for action in ("start", "next", "finish", "abandon"):
            assert intruder.post(f"{BASE}/sessions/{sid}/{action}").status_code == 404
        r = intruder.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 2, "answer": "x"})
        assert r.status_code == 404
        assert intruder.get(f"{BASE}/sessions").json() == []
    # Staff of the same institute are not students: 403.
    assert a.teacher.get(f"{BASE}/sessions").status_code == 403

    # RLS as app_user: visible only with the owning tenant's GUC.
    q_s = "SELECT count(*) AS n FROM mock_interview_sessions WHERE id = :i"
    q_t = "SELECT count(*) AS n FROM mock_interview_turns WHERE session_id = :i"
    assert sql(q_s, {"i": sid}, tenant=a.tenant) == [{"n": 1}]
    assert sql(q_t, {"i": sid}, tenant=a.tenant) == [{"n": 2}]
    assert sql(q_s, {"i": sid}, tenant=b.tenant) == [{"n": 0}]
    assert sql(q_t, {"i": sid}, tenant=b.tenant) == [{"n": 0}]
    assert sql(q_s, {"i": sid}) == [{"n": 0}]  # no tenant GUC: default deny
    row = sql("SELECT tenant_id, student_id, resume_id, status FROM mock_interview_sessions WHERE id = :i",
              {"i": sid}, tenant=a.tenant)[0]
    assert row == {"tenant_id": a.tenant, "student_id": a.student.id,
                   "resume_id": own_cv, "status": "in_progress"}

    done = a.student.post(f"{BASE}/sessions/{sid}/finish").json()
    assert done["status"] == "completed" and done["end_reason"] == "ended_by_candidate"


def test_pg_mi_parallel_creates_yield_one_active_session(campus, sql, mi_flag, fake, monkeypatch):
    import app.services.mock_interview as svc

    real_active = svc.active_session

    def slow_active(db, user):
        # Widen the check-then-insert window so the parallel requests really
        # overlap; only the per-student FOR UPDATE lock keeps this to one.
        found = real_active(db, user)
        time.sleep(0.3)
        return found

    monkeypatch.setattr(svc, "active_session", slow_active)
    a = campus.a
    actors = [_fresh_actor(campus, a.student, f"c{i}") for i in range(6)]
    results = _parallel(6, lambda i: _create(actors[i]).status_code)
    assert sorted(results) == [201] + [409] * 5, results
    n = sql("SELECT count(*) AS n FROM mock_interview_sessions WHERE student_id = :s "
            "AND status IN ('setup','in_progress')", {"s": a.student.id}, tenant=a.tenant)
    assert n == [{"n": 1}]


def test_pg_mi_parallel_starts_plan_once(campus, sql, mi_flag, fake):
    a = campus.a
    sid = _create(a.student).json()["id"]
    fake.delay = 0.5
    actors = [_fresh_actor(campus, a.student, f"s{i}") for i in range(5)]
    codes = _parallel(5, lambda i: actors[i].post(f"{BASE}/sessions/{sid}/start").status_code)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}, codes
    assert fake.plan_calls == 1 and fake.turn_calls == 1, (fake.plan_calls, fake.turn_calls)
    assert sql("SELECT seq FROM mock_interview_turns WHERE session_id = :i",
               {"i": sid}, tenant=a.tenant) == [{"seq": 1}]


def test_pg_mi_one_ai_step_at_a_time(campus, sql, mi_flag, fake):
    a = campus.a
    sid = _started(a.student)
    fake.fail_turn = True
    r = a.student.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": "Saved"})
    assert r.status_code == 503, _d(r)
    fake.fail_turn = False
    calls_before = fake.turn_calls
    fake.delay = 0.7  # keep the first AI step in flight while the others arrive
    actors = [_fresh_actor(campus, a.student, f"n{i}") for i in range(6)]
    codes = _parallel(6, lambda i: actors[i].post(f"{BASE}/sessions/{sid}/next").status_code)
    assert fake.turn_calls - calls_before == 1, (fake.turn_calls, calls_before, codes)
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}, codes
    seqs = sql("SELECT seq FROM mock_interview_turns WHERE session_id = :i ORDER BY seq",
               {"i": sid}, tenant=a.tenant)
    assert seqs == [{"seq": 1}, {"seq": 2}]
    lease = sql("SELECT (plan_json::jsonb -> 'lease') IS NOT NULL AS has "
                "FROM mock_interview_sessions WHERE id = :i",
                {"i": sid}, tenant=a.tenant)
    assert lease == [{"has": False}]  # released after the step


def test_pg_mi_concurrent_answers_keep_one(campus, sql, mi_flag, fake):
    a = campus.a
    sid = _started(a.student)
    calls_before = fake.turn_calls
    fake.delay = 0.3
    actors = [_fresh_actor(campus, a.student, f"a{i}") for i in range(6)]
    texts = ["Answer X" if i % 2 == 0 else "Answer Y" for i in range(6)]
    codes = _parallel(6, lambda i: actors[i].post(
        f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": texts[i]}).status_code)
    stored = sql("SELECT answer_text FROM mock_interview_turns WHERE session_id = :i AND seq = 1",
                 {"i": sid}, tenant=a.tenant)[0]["answer_text"]
    assert stored in ("Answer X", "Answer Y")
    for i, code in enumerate(codes):
        if texts[i] != stored:
            assert code == 409, (codes, texts, stored)
        else:
            assert code in (200, 409), codes  # 409 = lease busy while the winner generates
    assert fake.turn_calls - calls_before == 1
    assert sql("SELECT count(*) AS n FROM mock_interview_turns WHERE session_id = :i",
               {"i": sid}, tenant=a.tenant) == [{"n": 2}]


def test_pg_mi_finish_beats_late_ai_result(campus, sql, mi_flag, fake):
    a = campus.a
    sid = _started(a.student)
    fake.gate = threading.Event()
    fake.entered.clear()
    answering = _fresh_actor(campus, a.student, "late")
    result = {}

    def answer():
        result["r"] = answering.post(f"{BASE}/sessions/{sid}/answer",
                                     json={"turn_seq": 1, "answer": "Thinking..."})

    t = threading.Thread(target=answer)
    t.start()
    assert fake.entered.wait(20), "AI step never started"
    done = a.student.post(f"{BASE}/sessions/{sid}/finish")
    assert done.status_code == 200 and done.json()["status"] == "completed", _d(done)
    fake.gate.set()  # the AI result arrives late
    t.join(30)
    assert result["r"].status_code == 200, _d(result["r"])
    body = result["r"].json()
    assert body["status"] == "completed" and body["end_reason"] == "ended_by_candidate"
    assert sql("SELECT seq, answer_text FROM mock_interview_turns WHERE session_id = :i",
               {"i": sid}, tenant=a.tenant) == [{"seq": 1, "answer_text": "Thinking..."}]
    assert sql("SELECT status FROM mock_interview_sessions WHERE id = :i",
               {"i": sid}, tenant=a.tenant) == [{"status": "completed"}]
