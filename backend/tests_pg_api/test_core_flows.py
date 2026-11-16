"""Phase 5E - core campus flows through the REAL /v1 API on Postgres (RLS).

Every test builds two fresh synthetic institutes (A, B) purely through API
routes: public institute signup -> admin login (/v1/auth/login) -> department
+ section -> staff/student invitations accepted via /v1/tenants/accept-invite
(plus one admin-created student) -> every user logs in with the real login
route and keeps its OWN path-aware cookie session (no minted JWTs).

Flows: (1) signup/invite/user creation, (2) skill claim + teacher
verification, (3) attendance marking + own view, (4) assignment create ->
submit -> grade. Each has a same-tenant success path and permission /
cross-tenant denials. The AI worker is mocked; nothing leaves the process.
Assertion messages carry only status codes, ids and server 'detail' strings.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest



from flowkit import EMAIL_DOMAIN, MARK, Actor, _d  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh_rate_limits(fresh_rate_limits):
    """Keep this module's original per-test limiter reset (now in conftest)."""
    yield

# ==========================================================================
# Flow 1 - institute signup, invitations, user creation
# ==========================================================================
def test_flow1_signup_invites_and_logins_land_in_own_institute(campus, sql):
    for t in (campus.a, campus.b):
        roles = {"admin": t.admin, "teacher": t.teacher, "tpo": t.tpo,
                 "student": t.student}
        for role, actor in roles.items():
            assert actor.role == role, (role, actor.role)
        ids = [x.id for x in (*roles.values(), t.student2)]
        rows = sql("SELECT DISTINCT tenant_id FROM users WHERE id = ANY(:ids)", {"ids": ids})
        assert rows == [{"tenant_id": t.tenant}]
        inv = t.admin.get("/v1/tenants/invites?limit=100")
        assert inv.status_code == 200
        assert {i["status"] for i in inv.json()} == {"accepted"} and len(inv.json()) == 3
    # A's admin sees only A's invites.
    a_emails = {i["email"] for i in campus.a.admin.get("/v1/tenants/invites?limit=100").json()}
    assert not any(f"-b-{campus.run}@" in e for e in a_emails)


def test_flow1_invite_permissions_and_cross_tenant_denials(campus, sql):
    a, b, run = campus.a, campus.b, campus.run
    # Students and teachers cannot invite; TPO can (control).
    for who in (a.student, a.teacher):
        r = who.post("/v1/tenants/invites", json={"email": f"x-{uuid.uuid4().hex[:6]}@{EMAIL_DOMAIN}"})
        assert r.status_code == 403, _d(r)
    ok = a.tpo.post("/v1/tenants/invites", json={"email": f"tpo-inv-{run}@{EMAIL_DOMAIN}", "role": "student"})
    assert ok.status_code == 201, _d(ok)
    # A's admin cannot revoke B's pending invite.
    b_inv = b.admin.post("/v1/tenants/invites", json={"email": f"pending-b-{run}@{EMAIL_DOMAIN}"})
    assert b_inv.status_code == 201
    r = a.admin.post(f"/v1/tenants/invites/{b_inv.json()['invite']['id']}/revoke")
    assert r.status_code == 404, _d(r)
    # Accepting B's invite with an A-pointing hint still lands in B; reuse fails.
    acc = campus.new_client().post("/v1/tenants/accept-invite", json={
        "token": b_inv.json()["token"], "full_name": "Hinted", "password": secrets.token_urlsafe(12),
        "tenant_id": a.tenant}, headers={"X-Tenant-ID": str(a.tenant)})
    assert acc.status_code == 200, _d(acc)
    assert sql("SELECT tenant_id FROM users WHERE email = :e",
               {"e": f"pending-b-{run}@{EMAIL_DOMAIN}"}) == [{"tenant_id": b.tenant}]
    again = campus.new_client().post("/v1/tenants/accept-invite", json={
        "token": b_inv.json()["token"], "full_name": "Again", "password": secrets.token_urlsafe(12)})
    assert again.status_code == 400, _d(again)
    # A's admin cannot manage B's users; same-tenant control succeeds.
    for url, body in ((f"/v1/admin/users/{b.student.id}/role", {"role": "teacher"}),
                      (f"/v1/admin/users/{b.student.id}/section", {"section_id": None})):
        r = a.admin.patch(url, json=body)
        assert r.status_code == 404, (url, _d(r))
    assert sql("SELECT role::text AS r FROM users WHERE id = :u", {"u": b.student.id}) == [{"r": "student"}]
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/role", json={"role": "student"}).status_code == 200
    # Students cannot create users.
    r = a.student.post("/v1/admin/users", json={"email": f"s-{run}@{EMAIL_DOMAIN}",
                                                 "full_name": "x", "password": "abcdef12"})
    assert r.status_code == 403, _d(r)


