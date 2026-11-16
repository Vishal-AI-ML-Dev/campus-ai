"""Phase 5J - placement + recruiter flows through the REAL /v1 API (Postgres/RLS).

Uses the API-built `campus` fixture (two synthetic institutes, every user
logged in through /v1/auth/login with its own cookie session). Recruiters are
onboarded through the dedicated flow: TPO invite -> public accept-invite ->
real login. Flows:

  P1 TPO creates a drive          P3 TPO reviews + shortlists
  P2 student eligibility + apply  R1 recruiter invite / accept / login
  R2 recruiter sees permitted candidates only
  R3 recruiter offer -> student response

Each step has a same-tenant success path plus role and cross-tenant denials.
No AI or external calls; assertion messages carry only codes/ids/details.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace

import pytest

from flowkit import EMAIL_DOMAIN, MARK, Actor, _d  # noqa: F401


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _drive(tpo, **criteria):
    body = {"company_name": "Synthetic Co", "role_title": "Backend Engineer",
            "location": "Remote", "package_lpa": 12.0, **criteria}
    r = tpo.post("/v1/drives", json=body)
    assert r.status_code == 201, _d(r)
    return r.json()


def _apply(student, drive_id, expect=201):
    r = student.post(f"/v1/drives/{drive_id}/apply")
    assert r.status_code == expect, _d(r)
    return r.json() if expect == 201 else None


def _shortlist(tpo, application_id, status="shortlisted"):
    r = tpo.patch(f"/v1/drives/applications/{application_id}/status",
                  json={"status": status, "note": "synthetic"})
    assert r.status_code == 200, _d(r)
    return r.json()


def _onboard_recruiter(campus, t, label):
    """TPO invites -> recruiter accepts (public) -> real /v1/auth/login."""
    email = f"rec-{label}-{campus.run}@{EMAIL_DOMAIN}"
    inv = t.tpo.post("/v1/recruiters/invite", json={
        "company_name": f"{MARK}-{campus.run} {label} Co", "email": email})
    assert inv.status_code == 201, _d(inv)
    pw = secrets.token_urlsafe(14)
    acc = campus.new_client().post("/v1/recruiters/accept-invite", json={
        "token": inv.json()["token"], "full_name": f"Recruiter {label}", "password": pw})
    assert acc.status_code == 200, _d(acc)
    actor = campus.login(email, pw, f"recruiter-{label}")
    actor.company = inv.json()["recruiter"]["id"]
    actor.invite_token = inv.json()["token"]
    return actor


def _link(tpo, drive_id, company_id):
    r = tpo.patch(f"/v1/recruiters/drives/{drive_id}/recruiter", json={"recruiter_id": company_id})
    assert r.status_code == 200, _d(r)


@pytest.fixture
def pipeline(campus):
    """A drive in institute A with one SHORTLISTED (student) and one APPLIED
    (student2) application, linked to recruiter company X. Also: a second
    company Y in A, and a recruiter in B."""
    a = campus.a
    drive = _drive(a.tpo)
    app1 = _apply(a.student, drive["id"])
    app2 = _apply(a.student2, drive["id"])
    _shortlist(a.tpo, app1["id"])
    rec_x = _onboard_recruiter(campus, a, "x")
    rec_y = _onboard_recruiter(campus, a, "y")
    rec_b = _onboard_recruiter(campus, campus.b, "b")
    _link(a.tpo, drive["id"], rec_x.company)
    return SimpleNamespace(drive=drive, app1=app1, app2=app2,
                           rec_x=rec_x, rec_y=rec_y, rec_b=rec_b)


# ==========================================================================
# P1 - TPO creates a placement drive
# ==========================================================================
def test_p1_tpo_creates_drive_visible_only_in_own_institute(campus, sql):
    a, b = campus.a, campus.b
    drive = _drive(a.tpo, min_cgpa=0, required_skills=None)
    assert drive["is_open"] is True
    assert sql("SELECT tenant_id FROM drives WHERE id = :d", {"d": drive["id"]},
               tenant=a.tenant) == [{"tenant_id": a.tenant}]
    assert drive["id"] in {d["id"] for d in a.student.get("/v1/drives/open").json()}
    assert drive["id"] in {d["id"] for d in a.tpo.get("/v1/drives").json()}
    assert drive["id"] not in {d["id"] for d in b.student.get("/v1/drives/open").json()}
    assert drive["id"] not in {d["id"] for d in b.tpo.get("/v1/drives").json()}
    for who in (b.tpo, b.student):
        r = who.get(f"/v1/drives/{drive['id']}")
        assert r.status_code == 404, (who.role, _d(r))
    r = b.tpo.patch(f"/v1/drives/{drive['id']}/status", json={"is_open": False})
    assert r.status_code == 404, _d(r)
    assert a.tpo.patch(f"/v1/drives/{drive['id']}/status", json={"is_open": False}).status_code == 200


def test_p1_only_tpo_can_create_or_close_drives(campus):
    a = campus.a
    drive = _drive(a.tpo)
    for who in (a.student, a.teacher):
        r = who.post("/v1/drives", json={"company_name": "x", "role_title": "y"})
        assert r.status_code == 403, (who.role, _d(r))
        r = who.patch(f"/v1/drives/{drive['id']}/status", json={"is_open": False})
        assert r.status_code == 403, (who.role, _d(r))


# ==========================================================================
# P2 - Student eligibility and application
# ==========================================================================
def test_p2_eligibility_uses_verified_skills_then_student_applies(campus, sql):
    a, b = campus.a, campus.b
    drive = _drive(a.tpo, required_skills="FastAPI")
    elig = a.student.get(f"/v1/drives/{drive['id']}/my-eligibility")
    assert elig.status_code == 200 and elig.json()["eligible"] is False
    reasons = {x["criterion"]: x for x in elig.json()["reasons"]}
    assert reasons["Required skills"]["passed"] is False
    r = a.student.post(f"/v1/drives/{drive['id']}/apply")
    assert r.status_code == 403, _d(r)
    # Claim + teacher verification makes the student eligible.
    sk = a.student.post("/v1/skills", json={"name": "FastAPI"}).json()
    assert a.teacher.patch(f"/v1/skills/{sk['id']}/decision",
                           json={"status": "verified"}).status_code == 200
    assert a.student.get(f"/v1/drives/{drive['id']}/my-eligibility").json()["eligible"] is True
    tpo_view = {x["student_id"]: x for x in a.tpo.get(f"/v1/drives/{drive['id']}/eligibility").json()}
    assert tpo_view[a.student.id]["eligible"] is True and tpo_view[a.student2.id]["eligible"] is False
    app_ = _apply(a.student, drive["id"])
    assert app_["status"] == "applied" and app_["student_id"] == a.student.id
    _apply(a.student, drive["id"], expect=409)  # no duplicate application
    mine = a.student.get("/v1/drives/me/applications").json()
    assert drive["id"] in {m["drive"]["id"] for m in mine}
    assert sql("SELECT tenant_id FROM applications WHERE id = :i", {"i": app_["id"]},
               tenant=a.tenant) == [{"tenant_id": a.tenant}]
    # Cross-tenant: B's student and TPO cannot see or use A's drive.
    assert b.student.get(f"/v1/drives/{drive['id']}/my-eligibility").status_code == 404
    _apply(b.student, drive["id"], expect=404)
    assert b.tpo.get(f"/v1/drives/{drive['id']}/eligibility").status_code == 404


def test_p2_closed_drive_rejects_applications(campus, count_rows):
    a = campus.a
    drive = _drive(a.tpo)
    assert a.tpo.patch(f"/v1/drives/{drive['id']}/status", json={"is_open": False}).status_code == 200
    _apply(a.student, drive["id"], expect=400)
    assert count_rows(a.tenant, "applications", "drive_id = :d", {"d": drive["id"]}) == 0


def test_p2_only_students_can_apply(campus, count_rows):
    """Applications represent candidates; staff and recruiter accounts must
    not be able to apply to a drive (no criteria, so eligibility passes)."""
    a = campus.a
    drive = _drive(a.tpo)
    rec = _onboard_recruiter(campus, a, "applicant")
    codes = {}
    for who in (a.teacher, a.tpo, a.admin, rec):
        codes[who.role] = who.post(f"/v1/drives/{drive['id']}/apply").status_code
    created = count_rows(a.tenant, "applications", "drive_id = :d", {"d": drive["id"]})
    assert all(c == 403 for c in codes.values()) and created == 0, (
        f"non-student apply status codes={codes}; applications created={created}")


# ==========================================================================
# P3 - TPO reviews and shortlists
# ==========================================================================
def test_p3_tpo_reviews_and_shortlists(campus):
    a = campus.a
    drive = _drive(a.tpo)
    app_ = _apply(a.student, drive["id"])
    listing = a.tpo.get(f"/v1/drives/{drive['id']}/applications")
    assert listing.status_code == 200
    assert [x["application_id"] for x in listing.json()] == [app_["id"]]
    out = _shortlist(a.tpo, app_["id"])
    assert out["status"] == "shortlisted"
    mine = {m["drive"]["id"]: m for m in a.student.get("/v1/drives/me/applications").json()}
    assert mine[drive["id"]]["status"] == "shortlisted"
    notes = a.student.get("/v1/notifications").json()
    assert any("shortlisted" in (n.get("title") or "") for n in notes)


def test_p3_shortlisting_permissions_and_cross_tenant_denials(campus, sql):
    a, b = campus.a, campus.b
    drive = _drive(a.tpo)
    app_ = _apply(a.student, drive["id"])
    url = f"/v1/drives/applications/{app_['id']}/status"
    rec = _onboard_recruiter(campus, a, "noshortlist")
    for who, code in ((a.student, 403), (a.teacher, 403), (rec, 403), (b.tpo, 404)):
        r = who.patch(url, json={"status": "shortlisted"})
        assert r.status_code == code, (who.role, _d(r))
    assert a.tpo.patch(url, json={"status": "applied"}).status_code == 400
    for who, code in ((b.tpo, 404), (a.student, 403), (rec, 403)):
        r = who.get(f"/v1/drives/{drive['id']}/applications")
        assert r.status_code == code, (who.role, _d(r))
    assert sql("SELECT status::text AS s FROM applications WHERE id = :i", {"i": app_["id"]},
               tenant=a.tenant) == [{"s": "applied"}]


# ==========================================================================
# R1 - Recruiter invite, acceptance, login
# ==========================================================================
def test_r1_recruiter_invite_accept_login(campus, sql):
    a, b = campus.a, campus.b
    rec = _onboard_recruiter(campus, a, "r1")
    assert rec.role == "recruiter"
    me = rec.get("/v1/recruiters/me")
    assert me.status_code == 200 and me.json()["recruiter"]["id"] == rec.company
    assert me.json()["recruiter"]["status"] == "active"
    assert sql("SELECT tenant_id FROM users WHERE id = :u", {"u": rec.id}) == [{"tenant_id": a.tenant}]
    again = campus.new_client().post("/v1/recruiters/accept-invite", json={
        "token": rec.invite_token, "full_name": "Again", "password": secrets.token_urlsafe(12)})
    assert again.status_code == 400, _d(again)
    assert rec.company not in {c["id"] for c in b.tpo.get("/v1/recruiters").json()}
    for who in (a.student, a.teacher):
        r = who.post("/v1/recruiters/invite", json={"company_name": "x", "email": f"n-{campus.run}@{EMAIL_DOMAIN}"})
        assert r.status_code == 403, (who.role, _d(r))


# ==========================================================================
# R2 - Recruiter views permitted candidates only
# ==========================================================================
def test_r2_recruiter_sees_only_shortlisted_candidates_on_own_drives(campus, pipeline):
    a, p = campus.a, pipeline
    drives = p.rec_x.get("/v1/recruiters/me/drives").json()
    assert [(d["id"], d["shortlisted_count"]) for d in drives] == [(p.drive["id"], 1)]
    cands = p.rec_x.get("/v1/recruiters/me/candidates").json()
    assert [c["application_id"] for c in cands] == [p.app1["id"]]  # 'applied' hidden
    assert cands[0]["email"] is None and cands[0]["contact_revealed"] is False
    r = p.rec_x.get("/v1/recruiters/me/candidates?status_filter=applied")
    assert r.status_code == 400, _d(r)
    # Contact appears only after the TPO reveals it.
    rv = a.tpo.patch(f"/v1/recruiters/applications/{p.app1['id']}/contact", json={"revealed": True})
    assert rv.status_code == 200, _d(rv)
    cand = p.rec_x.get("/v1/recruiters/me/candidates").json()[0]
    assert cand["contact_revealed"] is True and cand["email"] == a.student.email


def test_r2_other_companies_and_institutes_see_no_candidates(campus, pipeline):
    p = pipeline
    for rec in (p.rec_y, p.rec_b):
        assert rec.get("/v1/recruiters/me/drives").json() == []
        assert rec.get("/v1/recruiters/me/candidates").json() == []
        r = rec.patch(f"/v1/recruiters/me/candidates/{p.app1['id']}/decision",
                      json={"decision": "interested"})
        assert r.status_code in (403, 404), (rec.email.split("@")[0][:12], _d(r))
    for who in (campus.a.student, campus.a.tpo):
        assert who.get("/v1/recruiters/me/candidates").status_code == 403


def test_r2_recruiter_cannot_use_tpo_placement_routes(campus, pipeline):
    p, d = pipeline, pipeline.drive["id"]
    rec = p.rec_x
    checks = [
        ("post", "/v1/drives", {"json": {"company_name": "x", "role_title": "y"}}),
        ("get", "/v1/drives", {}),
        ("get", f"/v1/drives/{d}/applications", {}),
        ("get", f"/v1/drives/{d}/eligibility", {}),
        ("patch", f"/v1/drives/applications/{p.app2['id']}/status", {"json": {"status": "shortlisted"}}),
        ("patch", f"/v1/recruiters/applications/{p.app1['id']}/contact", {"json": {"revealed": True}}),
        ("patch", f"/v1/recruiters/drives/{d}/recruiter", {"json": {"recruiter_id": rec.company}}),
    ]
    for method, url, kw in checks:
        r = getattr(rec.client, method)(url, **kw)
        assert r.status_code == 403, (method, url, _d(r))


def test_r2_recruiter_cannot_browse_unlinked_drives(campus, pipeline):
    """A recruiter should only see drives linked to its own company, not the
    institute's other drives (other companies' roles, packages, criteria)."""
    other = _drive(campus.a.tpo, company_name="Competitor Co")
    rec = pipeline.rec_x
    listed = rec.get("/v1/drives/open")
    sees_other = listed.status_code == 200 and other["id"] in {d["id"] for d in listed.json()}
    single = rec.get(f"/v1/drives/{other['id']}")
    assert not sees_other and single.status_code in (403, 404), (
        f"recruiter: /drives/open {_d(listed)} lists unlinked drive={sees_other}; "
        f"GET unlinked drive {_d(single)}")


# ==========================================================================
# R3 - Recruiter creates an offer; student responds
# ==========================================================================
def test_r3_offer_created_and_accepted_by_candidate(campus, pipeline, sql):
    a, b, p = campus.a, campus.b, pipeline
    dec = p.rec_x.patch(f"/v1/recruiters/me/candidates/{p.app1['id']}/decision",
                        json={"decision": "interested", "note": "strong"})
    assert dec.status_code == 200 and dec.json()["recruiter_decision"] == "interested"
    r = p.rec_x.post("/v1/recruiters/me/offers", json={"application_id": p.app1["id"]})
    assert r.status_code == 201, _d(r)
    offer = r.json()
    assert offer["status"] == "extended" and offer["student_id"] == a.student.id
    assert offer["role_title"] == "Backend Engineer" and offer["package_lpa"] == 12.0
    assert sql("SELECT tenant_id, recruiter_id FROM offers WHERE id = :o", {"o": offer["id"]},
               tenant=a.tenant) == [{"tenant_id": a.tenant, "recruiter_id": p.rec_x.company}]
    dup = p.rec_x.post("/v1/recruiters/me/offers", json={"application_id": p.app1["id"]})
    assert dup.status_code == 409, _d(dup)
    assert offer["id"] in {o["id"] for o in p.rec_x.get("/v1/recruiters/me/offers").json()}
    # Student sees and accepts; nobody else can respond.
    assert offer["id"] in {o["id"] for o in a.student.get("/v1/recruiters/offers/mine").json()}
    assert a.student2.get("/v1/recruiters/offers/mine").json() == []
    for who in (a.student2, b.student):
        rr = who.patch(f"/v1/recruiters/offers/{offer['id']}/respond", json={"accept": True})
        assert rr.status_code == 404, _d(rr)
    acc = a.student.patch(f"/v1/recruiters/offers/{offer['id']}/respond", json={"accept": True})
    assert acc.status_code == 200 and acc.json()["status"] == "accepted", _d(acc)
    assert a.student.patch(f"/v1/recruiters/offers/{offer['id']}/respond",
                           json={"accept": False}).status_code == 409
    assert p.rec_x.patch(f"/v1/recruiters/me/offers/{offer['id']}/withdraw").status_code == 409
    oversight = {o["id"]: o for o in a.tpo.get("/v1/recruiters/offers").json()}
    assert oversight[offer["id"]]["status"] == "accepted"
    assert offer["id"] not in {o["id"] for o in b.tpo.get("/v1/recruiters/offers").json()}


def test_r3_offer_denials(campus, pipeline, count_rows):
    a, p = campus.a, pipeline
    body = lambda app_id: {"json": {"application_id": app_id}}  # noqa: E731
    cases = [
        (p.rec_x, p.app2["id"], 403),   # candidate only 'applied' (not visible)
        (p.rec_y, p.app1["id"], 403),   # other company, same institute
        (p.rec_b, p.app1["id"], (403, 404)),  # other institute
        (a.student, p.app1["id"], 403),  # wrong roles
        (a.tpo, p.app1["id"], 403),
    ]
    for who, app_id, want in cases:
        r = who.post("/v1/recruiters/me/offers", **body(app_id))
        ok = r.status_code in want if isinstance(want, tuple) else r.status_code == want
        assert ok, (who.role, app_id, _d(r))
    assert count_rows(a.tenant, "offers", "drive_id = :d", {"d": p.drive["id"]}) == 0
    # Withdraw: only the issuing company.
    offer = p.rec_x.post("/v1/recruiters/me/offers", **body(p.app1["id"])).json()
    for rec in (p.rec_y, p.rec_b):
        assert rec.patch(f"/v1/recruiters/me/offers/{offer['id']}/withdraw").status_code == 404
    w = p.rec_x.patch(f"/v1/recruiters/me/offers/{offer['id']}/withdraw")
    assert w.status_code == 200 and w.json()["status"] == "withdrawn"


# ==========================================================================
# Phase 5J fixes - regression tests (both route aliases)
# ==========================================================================
ALIASES = ("/v1", "")  # versioned + deprecated unversioned mount


def test_fix_only_students_apply_on_both_aliases(campus, count_rows):
    a = campus.a
    drive = _drive(a.tpo)
    rec = _onboard_recruiter(campus, a, "fixapply")
    for prefix in ALIASES:
        for who in (a.teacher, a.tpo, a.admin, rec):
            r = who.post(f"{prefix}/drives/{drive['id']}/apply")
            assert r.status_code == 403, (prefix, who.role, _d(r))
    assert count_rows(a.tenant, "applications", "drive_id = :d", {"d": drive["id"]}) == 0
    # Students still apply - on either alias.
    _apply(a.student, drive["id"])
    r = a.student2.post(f"/drives/{drive['id']}/apply")
    assert r.status_code == 201, _d(r)
    assert count_rows(a.tenant, "applications", "drive_id = :d", {"d": drive["id"]}) == 2


def test_fix_recruiter_blocked_from_student_staff_drive_routes(campus, pipeline):
    p = pipeline
    unlinked = _drive(campus.a.tpo, company_name="Competitor Co")
    for prefix in ALIASES:
        for drive_id in (p.drive["id"], unlinked["id"]):  # even its OWN linked drive
            for method, url in (("get", f"{prefix}/drives/{drive_id}"),
                                ("get", f"{prefix}/drives/{drive_id}/my-eligibility"),
                                ("post", f"{prefix}/drives/{drive_id}/apply")):
                r = getattr(p.rec_x.client, method)(url)
                assert r.status_code == 403, (method, url, _d(r))
        for url in (f"{prefix}/drives/open", f"{prefix}/drives/me/applications", f"{prefix}/drives"):
            r = p.rec_x.get(url)
            assert r.status_code == 403, (url, _d(r))


def test_fix_recruiter_uses_dedicated_linked_drive_routes(campus, pipeline):
    """Positive control: the recruiter still sees its OWN linked drive (and
    its candidates) through /recruiters/me/*, on both aliases; an unlinked
    drive never appears there."""
    p = pipeline
    unlinked = _drive(campus.a.tpo, company_name="Competitor Co")
    for prefix in ALIASES:
        drives = p.rec_x.get(f"{prefix}/recruiters/me/drives")
        assert drives.status_code == 200, _d(drives)
        mine = {d["id"]: d for d in drives.json()}
        assert set(mine) == {p.drive["id"]}
        assert mine[p.drive["id"]]["role_title"] == "Backend Engineer"
        assert mine[p.drive["id"]]["package_lpa"] == 12.0
        own = p.rec_x.get(f"{prefix}/recruiters/me/candidates?drive_id={p.drive['id']}")
        assert [c["application_id"] for c in own.json()] == [p.app1["id"]]
        other = p.rec_x.get(f"{prefix}/recruiters/me/candidates?drive_id={unlinked['id']}")
        assert other.status_code == 200 and other.json() == []


def test_fix_student_and_staff_drive_access_preserved(campus, pipeline):
    """Positive controls: legitimate student / teacher / TPO / admin access."""
    a, d = campus.a, pipeline.drive["id"]
    for prefix in ALIASES:
        for who in (a.student, a.teacher, a.tpo, a.admin):
            assert who.get(f"{prefix}/drives/open").status_code == 200, (prefix, who.role)
            assert who.get(f"{prefix}/drives/{d}").status_code == 200, (prefix, who.role)
        el = a.student.get(f"{prefix}/drives/{d}/my-eligibility")
        assert el.status_code == 200 and "eligible" in el.json()
        apps = a.student.get(f"{prefix}/drives/me/applications")
        assert apps.status_code == 200 and d in {x["drive"]["id"] for x in apps.json()}
        for who in (a.tpo, a.admin):
            lst = who.get(f"{prefix}/drives")
            assert lst.status_code == 200 and d in {x["id"] for x in lst.json()}, (prefix, who.role)
        assert a.tpo.get(f"{prefix}/drives/{d}/applications").status_code == 200
        assert a.tpo.get(f"{prefix}/drives/{d}/eligibility").status_code == 200
