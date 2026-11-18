"""Student self-registration approval on real Postgres (RLS, NOBYPASSRLS role).

Mirrors tests/test_student_approval.py against the real database: a
self-registered student is created INACTIVE and ``approval_pending`` in the
institute named by the slug, cannot sign in or use a token, and only an admin
of that same institute can approve it (audited). Approval never re-enables a
disabled student, and inactive students are kept off staff rosters and new
attendance while historical attendance stays intact. All data is synthetic
and removed by the ``campus`` fixture.
"""

from __future__ import annotations

import secrets
from datetime import date

from flowkit import EMAIL_DOMAIN, _d


def _register(campus, slug, label, **extra):
    email = f"{label}-{campus.run}@{EMAIL_DOMAIN}"
    pw = secrets.token_urlsafe(16)
    r = campus.new_client().post("/v1/auth/register", json={
        "email": email, "full_name": f"Self {label}", "password": pw,
        "tenant_slug": slug, **extra})
    assert r.status_code == 201, _d(r)
    return r.json(), email, pw


def _login_status(campus, email, pw):
    return campus.new_client().post(
        "/v1/auth/login", data={"username": email, "password": pw}).status_code


def test_pg_pending_student_cannot_sign_in_or_use_a_token(campus, sql):
    from app.core.security import create_access_token

    a, b = campus.a, campus.b
    body, email, pw = _register(campus, a.slug, "pend-login", role="admin", is_active=True,
                                approval_pending=False, tenant_id=b.tenant)
    assert (body["role"], body["is_active"], body["approval_pending"]) == ("student", False, True), _d(body)
    assert sql("SELECT tenant_id, role::text AS r, is_active, approval_pending FROM users WHERE id = :i",
               {"i": body["id"]}) == [{"tenant_id": a.tenant, "r": "student",
                                       "is_active": False, "approval_pending": True}]

    c = campus.new_client()
    assert c.post("/v1/auth/login", data={"username": email, "password": pw}).status_code == 401
    assert "access_token" not in c.cookies and "refresh_token" not in c.cookies
    assert sql("SELECT count(*) AS n FROM refresh_tokens WHERE user_id = :i",
               {"i": body["id"]}, tenant=a.tenant) == [{"n": 0}]

    bearer = {"Authorization": f"Bearer {create_access_token(subject=str(body['id']))}"}
    for path in ("/v1/auth/me", "/v1/announcements", "/v1/skills/me", "/v1/timetable/me"):
        r = campus.new_client().get(path, headers=bearer)
        assert r.status_code == 401, (path, _d(r))


def test_pg_only_same_institute_admin_can_approve_and_it_is_audited(campus, sql):
    a, b = campus.a, campus.b
    body, email, pw = _register(campus, a.slug, "pend-approve")
    uid = body["id"]

    for who, code in ((a.teacher, 403), (a.tpo, 403), (a.student, 403), (b.admin, 404)):
        r = who.post(f"/v1/admin/users/{uid}/approve")
        assert r.status_code == code, (who.role, _d(r))
    # No other status path can activate or promote it.
    assert a.admin.patch(f"/v1/admin/users/{uid}/status", json={"is_active": True}).status_code == 409
    assert a.admin.patch(f"/v1/admin/users/{uid}/role", json={"role": "admin"}).status_code == 409
    assert b.admin.get("/v1/admin/users?approval_pending=true").json() == []
    assert {u["email"] for u in a.admin.get("/v1/admin/users?approval_pending=true").json()} == {email}
    assert _login_status(campus, email, pw) == 401

    ok = a.admin.post(f"/v1/admin/users/{uid}/approve")
    assert ok.status_code == 200, _d(ok)
    assert (ok.json()["is_active"], ok.json()["approval_pending"]) == (True, False)
    assert a.admin.post(f"/v1/admin/users/{uid}/approve").status_code == 409  # not pending any more
    me = campus.login(email, pw, "approved-student")
    assert me.role == "student"

    audit = sql("SELECT action, actor_id, tenant_id FROM audit_logs WHERE target_type = 'user' "
                "AND target_id = :t ORDER BY id", {"t": str(uid)}, tenant=a.tenant)
    assert audit == [{"action": "user.registration_approved", "actor_id": a.admin.id,
                      "tenant_id": a.tenant}], audit


def test_pg_approve_never_re_enables_a_disabled_student(campus, sql):
    a = campus.a
    off = a.admin.patch(f"/v1/admin/users/{a.student2.id}/status", json={"is_active": False})
    assert off.status_code == 200 and off.json()["approval_pending"] is False, _d(off)
    assert a.student2.id not in {u["id"] for u in a.admin.get("/v1/admin/users?approval_pending=true").json()}
    assert a.admin.post(f"/v1/admin/users/{a.student2.id}/approve").status_code == 409
    assert sql("SELECT is_active FROM users WHERE id = :i", {"i": a.student2.id}) == [{"is_active": False}]
    assert _login_status(campus, a.student2.email, a.student2.password) == 401

    # Declining a request turns it into an ordinary disabled account (audited).
    body, email, pw = _register(campus, a.slug, "pend-decline")
    dec = a.admin.patch(f"/v1/admin/users/{body['id']}/status", json={"is_active": False})
    assert dec.status_code == 200 and (dec.json()["is_active"], dec.json()["approval_pending"]) == (False, False)
    assert a.admin.post(f"/v1/admin/users/{body['id']}/approve").status_code == 409
    assert sql("SELECT action FROM audit_logs WHERE target_type = 'user' AND target_id = :t",
               {"t": str(body["id"])}, tenant=a.tenant) == [{"action": "user.registration_declined"}]


def test_pg_inactive_students_off_rosters_and_new_attendance(campus, sql):
    a = campus.a
    body, email, pw = _register(campus, a.slug, "pend-roster")
    pid = body["id"]
    assert a.admin.patch(f"/v1/admin/users/{pid}/section", json={"section_id": a.section}).status_code == 200

    past = "2026-01-05"
    r = a.teacher.post("/v1/attendance/mark", json={"section_id": a.section, "date": past,
                       "records": [{"student_id": a.student2.id, "status": "present"}]})
    assert r.status_code == 200, _d(r)
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/status", json={"is_active": False}).status_code == 200

    for who in (a.teacher, a.tpo):
        allstu = {u["id"] for u in who.get("/v1/people/students").json()}
        roster = {u["id"] for u in who.get(f"/v1/people/students?section_id={a.section}").json()}
        assert pid not in allstu and a.student2.id not in allstu, who.role
        assert roster == {a.student.id}, (who.role, roster)

    today = str(date.today())
    for sid in (pid, a.student2.id):
        r = a.teacher.post("/v1/attendance/mark", json={"section_id": a.section, "date": today,
                           "records": [{"student_id": sid, "status": "present"}]})
        assert r.status_code == 400, (sid, _d(r))
    assert sql("SELECT count(*) AS n FROM attendance_records WHERE student_id IN (:p, :d) AND date = :t",
               {"p": pid, "d": a.student2.id, "t": today}, tenant=a.tenant) == [{"n": 0}]

    # Historical attendance of the disabled student is unchanged and still listed.
    hist = a.teacher.get(f"/v1/attendance/section/{a.section}?date={past}")
    assert hist.status_code == 200, _d(hist)
    assert [(x["student_id"], x["status"]) for x in hist.json()] == [(a.student2.id, "present")]