def test_flow1_institute_admin_cannot_list_or_create_other_institutes(campus, sql):
    """/v1/tenants is documented as PLATFORM-admin only, but it is gated on the
    per-institute `admin` role. An institute admin must not enumerate other
    institutes or create new ones."""
    lst = campus.a.admin.get("/v1/tenants?limit=200")
    visible_b = lst.status_code == 200 and any(t.get("slug") == campus.b.slug for t in lst.json())
    slug = f"{MARK}-{campus.run}-x"
    mk = campus.a.admin.post("/v1/tenants", json={"name": "Rogue", "slug": slug})
    created = sql("SELECT count(*) AS n FROM tenants WHERE slug = :s", {"s": slug})[0]["n"]
    if created:
        sql("DELETE FROM tenants WHERE slug = :s", {"s": slug})
    assert not visible_b and lst.status_code in (403, 404) and created == 0, (
        f"institute admin: list tenants {_d(lst)} (sees B: {visible_b}); "
        f"create tenant {_d(mk)} (rows created: {created})")


def test_flow1_tpo_cannot_invite_an_admin(campus, sql):
    """Role escalation check: a TPO (inviter) issuing an ADMIN invite."""
    email = f"esc-{campus.run}@{EMAIL_DOMAIN}"
    r = campus.a.tpo.post("/v1/tenants/invites", json={"email": email, "role": "admin"})
    assert r.status_code in (403, 422), f"TPO invited role=admin: {_d(r)}"


# ==========================================================================
# Flow 2 - skill claim and teacher verification
# ==========================================================================
def _claim(student, name):
    r = student.post("/v1/skills", json={"name": name, "evidence_url": "https://example.com/proof"})
    assert r.status_code == 201, _d(r)
    return r.json()


def test_flow2_skill_claim_and_teacher_verification(campus):
    a, b = campus.a, campus.b
    skill = _claim(a.student, "FastAPI")
    assert skill["status"] == "pending" and skill["student_id"] == a.student.id
    q = a.teacher.get("/v1/skills/queue")
    assert q.status_code == 200 and skill["id"] in {s["id"] for s in q.json()}
    assert skill["id"] not in {s["id"] for s in b.teacher.get("/v1/skills/queue").json()}
    d = a.teacher.patch(f"/v1/skills/{skill['id']}/decision",
                        json={"status": "verified", "review_note": "ok"})
    assert d.status_code == 200 and d.json()["status"] == "verified", _d(d)
    mine = {s["id"]: s for s in a.student.get("/v1/skills/me").json()}
    assert mine[skill["id"]]["status"] == "verified"
    v = a.teacher.get(f"/v1/skills/student/{a.student.id}?status_filter=verified")
    assert skill["id"] in {s["id"] for s in v.json()}
    assert skill["id"] not in {s["id"] for s in a.teacher.get("/v1/skills/queue").json()}


def test_flow2_skill_permissions_and_cross_tenant_denials(campus, sql):
    a, b = campus.a, campus.b
    skill = _claim(a.student, "SQL")
    body = {"status": "verified"}
    for who, code in ((a.student, 403), (a.tpo, 403), (b.teacher, 404)):
        r = who.patch(f"/v1/skills/{skill['id']}/decision", json=body)
        assert r.status_code == code, (who.role, _d(r))
    assert a.teacher.patch(f"/v1/skills/{skill['id']}/decision",
                           json={"status": "pending"}).status_code == 400
    r = b.student.delete(f"/v1/skills/{skill['id']}")
    assert r.status_code == 404, _d(r)
    r = a.student2.delete(f"/v1/skills/{skill['id']}")  # same tenant, not owner
    assert r.status_code == 403, _d(r)
    assert b.teacher.get(f"/v1/skills/student/{a.student.id}").json() == []
    rows = sql("SELECT status::text AS s FROM skills WHERE id = :i", {"i": skill["id"]},
               tenant=a.tenant)
    assert rows == [{"s": "pending"}]


