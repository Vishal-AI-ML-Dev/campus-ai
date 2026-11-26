"""Phase 6.1: video interview foundations (SQLite, offline; worker stubbed).

Video = a camera self-view that never leaves the browser, plus the Phase 5
browser speech path. The server only ever receives text. These tests prove the
access gate (master switch + master allow-list + video switch + a NON-EMPTY
video allow-list + student role), that ``mode="video"`` is otherwise rejected
with 422, that video sessions accept ``source="stt"``, that they reuse the
unchanged engine/report path, and that no media route or field exists.
"""

import pytest
from sqlalchemy import select

import app.models as models
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.routers.mock_interview import mock_interview_video_enabled_for
from tests.test_mock_interview_reports import FakeReporter
from tests.test_mock_interview_sessions import BASE, FakeEngine, _create, _started, _student
from tests.test_mock_interview_voice import EXPECTED_DISCLAIMER


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


@pytest.fixture
def reporter(monkeypatch):
    r = FakeReporter()
    monkeypatch.setattr(engine, "generate_report", r)
    return r


@pytest.fixture
def stu(make_user, token_header):
    """(user, header) for a student of the default institute."""
    return _student(make_user, token_header)


@pytest.fixture
def video(monkeypatch, flag, stu):
    """Every gate open for ``stu``'s institute."""
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(stu[0].tenant_id))
    return stu


def _modes(client, h):
    r = client.get(f"{BASE}/options", headers=h)
    assert r.status_code == 200, r.text
    return [m["value"] for m in r.json()["modes"]]


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


def _no_sessions(session):
    session.expire_all()
    return session.scalars(select(models.MockInterviewSession)).all() == []


# --- Flag defaults ---------------------------------------------------------------
def test_video_flags_default_off():
    assert settings.AI_MOCK_INTERVIEW_VIDEO_ENABLED is False
    assert settings.AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS == ""


def test_defaults_offer_no_video_and_reject_it(client, stu, flag, session):
    _, h = stu
    assert _modes(client, h) == ["text"]
    r = _create(client, h, mode="video")
    assert r.status_code == 422
    assert r.json()["detail"] == "Video interviews are not available"
    assert _no_sessions(session)


# --- Access matrix ---------------------------------------------------------------
def test_master_switch_off_hides_everything(client, stu, monkeypatch, session):
    user, h = stu
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(user.tenant_id))
    assert settings.AI_MOCK_INTERVIEW_ENABLED is False
    assert mock_interview_video_enabled_for(user) is False
    assert client.get(f"{BASE}/options", headers=h).status_code == 404
    assert _create(client, h, mode="video").status_code == 404
    assert _no_sessions(session)


def test_tenant_missing_from_master_list_is_denied(client, video, monkeypatch, session):
    user, h = video
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", str(user.tenant_id + 1000))
    assert mock_interview_video_enabled_for(user) is False
    assert client.get(f"{BASE}/options", headers=h).status_code == 404
    assert _create(client, h, mode="video").status_code == 404
    assert _no_sessions(session)


def test_video_switch_off_with_tenant_listed_is_denied(client, video, monkeypatch, session):
    user, h = video
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", False)
    assert mock_interview_video_enabled_for(user) is False
    assert _modes(client, h) == ["text"]
    assert _create(client, h, mode="video").status_code == 422
    assert _no_sessions(session)


@pytest.mark.parametrize("raw", ["", "   ", ",", "abc", "1x", "²"])
def test_empty_or_invalid_video_list_fails_closed(client, video, monkeypatch, session, raw):
    user, h = video
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", raw)
    assert mock_interview_video_enabled_for(user) is False
    assert _modes(client, h) == ["text"]
    assert _create(client, h, mode="video").status_code == 422
    assert _no_sessions(session)


def test_other_tenant_in_video_list_is_denied(client, video, monkeypatch, session):
    user, h = video
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", str(user.tenant_id + 1000))
    assert mock_interview_video_enabled_for(user) is False
    assert _create(client, h, mode="video").status_code == 422
    assert _no_sessions(session)


