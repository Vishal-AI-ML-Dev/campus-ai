"""Phase 1: AI Mock Interview feature flag + isolation.

The feature must be OFF by default, invisible (404) while off, reversible by
flipping the flag, and restrictable to a pilot list of institutes. Recruiters
never get it. Existing routes are covered by the rest of the suite.
"""

import pytest

import app.models as models
from app.core.config import Settings, settings

PREFIXES = ("/v1", "")  # canonical + deprecated unversioned mount


@pytest.fixture
def flag(monkeypatch):
    """Set the mock-interview flags for one test (restored automatically)."""

    def _set(enabled: bool, tenant_ids: str = ""):
        monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", enabled)
        monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", tenant_ids)

    return _set


def test_flags_default_off():
    fields = Settings.model_fields
    assert fields["AI_MOCK_INTERVIEW_ENABLED"].default is False
    assert fields["AI_MOCK_INTERVIEW_TENANT_IDS"].default == ""


@pytest.mark.parametrize("prefix", PREFIXES)
def test_disabled_status_false_and_routes_404(client, make_user, token_header, prefix):
    make_user("stu@test.dev")
    h = token_header("stu@test.dev")

    status_resp = client.get(f"{prefix}/mock-interview/status", headers=h)
    assert status_resp.status_code == 200
    assert status_resp.json() == {"enabled": False}

    catalog = client.get(f"{prefix}/mock-interview/catalog", headers=h)
    assert catalog.status_code == 404


def test_requires_authentication(client, flag):
    flag(True)
    assert client.get("/v1/mock-interview/status").status_code == 401
    assert client.get("/v1/mock-interview/catalog").status_code == 401


@pytest.mark.parametrize("role", ["student", "teacher", "tpo", "admin"])
def test_enabled_for_campus_roles(client, make_user, token_header, flag, role):
    flag(True)
    email = f"{role}@test.dev"
    make_user(email, role=models.UserRole(role))
    h = token_header(email)

    assert client.get("/v1/mock-interview/status", headers=h).json() == {"enabled": True}
    catalog = client.get("/v1/mock-interview/catalog", headers=h)
    assert catalog.status_code == 200
    body = catalog.json()
    assert "hr" in body["interview_types"]
    assert set(body["languages"]) == {"en", "hi", "hinglish"}


def test_recruiters_never_get_the_feature(client, make_user, token_header, flag):
    flag(True)
    make_user("rec@test.dev", role=models.UserRole.recruiter)
    h = token_header("rec@test.dev")

    assert client.get("/v1/mock-interview/status", headers=h).json() == {"enabled": False}
    assert client.get("/v1/mock-interview/catalog", headers=h).status_code == 404


def test_tenant_allow_list(client, make_tenant, make_user, token_header, flag):
    pilot = make_tenant(slug="pilot", name="Pilot Institute")
    other = make_tenant(slug="other", name="Other Institute")
    make_user("in@test.dev", tenant=pilot)
    make_user("out@test.dev", tenant=other)
    flag(True, tenant_ids=f" {pilot.id} ,")

    h_in = token_header("in@test.dev")
    h_out = token_header("out@test.dev")
    assert client.get("/v1/mock-interview/status", headers=h_in).json() == {"enabled": True}
    assert client.get("/v1/mock-interview/catalog", headers=h_in).status_code == 200
    assert client.get("/v1/mock-interview/status", headers=h_out).json() == {"enabled": False}
    assert client.get("/v1/mock-interview/catalog", headers=h_out).status_code == 404


def test_invalid_allow_list_fails_closed(client, make_user, token_header, flag):
    make_user("stu@test.dev")
    flag(True, tenant_ids="pilot, abc")
    h = token_header("stu@test.dev")
    assert client.get("/v1/mock-interview/status", headers=h).json() == {"enabled": False}
    assert client.get("/v1/mock-interview/catalog", headers=h).status_code == 404


@pytest.mark.parametrize("raw", ["²", "-1", "1.0", " "])
def test_odd_allow_list_entries_never_crash(client, make_user, token_header, flag, raw):
    """Non-ASCII digits, signs and decimals are ignored (fail closed), not a 500."""
    make_user("stu@test.dev")
    flag(True, tenant_ids=f"{raw},")
    h = token_header("stu@test.dev")
    resp = client.get("/v1/mock-interview/status", headers=h)
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}
    assert client.get("/v1/mock-interview/catalog", headers=h).status_code == 404


def test_turning_flag_off_again_disables_immediately(client, make_user, token_header, flag):
    make_user("stu@test.dev")
    h = token_header("stu@test.dev")
    flag(True)
    assert client.get("/v1/mock-interview/catalog", headers=h).status_code == 200
    flag(False)
    assert client.get("/v1/mock-interview/catalog", headers=h).status_code == 404


def test_only_planned_interview_tables_exist():
    """Phase 1 added no tables; Phase 2 adds exactly sessions, turns, reports;
    Phase 8.1 (migration e7a1c4d9b2f6) adds exactly code attempts and code submissions."""
    assert sorted(t for t in models.Base.metadata.tables if "interview" in t) == [
        "mock_interview_code_attempts",
        "mock_interview_code_submissions",
        "mock_interview_reports",
        "mock_interview_sessions",
        "mock_interview_turns",
    ]