def test_flow2_ai_proof_score_is_persisted_with_mocked_worker(campus, sql, monkeypatch):
    """The claim enqueues ai_client.score_skill (in-process BackgroundTasks
    here: REDIS_URL is empty). With the worker MOCKED to return 77, the score
    must be saved on the claim. score_skill opens its own SessionLocal()."""
    import ai_client

    calls = []

    class _Resp:
        def json(self):
            return {"score": 77}

    def fake_worker(method, path, **kw):
        calls.append(path)
        return _Resp()

    monkeypatch.setattr(ai_client, "_worker_request", fake_worker)
    skill = _claim(campus.a.student, "Docker")
    stored = sql("SELECT ai_score FROM skills WHERE id = :i", {"i": skill["id"]},
                 tenant=campus.a.tenant)
    if calls == ["/score/proof"] and stored == [{"ai_score": 77}]:
        return
    # Evidence for the failure (read-only): can the job's own session see the
    # row, and does a direct call reach the worker?
    import app.core.db as appdb
    from app.models import Skill

    s = appdb.SessionLocal()
    try:
        visible_to_job_session = s.get(Skill, skill["id"]) is not None
    finally:
        s.close()
    before = len(calls)
    ai_client.score_skill(skill["id"])
    pytest.fail(
        f"worker calls via API={calls[:before]}; stored ai_score={stored}; "
        f"row visible to a fresh SessionLocal (no tenant)={visible_to_job_session}; "
        f"direct score_skill() reached worker={len(calls) > before}")


# ==========================================================================
# Flow 3 - attendance
# ==========================================================================
def test_flow3_teacher_marks_and_students_see_only_their_own(campus):
    a = campus.a
    day = str(date(2031, 3, 3))
    r = a.teacher.post("/v1/attendance/mark", json={
        "section_id": a.section, "date": day,
        "records": [{"student_id": a.student.id, "status": "present"},
                    {"student_id": a.student2.id, "status": "absent"}]})
    assert r.status_code == 200 and len(r.json()) == 2, _d(r)
    for s, want in ((a.student, "present"), (a.student2, "absent")):
        mine = s.get("/v1/attendance/me")
        assert mine.status_code == 200
        rows = [x for x in mine.json() if x["date"] == day]
        assert [(x["student_id"], x["status"]) for x in rows] == [(s.id, want)]
    # Re-marking is idempotent (update, not duplicate).
    r2 = a.teacher.post("/v1/attendance/mark", json={
        "section_id": a.section, "date": day,
        "records": [{"student_id": a.student.id, "status": "late"}]})
    assert r2.status_code == 200
    rows = [x for x in a.student.get("/v1/attendance/me").json() if x["date"] == day]
    assert [x["status"] for x in rows] == ["late"]


def test_flow3_attendance_permissions_and_cross_tenant_denials(campus, count_rows):
    a, b = campus.a, campus.b
    payload = {"section_id": a.section, "date": str(date(2031, 3, 4)),
               "records": [{"student_id": a.student.id, "status": "present"}]}
    for who, code in ((a.student, 403), (a.tpo, 403), (b.teacher, 404)):
        r = who.post("/v1/attendance/mark", json=payload)
        assert r.status_code == code, (who.role, _d(r))
    assert count_rows(a.tenant, "attendance_records", "date = :d", {"d": date(2031, 3, 4)}) == 0
    ok = a.teacher.post("/v1/attendance/mark", json=payload)
    assert ok.status_code == 200
    assert all(x["student_id"] == b.student.id for x in b.student.get("/v1/attendance/me").json())
    assert b.student.get("/v1/attendance/me").json() == []


# ==========================================================================
# Flow 4 - assignment create -> submit -> grade
# ==========================================================================
def _assignment(teacher, section, max_marks=50):
    due = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    r = teacher.post("/v1/assignments", json={"section_id": section, "title": "Synthetic HW",
                                              "due_date": due, "max_marks": max_marks})
    assert r.status_code == 201, _d(r)
    return r.json()


