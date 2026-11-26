"""Phase 8.4: company profiles and company-context questions (SQLite, offline).

Decisions P8-D9..D11 (approved 2026-11-23). Proves:

* flag off -> every company route 404, create with a profile 422;
* RBAC: admin and TPO write; teacher and recruiter 403; students read
  PUBLISHED profiles of their own institute only; another institute -> 404;
* one CONTENT-FREE audit row per write; archive instead of delete;
* publishing needs >= 1 source record; drive sources / "create from drive"
  accept only OPEN drives of the same institute;
* public URLs are never fetched and reach students as label + domain only;
* sessions keep a frozen copy in plan_json; generation payloads carry the
  company context, the EVALUATION payload and stored report are identical with
  and without a profile, and no company-fit / competency score exists;
* JD skill map: every skill quotes the JD (others dropped), matched against
  verified skills; ledger rows carry no JD text.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

import ai_client
import app.models as models
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.core.db import SessionLocal
from app.services import mock_interview_company as company_svc
from tests.test_mock_interview_report_parity import _install_reporter, _row, _without_session_fields
from tests.test_mock_interview_sessions import BASE, FakeEngine, _create, _student

C = f"{BASE}/company"
NAME_SENTINEL = "NAME-SENTINEL-Zyxcorp"
JD_SENTINEL = "JD-SENTINEL-4b2c"
NOTE_SENTINEL = "NOTE-SENTINEL-77f1"


def profile_body(**overrides):
    body = {
        "name": NAME_SENTINEL,
        "role_title": "Backend Developer",
        "role_template_key": "backend_developer",
        "summary": f"Our alumni say the team values clean APIs. {NOTE_SENTINEL}",
        "job_description": f"We need Python, PostgreSQL and REST APIs. {JD_SENTINEL}",
        "focus_skills": [{"name": "REST APIs", "weight": 3}, {"name": "SQL", "weight": 2}],
        "competencies": ["Ownership", "Communication"],
        "sources": [
            {"kind": "jd", "label": "JD shared with the TPO office"},
            {"kind": "public_url", "label": "Careers page", "url": "https://www.example.com/careers?x=1"},
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", True)
    from app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
def people(make_user, token_header):
    def mk(email, role):
        make_user(email, role=role)
        return token_header(email)

    stu_user, h_stu = _student(make_user, token_header, "cstu@x.edu")
    return {
        "student": h_stu,
        "student_user": stu_user,
        "admin": mk("cadmin@x.edu", models.UserRole.admin),
        "tpo": mk("ctpo@x.edu", models.UserRole.tpo),
        "teacher": mk("cteacher@x.edu", models.UserRole.teacher),
        "recruiter": mk("crec@x.edu", models.UserRole.recruiter),
    }


@pytest.fixture
def no_network(monkeypatch):
    """Any outbound HTTP call (e.g. fetching a source URL) fails the test."""
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("no outbound HTTP call is allowed here")

    import urllib.request

    # Module-level HTTP entry points (the TestClient's own transport is untouched).
    for name in ("request", "get", "post", "head", "stream"):
        monkeypatch.setattr(httpx, name, refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    return calls


def drive(session, tenant_id, *, is_open=True, description="Python, SQL and REST APIs for our platform."):
    d = models.Drive(tenant_id=tenant_id, company_name="Acme", role_title="Backend Intern",
                     description=description, required_skills="Python, SQL, REST APIs", is_open=is_open)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def create_profile(client, h, **overrides):
    r = client.post(f"{C}/profiles/admin", json=profile_body(**overrides), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def audit_rows(session, target_id=None):
    session.expire_all()
    q = select(models.AuditLog).where(models.AuditLog.target_type == "mock_interview_company_profile")
    if target_id is not None:
        q = q.where(models.AuditLog.target_id == str(target_id))
    return list(session.scalars(q.order_by(models.AuditLog.id)))


# --- Flags ------------------------------------------------------------------------------------


def test_flag_off_hides_everything(client, people, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    assert settings.AI_MOCK_INTERVIEW_COMPANY_ENABLED is False
    for h in (people["admin"], people["student"]):
        for method, url in (("get", f"{C}/role-templates"), ("get", f"{C}/profiles"),
                            ("get", f"{C}/profiles/admin"), ("post", f"{C}/profiles/admin")):
            r = getattr(client, method)(url, headers=h, **({"json": profile_body()} if method == "post" else {}))
            assert r.status_code == 404, (url, r.status_code)
    r = _create(client, people["student"], role_template_key="sde_fresher")
    assert r.status_code == 422 and r.json()["detail"] == "Company profiles are not available"
    # Master switch off too: still 404.
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", False)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", True)
    assert client.get(f"{C}/profiles/admin", headers=people["admin"]).status_code == 404


# --- RBAC, audit, lifecycle -----------------------------------------------------------------


def test_rbac_matrix(client, on, people):
    p = create_profile(client, people["admin"])
    for role in ("teacher", "recruiter"):
        h = people[role]
        assert client.get(f"{C}/profiles/admin", headers=h).status_code == 403
        assert client.post(f"{C}/profiles/admin", json=profile_body(), headers=h).status_code == 403
        assert client.put(f"{C}/profiles/{p['id']}", json=profile_body(), headers=h).status_code == 403
        assert client.post(f"{C}/profiles/{p['id']}/publish", headers=h).status_code == 403
        assert client.get(f"{C}/profiles", headers=h).status_code == 403
        assert client.get(f"{C}/role-templates", headers=h).status_code == 403
    # Students cannot write; staff use the staff routes, not the student ones.
    assert client.post(f"{C}/profiles/admin", json=profile_body(), headers=people["student"]).status_code == 403
    assert client.post(f"{C}/profiles/{p['id']}/publish", headers=people["student"]).status_code == 403
    assert client.get(f"{C}/profiles", headers=people["admin"]).status_code == 403
    # TPO may edit and publish what the admin wrote (either may publish).
    r = client.put(f"{C}/profiles/{p['id']}", json=profile_body(role_title="SDE"), headers=people["tpo"])
    assert r.status_code == 200 and r.json()["role_title"] == "SDE"
    assert client.post(f"{C}/profiles/{p['id']}/publish", headers=people["tpo"]).json()["status"] == "published"
    assert client.get(f"{C}/role-templates", headers=people["tpo"]).status_code == 200


def test_audit_one_content_free_row_per_write_and_never_deleted(client, on, people, session):
    p = create_profile(client, people["admin"])
    pid = p["id"]
    client.put(f"{C}/profiles/{pid}", json=profile_body(), headers=people["admin"])
    client.post(f"{C}/profiles/{pid}/publish", headers=people["admin"])
    client.post(f"{C}/profiles/{pid}/publish", headers=people["admin"])  # idempotent: no row
    client.post(f"{C}/profiles/{pid}/archive", headers=people["tpo"])
    client.post(f"{C}/profiles/{pid}/archive", headers=people["tpo"])  # idempotent: no row
    rows = audit_rows(session, pid)
    assert [r.action for r in rows] == [
        "mock_interview.company_profile_create",
        "mock_interview.company_profile_update",
        "mock_interview.company_profile_publish",
        "mock_interview.company_profile_archive",
    ]
    dumped = json.dumps([[r.summary, r.action, r.target_id] for r in rows])
    for sentinel in (NAME_SENTINEL, JD_SENTINEL, NOTE_SENTINEL, "example.com", "Careers page"):
        assert sentinel not in dumped
    assert all(r.tenant_id == people["student_user"].tenant_id for r in rows)
    # Archived: read-only, not publishable, still there (no delete route at all).
    assert client.put(f"{C}/profiles/{pid}", json=profile_body(), headers=people["admin"]).status_code == 409
    assert client.post(f"{C}/profiles/{pid}/publish", headers=people["admin"]).status_code == 409
    assert client.delete(f"{C}/profiles/{pid}", headers=people["admin"]).status_code == 405
    assert session.get(models.MockInterviewCompanyProfile, pid) is not None


def test_publish_needs_a_source_and_validation(client, on, people):
    p = create_profile(client, people["admin"], sources=[])
    assert p["can_publish"] is False and p["publish_blockers"]
    r = client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    assert r.status_code == 422 and "source" in r.json()["detail"]
    bad = [
        {"role_template_key": "astronaut"},
        {"sources": [{"kind": "public_url", "label": "Page", "url": "javascript:alert(1)"}]},
        {"sources": [{"kind": "public_url", "label": "Page"}]},
        {"sources": [{"kind": "jd", "label": "JD", "url": "https://example.com"}]},
        {"sources": [{"kind": "drive", "label": "Drive"}]},
        {"sources": [{"kind": "recruiter_about", "label": "About"}]},  # recruiter text is not a source
        {"extra": "field"},
    ]
    for override in bad:
        assert client.post(f"{C}/profiles/admin", json=profile_body(**override),
                           headers=people["admin"]).status_code == 422, override


def test_other_institute_is_404_everywhere(client, on, people, make_user, make_tenant, token_header, session):
    p = create_profile(client, people["admin"])
    client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    other = make_tenant("other-co")
    make_user("oadmin@y.edu", role=models.UserRole.admin, tenant=other)
    h_oadmin = token_header("oadmin@y.edu")
    _, h_ostu = _student(make_user, token_header, "ostu@y.edu", tenant=other)
    pid = p["id"]
    assert client.put(f"{C}/profiles/{pid}", json=profile_body(), headers=h_oadmin).status_code == 404
    assert client.post(f"{C}/profiles/{pid}/publish", headers=h_oadmin).status_code == 404
    assert client.post(f"{C}/profiles/{pid}/archive", headers=h_oadmin).status_code == 404
    assert client.get(f"{C}/profiles/admin", headers=h_oadmin).json() == []
    assert client.get(f"{C}/profiles/{pid}", headers=h_ostu).status_code == 404
    assert client.get(f"{C}/profiles", headers=h_ostu).json() == []
    r = _create(client, h_ostu, company_profile_id=pid)
    assert r.status_code == 422 and r.json()["detail"] == company_svc.MSG_UNAVAILABLE_PROFILE
    # Their drive cannot be used by our staff.
    d = drive(session, other.id)
    assert client.post(f"{C}/profiles/from-drive/{d.id}", headers=people["admin"]).status_code == 404
    r = client.post(f"{C}/profiles/admin", headers=people["admin"], json=profile_body(
        sources=[{"kind": "drive", "label": "Their drive", "drive_id": d.id}]))
    assert r.status_code == 404


def test_create_from_open_drive_only(client, on, people, session, no_network):
    tenant_id = people["student_user"].tenant_id
    d = drive(session, tenant_id)
    r = client.post(f"{C}/profiles/from-drive/{d.id}", headers=people["tpo"])
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["name"], p["role_title"], p["status"], p["drive_id"]) == ("Acme", "Backend Intern", "draft", d.id)
    assert [s["name"] for s in p["focus_skills"]] == ["Python", "SQL", "REST APIs"]
    assert p["sources"][0]["kind"] == "drive" and p["sources"][0]["drive_id"] == d.id
    assert p["can_publish"] is True
    closed = drive(session, tenant_id, is_open=False)
    assert client.post(f"{C}/profiles/from-drive/{closed.id}", headers=people["tpo"]).status_code == 404
    assert client.post(f"{C}/profiles/from-drive/999999", headers=people["tpo"]).status_code == 404
    assert no_network == []


# --- Students: published only, plain-text sources ---------------------------------------------


def test_students_see_published_profiles_with_plain_text_sources(client, on, people, no_network):
    draft = create_profile(client, people["admin"], name="Draft Co")
    pub = create_profile(client, people["admin"])
    archived = create_profile(client, people["admin"], name="Old Co")
    client.post(f"{C}/profiles/{pub['id']}/publish", headers=people["admin"])
    client.post(f"{C}/profiles/{archived['id']}/publish", headers=people["admin"])
    client.post(f"{C}/profiles/{archived['id']}/archive", headers=people["admin"])
    h = people["student"]
    listed = client.get(f"{C}/profiles", headers=h).json()
    assert [p["id"] for p in listed] == [pub["id"]]
    for pid in (draft["id"], archived["id"]):
        assert client.get(f"{C}/profiles/{pid}", headers=h).status_code == 404
    view = client.get(f"{C}/profiles/{pub['id']}", headers=h).json()
    assert view["attribution"] == "From your institute"
    assert NAME_SENTINEL in view["disclaimer"] and "not actual questions" in view["disclaimer"]
    assert view["sources"][1] == {"kind": "public_url", "label": "Careers page", "domain": "example.com"}
    assert "https://" not in json.dumps(view) and "job_description" not in view  # no URL, no JD text
    # Staff see the address they recorded (to edit it); it was never fetched.
    staff = client.get(f"{C}/profiles/admin", headers=people["admin"]).json()
    assert any(s.get("url") == "https://www.example.com/careers?x=1" for p in staff for s in p["sources"])
    assert no_network == []


# --- Sessions: frozen copy, generation only, evaluation parity --------------------------------


@pytest.fixture
def fake(monkeypatch):
    f = FakeEngine()
    monkeypatch.setattr(engine, "plan_interview", f.plan)
    monkeypatch.setattr(engine, "interview_turn", f.turn)
    return f


def test_session_keeps_a_frozen_copy_and_generation_gets_context(client, on, people, fake, session):
    p = create_profile(client, people["admin"])
    client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    h = people["student"]
    r = _create(client, h, company_profile_id=p["id"], focus_skills=["Caching", "SQL"])
    assert r.status_code == 201, r.text
    body = r.json()
    ctx = body["company_context"]
    assert ctx["profile_id"] == p["id"] and ctx["name"] == NAME_SENTINEL
    assert ctx["evaluation"] == "standard_rubric" and ctx["attribution"] == "From your institute"
    assert ctx["sources"][1] == {"kind": "public_url", "label": "Careers page", "domain": "example.com"}
    sid = body["id"]
    # Later edits never change the session's copy.
    client.put(f"{C}/profiles/{p['id']}", headers=people["admin"],
               json=profile_body(name="Renamed Co", summary="changed"))
    row = session.get(models.MockInterviewSession, sid)
    session.refresh(row)
    snap = row.plan_json["company"]
    assert snap["name"] == NAME_SENTINEL and NOTE_SENTINEL in snap["summary"]
    assert snap["focus_skills"][:3] == ["Caching", "SQL", "REST APIs"]
    assert row.company_name is None  # the session column is untouched
    # Generation payloads (plan + turn) carry the context.
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    assert fake.plan_calls[-1]["company_context"]["name"] == NAME_SENTINEL
    assert fake.turn_calls[-1]["company_context"]["focus_skills"][0] == "Caching"
    assert client.get(f"{BASE}/sessions/{sid}", headers=h).json()["company_context"] == ctx


def test_sessions_without_company_context_are_unchanged(client, on, people, fake):
    h = people["student"]
    r = _create(client, h)
    assert "company_context" not in r.json()
    sid = r.json()["id"]
    client.post(f"{BASE}/sessions/{sid}/start", headers=h)
    assert "company_context" not in fake.plan_calls[-1] and "company_context" not in fake.turn_calls[-1]
    assert "company_context" not in client.get(f"{BASE}/sessions/{sid}", headers=h).json()


def test_create_rules(client, on, people, monkeypatch):
    h = people["student"]
    draft = create_profile(client, people["admin"])
    assert _create(client, h, company_profile_id=draft["id"]).status_code == 422  # draft
    assert _create(client, h, company_profile_id=999999).status_code == 422
    assert _create(client, h, role_template_key="astronaut").status_code == 422
    r = _create(client, h, interview_type="coding", duration_minutes=30, role_template_key="sde_fresher")
    assert r.status_code == 422
    r = _create(client, h, role_template_key="data_analyst")
    assert r.status_code == 201 and r.json()["company_context"]["role_template_label"] == "Data Analyst"
    assert r.json()["company_context"]["name"] is None
    assert "not actual questions from any company" in r.json()["company_context"]["disclaimer"]


def _interview(client, h, reporter, **extra):
    r = _create(client, h, interview_type="technical", target_role="Backend Developer", company_name="Acme",
                difficulty="hard", **extra)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    for seq, text in enumerate(["I built a REST API with caching and SQL indexes for search.",
                                "I would shard the table by user id and add read replicas."], start=1):
        assert client.post(f"{BASE}/sessions/{sid}/answer", json={"turn_seq": seq, "answer": text},
                           headers=h).status_code == 200
    assert client.post(f"{BASE}/sessions/{sid}/finish", headers=h).json()["status"] == "completed"
    posted = client.post(f"{BASE}/sessions/{sid}/report", headers=h)
    assert posted.status_code == 200, posted.text
    return sid, reporter.calls[-1], posted.json()


def test_evaluation_is_identical_with_and_without_a_profile(
    client, on, people, fake, session, monkeypatch, make_user, token_header
):
    p = create_profile(client, people["admin"])
    client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    reporter = _install_reporter(monkeypatch, "technical")
    _, h_plain = _student(make_user, token_header, "plain@x.edu")
    _, h_co = _student(make_user, token_header, "withco@x.edu")
    sid_plain, payload_plain, report_plain = _interview(client, h_plain, reporter)
    sid_co, payload_co, report_co = _interview(client, h_co, reporter, company_profile_id=p["id"])
    # The worker's evaluation payload is byte-identical: no company data reaches evaluation.
    assert json.dumps(payload_plain, sort_keys=True) == json.dumps(payload_co, sort_keys=True)
    assert set(payload_co) == {"config", "plan", "transcript"}
    assert NAME_SENTINEL not in json.dumps(payload_co)
    # Same stored report and the same API response (standard technical-v1 rubric).
    assert _row(session, sid_plain) == _row(session, sid_co)
    assert _without_session_fields(report_plain) == _without_session_fields(report_co)
    assert report_co["rubric_version"] == "technical-v1"


def test_no_company_fit_or_competency_score_in_openapi(client):
    schema = json.dumps(client.app.openapi())
    for banned in ("company_fit", "companyfit", "competency_score", "fit_score"):
        assert banned not in schema.lower()


# --- JD skill map ------------------------------------------------------------------------------


class JDWorker:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, method, url, *, headers=None, json=None, timeout=None):
        assert url.endswith("/interview/jd/skills"), url
        self.calls.append(json)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        status, body = self.outcome
        return httpx.Response(status, json=body, request=httpx.Request(method, url))


JD = f"We are hiring a backend intern. Must know Python and PostgreSQL. Bonus: Kubernetes. {JD_SENTINEL}"
JD_OK = (200, {"provider": "fake", "skills": [
    {"skill": "Python", "category": "technical", "importance": 3, "evidence": "Must know Python and PostgreSQL"},
    {"skill": "postgres", "category": "technical", "importance": 3, "evidence": "Python and PostgreSQL"},
    {"skill": "Kubernetes", "category": "tool", "importance": 1, "evidence": "Bonus: Kubernetes"},
    {"skill": "Leadership", "category": "soft", "importance": 2, "evidence": "Leads a team of ten engineers"},
], "usage": {"provider": "fake", "llm_calls": 1, "input_tokens": 5, "output_tokens": 5}})


def test_jd_skill_map_quotes_the_jd_and_matches_verified_skills(client, on, people, monkeypatch, session):
    w = JDWorker(JD_OK)
    monkeypatch.setattr(ai_client.httpx, "request", w)
    monkeypatch.setattr(company_svc, "_build_verified_profile",
                        lambda db, uid: {"verified_skills": ["python3", "PostgreSQL"]})
    r = client.post(f"{C}/jd-skills", json={"job_description": JD}, headers=people["student"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["skill"] for s in body["skills"]] == ["Python", "postgres", "Kubernetes"]  # unquoted one dropped
    assert body["matched"] == ["Python", "postgres"] and body["missing"] == ["Kubernetes"]
    assert body["focus_order"][0] == "Kubernetes"
    assert w.calls == [{"job_description": JD}]
    with SessionLocal() as db:
        rows = list(db.scalars(select(models.AIUsageEvent).where(models.AIUsageEvent.step == "jd_skills")))
    assert len(rows) == 1 and rows[0].outcome == "ok"
    dumped = json.dumps({c.name: str(getattr(rows[0], c.name)) for c in rows[0].__table__.columns})
    assert JD_SENTINEL not in dumped and "PostgreSQL" not in dumped


def test_jd_skill_sources_and_failures(client, on, people, monkeypatch, session):
    monkeypatch.setattr(company_svc, "_build_verified_profile", lambda db, uid: {"verified_skills": []})
    h = people["student"]
    assert client.post(f"{C}/jd-skills", json={}, headers=h).status_code == 422
    assert client.post(f"{C}/jd-skills", json={"job_description": "x", "profile_id": 1}, headers=h).status_code == 422
    monkeypatch.setattr(ai_client.httpx, "request", JDWorker(httpx.ConnectError("down")))
    assert client.post(f"{C}/jd-skills", json={"job_description": JD}, headers=h).status_code == 503
    w = JDWorker(JD_OK)
    monkeypatch.setattr(ai_client.httpx, "request", w)
    tenant_id = people["student_user"].tenant_id
    open_d = drive(session, tenant_id, description=JD)
    closed = drive(session, tenant_id, is_open=False, description=JD)
    assert client.post(f"{C}/jd-skills", json={"drive_id": open_d.id}, headers=h).json()["source"] == "drive"
    assert client.post(f"{C}/jd-skills", json={"drive_id": closed.id}, headers=h).status_code == 404
    draft = create_profile(client, people["admin"], job_description=JD)
    assert client.post(f"{C}/jd-skills", json={"profile_id": draft["id"]}, headers=h).status_code == 404
    client.post(f"{C}/profiles/{draft['id']}/publish", headers=people["admin"])
    assert client.post(f"{C}/jd-skills", json={"profile_id": draft["id"]}, headers=h).json()["source"] == "profile"
    assert client.post(f"{C}/jd-skills", json={"job_description": JD}, headers=people["admin"]).status_code == 403


# --- Review fixes (2026-11-23) -------------------------------------------------------------------


def test_published_profile_cannot_lose_its_sources(client, on, people, session):
    p = create_profile(client, people["admin"])
    client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    r = client.put(f"{C}/profiles/{p['id']}", json=profile_body(sources=[]), headers=people["admin"])
    assert r.status_code == 422
    summaries = [x.summary for x in audit_rows(session, p["id"])]
    assert summaries[-1].startswith(f"Company profile #{p['id']} published")


def test_company_context_stops_when_the_feature_is_switched_off(client, on, people, fake, monkeypatch):
    p = create_profile(client, people["admin"])
    client.post(f"{C}/profiles/{p['id']}/publish", headers=people["admin"])
    h = people["student"]
    sid = _create(client, h, company_profile_id=p["id"]).json()["id"]
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", False)
    assert client.post(f"{BASE}/sessions/{sid}/start", headers=h).status_code == 200
    assert "company_context" not in fake.plan_calls[-1] and "company_context" not in fake.turn_calls[-1]
    # The student still sees the disclaimer of the session they created.
    assert client.get(f"{BASE}/sessions/{sid}", headers=h).json()["company_context"]["profile_id"] == p["id"]
