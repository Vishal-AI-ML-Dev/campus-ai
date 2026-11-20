"""Phase 6.5: report parity across text, voice and video interviews (SQLite, offline).

The same interview (type, role, settings, questions and answer text) is run as
text, voice and video. The worker payload, the stored report row and the API
report response must be identical apart from per-session identifiers and
timestamps: the interview mode, the answer source (typed / speech-to-text) and
the camera never reach evaluation. Reuses the Phase 3/4 fakes (scripted engine,
deterministic reporter that scores only the payload it receives).
"""

import json

import pytest
from sqlalchemy import select

import app.models as models
import app.services.mock_interview_ai as engine
from app.core.config import settings
from tests.test_mock_interview_reports import HR, TECH, FakeReporter
from tests.test_mock_interview_sessions import BASE, FakeEngine, _create, _student

ANSWERS = [
    "I led a team of four in my final year project and shipped it a week early.",
    "I want to grow into a backend engineer who designs reliable APIs.",
]
# Every key the worker receives, at every level. Anything else would be a leak.
PAYLOAD_KEYS = {"config", "plan", "transcript"}
CONFIG_KEYS = {"interview_type", "difficulty", "duration_minutes", "target_role", "company_name", "language"}
TURN_KEYS = {"seq", "topic", "question", "is_follow_up", "answer"}
# Differ by construction (each run is its own session).
PER_SESSION = {"session_id", "created_at", "started_at", "completed_at"}
# Plan topics carry a "source" key (the topic's origin, set by the worker itself), so the answer
# source is covered by the exact TURN_KEYS check instead.
FORBIDDEN = ("video", "camera", "frame", "image", "media", "audio", "stt", "typed", "mode", "device")


@pytest.fixture
def all_modes(monkeypatch, make_user, token_header):
    """Master switch on; voice on; video on for the default institute only."""
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", True)
    first, _ = _student(make_user, token_header, "probe@x.edu")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(first.tenant_id))


@pytest.fixture
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f


def _install_reporter(monkeypatch, interview_type):
    r = FakeReporter() if interview_type == "hr" else FakeReporter(rubric=TECH, version="technical-v1")
    monkeypatch.setattr(engine, "generate_report", r)
    return r