def test_flow4_assignment_create_submit_grade(campus):
    a = campus.a
    asg = _assignment(a.teacher, a.section)
    listed = {x["id"]: x for x in a.student.get("/v1/assignments/me").json()}
    assert asg["id"] in listed and listed[asg["id"]]["submitted"] is False
    sub = a.student.post(f"/v1/assignments/{asg['id']}/submit", json={"content": "my work"})
    assert sub.status_code == 200 and sub.json()["status"] == "submitted", _d(sub)
    subs = a.teacher.get(f"/v1/assignments/{asg['id']}/submissions")
    assert subs.status_code == 200 and [s["id"] for s in subs.json()] == [sub.json()["id"]]
    g = a.teacher.patch(f"/v1/assignments/submissions/{sub.json()['id']}/grade",
                        json={"marks": 42, "feedback": "good"})
    assert g.status_code == 200 and g.json()["status"] == "graded", _d(g)
    mine = a.student.get(f"/v1/assignments/{asg['id']}/my-submission").json()
    assert mine["status"] == "graded" and mine["marks"] == 42
    listed = {x["id"]: x for x in a.student.get("/v1/assignments/me").json()}
    assert listed[asg["id"]]["marks"] == 42 and listed[asg["id"]]["submission_status"] == "graded"
    notes = a.student.get("/v1/notifications")
    assert notes.status_code == 200
    assert any("graded" in (n.get("title") or "").lower() for n in notes.json())


def test_flow4_assignment_permissions_and_cross_tenant_denials(campus, count_rows):
    a, b = campus.a, campus.b
    asg = _assignment(a.teacher, a.section, max_marks=10)
    sub = a.student.post(f"/v1/assignments/{asg['id']}/submit", json={"content": "x"}).json()
    url = f"/v1/assignments/submissions/{sub['id']}/grade"
    for who, code in ((a.student, 403), (a.tpo, 403), (b.teacher, 404)):
        r = who.patch(url, json={"marks": 5})
        assert r.status_code == code, (who.role, _d(r))
    assert a.teacher.patch(url, json={"marks": 11}).status_code == 400  # > max
    assert b.teacher.get(f"/v1/assignments/{asg['id']}/submissions").status_code == 404
    assert b.student.post(f"/v1/assignments/{asg['id']}/submit", json={"content": "x"}).status_code == 404
    assert a.student.post("/v1/assignments", json={
        "section_id": a.section, "title": "x",
        "due_date": "2031-01-01T00:00:00Z"}).status_code == 403
    # Same-tenant student of ANOTHER section cannot submit.
    sec2 = a.admin.post(f"/v1/admin/departments/{a.dept}/sections", json={"name": "S2"}).json()["id"]
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/section",
                         json={"section_id": sec2}).status_code == 200
    r = a.student2.post(f"/v1/assignments/{asg['id']}/submit", json={"content": "x"})
    assert r.status_code == 403, _d(r)
    assert count_rows(a.tenant, "submissions", "assignment_id = :i", {"i": asg["id"]}) == 1
    assert a.student.get(f"/v1/assignments/{asg['id']}/my-submission").json()["status"] == "submitted"



# ==========================================================================
# Phase 5F regression tests
# ==========================================================================
# --- Platform routes (both the /v1 and the deprecated unversioned alias) ----
def test_platform_routes_refuse_every_institute_role_on_both_aliases(campus, sql):
    a = campus.a
    slug = f"{MARK}-{campus.run}-rogue"
    for prefix in ("/v1", ""):
        for who in (a.admin, a.tpo, a.teacher, a.student):
            lst = who.get(f"{prefix}/tenants")
            mk = who.post(f"{prefix}/tenants", json={"name": "Rogue", "slug": slug})
            assert lst.status_code == 403 and mk.status_code == 403, (
                prefix, who.role, _d(lst), _d(mk))
        anon = campus.new_client()
        assert anon.get(f"{prefix}/tenants").status_code == 401
    assert sql("SELECT count(*) AS n FROM tenants WHERE slug = :s", {"s": slug}) == [{"n": 0}]


