"""Student self-registration requires approval by an admin of the SAME institute.

POST /auth/register creates an INACTIVE student marked ``approval_pending``.
Until an admin of that institute approves it via POST /admin/users/{id}/approve
the account cannot log in, use an access token, or refresh a session. The
generic status toggle cannot activate a pending request, and approval can never
re-enable an account an admin disabled. Inactive students (pending or
disabled) are hidden from staff rosters and cannot receive new attendance;
historical attendance is untouched. There is no email verification.
"""

from datetime import date

import app.models as models
from app.core.security import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    create_access_token,
    issue_refresh_token,
)

PW = "secret123"
AUTH_COOKIES = {ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME}


def _register(client, email, slug, **extra):
    return client.post(
        "/auth/register",
        json={
            "email": email,
            "full_name": "Self Registered",
            "password": PW,
            "tenant_slug": slug,
            **extra,
        },
    )


def _set_cookie_names(resp):
    return {h.split("=", 1)[0].strip() for h in resp.headers.get_list("set-cookie")}


def _login_status(client, email, password=PW):
    return client.post(
        "/auth/login", data={"username": email, "password": password}
    ).status_code


def _audit_actions(session, target_id):
    return [
        a.action
        for a in session.query(models.AuditLog)
        .filter_by(target_type="user", target_id=str(target_id))
        .order_by(models.AuditLog.id)
    ]


# --- registration -----------------------------------------------------------
def test_self_registered_student_is_pending_and_cannot_log_in(client, make_tenant):
    make_tenant(slug="acme")
    reg = _register(client, "pending@test.dev", "acme")
    assert reg.status_code == 201, reg.text
    body = reg.json()
    assert body["role"] == "student"
    assert body["is_active"] is False
    assert body["approval_pending"] is True
    assert not _set_cookie_names(reg) & AUTH_COOKIES  # no session started

    login = client.post(
        "/auth/login", data={"username": "pending@test.dev", "password": PW}
    )
    assert login.status_code == 401
    assert not _set_cookie_names(login) & AUTH_COOKIES
    wrong = client.post(
        "/auth/login", data={"username": "pending@test.dev", "password": "nope-nope"}
    )
    # Same generic answer as a wrong password: pending state is not revealed.
    assert login.json()["detail"] == wrong.json()["detail"]


def test_register_cannot_choose_role_status_or_institute(client, make_tenant, session):
    acme = make_tenant(slug="acme")
    other = make_tenant(slug="other", name="Other Institute")
    reg = _register(
        client, "sneaky@test.dev", "acme",
        role="admin", is_active=True, approval_pending=False, tenant_id=other.id,
    )
    assert reg.status_code == 201, reg.text
    row = session.query(models.User).filter_by(email="sneaky@test.dev").one()
    assert (row.role, row.is_active, row.approval_pending, row.tenant_id) == (
        models.UserRole.student, False, True, acme.id,
    )


def test_pending_user_cannot_use_access_token_or_refresh(client, make_tenant, session):
    make_tenant(slug="acme")
    uid = _register(client, "tok@test.dev", "acme").json()["id"]
    bearer = {"Authorization": f"Bearer {create_access_token(subject=str(uid))}"}
    assert client.get("/auth/me", headers=bearer).status_code == 401
    assert client.get("/announcements", headers=bearer).status_code == 401

    raw = issue_refresh_token(session, session.get(models.User, uid))
    session.commit()
    refreshed = client.post("/auth/refresh", json={"refresh_token": raw})
    assert refreshed.status_code == 401
    assert not _set_cookie_names(refreshed) & AUTH_COOKIES


def test_existing_accounts_are_not_pending(make_user, make_tenant, client, token_header):
    """Accounts created any other way default to approval_pending=False."""
    acme = make_tenant(slug="acme")
    make_user("adm@test.dev", role=models.UserRole.admin, tenant=acme)
    made = client.post(
        "/admin/users",
        json={"email": "made@test.dev", "full_name": "Made", "password": PW, "role": "student"},
        headers=token_header("adm@test.dev"),
    )
    assert made.status_code == 201, made.text
    assert made.json()["approval_pending"] is False
    assert made.json()["is_active"] is True


