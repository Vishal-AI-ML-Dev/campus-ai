"""Phase 5: voice interviews (SQLite, offline; worker stubbed).

Voice is browser speech-to-text: the server only receives text, labelled
``source="stt"``. Proves the sub-flag, the additive API fields, that a spoken
answer is stored like a typed one, ownership 404s and that a voice session
goes through the same report path. No audio route or field exists.
"""

import pytest
from sqlalchemy import select

import app.models as models
import app.services.mock_interview_ai as engine
from app.core.config import settings
from tests.test_mock_interview_reports import FakeReporter
from tests.test_mock_interview_sessions import (
    BASE,
    FakeEngine,
    _create,
    _started,
    _student,
)


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


@pytest.fixture
def voice(monkeypatch, flag):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)


@pytest.fixture
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f


@pytest.fixture
def reporter(monkeypatch):
    r = FakeReporter()
    monkeypatch.setattr(engine, "generate_report", r)
    return r


def _say(client, h, sid, seq, text, source="stt"):
    return client.post(
        f"{BASE}/sessions/{sid}/answer",
        json={"turn_seq": seq, "answer": text, "source": source},
        headers=h,
    )


def _rows(session, sid):
    session.expire_all()
    return session.scalars(
        select(models.MockInterviewTurn)
        .where(models.MockInterviewTurn.session_id == sid)
        .order_by(models.MockInterviewTurn.seq)
    ).all()


# --- Flag -----------------------------------------------------------------------
def test_voice_flag_defaults_off():
    assert settings.AI_MOCK_INTERVIEW_VOICE_ENABLED is False


def test_flag_off_offers_text_only_and_rejects_voice(client, make_user, token_header, flag, fake, session):
    _, h = _student(make_user, token_header)
    body = client.get(f"{BASE}/options", headers=h).json()
    assert body["modes"] == [{"value": "text", "label": "Text"}]
    r = _create(client, h, mode="voice")
    assert r.status_code == 422
    assert r.json()["detail"] == "Voice interviews are not available"
    assert session.scalars(select(models.MockInterviewSession)).all() == []


def test_voice_flag_alone_does_not_enable_the_feature(client, make_user, token_header, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)
    _, h = _student(make_user, token_header)
    assert settings.AI_MOCK_INTERVIEW_ENABLED is False
    assert client.get(f"{BASE}/options", headers=h).status_code == 404
    assert _create(client, h, mode="voice").status_code == 404


def test_voice_flag_respects_the_tenant_allow_list(client, make_user, token_header, voice, monkeypatch):
    _, h = _student(make_user, token_header)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "999999")
    assert client.get(f"{BASE}/options", headers=h).status_code == 404
    assert _create(client, h, mode="voice").status_code == 404


def test_flag_on_offers_voice(client, make_user, token_header, voice):
    _, h = _student(make_user, token_header)
    body = client.get(f"{BASE}/options", headers=h).json()
    assert [m["value"] for m in body["modes"]] == ["text", "voice"]


@pytest.mark.parametrize("bad", [{"mode": "video"}, {"mode": "VOICE"}, {"mode": None}])
def test_create_rejects_unknown_modes(client, make_user, token_header, voice, bad):
    _, h = _student(make_user, token_header)
    assert _create(client, h, **bad).status_code == 422