def test_listed_tenant_is_allowed(client, video, session):
    user, h = video
    assert mock_interview_video_enabled_for(user) is True
    assert _modes(client, h) == ["text", "video"]
    r = _create(client, h, mode="video")
    assert r.status_code == 201, r.text
    assert r.json()["mode"] == "video"
    session.expire_all()
    assert session.scalars(select(models.MockInterviewSession.mode)).all() == ["video"]


def test_allowed_with_master_list_and_spaces(client, video, monkeypatch):
    user, h = video
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", f" {user.tenant_id} , 999999 ")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", f" 999998 ,{user.tenant_id} ")
    assert _modes(client, h) == ["text", "video"]
    assert _create(client, h, mode="video").status_code == 201


def test_video_and_voice_are_independent(client, video, monkeypatch):
    _, h = video
    assert _modes(client, h) == ["text", "video"]  # video does not need the voice flag
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VOICE_ENABLED", True)
    assert _modes(client, h) == ["text", "voice", "video"]
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", False)
    assert _modes(client, h) == ["text", "voice"]  # Phase 5 behaviour unchanged


def test_only_the_listed_institute_gets_video(client, video, make_user, make_tenant, token_header, session):
    other = make_tenant(slug="other", name="Other Institute")
    _, h_other = _student(make_user, token_header, "o@other.edu", tenant=other)
    assert _modes(client, h_other) == ["text"]  # master list is empty = all institutes
    assert _create(client, h_other, mode="video").status_code == 422
    assert _no_sessions(session)


@pytest.mark.parametrize("role", [models.UserRole.admin, models.UserRole.teacher, models.UserRole.tpo])
def test_staff_are_denied_even_when_their_institute_is_listed(
    client, video, make_user, token_header, session, role
):
    staff = make_user(f"{role.value}@x.edu", role=role)
    h = token_header(f"{role.value}@x.edu")
    assert mock_interview_video_enabled_for(staff) is False
    assert client.get(f"{BASE}/options", headers=h).status_code == 403
    assert _create(client, h, mode="video").status_code == 403
    assert _no_sessions(session)


def test_recruiter_is_denied(client, video, make_user, token_header):
    rec = make_user("r@x.edu", role=models.UserRole.recruiter)
    assert mock_interview_video_enabled_for(rec) is False
    assert client.get(f"{BASE}/options", headers=token_header("r@x.edu")).status_code == 404


def test_status_response_is_unchanged(client, video):
    _, h = video
    assert client.get(f"{BASE}/status", headers=h).json() == {"enabled": True}


# --- Sessions and answers --------------------------------------------------------
def test_video_session_accepts_stt_and_typed_answers(client, video, fake, session):
    _, h = video
    sid, s = _started(client, h, mode="video", language="hinglish")
    assert (s["mode"], s["language"], s["status"]) == ("video", "hinglish", "in_progress")
    r = _say(client, h, sid, 1, "main ek backend developer hoon aur maine Flask API banaya")
    assert r.status_code == 200, r.text
    assert r.json()["turns"][0]["answer_source"] == "stt"
    assert _say(client, h, sid, 2, "Typing this one instead.", source="typed").status_code == 200
    assert [t.answer_source for t in _rows(session, sid)][:2] == ["stt", "typed"]
    # The engine gets plain text and the same config keys as text/voice (no mode).
    assert fake.turn_calls[1]["transcript"][0]["answer"].startswith("main ek backend")
    assert "mode" not in fake.turn_calls[0]["config"]
    assert "mode" not in fake.plan_calls[0]["config"]


def test_stt_on_a_text_session_is_still_rejected(client, video, fake, session):
    _, h = video
    sid, _ = _started(client, h)
    r = _say(client, h, sid, 1, "Spoken answer")
    assert r.status_code == 422
    assert r.json()["detail"] == "Spoken answers are only accepted in a voice interview"
    assert _rows(session, sid)[0].answer_text is None