# --- approval ---------------------------------------------------------------
def test_only_an_admin_of_the_same_institute_can_approve(
    client, make_tenant, make_user, token_header, session
):
    acme = make_tenant(slug="acme")
    other = make_tenant(slug="other", name="Other Institute")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    make_user("acme.teacher@test.dev", role=models.UserRole.teacher, tenant=acme)
    make_user("acme.tpo@test.dev", role=models.UserRole.tpo, tenant=acme)
    make_user("acme.student@test.dev", role=models.UserRole.student, tenant=acme)
    make_user("other.admin@test.dev", role=models.UserRole.admin, tenant=other)
    uid = _register(client, "joiner@test.dev", "acme").json()["id"]

    for who, code in (
        ("acme.teacher@test.dev", 403),
        ("acme.tpo@test.dev", 403),
        ("acme.student@test.dev", 403),
        ("other.admin@test.dev", 404),  # another institute cannot even see it
    ):
        resp = client.post(f"/admin/users/{uid}/approve", headers=token_header(who))
        assert resp.status_code == code, (who, resp.text)
    assert _login_status(client, "joiner@test.dev") == 401  # still pending
    other_list = client.get(
        "/admin/users?approval_pending=true", headers=token_header("other.admin@test.dev")
    )
    assert other_list.json() == []

    ok = client.post(
        f"/admin/users/{uid}/approve", headers=token_header("acme.admin@test.dev")
    )
    assert ok.status_code == 200, ok.text
    assert (ok.json()["is_active"], ok.json()["approval_pending"], ok.json()["role"]) == (
        True, False, "student",
    )
    assert _audit_actions(session, uid) == ["user.registration_approved"]
    me = client.get("/auth/me", headers=token_header("joiner@test.dev", PW))
    assert me.status_code == 200 and me.json()["role"] == "student"

    # Approving twice is refused: there is no longer a pending request.
    again = client.post(
        f"/admin/users/{uid}/approve", headers=token_header("acme.admin@test.dev")
    )
    assert again.status_code == 409


def test_pending_request_cannot_be_activated_or_promoted_via_other_endpoints(
    client, make_tenant, make_user, token_header, session
):
    acme = make_tenant(slug="acme")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    uid = _register(client, "joiner@test.dev", "acme").json()["id"]
    admin = token_header("acme.admin@test.dev")

    st = client.patch(f"/admin/users/{uid}/status", json={"is_active": True}, headers=admin)
    assert st.status_code == 409, st.text
    role = client.patch(f"/admin/users/{uid}/role", json={"role": "admin"}, headers=admin)
    assert role.status_code == 409, role.text

    row = session.get(models.User, uid)
    session.refresh(row)
    assert (row.is_active, row.approval_pending, row.role) == (
        False, True, models.UserRole.student,
    )
    assert _login_status(client, "joiner@test.dev") == 401
    assert _audit_actions(session, uid) == []


def test_approve_never_re_enables_a_disabled_student(
    client, make_tenant, make_user, token_header, session
):
    acme = make_tenant(slug="acme")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    active = make_user("active@test.dev", role=models.UserRole.student, tenant=acme)
    disabled = make_user(
        "disabled@test.dev", role=models.UserRole.student, tenant=acme, is_active=False
    )
    admin = token_header("acme.admin@test.dev")
    _register(client, "joiner@test.dev", "acme")

    pending = client.get("/admin/users?approval_pending=true", headers=admin).json()
    assert {u["email"] for u in pending} == {"joiner@test.dev"}

    for u in (active, disabled):
        resp = client.post(f"/admin/users/{u.id}/approve", headers=admin)
        assert resp.status_code == 409, resp.text
    session.refresh(disabled)
    assert disabled.is_active is False
    assert _login_status(client, "disabled@test.dev") == 401

    # The ordinary status toggle still works for non-pending accounts.
    en = client.patch(f"/admin/users/{disabled.id}/status", json={"is_active": True}, headers=admin)
    assert en.status_code == 200 and en.json()["is_active"] is True


def test_declining_a_request_makes_it_an_ordinary_disabled_account(
    client, make_tenant, make_user, token_header, session
):
    acme = make_tenant(slug="acme")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    uid = _register(client, "spam@test.dev", "acme").json()["id"]
    admin = token_header("acme.admin@test.dev")

    dec = client.patch(f"/admin/users/{uid}/status", json={"is_active": False}, headers=admin)
    assert dec.status_code == 200, dec.text
    assert (dec.json()["is_active"], dec.json()["approval_pending"]) == (False, False)
    assert client.get("/admin/users?approval_pending=true", headers=admin).json() == []
    assert client.post(f"/admin/users/{uid}/approve", headers=admin).status_code == 409
    assert _login_status(client, "spam@test.dev") == 401
    assert _audit_actions(session, uid) == ["user.registration_declined"]


