"""Phase 8.1: coding round lifecycle, review-only (SQLite, offline).

A coding round is an ordinary session (``interview_type = "coding"``). These
tests prove create/start/problem assignment, drafts, graded submissions stored
as ``not_executed`` (nothing is ever executed), authored hints, closing a
problem, finish / abandon / time-up (with automatic submission of saved
drafts), ownership and tenant isolation, and that the HR/technical question
and report routes answer coding rounds with the documented 409 - so a coding
session can never reach the AI engine or the HR/technical rubric.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

import app.models as models
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.services import coding_bank
from app.services import mock_interview_coding as coding
from tests.test_mock_interview_sessions import BASE, _create, _student

PY = "import sys\nprint(sum(map(int, sys.stdin.read().split()[1:])))\n"


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


@pytest.fixture
def no_engine(monkeypatch):
    """Any AI call from a coding round is a bug: fail loudly."""
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("coding rounds must not call the AI engine in Batch 1")

    for name in ("plan_interview", "interview_turn", "generate_report"):
        monkeypatch.setattr(engine, name, boom)
    return calls


@pytest.fixture
def stu(make_user, token_header):
    return _student(make_user, token_header)


@pytest.fixture
def no_rate_limit(monkeypatch):
    """Most tests submit more than 3 times a minute; the limit has its own test."""
    from app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
def gate(monkeypatch, flag, stu, no_engine, no_rate_limit):
    """Every coding gate open for ``stu``'s institute; draft problems allowed (dev, B1-b)."""
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", str(stu[0].tenant_id))
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS", True)
    return stu


def expire(session, sid, minutes=61):
    """Move a round's start back so its time is up."""
    row = session.get(models.MockInterviewSession, sid)
    session.execute(
        update(models.MockInterviewSession)
        .where(models.MockInterviewSession.id == sid)
        .values(started_at=row.started_at - timedelta(minutes=minutes))
    )
    session.commit()


def create_coding(client, h, **overrides):
    body = {"interview_type": "coding", "target_role": "Software Engineer", "duration_minutes": 45}
    body.update(overrides)
    return client.post(f"{BASE}/sessions", json=body, headers=h)


def started_coding(client, h, **overrides):
    r = create_coding(client, h, **overrides)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"{BASE}/sessions/{sid}/start", headers=h)
    assert r.status_code == 200, r.text
    return sid, r.json()