def test_no_media_field_is_accepted(client, video, fake):
    _, h = video
    assert _create(client, h, mode="video", camera=True).status_code == 422
    sid, _ = _started(client, h, mode="video")
    for extra in ({"video": "AAAA"}, {"frame": "data:image/png;base64,AAAA"}, {"audio": "UklGRg=="}):
        body = {"turn_seq": 1, "answer": "Hi", "source": "stt", **extra}
        assert client.post(f"{BASE}/sessions/{sid}/answer", json=body, headers=h).status_code == 422


def test_duplicate_answer_is_idempotent(client, video, fake, session):
    _, h = video
    sid, _ = _started(client, h, mode="video")
    assert _say(client, h, sid, 1, "I like distributed systems.").status_code == 200
    calls = len(fake.turn_calls)
    assert _say(client, h, sid, 1, "I like distributed systems.").status_code == 200
    assert len(fake.turn_calls) == calls
    assert _say(client, h, sid, 1, "Something different").status_code == 409


def test_other_students_and_institutes_get_404(client, video, make_user, make_tenant, token_header, fake, session):
    _, h = video
    sid, _ = _started(client, h, mode="video")
    _, mate = _student(make_user, token_header, "mate@x.edu")
    _, outsider = _student(make_user, token_header, "out@y.edu", tenant=make_tenant(slug="o", name="O"))
    for intruder in (mate, outsider):
        assert _say(client, intruder, sid, 1, "Not my interview").status_code == 404
        assert client.get(f"{BASE}/sessions/{sid}", headers=intruder).status_code == 404
        assert client.post(f"{BASE}/sessions/{sid}/finish", headers=intruder).status_code == 404
    assert _rows(session, sid)[0].answer_text is None


@pytest.mark.parametrize("turn_off", ["switch", "tenant_list"])
def test_video_session_finishes_after_access_is_withdrawn(
    client, video, fake, reporter, monkeypatch, turn_off
):
    _, h = video
    sid, _ = _started(client, h, mode="video")
    assert _say(client, h, sid, 1, "I led a team of four in my final year project.").status_code == 200
    if turn_off == "switch":
        monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_ENABLED", False)
    else:
        monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS", "")
    # D12: the in-flight session keeps working; only new ones are refused.
    assert _say(client, h, sid, 2, "I want to grow into a backend engineer.").status_code == 200
    assert _modes(client, h) == ["text"]
    done = client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()
    assert done["status"] == "completed" and done["mode"] == "video"
    assert _create(client, h, mode="video").status_code == 422
    rep = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert rep.status_code == 200, rep.text
    assert rep.json()["disclaimer"] == EXPECTED_DISCLAIMER  # Phase 5 wording kept
    hist = client.get(f"{BASE}/history", headers=h).json()
    assert hist[0]["id"] == sid and hist[0]["mode"] == "video"


def test_video_report_uses_the_same_worker_payload(client, video, fake, reporter):
    _, h = video
    sid, _ = _started(client, h, mode="video")
    assert _say(client, h, sid, 1, "I led a team of four in my final year project.").status_code == 200
    client.post(f"{BASE}/sessions/{sid}/finish", headers=h)
    assert client.post(f"{BASE}/sessions/{sid}/report", headers=h).status_code == 200
    sent = reporter.calls[0]
    assert set(sent["transcript"][0]) == {"seq", "topic", "question", "is_follow_up", "answer"}
    assert "mode" not in sent.get("config", {})


def test_openapi_has_no_media_route(client):
    paths = client.app.openapi()["paths"]
    mi = [p for p in paths if "mock-interview" in p]
    banned = ("video", "camera", "media", "frame", "upload", "record", "stream", "audio")
    assert mi and not any(word in p for p in mi for word in banned)