def test_public_institute_signup_still_works_and_rejects_duplicate_slug(campus, sql):
    c = campus.new_client()
    body = {"institution_name": "Synthetic C", "institution_slug": campus.a.slug,
            "admin_full_name": "x", "admin_email": f"dup-{campus.run}@{EMAIL_DOMAIN}",
            "admin_password": secrets.token_urlsafe(12)}
    assert c.post("/v1/tenants/signup", json=body).status_code == 409
    # (Successful signup of A and B is exercised by the campus fixture itself.)
    assert sql("SELECT count(*) AS n FROM users WHERE email = :e",
               {"e": body["admin_email"]}) == [{"n": 0}]


# --- Invite role policy -------------------------------------------------------
@pytest.mark.parametrize("inviter,role,allowed", [
    ("admin", "admin", True), ("admin", "tpo", True), ("admin", "teacher", True),
    ("admin", "student", True), ("admin", "recruiter", False),
    ("tpo", "teacher", True), ("tpo", "student", True), ("tpo", "admin", False),
    ("tpo", "tpo", False), ("tpo", "recruiter", False),
])
def test_invite_role_policy(campus, sql, inviter, role, allowed):
    actor = getattr(campus.a, inviter)
    email = f"pol-{inviter}-{role}-{campus.run}@{EMAIL_DOMAIN}"
    for prefix in ("/v1", ""):  # legacy alias enforces the same policy
        r = actor.post(f"{prefix}/tenants/invites", json={"email": email, "role": role})
        if allowed:
            assert r.status_code == 201, (prefix, _d(r))
            break  # one invite per email; legacy path covered by refusals
        assert r.status_code == 403, (prefix, _d(r))
    n = sql("SELECT count(*) AS n FROM tenant_invites WHERE email = :e", {"e": email})[0]["n"]
    assert n == (1 if allowed else 0)


def test_admin_invited_admin_lands_as_admin_in_same_tenant(campus, sql):
    email = f"newadmin-{campus.run}@{EMAIL_DOMAIN}"
    inv = campus.a.admin.post("/v1/tenants/invites", json={"email": email, "role": "admin"})
    assert inv.status_code == 201
    pw = secrets.token_urlsafe(14)
    acc = campus.new_client().post("/v1/tenants/accept-invite", json={
        "token": inv.json()["token"], "full_name": "New Admin", "password": pw})
    assert acc.status_code == 200, _d(acc)
    new_admin = campus.login(email, pw, "new-admin")
    assert new_admin.role == "admin"
    assert sql("SELECT tenant_id FROM users WHERE email = :e", {"e": email}) == [
        {"tenant_id": campus.a.tenant}]
    # ...and only sees its own institute's invites.
    assert all(f"-b-{campus.run}@" not in i["email"]
               for i in new_admin.get("/v1/tenants/invites?limit=100").json())


# --- Direct admin user paths (create / role change / bulk import) -------------
def test_admin_user_paths_refuse_recruiter_role_but_allow_others(campus, sql):
    a, run = campus.a, campus.run
    for role, code in (("recruiter", 403), ("tpo", 201)):
        r = a.admin.post("/v1/admin/users", json={
            "email": f"cu-{role}-{run}@{EMAIL_DOMAIN}", "full_name": "x",
            "password": secrets.token_urlsafe(10), "role": role})
        assert r.status_code == code, (role, _d(r))
    assert sql("SELECT count(*) AS n FROM users WHERE email = :e",
               {"e": f"cu-recruiter-{run}@{EMAIL_DOMAIN}"}) == [{"n": 0}]
    for prefix in ("/v1", ""):
        r = a.admin.patch(f"{prefix}/admin/users/{a.student2.id}/role", json={"role": "recruiter"})
        assert r.status_code == 403, (prefix, _d(r))
    assert sql("SELECT role::text AS r FROM users WHERE id = :u", {"u": a.student2.id}) == [{"r": "student"}]
    ok = a.admin.patch(f"/v1/admin/users/{a.student2.id}/role", json={"role": "teacher"})
    assert ok.status_code == 200 and ok.json()["role"] == "teacher"