def state(client, h, sid):
    r = client.get(f"{BASE}/sessions/{sid}/coding", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def submit(client, h, sid, seq=1, source=PY, key="key-00001", language="python313"):
    return client.post(
        f"{BASE}/sessions/{sid}/coding/attempts/{seq}/submissions",
        json={"language": language, "source": source, "idempotency_key": key},
        headers=h,
    )


def draft(client, h, sid, seq=1, source=PY, rev=0, language="python313"):
    return client.put(
        f"{BASE}/sessions/{sid}/coding/attempts/{seq}/draft",
        json={"language": language, "source": source, "draft_rev": rev},
        headers=h,
    )


# --- Create and start ------------------------------------------------------------------


def test_create_stores_choices_and_starts_without_any_ai_call(client, gate, session, no_engine):
    _, h = gate
    r = create_coding(client, h, difficulty="medium",
                      coding={"languages": ["python313", "cpp17"], "topics": ["graphs"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["interview_type"] == "coding" and body["mode"] == "text"
    assert body["awaiting"] == "start" and body["turns"] == []
    assert body["coding_summary"] == {
        "problems": 0, "problems_closed": 0, "graded_submissions": 0, "review_only": True}
    row = session.get(models.MockInterviewSession, body["id"])
    assert row.plan_json == {"coding": {"languages": ["python313", "cpp17"], "topics": ["graphs"],
                                        "problem_key": None, "review_only": True}}

    started = client.post(f"{BASE}/sessions/{body['id']}/start", headers=h)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress" and started.json()["awaiting"] == "none"
    assert started.json()["coding_summary"]["problems"] == 2
    assert no_engine == []
    st = state(client, h, body["id"])
    assert st["review_only"] is True and st["languages"] == ["python313", "cpp17"]
    keys = [a["problem"]["key"] for a in st["attempts"]]
    # Topic preference: both medium graph problems are chosen.
    assert sorted(keys) == ["count-islands", "tree-level-sums"]
    assert all(a["status"] == "pending" and a["submissions_remaining"] == 3 for a in st["attempts"])
    # Start is idempotent and keeps the same problems.
    again = client.post(f"{BASE}/sessions/{body['id']}/start", headers=h)
    assert again.status_code == 200
    assert [a["problem"]["key"] for a in state(client, h, body["id"])["attempts"]] == keys


@pytest.mark.parametrize(
    "difficulty,duration,expected",
    [("easy", 30, ["easy"]), ("easy", 45, ["easy", "easy"]), ("medium", 60, ["medium", "medium"]),
     ("hard", 60, ["hard", "medium"])],
)
def test_problem_count_and_difficulty_per_round(client, gate, difficulty, duration, expected):
    _, h = gate
    sid, _ = started_coding(client, h, difficulty=difficulty, duration_minutes=duration)
    assert [a["problem"]["difficulty"] for a in state(client, h, sid)["attempts"]] == expected


def test_practice_mode_assigns_exactly_the_chosen_problem(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h, duration_minutes=30, coding={"problem_key": "balanced-brackets"})
    attempts = state(client, h, sid)["attempts"]
    assert [a["problem"]["key"] for a in attempts] == ["balanced-brackets"]


def test_next_round_avoids_recently_seen_problems(client, gate):
    _, h = gate
    first, _ = started_coding(client, h, difficulty="medium")
    seen = {a["problem"]["key"] for a in state(client, h, first)["attempts"]}
    assert client.post(f"{BASE}/sessions/{first}/abandon", headers=h).status_code == 200
    second, _ = started_coding(client, h, difficulty="medium")
    again = {a["problem"]["key"] for a in state(client, h, second)["attempts"]}
    assert not seen & again


@pytest.mark.parametrize("override,status", [
    ({"coding": {"topics": ["not-a-topic"]}}, 422),
    ({"coding": {"problem_key": "no-such-problem"}}, 422),
    ({"coding": {"languages": ["python313", "python313"]}}, 422),
    ({"coding": {"languages": ["ruby"]}}, 422),
    ({"coding": {"languages": []}}, 422),
    ({"coding": {"unknown": 1}}, 422),
    ({"duration_minutes": 15}, 422),
    ({"mode": "voice"}, 422),
    ({"mode": "video"}, 422),
])
def test_create_validation(client, gate, session, override, status):
    _, h = gate
    r = create_coding(client, h, **override)
    assert r.status_code == status, r.text
    assert session.scalar(select(models.MockInterviewSession.id)) is None


def test_coding_is_text_only_even_with_voice_and_video_on(client, gate, monkeypatch):
    stu_user, h = gate
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(stu_user.tenant_id))
    for mode in ("voice", "video"):
        r = create_coding(client, h, mode=mode)
        assert r.status_code == 422 and r.json()["detail"] == coding.MSG_TEXT_ONLY


def test_hr_and_technical_durations_and_fields_unchanged(client, gate):
    _, h = gate
    assert _create(client, h, duration_minutes=60).status_code == 422
    assert _create(client, h, interview_type="technical", duration_minutes=60).status_code == 422
    assert _create(client, h, coding={"languages": ["c11"]}).status_code == 422
    r = _create(client, h)
    assert r.status_code == 201
    # Existing responses are byte-identical: no coding key on HR/technical sessions.
    assert "coding_summary" not in r.json()
    sid = r.json()["id"]
    for url in (f"{BASE}/sessions/{sid}", f"{BASE}/sessions/active"):
        assert "coding_summary" not in client.get(url, headers=h).json()


def test_one_active_session_and_daily_limit_include_coding(client, gate):
    _, h = gate
    sid = create_coding(client, h).json()["id"]
    assert _create(client, h).status_code == 409
    assert client.post(f"{BASE}/sessions/{sid}/abandon", headers=h).status_code == 200
    assert _create(client, h).status_code == 201


# --- State: public material only -------------------------------------------------------


def test_state_exposes_only_public_problem_material(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h)
    a = state(client, h, sid)["attempts"][0]
    p = a["problem"]
    assert set(p) == {
        "key", "version", "title", "difficulty", "topics", "statement", "input_format",
        "output_format", "constraints", "examples", "visible_tests", "starter", "time_limit_ms",
        "memory_limit_mb", "hints_revealed",
    }
    assert set(p["starter"]) == {"python313", "cpp17", "java21", "c11"}
    assert p["hints_revealed"] == []
    assert p["visible_tests"] and all(set(t) == {"input", "output"} for t in p["visible_tests"])


# --- Drafts --------------------------------------------------------------------------


def test_draft_autosave_uses_optimistic_revision(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h)
    r = draft(client, h, sid, rev=0)
    assert r.status_code == 200 and r.json()["draft_rev"] == 1
    stale = draft(client, h, sid, source="print(1)\n", rev=0)
    assert stale.status_code == 409 and stale.json()["detail"] == coding.MSG_DRAFT_STALE
    assert draft(client, h, sid, source="print(2)\n", rev=1).json()["draft_rev"] == 2
    a = state(client, h, sid)["attempts"][0]
    assert a["status"] == "working" and a["draft_source"] == "print(2)\n" and a["language"] == "python313"


def test_draft_and_submit_validation(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h, coding={"languages": ["cpp17"]})
    wrong_lang = draft(client, h, sid, language="python313")
    assert wrong_lang.status_code == 422 and wrong_lang.json()["detail"] == coding.MSG_LANGUAGE
    big = "é" * 33_000  # 66,000 UTF-8 bytes but only 33,000 characters
    too_big = draft(client, h, sid, source=big, language="cpp17")
    assert too_big.status_code == 422 and too_big.json()["detail"] == coding.MSG_TOO_LARGE
    blank = submit(client, h, sid, source="   \n", language="cpp17")
    assert blank.status_code == 422 and blank.json()["detail"] == coding.MSG_EMPTY
    bad_key = submit(client, h, sid, key="no", language="cpp17")
    assert bad_key.status_code == 422
    assert draft(client, h, sid, seq=9).status_code == 404


# --- Graded submissions (review-only) -----------------------------------------------------


def test_graded_submission_is_stored_but_never_executed(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h)
    r = submit(client, h, sid)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "not_executed" and body["verdict"] is None
    assert body["verification"] == "not_verified" and body["is_best"] is False
    assert "source" not in body
    row = session.get(models.MockInterviewCodeSubmission, body["id"])
    assert row.kind == "graded" and row.source == PY and row.status == "not_executed"
    assert row.group_results_json is None and row.visible_results_json is None
    got = client.get(f"{BASE}/sessions/{sid}/coding/submissions/{body['id']}", headers=h)
    assert got.status_code == 200 and got.json() == body


def test_idempotency_duplicates_and_the_three_submission_limit(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h)
    first = submit(client, h, sid, key="retry-key-1").json()
    assert submit(client, h, sid, key="retry-key-1").json()["id"] == first["id"]  # retry
    reused = submit(client, h, sid, source="print(9)\n", key="retry-key-1")
    assert reused.status_code == 409 and reused.json()["detail"] == coding.MSG_KEY_REUSED
    assert submit(client, h, sid, key="other-key-2").json()["id"] == first["id"]  # identical code
    assert state(client, h, sid)["attempts"][0]["submissions_used"] == 1
    for i in (2, 3):
        assert submit(client, h, sid, source=f"print({i})\n", key=f"key-{i:05d}").status_code == 202
    over = submit(client, h, sid, source="print(4)\n", key="key-00004")
    assert over.status_code == 409 and over.json()["detail"] == coding.MSG_GRADED_LIMIT
    a = state(client, h, sid)["attempts"][0]
    assert a["submissions_used"] == 3 and a["submissions_remaining"] == 0
    history = client.get(f"{BASE}/sessions/{sid}/coding/attempts/1/submissions", headers=h).json()
    assert [s["kind"] for s in history] == ["graded"] * 3


def test_weekly_cap_per_problem_across_rounds(client, gate):
    _, h = gate
    pick = {"problem_key": "pair-with-target-sum"}
    for round_no in range(2):
        sid, _ = started_coding(client, h, duration_minutes=30, coding=pick)
        for i in range(3):
            r = submit(client, h, sid, source=f"print({round_no}{i})\n", key=f"w{round_no}-{i:06d}")
            assert r.status_code == 202, r.text
        assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()["status"] == "completed"
    sid, _ = started_coding(client, h, duration_minutes=30, coding=pick)
    capped = submit(client, h, sid, source="print(77)\n", key="w3-000000")
    assert capped.status_code == 429 and capped.json()["detail"] == coding.MSG_WEEKLY_LIMIT


# --- Hints ---------------------------------------------------------------------------


@pytest.mark.parametrize("difficulty,cap", [("easy", 3), ("hard", 2)])
def test_hints_are_authored_capped_and_remembered(client, gate, difficulty, cap):
    _, h = gate
    sid, _ = started_coding(client, h, difficulty=difficulty)
    for n in range(1, cap + 1):
        r = client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/hints", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["hint_number"] == n and r.json()["hints_available"] == cap
    more = client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/hints", headers=h)
    assert more.status_code == 409 and more.json()["detail"] == coding.MSG_NO_HINTS
    a = state(client, h, sid)["attempts"][0]
    problem = coding_bank.get_problem(a["problem"]["key"])
    assert a["problem"]["hints_revealed"] == list(problem.hints[:cap]) and a["hints_used"] == cap


# --- Done, finish, abandon, time up ---------------------------------------------------


def test_done_closes_one_problem_and_blocks_further_changes(client, gate):
    _, h = gate
    sid, _ = started_coding(client, h)
    assert draft(client, h, sid).status_code == 200
    done = client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/done", headers=h)
    assert done.status_code == 200 and done.json()["attempts"][0]["status"] == "closed"
    assert client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/done", headers=h).status_code == 200
    for r in (draft(client, h, sid, rev=1), submit(client, h, sid),
              client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/hints", headers=h)):
        assert r.status_code == 409 and r.json()["detail"] == coding.MSG_PROBLEM_CLOSED


def test_finish_auto_submits_saved_drafts_and_completes(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h)  # medium: two problems
    assert draft(client, h, sid, seq=1).status_code == 200
    r = client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed" and body["end_reason"] == "ended_by_candidate"
    assert body["coding_summary"]["graded_submissions"] == 1
    subs = session.scalars(select(models.MockInterviewCodeSubmission)).all()
    assert [(s.kind, s.status) for s in subs] == [("auto", "not_executed")]
    assert {a["status"] for a in state(client, h, sid)["attempts"]} == {"closed"}
    # Idempotent; the round can no longer change.
    assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()["status"] == "completed"
    late = submit(client, h, sid)
    assert late.status_code == 409 and late.json()["detail"] == coding.MSG_NOT_IN_PROGRESS


def test_finish_does_not_duplicate_already_submitted_code(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h, duration_minutes=30, difficulty="easy")
    assert draft(client, h, sid).status_code == 200
    assert submit(client, h, sid).status_code == 202
    client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    kinds = [s.kind for s in session.scalars(select(models.MockInterviewCodeSubmission))]
    assert kinds == ["graded"]


def test_finish_with_nothing_written_is_abandoned_and_abandon_keeps_no_auto_submission(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h)
    body = client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()
    assert body["status"] == "abandoned" and body["end_reason"] == "abandoned"

    sid2, _ = started_coding(client, h)
    assert draft(client, h, sid2).status_code == 200
    body = client.post(f"{BASE}/sessions/{sid2}/abandon", headers=h).json()
    assert body["status"] == "abandoned"
    assert session.scalar(select(models.MockInterviewCodeSubmission.id)) is None
    assert {a["status"] for a in state(client, h, sid2)["attempts"]} == {"abandoned"}


def test_time_up_ends_the_round_and_auto_submits(client, gate, session):
    _, h = gate
    sid, _ = started_coding(client, h, duration_minutes=30, difficulty="easy")
    assert draft(client, h, sid).status_code == 200
    expire(session, sid)
    r = client.get(f"{BASE}/sessions/{sid}", headers=h)
    assert r.json()["status"] == "completed" and r.json()["end_reason"] == "time_up"
    session.expire_all()
    kinds = [s.kind for s in session.scalars(select(models.MockInterviewCodeSubmission))]
    assert kinds == ["auto"]
    late = draft(client, h, sid, rev=1)
    assert late.status_code == 409 and late.json()["detail"] == coding.MSG_NOT_IN_PROGRESS


# --- HR/technical routes refuse coding rounds (until 8.3) ---------------------------------


def test_question_and_report_routes_reject_coding_with_documented_409(client, gate, no_engine):
    _, h = gate
    sid, _ = started_coding(client, h)
    for url, body in ((f"{BASE}/sessions/{sid}/answer", {"turn_seq": 1, "answer": "hello"}),
                      (f"{BASE}/sessions/{sid}/next", None)):
        r = client.post(url, json=body, headers=h)
        assert r.status_code == 409 and r.json()["detail"] == coding.CODING_STEP_UNSUPPORTED
    assert submit(client, h, sid).status_code == 202
    client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    for method in (client.get, client.post):
        r = method(f"{BASE}/sessions/{sid}/report", headers=h)
        assert r.status_code == 409 and r.json()["detail"] == coding.CODING_FEEDBACK_UNAVAILABLE
    assert no_engine == []
    item = next(i for i in client.get(f"{BASE}/history", headers=h).json() if i["id"] == sid)
    assert item["interview_type"] == "coding" and item["report"] is None


# --- Isolation -----------------------------------------------------------------------


def test_other_students_institutes_and_non_coding_sessions_get_404(
    client, gate, make_user, make_tenant, token_header, monkeypatch
):
    stu_user, h = gate
    sid, _ = started_coding(client, h)
    sub_id = submit(client, h, sid).json()["id"]
    _, h_peer = _student(make_user, token_header, email="peer@x.edu")
    other = make_tenant("other")
    other_user, h_other = _student(make_user, token_header, email="o@y.edu", tenant=other)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS",
                        f"{stu_user.tenant_id},{other_user.tenant_id}")
    urls = [
        ("get", f"{BASE}/sessions/{sid}/coding", None),
        ("put", f"{BASE}/sessions/{sid}/coding/attempts/1/draft",
         {"language": "python313", "source": "x", "draft_rev": 0}),
        ("post", f"{BASE}/sessions/{sid}/coding/attempts/1/submissions",
         {"language": "python313", "source": "x", "idempotency_key": "peer-key-1"}),
        ("get", f"{BASE}/sessions/{sid}/coding/submissions/{sub_id}", None),
        ("get", f"{BASE}/sessions/{sid}/coding/attempts/1/submissions", None),
        ("post", f"{BASE}/sessions/{sid}/coding/attempts/1/hints", None),
        ("post", f"{BASE}/sessions/{sid}/coding/attempts/1/done", None),
    ]
    for hdr in (h_peer, h_other):
        for method, url, body in urls:
            kwargs = {"headers": hdr}
            if body is not None:
                kwargs["json"] = body
            assert getattr(client, method)(url, **kwargs).status_code == 404, (method, url)
    # A submission id from another session is not reachable through mine.
    hr = _create(client, h_peer).json()["id"]
    assert client.get(f"{BASE}/sessions/{hr}/coding", headers=h_peer).status_code == 404