# --- isolation after approval ------------------------------------------------
def test_approved_student_stays_inside_their_institute(
    client, make_tenant, make_user, token_header
):
    acme = make_tenant(slug="acme")
    other = make_tenant(slug="other", name="Other Institute")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    make_user("other.admin@test.dev", role=models.UserRole.admin, tenant=other)
    other_student = make_user("other.student@test.dev", role=models.UserRole.student, tenant=other)
    for hdr_email, title in (("acme.admin@test.dev", "ACME notice"), ("other.admin@test.dev", "OTHER notice")):
        made = client.post(
            "/announcements",
            json={"title": title, "body": "hello", "audience": "all"},
            headers=token_header(hdr_email),
        )
        assert made.status_code == 201, made.text

    uid = _register(client, "joiner@test.dev", "acme").json()["id"]
    client.post(f"/admin/users/{uid}/approve", headers=token_header("acme.admin@test.dev"))
    hdr = token_header("joiner@test.dev", PW)

    titles = {a["title"] for a in client.get("/announcements", headers=hdr).json()}
    assert "ACME notice" in titles and "OTHER notice" not in titles
    assert client.get("/admin/users", headers=hdr).status_code == 403
    assert client.post(f"/admin/users/{other_student.id}/approve", headers=hdr).status_code == 403


# --- rosters and attendance ------------------------------------------------------
def _section(client, admin_hdr):
    dept = client.post("/admin/departments", json={"name": "CSE", "code": "CSE"}, headers=admin_hdr)
    assert dept.status_code == 201, dept.text
    sec = client.post(f"/admin/departments/{dept.json()['id']}/sections", json={"name": "A"}, headers=admin_hdr)
    assert sec.status_code == 201, sec.text
    return dept.json()["id"], sec.json()["id"]


def test_inactive_students_hidden_from_rosters_and_new_attendance(
    client, make_tenant, make_user, token_header
):
    acme = make_tenant(slug="acme")
    make_user("acme.admin@test.dev", role=models.UserRole.admin, tenant=acme)
    make_user("acme.teacher@test.dev", role=models.UserRole.teacher, tenant=acme)
    make_user("acme.tpo@test.dev", role=models.UserRole.tpo, tenant=acme)
    active = make_user("active@test.dev", role=models.UserRole.student, tenant=acme, full_name="Active")
    to_disable = make_user("later.off@test.dev", role=models.UserRole.student, tenant=acme, full_name="Later Off")
    admin = token_header("acme.admin@test.dev")
    teacher = token_header("acme.teacher@test.dev")
    dept_id, sec = _section(client, admin)
    pid = _register(client, "joiner@test.dev", "acme").json()["id"]
    for uid in (active.id, to_disable.id, pid):  # admin may still place a pending student
        r = client.patch(f"/admin/users/{uid}/section", json={"section_id": sec}, headers=admin)
        assert r.status_code == 200, r.text

    # Historical attendance for the student who will be disabled.
    past = client.post(
        "/attendance/mark",
        json={"section_id": sec, "date": "2026-01-05",
              "records": [{"student_id": to_disable.id, "status": "present"}]},
        headers=teacher,
    )
    assert past.status_code == 200, past.text
    client.patch(f"/admin/users/{to_disable.id}/status", json={"is_active": False}, headers=admin)

    for who in ("acme.teacher@test.dev", "acme.tpo@test.dev"):
        hdr = token_header(who)
        everyone = {u["id"] for u in client.get("/people/students", headers=hdr).json()}
        roster = {u["id"] for u in client.get(f"/people/students?section_id={sec}", headers=hdr).json()}
        assert everyone == {active.id} and roster == {active.id}, who

    today = str(date.today())
    for sid in (pid, to_disable.id):
        r = client.post(
            "/attendance/mark",
            json={"section_id": sec, "date": today, "records": [{"student_id": sid, "status": "present"}]},
            headers=teacher,
        )
        assert r.status_code == 400, r.text
    ok = client.post(
        "/attendance/mark",
        json={"section_id": sec, "date": today, "records": [{"student_id": active.id, "status": "present"}]},
        headers=teacher,
    )
    assert ok.status_code == 200, ok.text

    # Historical record of the now-disabled student is still there, unchanged.
    hist = client.get(f"/attendance/section/{sec}?date=2026-01-05", headers=teacher)
    assert hist.status_code == 200, hist.text
    assert [(r["student_id"], r["status"]) for r in hist.json()] == [(to_disable.id, "present")]

    # Teachers cannot record results for a never-approved request either.
    subj = client.post(
        "/academics/subjects",
        json={"department_id": dept_id, "name": "Maths", "code": "MA101", "credits": 4, "semester": 1},
        headers=teacher,
    )
    assert subj.status_code == 201, subj.text
    for sid, code in ((pid, 404), (active.id, 200)):
        res = client.post(
            "/academics/results",
            json={"student_id": sid, "subject_id": subj.json()["id"], "marks_obtained": 50, "max_marks": 100},
            headers=teacher,
        )
        assert res.status_code == code, (sid, res.text)