def _run(client, make_user, token_header, reporter, *, mode, interview_type, sources):
    """One complete interview in ``mode``; returns (worker payload, POST report, GET report, report row)."""
    _, h = _student(make_user, token_header, f"{mode}-{interview_type}-{'-'.join(sources)}@x.edu")
    extra = {} if mode == "text" else {"mode": mode}
    created = _create(
        client, h, interview_type=interview_type, target_role="Backend Developer", company_name="Acme",
        difficulty="hard", language="hinglish", **extra,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert created.json()["mode"] == mode
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    for seq, (text, source) in enumerate(zip(ANSWERS, sources), start=1):
        body = {"turn_seq": seq, "answer": text}
        if mode != "text":
            body["source"] = source
        r = client.post(f"{BASE}/sessions/{sid}/answer", json=body, headers=h)
        assert r.status_code == 200, r.text
    done = client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    assert done.json()["status"] == "completed"
    calls_before = len(reporter.calls)
    posted = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert posted.status_code == 200, posted.text
    assert len(reporter.calls) == calls_before + 1  # exactly one worker call per report
    fetched = client.get(f"{BASE}/sessions/{sid}/report", headers=h)
    assert fetched.status_code == 200
    return sid, reporter.calls[-1], posted.json(), fetched.json()


def _row(session, sid):
    session.expire_all()
    r = session.scalars(select(models.MockInterviewReport).where(models.MockInterviewReport.session_id == sid)).one()
    return {
        "overall_score": r.overall_score,
        "category_scores_json": r.category_scores_json,
        "strengths_json": r.strengths_json,
        "weaknesses_json": r.weaknesses_json,
        "recommendations_json": r.recommendations_json,
        "rubric_version": r.rubric_version,
        "provider": r.provider,
    }


def _without_session_fields(report):
    return {k: v for k, v in report.items() if k not in PER_SESSION}


@pytest.mark.parametrize("interview_type", ["hr", "technical"])
def test_text_voice_and_video_reports_are_identical(
    client, make_user, token_header, session, all_modes, fake, monkeypatch, interview_type
):
    reporter = _install_reporter(monkeypatch, interview_type)
    runs = {
        "text": _run(client, make_user, token_header, reporter, mode="text", interview_type=interview_type,
                     sources=["typed", "typed"]),
        "voice": _run(client, make_user, token_header, reporter, mode="voice", interview_type=interview_type,
                      sources=["stt", "stt"]),
        "video": _run(client, make_user, token_header, reporter, mode="video", interview_type=interview_type,
                      sources=["stt", "typed"]),
    }
    payloads = {m: r[1] for m, r in runs.items()}

    # 1. The worker payload is byte-for-byte the same JSON for all three modes.
    canonical = {m: json.dumps(p, sort_keys=True) for m, p in payloads.items()}
    assert canonical["voice"] == canonical["text"]
    assert canonical["video"] == canonical["text"]

    # 2. Its shape is exactly the documented contract; nothing about mode, source or camera.
    p = payloads["video"]
    assert set(p) == PAYLOAD_KEYS
    assert set(p["config"]) == CONFIG_KEYS
    assert p["config"]["interview_type"] == interview_type
    assert all(set(t) == TURN_KEYS for t in p["transcript"])
    assert [t["answer"] for t in p["transcript"][:2]] == ANSWERS
    keys = json.dumps(sorted(_all_keys(p)))
    for word in FORBIDDEN:
        assert word not in keys, word

    # 3. The stored report rows and the API responses are identical (same scoring, same rubric).
    rows = {m: _row(session, r[0]) for m, r in runs.items()}
    assert rows["voice"] == rows["text"]
    assert rows["video"] == rows["text"]
    expected_rubric = "hr-v1" if interview_type == "hr" else "technical-v1"
    assert rows["video"]["rubric_version"] == expected_rubric
    for m in ("voice", "video"):
        for idx in (2, 3):  # POST (generated) and GET (stored)
            assert _without_session_fields(runs[m][idx]) == _without_session_fields(runs["text"][idx])
    rubric = HR if interview_type == "hr" else TECH
    assert [c["key"] for c in runs["video"][3]["categories"]] == [k for k, _, _ in rubric]

    # 4. The report response carries no mode/camera field either (unchanged contract).
    assert set(runs["video"][3]) == set(runs["text"][3])
    assert "mode" not in runs["video"][3]


def _all_keys(obj):
    """Every dict key at any depth (values are the student's own text and are not checked)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_answer_source_never_changes_the_payload(client, make_user, token_header, all_modes, fake, monkeypatch):
    """A video interview answered by dictation vs typing yields the same worker payload."""
    reporter = _install_reporter(monkeypatch, "hr")
    dictated = _run(client, make_user, token_header, reporter, mode="video", interview_type="hr", sources=["stt", "stt"])
    typed = _run(client, make_user, token_header, reporter, mode="video", interview_type="hr", sources=["typed", "typed"])
    assert json.dumps(dictated[1], sort_keys=True) == json.dumps(typed[1], sort_keys=True)


def test_history_labels_and_report_flow_per_mode(client, make_user, token_header, all_modes, fake, monkeypatch):
    """History carries the mode (for the frontend label) and the report score for every mode."""
    reporter = _install_reporter(monkeypatch, "hr")
    _, h = _student(make_user, token_header, "history@x.edu")
    sids = {}
    for mode in ("text", "voice", "video"):
        extra = {} if mode == "text" else {"mode": mode}
        sid = _create(client, h, **extra).json()["id"]
        client.post(f"{BASE}/sessions/{sid}/start", headers=h)
        client.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": ANSWERS[0]}, headers=h)
        client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
        assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200
        sids[mode] = sid
    hist = {item["id"]: item for item in client.get(f"{BASE}/history", headers=h).json()}
    assert {hist[sids[m]]["mode"] for m in sids} == {"text", "voice", "video"}
    scores = {hist[sids[m]]["report"]["overall_score"] for m in sids}
    assert len(scores) == 1  # same answer, same score, whatever the mode
    assert len(reporter.calls) == 3