def test_bulk_import_skips_recruiter_rows_and_creates_others(campus, sql):
    a, run = campus.a, campus.run
    pw = secrets.token_urlsafe(10)
    csv = ("email,full_name,role,password\n"
           f"bulk-s-{run}@{EMAIL_DOMAIN},Bulk S,student,{pw}\n"
           f"bulk-r-{run}@{EMAIL_DOMAIN},Bulk R,recruiter,{pw}\n")
    r = a.admin.post("/v1/admin/users/bulk-import",
                     files={"file": ("users.csv", csv.encode(), "text/csv")})
    assert r.status_code in (200, 201), _d(r)
    status_by_email = {row["email"]: row["status"] for row in r.json()["results"]}
    assert status_by_email == {f"bulk-s-{run}@{EMAIL_DOMAIN}": "created",
                               f"bulk-r-{run}@{EMAIL_DOMAIN}": "skipped"}
    rows = sql("SELECT email, role::text AS r, tenant_id FROM users WHERE email LIKE :p",
               {"p": f"bulk-%-{run}@{EMAIL_DOMAIN}"})
    assert rows == [{"email": f"bulk-s-{run}@{EMAIL_DOMAIN}", "r": "student", "tenant_id": a.tenant}]


def test_non_admins_cannot_use_admin_user_paths(campus):
    a = campus.a
    for who in (a.tpo, a.teacher, a.student):
        r = who.post("/v1/admin/users", json={"email": f"esc-{who.role}-{campus.run}@{EMAIL_DOMAIN}",
                                              "full_name": "x", "password": "abcdef12", "role": "admin"})
        assert r.status_code == 403, (who.role, _d(r))
        r = who.patch(f"/v1/admin/users/{who.id}/role", json={"role": "admin"})
        assert r.status_code == 403, (who.role, _d(r))


# --- Background scoring jobs: tenant context ---------------------------------
@pytest.fixture
def fake_worker(monkeypatch):
    import ai_client

    calls: list[str] = []

    class _Resp:
        def json(self):
            return {"score": 77}

    def worker(method, path, **kw):
        calls.append(path)
        return _Resp()

    monkeypatch.setattr(ai_client, "_worker_request", worker)
    return calls