# --- Sessions and answers ---------------------------------------------------------
def test_default_mode_is_text_and_default_source_is_typed(client, make_user, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, s = _started(client, h)
    assert s["mode"] == "text"
    r = client.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": 1, "answer": "I build web apps."},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["turns"][0]["answer_source"] == "typed"
    assert r.json()["turns"][1]["answer_source"] is None  # not answered yet
    assert _rows(session, sid)[0].answer_source == "typed"


def test_spoken_answer_is_stored_as_stt(client, make_user, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, s = _started(client, h, mode="voice", language="hinglish")
    assert (s["mode"], s["language"]) == ("voice", "hinglish")
    r = _say(client, h, sid, 1, "main ek backend developer hoon aur maine Flask API banaya")
    assert r.status_code == 200, r.text
    assert r.json()["turns"][0]["answer_source"] == "stt"
    # A voice session also accepts typed answers (typing fallback).
    r2 = _say(client, h, sid, 2, "I prefer typing this one.", source="typed")
    assert r2.status_code == 200, r2.text
    assert [t.answer_source for t in _rows(session, sid)][:2] == ["stt", "typed"]
    # The engine receives plain text, exactly as for a typed answer.
    assert fake.turn_calls[1]["transcript"][0]["answer"].startswith("main ek backend")


def test_stt_on_a_text_session_is_rejected(client, make_user, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h)
    calls = len(fake.turn_calls)
    r = _say(client, h, sid, 1, "Spoken answer")
    assert r.status_code == 422
    assert _rows(session, sid)[0].answer_text is None
    assert len(fake.turn_calls) == calls


def test_finished_session_answers_409_before_source_check(client, make_user, token_header, voice, fake):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h)  # text session
    client.post(f"{BASE}/sessions/{sid}/abandon", headers=h)
    # Not in progress wins over "stt on a text session": same 409 as Phase 3.
    assert _say(client, h, sid, 1, "Late spoken answer").status_code == 409
    assert _say(client, h, sid, 1, "Late typed answer", source="typed").status_code == 409


@pytest.mark.parametrize("bad", ["voice", "audio", "", None])
def test_unknown_source_is_rejected(client, make_user, token_header, voice, fake, bad):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    assert _say(client, h, sid, 1, "Hello there", source=bad).status_code == 422


def test_no_audio_field_is_accepted(client, make_user, token_header, voice, fake):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    r = client.post(f"{BASE}/sessions/{sid}/answer",
                    json={"turn_seq": 1, "answer": "Hi", "source": "stt", "audio": "UklGRg=="}, headers=h)
    assert r.status_code == 422  # extra="forbid"


def test_spoken_answer_gets_the_same_validation(client, make_user, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    assert _say(client, h, sid, 1, "   ").status_code == 422
    assert _say(client, h, sid, 1, "x" * 5001).status_code == 422
    r = _say(client, h, sid, 1, "  hello\x00 world\x07  ")
    assert r.status_code == 200
    assert _rows(session, sid)[0].answer_text == "hello world"


def test_retry_with_other_source_is_idempotent(client, make_user, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    assert _say(client, h, sid, 1, "I like distributed systems.").status_code == 200
    calls = len(fake.turn_calls)
    again = _say(client, h, sid, 1, "I like distributed systems.", source="typed")
    assert again.status_code == 200
    assert len(fake.turn_calls) == calls
    assert _rows(session, sid)[0].answer_source == "stt"


def test_other_students_and_institutes_get_404(client, make_user, make_tenant, token_header, voice, fake, session):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    _, mate = _student(make_user, token_header, "mate@x.edu")
    _, outsider = _student(make_user, token_header, "out@y.edu", tenant=make_tenant(slug="o", name="O"))
    for intruder in (mate, outsider):
        assert _say(client, intruder, sid, 1, "Not my interview").status_code == 404
        assert client.get(f"{BASE}/sessions/{sid}", headers=intruder).status_code == 404
    assert _rows(session, sid)[0].answer_text is None


def test_voice_session_finishes_after_the_flag_is_turned_off(
    client, make_user, token_header, voice, fake, reporter, monkeypatch
):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    assert _say(client, h, sid, 1, "I led a team of four in my final year project.").status_code == 200
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", False)
    # In-flight voice sessions keep working; only new ones are refused.
    assert _say(client, h, sid, 2, "I want to grow into a backend engineer.").status_code == 200
    assert _create(client, h, mode="voice").status_code == 422
    done = client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()
    assert done["status"] == "completed" and done["mode"] == "voice"
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert rep.status_code == 200, rep.text
    assert rep.json()["questions"][0]["answer_text"].startswith("I led a team")
    assert len(reporter.calls) == 1
    hist = client.get(f"{BASE}/history", headers=h).json()
    assert hist[0]["id"] == sid and hist[0]["mode"] == "voice"


def test_voice_report_uses_the_same_worker_payload(client, make_user, token_header, voice, fake, reporter):
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    assert _say(client, h, sid, 1, "I led a team of four in my final year project.").status_code == 200
    client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200
    sent = reporter.calls[0]
    # The transcript keys are unchanged: no source/audio is sent to the worker.
    assert set(sent["transcript"][0]) == {"seq", "topic", "question", "is_follow_up", "answer"}


def test_openapi_has_no_audio_route(client):
    paths = client.app.openapi()["paths"]
    mi = [p for p in paths if "mock-interview" in p]
    assert mi and not any("audio" in p or "voice" in p or "speech" in p for p in mi)


# --- Quality pass: transcript persistence + modality-independent disclaimer ------
EXPECTED_DISCLAIMER = (
    "This is AI-generated practice feedback based on the answers you submitted during this "
    "practice session, including typed or speech-to-text answers. It can make mistakes, and "
    "practice scores do not predict selection in a real interview."
)


def test_edited_spoken_transcript_is_stored_verbatim(client, make_user, token_header, voice, fake, session):
    """The student edits the recognised text before submitting; exactly that text is kept."""
    _, h = _student(make_user, token_header)
    sid, _ = _started(client, h, mode="voice")
    edited = "I built a Flask REST API (recognised as 'flask rest a p i', corrected by me)."
    assert _say(client, h, sid, 1, edited).status_code == 200
    row = _rows(session, sid)[0]
    assert (row.answer_text, row.answer_source) == (edited, "stt")
    # Reading the session back (e.g. after a refresh) returns the same text and label.
    turn = client.get(f"{BASE}/sessions/{sid}", headers=h).json()["turns"][0]
    assert (turn["answer_text"], turn["answer_source"]) == (edited, "stt")
    # The AI follow-up still runs on that text.
    assert fake.turn_calls[-1]["transcript"][0]["answer"] == edited


@pytest.mark.parametrize(
    "mode,sources",
    [
        ("text", ["typed", "typed"]),  # text-only session
        ("voice", ["stt", "stt"]),  # all speech-to-text
        ("voice", ["stt", "typed"]),  # mixture
    ],
)
def test_report_disclaimer_is_modality_independent(
    client, make_user, token_header, voice, fake, reporter, mode, sources
):
    _, h = _student(make_user, token_header)
    extra = {"mode": "voice"} if mode == "voice" else {}
    sid, _ = _started(client, h, **extra)
    answers = ["I led a team of four in my final year project.", "I want to grow into a backend engineer."]
    for seq, (text, source) in enumerate(zip(answers, sources), start=1):
        assert _say(client, h, sid, seq, text, source=source).status_code == 200
    assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()["status"] == "completed"

    created = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert created.status_code == 200, created.text
    stored = client.get(f"{BASE}/sessions/{sid}/report", headers=h).json()
    for rep in (created.json(), stored):
        assert rep["disclaimer"] == EXPECTED_DISCLAIMER
        assert "typed answers" not in rep["disclaimer"]
        assert [q["answer_text"] for q in rep["questions"][:2]] == answers
    turns = client.get(f"{BASE}/sessions/{sid}", headers=h).json()["turns"]
    assert [t["answer_source"] for t in turns[:2]] == sources
