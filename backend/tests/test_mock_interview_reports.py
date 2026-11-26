"""Phase 4: interview reports + history API (SQLite, offline; worker stubbed).

Proves the APP-LAYER guarantees: flag + students only, owner/tenant 404s,
eligibility, one final report per session (idempotent, no extra AI call),
worker failure stores nothing, strict re-validation of the worker output,
the lease, privacy of the worker payload, history and the rate limit.
The evidence rules themselves are proven in ai-worker/tests/test_interview_report.py.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

import app.models as models
import app.services.mock_interview_ai as engine
import app.services.mock_interview_report as report_svc
from app.core.config import settings
from app.core.db import SessionLocal
from tests.test_mock_interview_sessions import (
    BASE,
    FakeEngine,
    _answer,
    _create,
    _lease,
    _resume,
    _started,
    _student,
)


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


@pytest.fixture
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f

HR = [
    ("relevance", "Relevance", 0.25),
    ("structure", "Structure", 0.20),
    ("specificity", "Specific examples", 0.20),
    ("communication", "Communication", 0.20),
    ("self_awareness", "Self-awareness", 0.15),
]
TECH = [
    ("technical_accuracy", "Technical accuracy", 0.35),
    ("depth", "Depth", 0.25),
    ("problem_solving", "Problem solving", 0.15),
    ("communication", "Communication", 0.15),
    ("relevance", "Relevance", 0.10),
]


class FakeReporter:
    """Worker-shaped report: every answer scores ``score`` with a real quote."""

    def __init__(self, score=4, rubric=HR, version="hr-v1"):
        self.score = score
        self.rubric = rubric
        self.version = version
        self.calls: list[dict] = []
        self.fail = False
        self.mutate = None  # callable(report) -> None, to inject bad output

    def __call__(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else engine.InterviewEngineUnavailable("report")
        HR = self.rubric  # noqa: N806 - local alias
        questions = []
        for t in payload["transcript"]:
            if t["answer"] is None:
                questions.append({"seq": t["seq"], "status": "no_answer", "feedback": "No answer given.",
                                  "scores": {k: {"score": 0, "evidence": ""} for k, _, _ in HR}})
            else:
                quote = t["answer"][:20]
                questions.append({"seq": t["seq"], "status": "scored", "feedback": "Clear answer.",
                                  "scores": {k: {"score": self.score, "evidence": quote} for k, _, _ in HR}})
        n = len(questions)
        answered = sum(1 for q in questions if q["status"] == "scored")
        cat = round(self.score * answered / n * 20, 1)
        first = next(t for t in payload["transcript"] if t["answer"])
        report = {
            "rubric_version": self.version,
            "overall_score": cat,
            "categories": [{"key": k, "label": label, "weight": w, "description": "d",
                            "score": cat, "assessed": n} for k, label, w in HR],
            "questions": questions,
            "strengths": [{"text": "Good example.", "seq": first["seq"], "evidence": first["answer"][:10]}],
            "weaknesses": [{"text": "Work on structure (40/100).", "seq": None, "evidence": ""}],
            "recommendations": ["Use STAR.", "Quantify results."],
            "provider": "fake",
        }
        if self.mutate:
            self.mutate(report)
        return report


@pytest.fixture
def reporter(monkeypatch):
    r = FakeReporter()
    monkeypatch.setattr(engine, "generate_report", r)
    return r


def _completed(client, h, answers=("I led a team of four in my final year project.",
                                    "I want to grow into a backend engineer."), **overrides):
    sid, _ = _started(client, h, **overrides)
    for seq, text in enumerate(answers, start=1):
        assert _answer(client, h, sid, seq, text).status_code == 200
    s = client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()
    assert s["status"] == "completed"
    return sid, s


def _report_rows(session, sid):
    session.expire_all()
    return session.scalars(
        select(models.MockInterviewReport).where(models.MockInterviewReport.session_id == sid)
    ).all()


# --- Flag, roles, ownership ------------------------------------------------------
def test_flag_off_hides_phase4_routes(client, make_user, token_header, reporter):
    _, h = _student(make_user, token_header)
    for method, path in [("get", "/history"), ("get", "/sessions/1/report"), ("post", "/sessions/1/report")]:
        for prefix in ("/v1", ""):
            r = getattr(client, method)(f"{prefix}/mock-interview{path}", headers=h)
            assert r.status_code == 404, (method, prefix, path)
    assert not reporter.calls


def test_staff_are_forbidden(client, make_user, token_header, flag, reporter):
    make_user("t@x.edu", role=models.UserRole.tpo)
    h = token_header("t@x.edu")
    assert client.get(f"{BASE}/history", headers=h).status_code == 403
    assert client.get(f"{BASE}/sessions/1/report", headers=h).status_code == 403
    assert client.post(f"{BASE}/sessions/1/report", headers=h).status_code == 403


def test_other_students_and_institutes_get_404(client, make_user, make_tenant, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200
    _, mate = _student(make_user, token_header, "mate@x.edu")
    _, outsider = _student(make_user, token_header, "out@y.edu", tenant=make_tenant(slug="o", name="O"))
    calls = len(reporter.calls)
    for intruder in (mate, outsider):
        assert client.get(f"{BASE}/sessions/{sid}/report", headers=intruder).status_code == 404
        assert client.post(f"{BASE}/sessions/{sid}/report", headers=intruder).status_code == 404
        assert client.get(f"{BASE}/history", headers=intruder).json() == []
    assert len(reporter.calls) == calls


# --- Generation ----------------------------------------------------------------------
def test_generate_store_and_read_back(client, make_user, token_header, flag, fake, reporter, session):
    user, h = _student(make_user, token_header)
    resume = _resume(session, user)
    sid, _ = _completed(client, h, resume_id=resume.id, job_description="Python, Django, REST APIs")

    missing = client.get(f"{BASE}/sessions/{sid}/report", headers=h)
    assert missing.status_code == 404 and missing.json()["detail"] == "Report not generated yet"

    r = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["session_id"] == sid and rep["overall_score"] == 53.3 and rep["rubric_version"] == "hr-v1"
    assert [c["key"] for c in rep["categories"]] == [k for k, _, _ in HR]
    q1 = rep["questions"][0]
    assert q1["seq"] == 1 and q1["status"] == "scored"
    assert q1["answer_text"].startswith("I led a team")  # joined from the turn, not copied
    assert q1["question"] and q1["scores"][0] == {
        "key": "relevance", "label": "Relevance", "score": 4, "evidence": q1["answer_text"][:20]}
    assert rep["strengths"][0]["seq"] == 1 and rep["recommendations"] == ["Use STAR.", "Quantify results."]
    assert "do not predict selection" in rep["disclaimer"]

    # Privacy: the worker gets the transcript only.
    payload = reporter.calls[0]
    assert set(payload) == {"config", "plan", "transcript"}
    assert "Flask API" not in str(payload)  # resume markdown
    assert "Django" not in str(payload)  # job description
    assert [t["seq"] for t in payload["transcript"]] == [1, 2, 3]  # 3rd asked, unanswered

    # Stored once; GET and a second POST return the same data with no AI call.
    assert client.get(f"{BASE}/sessions/{sid}/report", headers=h).json() == rep
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).json() == rep
    assert len(reporter.calls) == 1
    (row,) = _report_rows(session, sid)
    assert row.tenant_id == user.tenant_id and row.provider == "fake"
    assert set(row.category_scores_json) == {"categories", "questions"}
    # The lease was released together with the insert.
    session.refresh(session.get(models.MockInterviewSession, sid))
    assert "lease" not in session.get(models.MockInterviewSession, sid).plan_json


def test_unanswered_question_shows_as_no_answer(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h, answers=("Only one answer before ending early.",))
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h).json()
    assert [q["status"] for q in rep["questions"]] == ["scored", "no_answer"]
    assert rep["questions"][1]["answer_text"] is None
    assert all(s["score"] == 0 for s in rep["questions"][1]["scores"])


def test_eligibility(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    created = _create(client, h).json()["id"]
    assert client.post(f"{BASE}/sessions/{created}/report", headers=h).status_code == 409  # setup
    client.post(f"{BASE}/sessions/{created}/start", headers=h)
    r = client.post(f"{BASE}/sessions/{created}/report", headers=h)
    assert r.status_code == 409 and "Finish" in r.json()["detail"]  # in progress
    client.post(f"{BASE}/sessions/{created}/abandon", headers=h)
    r = client.post(f"{BASE}/sessions/{created}/report", headers=h)
    assert r.status_code == 409 and "Cancelled" in r.json()["detail"]
    assert not reporter.calls


def test_worker_failure_stores_nothing_and_retry_works(client, make_user, token_header, flag, fake, reporter, session):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    reporter.fail = True
    r = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert r.status_code == 503 and "answers are saved" in r.json()["detail"]
    assert _report_rows(session, sid) == []
    reporter.fail = False  # the lease was released: an immediate retry works
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200
    assert len(_report_rows(session, sid)) == 1


def _set(path, value):
    def mutate(report):
        target = report
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


@pytest.mark.parametrize("mutate", [
    _set(("overall_score",), 140),
    _set(("questions", 0, "scores", "relevance", "evidence"), "words never said by the student"),
    _set(("questions", 2, "scores", "relevance", "score"), 3),  # unanswered must stay 0
    _set(("questions", 0, "status"), "dont_know"),  # "don't know" with non-zero scores
    _set(("questions", 0, "scores"), {}),  # scored without scores
    _set(("strengths", 0, "evidence"), "something else entirely"),
    _set(("questions", 0, "scores", "relevance", "score"), 9),
    _set(("questions", 0, "scores", "relevance", "evidence"), ""),  # non-zero without a quote
    _set(("questions", 0, "scores", "made_up"), {"score": 1, "evidence": "x"}),
    _set(("questions", 0, "seq"), 7),  # a question that doesn't exist
    _set(("questions", 2, "status"), "scored"),  # unanswered question "scored"
    _set(("categories", 0, "key"), "charisma"),
    _set(("rubric_version",), "technical-v1"),
    _set(("strengths", 0, "seq"), 3),  # cites an unanswered question
    _set(("questions",), []),
], ids=["overall", "evidence-not-in-answer", "unanswered-nonzero", "dont-know-nonzero",
        "scored-empty", "strength-evidence", "range", "no-evidence", "unknown-key", "unknown-seq", "unanswered-scored",
        "unknown-category", "rubric", "strength-seq", "empty"])
def test_invalid_worker_output_is_rejected(client, make_user, token_header, flag, fake, reporter, session, mutate):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    reporter.mutate = mutate
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 503
    assert _report_rows(session, sid) == []


def test_only_one_generation_at_a_time(client, make_user, token_header, flag, fake, reporter, session):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    _lease(session, sid)  # another request is generating right now
    busy = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert busy.status_code == 409 and "being prepared" in busy.json()["detail"]
    assert not reporter.calls
    _lease(session, sid, seconds=-1)  # crashed request: the lease expires
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200


def test_parallel_insert_is_resolved_by_the_unique_key(client, make_user, token_header, flag, fake, reporter, monkeypatch):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)

    def racing(payload):
        out = FakeReporter(score=4)(payload)
        # Another request (lease bypassed) stores a report while the AI "thinks".
        other = SessionLocal()
        try:
            s = other.get(models.MockInterviewSession, sid)
            other.add(models.MockInterviewReport(
                tenant_id=s.tenant_id, session_id=sid, overall_score=12.5,
                category_scores_json={"categories": [], "questions": []}, strengths_json=[],
                weaknesses_json=[], recommendations_json=[], rubric_version="hr-v1", provider="other"))
            other.commit()
        finally:
            other.close()
        return out

    monkeypatch.setattr(engine, "generate_report", racing)
    r = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert r.status_code == 200 and r.json()["overall_score"] == 12.5  # the first stored wins


# --- History, rate limit ---------------------------------------------------------------
def test_history_lists_my_sessions_with_scores(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    first, _ = _completed(client, h)
    client.post(f"{BASE}/sessions/{first}/report", headers=h)
    second = _create(client, h).json()["id"]
    client.post(f"{BASE}/sessions/{second}/abandon", headers=h)
    items = client.get(f"{BASE}/history", headers=h).json()
    assert [i["id"] for i in items] == [second, first]
    assert items[0]["status"] == "abandoned" and items[0]["report"] is None and items[0]["answered"] == 0
    assert items[1]["report"]["overall_score"] == 53.3 and items[1]["answered"] == 2
    assert set(items[1]) >= {"interview_type", "target_role", "completed_at", "created_at"}


def test_report_generation_is_rate_limited(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    codes = [client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert len(reporter.calls) == 1
    assert client.get(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200  # reads unaffected


def test_report_service_never_regenerates(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    reporter.score = 1
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).json()["overall_score"] == 53.3  # 2 of 3 answered x 4/5
    assert len(reporter.calls) == 1
    assert report_svc.REPORT_LEASE_SECONDS > 120  # outlives the worker timeout


def test_backend_recomputes_scores_and_sends_a_slim_plan(client, make_user, token_header, flag, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)

    def inflate(report):
        report["overall_score"] = 99.0  # in range, but not what the scores say
        report["categories"][0]["score"] = 100.0
        report["categories"][0]["weight"] = 0.9

    reporter.mutate = inflate
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h).json()
    assert rep["overall_score"] == 53.3  # recomputed from the per-question scores
    assert rep["categories"][0]["score"] == 53.3 and rep["categories"][0]["weight"] == 0.25
    plan = reporter.calls[0]["plan"]
    assert set(plan) == {"topics", "question_budget"}
    assert all(set(t) == {"name", "focus"} for t in plan["topics"])


def test_technical_rubric(client, make_user, token_header, flag, fake, monkeypatch):
    r = FakeReporter(score=5, rubric=TECH, version="technical-v1")
    monkeypatch.setattr(engine, "generate_report", r)
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h, interview_type="technical")
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h).json()
    assert rep["rubric_version"] == "technical-v1"
    assert [c["key"] for c in rep["categories"]] == [k for k, _, _ in TECH]
    assert rep["overall_score"] == 66.7  # 5 x 2/3 x 20, half-up
    # An HR rubric for a technical interview is rejected.
    _, h2 = _student(make_user, token_header, "b@x.edu")
    sid2, _ = _completed(client, h2, interview_type="technical")
    monkeypatch.setattr(engine, "generate_report", FakeReporter())
    assert client.post(f"{BASE}/sessions/{sid2}/report", headers=h2).status_code == 503


def test_timeout_keeps_the_lease(client, make_user, token_header, flag, fake, reporter, session):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    reporter.fail = engine.InterviewEngineUnavailable("/interview/report", timed_out=True)
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 503
    reporter.fail = False
    # The worker may still be running: no second paid run until the lease expires.
    busy = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert busy.status_code == 409 and len(reporter.calls) == 1
    _lease(session, sid, seconds=-1)
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200


def test_unassessable_interview_stops_retrying(client, make_user, token_header, flag, fake, reporter, session):
    _, h = _student(make_user, token_header)
    sid, _ = _completed(client, h)
    reporter.fail = engine.InterviewEngineUnavailable("/interview/report", status_code=422)
    codes = [client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code for _ in range(3)]
    assert codes == [503, 503, 409]
    final = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert final.status_code == 409 and "couldn't assess" in final.json()["detail"]
    assert len(reporter.calls) == 3 and _report_rows(session, sid) == []


# --- Contract with the real worker output -------------------------------------------
CONTRACT = Path(__file__).parent / "fixtures" / "mock_interview_worker_report.json"
# Must match CONTRACT_ANSWERS in ai-worker/tests/test_interview_report.py.
CONTRACT_ANSWERS = {
    1: ("In my final year project I led a team of four to build a library management "
        "API in Flask, I split the work by module and we delivered two weeks early"),
    2: "I don't know",
    3: "I am a quick learner always.",
    4: None,
}


def test_real_worker_output_passes_backend_validation():
    raw = json.loads(CONTRACT.read_text(encoding="utf-8"))
    report = report_svc._validated(raw, "hr", dict(CONTRACT_ANSWERS))
    assert report.overall_score == raw["overall_score"] == 30.0
    assert [c.score for c in report.categories] == [c["score"] for c in raw["categories"]]
    assert [q.status for q in report.questions] == ["scored", "dont_know", "scored", "no_answer"]
    # The same output against a different answer fails the evidence re-check.
    with pytest.raises(ValueError):
        report_svc._validated(raw, "hr", {**CONTRACT_ANSWERS, 1: "A completely different answer text here"})