@pytest.fixture
def set_config_capture():
    """Record every tenant GUC write on the app engine (statement + params)."""
    import app.core.db as appdb
    from sqlalchemy import event

    seen: list[tuple[str, object]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "app.current_tenant_id" in statement and "set_config" in statement:
            seen.append((statement, parameters))

    event.listen(appdb.engine, "before_cursor_execute", capture)
    yield seen
    event.remove(appdb.engine, "before_cursor_execute", capture)


def _skill_row(sql, tenant, skill_id):
    return sql("SELECT ai_score FROM skills WHERE id = :i", {"i": skill_id}, tenant=tenant)


@pytest.mark.parametrize("bad", [None, 0, -1, True, "1"])
def test_score_skill_job_fails_closed_without_valid_tenant(campus, sql, fake_worker, bad):
    import ai_client

    skill = _claim(campus.a.student, f"NoTenant{bad!s}")
    fake_worker.clear()
    sql("UPDATE skills SET ai_score = NULL WHERE id = :i", {"i": skill["id"]}, tenant=campus.a.tenant)
    ai_client.score_skill(skill["id"], tenant_id=bad)
    assert fake_worker == [] and _skill_row(sql, campus.a.tenant, skill["id"]) == [{"ai_score": None}]


def test_score_skill_job_refuses_mismatched_tenant(campus, sql, fake_worker):
    import ai_client

    skill = _claim(campus.a.student, "Mismatch")
    fake_worker.clear()
    sql("UPDATE skills SET ai_score = NULL WHERE id = :i", {"i": skill["id"]}, tenant=campus.a.tenant)
    ai_client.score_skill(skill["id"], tenant_id=campus.b.tenant)  # B cannot see/modify A's row
    assert fake_worker == [] and _skill_row(sql, campus.a.tenant, skill["id"]) == [{"ai_score": None}]


def test_scoring_jobs_stamp_own_tenant_on_every_transaction(campus, sql, fake_worker,
                                                            set_config_capture):
    """A then B jobs run back to back on the shared pool (outside any request):
    each persists only its own row, every GUC write in the job is local and
    carries that job's tenant, and no pooled connection keeps a tenant."""
    import ai_client
    import app.core.db as appdb

    sa = _claim(campus.a.student, "JobA")
    sb = _claim(campus.b.student, "JobB")
    for t, s in ((campus.a, sa), (campus.b, sb)):
        sql("UPDATE skills SET ai_score = NULL WHERE id = :i", {"i": s["id"]}, tenant=t.tenant)
    for t, s in ((campus.a, sa), (campus.b, sb)):
        set_config_capture.clear()
        ai_client.score_skill(s["id"], tenant_id=t.tenant)
        assert set_config_capture, "job never set a tenant"
        for stmt, params in set_config_capture:
            assert "true)" in stmt.replace(" ", "") and params == {"tid": str(t.tenant)}
    assert _skill_row(sql, campus.a.tenant, sa["id"]) == [{"ai_score": 77}]
    assert _skill_row(sql, campus.b.tenant, sb["id"]) == [{"ai_score": 77}]
    conns = [appdb.engine.raw_connection() for _ in range(max(appdb.engine.pool.checkedin(), 1))]
    try:
        for c in conns:
            cur = c.cursor()
            cur.execute("SELECT app.current_tenant_id()")
            assert cur.fetchone()[0] is None
    finally:
        for c in conns:
            c.close()


def test_project_member_scoring_persists_with_mocked_worker(campus, sql, fake_worker):
    a = campus.a
    r = a.student.post("/v1/projects", json={
        "title": "Synthetic Project", "repo_url": "https://example.com/repo",
        "is_group": True, "members": [{"student_id": a.student2.id, "contribution": "API"}]})
    assert r.status_code == 201, _d(r)
    rows = sql("SELECT student_id, ai_score FROM project_members WHERE project_id = :p "
               "ORDER BY student_id", {"p": r.json()["id"]}, tenant=a.tenant)
    assert fake_worker.count("/score/proof") == 2
    assert [x["ai_score"] for x in rows] == [77, 77]


# --- Queue (Redis/RQ) path: isolated throwaway Redis only ---------------------
@pytest.fixture
def rq_queue(monkeypatch):
    import os

    url = os.environ.get("RQ_TEST_REDIS_URL", "")
    if not url.startswith("redis://"):
        pytest.skip("RQ_TEST_REDIS_URL not set: queue path PENDING (no isolated Redis)")
    from redis import Redis
    from rq import Queue

    import jobs

    conn = Redis.from_url(url)
    conn.flushdb()  # throwaway test Redis only
    q = Queue(f"zz-api-iso-{uuid.uuid4().hex[:8]}", connection=conn)
    monkeypatch.setattr(jobs, "get_queue", lambda: q)
    yield q
    q.empty()
    conn.flushdb()


def test_queue_path_job_carries_server_tenant_and_persists(campus, sql, fake_worker, rq_queue):
    from rq import SimpleWorker

    # Client tries to smuggle a tenant id in the body; it must not matter.
    r = campus.a.student.post("/v1/skills", json={"name": "Queued", "tenant_id": campus.b.tenant})
    assert r.status_code == 201, _d(r)
    sid = r.json()["id"]
    jobs_ = rq_queue.jobs
    assert len(jobs_) == 1 and fake_worker == []  # queued, not run inline
    assert jobs_[0].args == (sid,) and jobs_[0].kwargs == {"tenant_id": campus.a.tenant}
    SimpleWorker([rq_queue], connection=rq_queue.connection).work(burst=True)
    assert fake_worker == ["/score/proof"]
    assert _skill_row(sql, campus.a.tenant, sid) == [{"ai_score": 77}]


def test_queue_path_mismatched_or_missing_tenant_job_does_nothing(campus, sql, fake_worker, rq_queue):
    from rq import SimpleWorker

    import ai_client

    skill = _claim(campus.a.student, "QueuedBad")
    rq_queue.empty()
    fake_worker.clear()
    sql("UPDATE skills SET ai_score = NULL WHERE id = :i", {"i": skill["id"]}, tenant=campus.a.tenant)
    rq_queue.enqueue(ai_client.score_skill, skill["id"], tenant_id=campus.b.tenant)
    rq_queue.enqueue(ai_client.score_skill, skill["id"])  # legacy-shaped job
    SimpleWorker([rq_queue], connection=rq_queue.connection).work(burst=True)
    assert fake_worker == []
    assert _skill_row(sql, campus.a.tenant, skill["id"]) == [{"ai_score": None}]
