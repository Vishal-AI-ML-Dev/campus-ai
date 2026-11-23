"""Phase 8.1: coding gate (SQLite, offline).

Coding needs ALL of: the master switch and master allow-list, a student, the
coding switch, the institute in a NON-EMPTY coding allow-list (fail closed,
like video), and at least one problem that may be served. Production only ever
serves ``published`` problems; ``draft`` problems need the development-only
setting and are refused in production (decision B1-b). Every new flag defaults
to off, and with the defaults nothing about existing interviews changes.
"""

# Fixtures are shared by importing them from the sessions module; ruff reads
# the fixture parameters as redefinitions of those imports.
# ruff: noqa: F811

import pytest
from sqlalchemy import select

import app.models as models
from app.core.config import Settings, settings
from app.models import AIFeatureControl, UserRole
from app.routers.mock_interview import (
    mock_interview_coding_available_for,
    mock_interview_coding_enabled_for,
)
from app.services import coding_bank
from app.services import mock_interview_coding as coding
from tests.test_mock_interview_coding_sessions import (  # noqa: F401 (fixtures)
    create_coding,
    flag,
    gate,
    no_engine,
    no_rate_limit,
    stu,
)
from tests.test_mock_interview_sessions import BASE

CODING_ROUTES = [
    ("get", "/coding/catalog"),
    ("get", "/sessions/1/coding"),
    ("put", "/sessions/1/coding/attempts/1/draft"),
    ("post", "/sessions/1/coding/attempts/1/submissions"),
    ("get", "/sessions/1/coding/attempts/1/submissions"),
    ("get", "/sessions/1/coding/submissions/1"),
    ("post", "/sessions/1/coding/attempts/1/hints"),
    ("post", "/sessions/1/coding/attempts/1/done"),
]


def _types(client, h):
    r = client.get(f"{BASE}/options", headers=h)
    assert r.status_code == 200, r.text
    return [t["value"] for t in r.json()["interview_types"]]


def _call(client, method, path, h):
    kwargs = {"headers": h}
    if method in ("put", "post"):
        kwargs["json"] = {}
    return getattr(client, method)(f"{BASE}{path}", **kwargs)


def test_new_settings_default_off():
    defaults = Settings.model_fields
    assert defaults["AI_MOCK_INTERVIEW_CODING_ENABLED"].default is False
    assert defaults["AI_MOCK_INTERVIEW_CODING_TENANT_IDS"].default == ""
    assert defaults["AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS"].default is False


def test_master_flag_off_hides_every_coding_route(client, stu):
    _, h = stu
    for method, path in CODING_ROUTES:
        assert _call(client, method, path, h).status_code == 404, path


def test_coding_off_by_default_even_with_master_on(client, flag, stu):
    user, h = stu
    assert not mock_interview_coding_enabled_for(user)
    assert _types(client, h) == ["hr", "technical"]
    r = create_coding(client, h)
    assert r.status_code == 422 and r.json()["detail"] == coding.MSG_UNAVAILABLE
    for method, path in CODING_ROUTES:
        assert _call(client, method, path, h).status_code == 404, path


@pytest.mark.parametrize("tenant_ids", ["", "   ", "abc", "²", "999999"])
def test_coding_allow_list_fails_closed(client, flag, stu, monkeypatch, tenant_ids):
    user, h = stu
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", tenant_ids)
    assert not mock_interview_coding_enabled_for(user)
    assert "coding" not in _types(client, h)
    assert create_coding(client, h).status_code == 422
    assert client.get(f"{BASE}/coding/catalog", headers=h).status_code == 404


def test_master_allow_list_still_applies(client, flag, stu, monkeypatch):
    user, h = stu
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", str(user.tenant_id))
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", str(user.tenant_id + 1))
    assert not mock_interview_coding_enabled_for(user)
    assert client.get(f"{BASE}/coding/catalog", headers=h).status_code == 404


def test_only_published_problems_by_default(client, flag, stu, monkeypatch):
    """Every Batch 1 problem is a draft (pending human review): nothing can be served."""
    user, h = stu
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", str(user.tenant_id))
    assert mock_interview_coding_enabled_for(user)
    assert {p.status for p in coding_bank.bank().problems.values()} == {"draft"}
    assert not mock_interview_coding_available_for(user)
    assert "coding" not in _types(client, h)
    assert create_coding(client, h).status_code == 422
    assert client.get(f"{BASE}/coding/catalog", headers=h).status_code == 404


def test_draft_problems_are_refused_in_production(client, flag, stu, monkeypatch):
    user, h = stu
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", str(user.tenant_id))
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS", True)
    assert coding_bank.drafts_allowed()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert not coding_bank.drafts_allowed()
    assert coding_bank.selectable_problems() == []
    assert not mock_interview_coding_available_for(user)


def test_gate_open_offers_coding_and_catalog(client, gate):
    _, h = gate
    assert _types(client, h) == ["hr", "technical", "coding"]
    r = client.get(f"{BASE}/coding/catalog", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["value"] for x in body["languages"]] == ["python313", "cpp17", "java21", "c11"]
    assert body["durations_minutes"] == [30, 45, 60]
    assert body["graded_submissions_per_problem"] == 3 and body["review_only"] is True
    assert len(body["problems"]) == 24
    assert all(set(p) == {"key", "title", "difficulty", "topics"} for p in body["problems"])


def test_staff_are_forbidden(client, gate, make_user, token_header):
    make_user("t@x.edu", role=UserRole.teacher)
    h = token_header("t@x.edu")
    assert client.get(f"{BASE}/coding/catalog", headers=h).status_code == 403
    assert create_coding(client, h).status_code == 403


def test_invalid_bank_disables_coding(client, gate, monkeypatch):
    user, h = gate
    monkeypatch.setattr(coding_bank, "bank", lambda: coding_bank.Bank(problems={}, error="broken"))
    assert not mock_interview_coding_available_for(user)
    assert "coding" not in _types(client, h)
    assert create_coding(client, h).status_code == 422


def test_institute_pause_blocks_new_coding_rounds(client, gate, session, monkeypatch):
    user, h = gate
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_OPS_ENABLED", True)
    session.add(AIFeatureControl(tenant_id=user.tenant_id, feature="mock_interview", paused=True))
    session.commit()
    r = create_coding(client, h)
    assert r.status_code == 403
    assert session.scalar(select(models.MockInterviewSession.id)) is None


def test_existing_round_can_finish_after_coding_is_switched_off(client, gate, monkeypatch):
    _, h = gate
    sid = create_coding(client, h).json()["id"]
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_CODING_ENABLED", False)
    assert client.get(f"{BASE}/sessions/{sid}/coding", headers=h).status_code == 404
    # The ordinary session routes still let the student end it (Phase 6 D12 principle).
    assert client.post(f"{BASE}/sessions/{sid}/abandon", headers=h).json()["status"] == "abandoned"
