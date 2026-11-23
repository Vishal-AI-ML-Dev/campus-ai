"""Phase 8.1 coding round on real Postgres as the NOBYPASSRLS ``app_user``.

Proves what SQLite cannot:
  * the review-only coding flow works end to end under RLS;
  * the graded-submission limit holds under REAL parallel requests (atomic
    conditional UPDATE + row locks): exactly 3 of many concurrent submissions
    are accepted;
  * another institute's student gets 404 for every coding route.

No code is executed anywhere (Batch 1). Same safety model as the rest of
tests_pg_api (synthetic institutes, tenant cascade cleanup). Skipped unless
RLS_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import threading

import pytest

from flowkit import _d

BASE = "/v1/mock-interview"


@pytest.fixture
def coding_flags(monkeypatch, campus):
    from app.core.config import settings
    from app.core.rate_limit import limiter

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", f"{campus.a.tenant},{campus.b.tenant}")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS", True)
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    # Parallel submissions would otherwise meet the 3/minute rate limit first.
    monkeypatch.setattr(limiter, "enabled", False)


def _start(student, **coding):
    r = student.post(f"{BASE}/sessions", json={
        "interview_type": "coding", "target_role": "Engineer", "duration_minutes": 30,
        "difficulty": "easy", **({"coding": coding} if coding else {})})
    assert r.status_code == 201, _d(r)
    sid = r.json()["id"]
    r = student.post(f"{BASE}/sessions/{sid}/start")
    assert r.status_code == 200, _d(r)
    return sid


def test_pg_review_only_flow_under_rls(campus, sql, coding_flags):
    st = campus.a.student
    sid = _start(st)
    state = st.get(f"{BASE}/sessions/{sid}/coding")
    assert state.status_code == 200, _d(state)
    assert state.json()["review_only"] is True and len(state.json()["attempts"]) == 1
    r = st.put(f"{BASE}/sessions/{sid}/coding/attempts/1/draft",
               json={"language": "python313", "source": "print(1)\n", "draft_rev": 0})
    assert r.status_code == 200, _d(r)
    r = st.post(f"{BASE}/sessions/{sid}/coding/attempts/1/submissions",
                json={"language": "python313", "source": "print(2)\n", "idempotency_key": "pg-key-0001"})
    assert r.status_code == 202, _d(r)
    assert r.json()["status"] == "not_executed"
    r = st.post(f"{BASE}/sessions/{sid}/finish")
    assert r.status_code == 200 and r.json()["status"] == "completed", _d(r)
    rows = sql("SELECT kind, status FROM mock_interview_code_submissions WHERE session_id = :s ORDER BY id",
               {"s": sid}, tenant=campus.a.tenant)
    assert rows == [{"kind": "graded", "status": "not_executed"}, {"kind": "auto", "status": "not_executed"}]
    # Invisible to the other institute even at the SQL level.
    assert sql("SELECT count(*) AS n FROM mock_interview_code_submissions WHERE session_id = :s",
               {"s": sid}, tenant=campus.b.tenant) == [{"n": 0}]


def test_pg_graded_limit_holds_under_parallel_submissions(campus, sql, coding_flags):
    st = campus.a.student
    sid = _start(st)
    n = 8
    # One client (own connection) per thread, all signed in as the same student.
    clients = [campus.login(st.email, st.password, f"par{i}") for i in range(n)]
    barrier = threading.Barrier(n)
    codes: list = [None] * n

    def go(i):
        barrier.wait()
        codes[i] = clients[i].post(
            f"{BASE}/sessions/{sid}/coding/attempts/1/submissions",
            json={"language": "python313", "source": f"print({i})\n", "idempotency_key": f"par-{i:06d}"},
        ).status_code

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(90)
    assert sorted(codes) == [202, 202, 202] + [409] * (n - 3), codes
    rows = sql("SELECT submissions_used AS u FROM mock_interview_code_attempts WHERE session_id = :s",
               {"s": sid}, tenant=campus.a.tenant)
    assert rows == [{"u": 3}]
    assert sql("SELECT count(*) AS n FROM mock_interview_code_submissions WHERE session_id = :s",
               {"s": sid}, tenant=campus.a.tenant) == [{"n": 3}]


def test_pg_other_institute_gets_404(campus, coding_flags):
    sid = _start(campus.a.student)
    other = campus.b.student
    for method, path, body in (
        ("get", f"/sessions/{sid}/coding", None),
        ("put", f"/sessions/{sid}/coding/attempts/1/draft", {"language": "c11", "source": "x", "draft_rev": 0}),
        ("post", f"/sessions/{sid}/coding/attempts/1/submissions",
         {"language": "c11", "source": "x", "idempotency_key": "other-key-1"}),
        ("post", f"/sessions/{sid}/coding/attempts/1/hints", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(other, method)(f"{BASE}{path}", **kwargs)
        assert r.status_code == 404, (method, path, _d(r))
