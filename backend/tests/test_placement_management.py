"""Phase 9.1: placement management core v2 (SQLite, offline).

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §5, §7.
Proves:

* flag off / institute not listed / empty list -> every /placement v2 route 404
  and the legacy /drives routes behave as before;
* RBAC per route (student, teacher, tpo, admin, recruiter) and 404 for objects
  of another institute;
* student placement profiles: validation, roll-number uniqueness per
  institute, content-free audit;
* drive drafts, settings, lifecycle transitions (+ is_open sync, audit);
* eligibility v2: legacy parity, each new criterion, constant query count;
* apply v2: lifecycle, window, profile status, one-offer policy, criteria,
  duplicates (409) - and the legacy apply route enforces the same rules;
* application status transitions, compare-and-set, history, audit, notify;
* hardening: placement analytics and section analytics are tenant-filtered,
  legacy duplicate-apply race returns 409.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

import app.core.db as dbmod
import app.models as models
from app.core.config import settings
from app.routers import placement as legacy

P = "/v1/placement"
D = "/v1/drives"


# --- fixtures / helpers ------------------------------------------------------
@pytest.fixture
def on(monkeypatch, make_tenant):
    def _enable(*tenants):
        monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_ENABLED", True)
        monkeypatch.setattr(
            settings, "PLACEMENT_MANAGEMENT_TENANT_IDS", ",".join(str(t.id) for t in tenants)
        )

    return _enable


@pytest.fixture
def world(session, make_user, make_tenant, token_header):
    """Two institutes; A has a department/section, staff and students."""
    a = make_tenant("inst-a", name="Institute A")
    b = make_tenant("inst-b", name="Institute B")
    dept = models.Department(tenant_id=a.id, name="Computer Science", code="CSE")
    dept2 = models.Department(tenant_id=a.id, name="Mechanical", code="ME")
    dept_b = models.Department(tenant_id=b.id, name="B Dept", code="BD")
    session.add_all([dept, dept2, dept_b])
    session.commit()
    sec = models.Section(tenant_id=a.id, name="CSE-A", year=4, department_id=dept.id)
    sec2 = models.Section(tenant_id=a.id, name="ME-A", year=4, department_id=dept2.id)
    session.add_all([sec, sec2])
    session.commit()

    def user(email, role=models.UserRole.student, tenant=a, section=None, name="Test User"):
        u = make_user(email, role=role, tenant=tenant, full_name=name)
        if section is not None:
            u.section_id = section.id
            session.commit()
        return u, token_header(email)

    s1, h_s1 = user("s1@a.edu", section=sec, name="Asha")
    s2, h_s2 = user("s2@a.edu", section=sec2, name="Bala")
    tpo, h_tpo = user("tpo@a.edu", models.UserRole.tpo)
    admin, h_admin = user("admin@a.edu", models.UserRole.admin)
    teacher, h_teacher = user("teacher@a.edu", models.UserRole.teacher)
    rec, h_rec = user("rec@a.edu", models.UserRole.recruiter)
    sb, h_sb = user("sb@b.edu", tenant=b)
    tpo_b, h_tpo_b = user("tpo@b.edu", models.UserRole.tpo, tenant=b)
    return dict(
        a=a, b=b, dept=dept, dept2=dept2, dept_b=dept_b, sec=sec, sec2=sec2,
        s1=s1, s2=s2, tpo=tpo, admin=admin, teacher=teacher, rec=rec, sb=sb, tpo_b=tpo_b,
        h_s1=h_s1, h_s2=h_s2, h_tpo=h_tpo, h_admin=h_admin, h_teacher=h_teacher,
        h_rec=h_rec, h_sb=h_sb, h_tpo_b=h_tpo_b,
    )


def mk_drive(session, tenant, **kw):
    fields = dict(company_name="Acme", role_title="SDE", is_open=True)
    fields.update(kw)
    d = models.Drive(tenant_id=tenant.id, **fields)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def grades(session, student, dept, items):
    """items: [(grade_point, credits)] -> results rows."""
    for i, (gp, credits) in enumerate(items):
        subj = models.Subject(
            tenant_id=student.tenant_id, name=f"S{student.id}-{i}", code=f"C{student.id}{i}",
            credits=credits, semester=1, department_id=dept.id,
        )
        session.add(subj)
        session.flush()
        session.add(models.Result(
            student_id=student.id, tenant_id=student.tenant_id, subject_id=subj.id,
            marks_obtained=gp * 10, max_marks=100, grade_point=gp,
        ))
    session.commit()


def attendance(session, student, section, present, absent, late=0):
    day = date(2026, 1, 1)
    rows = (
        [models.AttendanceStatus.present] * present
        + [models.AttendanceStatus.absent] * absent
        + [models.AttendanceStatus.late] * late
    )
    for st in rows:
        session.add(models.AttendanceRecord(
            tenant_id=student.tenant_id, student_id=student.id, section_id=section.id, date=day, status=st,
        ))
        day += timedelta(days=1)
    session.commit()


def verified_skill(session, student, name):
    session.add(models.Skill(
        student_id=student.id, tenant_id=student.tenant_id, name=name, status=models.SkillStatus.verified,
    ))
    session.commit()


def profile_body(**kw):
    body = dict(roll_number="CSE/21/001", graduation_year=2027, tenth_percent=88.5,
                twelfth_percent=82.0, active_backlogs=0)
    body.update(kw)
    return body


def put_profile(client, h, student, **kw):
    r = client.put(f"{P}/students/{student.id}/profile", json=profile_body(**kw), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def put_settings(client, h, drive, **kw):
    r = client.put(f"{P}/drives/{drive.id}/settings", json=kw, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def iso(dt):
    return dt.isoformat()


def audit_rows(session, action):
    session.expire_all()
    return list(session.scalars(select(models.AuditLog).where(models.AuditLog.action == action)))


# --- gate ----------------------------------------------------------------------
NEW_ROUTES = [
    ("get", "/students"), ("get", "/students/1"), ("put", "/students/1/profile"), ("get", "/me/profile"),
    ("post", "/drives"), ("put", "/drives/1"), ("get", "/drives/1/settings"), ("put", "/drives/1/settings"),
    ("post", "/drives/1/lifecycle"), ("get", "/drives/1/eligibility"), ("get", "/drives/1/my-eligibility"),
    ("post", "/drives/1/apply"), ("patch", "/applications/1/status"), ("get", "/applications/1/history"),
]


@pytest.mark.parametrize("flag,ids", [(False, "A"), (True, ""), (True, "B"), (True, "junk")])
def test_gate_closed_means_404_everywhere(client, world, monkeypatch, flag, ids):
    lookup = {"A": str(world["a"].id), "B": str(world["b"].id), "": "", "junk": "x,y"}
    monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_ENABLED", flag)
    monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_TENANT_IDS", lookup[ids])
    for who in ("h_tpo", "h_s1", "h_admin"):
        for method, path in NEW_ROUTES:
            r = getattr(client, method)(f"{P}{path}", headers=world[who], **({"json": {}} if method != "get" else {}))
            assert r.status_code == 404, (who, method, path, r.status_code)


def test_gate_open_only_for_listed_institute_and_unversioned_alias(client, world, on):
    on(world["a"])
    assert client.get(f"{P}/students", headers=world["h_tpo"]).status_code == 200
    assert client.get("/placement/students", headers=world["h_tpo"]).status_code == 200
    assert client.get(f"{P}/students", headers=world["h_tpo_b"]).status_code == 404


def test_recruiter_forbidden_and_teacher_forbidden(client, world, on):
    on(world["a"])
    for method, path in NEW_ROUTES:
        r = getattr(client, method)(f"{P}{path}", headers=world["h_rec"], **({"json": {}} if method != "get" else {}))
        assert r.status_code == 403, (method, path)
    for path in ("/students", "/students/1", "/drives/1/settings", "/drives/1/eligibility"):
        assert client.get(f"{P}{path}", headers=world["h_teacher"]).status_code == 403


# --- student profiles ----------------------------------------------------------
def test_profile_crud_validation_and_audit(client, world, on, session):
    on(world["a"])
    s1 = world["s1"]
    r = client.get(f"{P}/students/{s1.id}", headers=world["h_tpo"])
    assert r.status_code == 200
    assert r.json()["profile"]["recorded"] is False
    assert r.json()["department_name"] == "Computer Science"

    out = put_profile(client, world["h_tpo"], s1)
    assert out["recorded"] and out["roll_number"] == "CSE/21/001" and out["placement_status"] == "active"
    # admin may also write; a partial body clears omitted fields (full replacement)
    out = put_profile(client, world["h_admin"], s1, tenth_percent=None)
    assert out["tenth_percent"] is None

    for bad in (
        {"graduation_year": 1999}, {"tenth_percent": 100.5}, {"active_backlogs": -1},
        {"roll_number": "bad roll!"}, {"placement_status": "placed"}, {"unknown": 1},
    ):
        r = client.put(f"{P}/students/{s1.id}/profile", json=profile_body(**bad), headers=world["h_tpo"])
        assert r.status_code == 422, bad

    rows = audit_rows(session, "placement.profile_update")
    assert len(rows) == 2
    for row in rows:
        assert "88.5" not in row.summary and "CSE/21/001" not in row.summary and "Asha" not in row.summary
    assert "tenth_percent" in rows[-1].summary


def test_profile_roll_number_unique_per_institute(client, world, on, session, make_user):
    on(world["a"], world["b"])
    put_profile(client, world["h_tpo"], world["s1"], roll_number="R1")
    r = client.put(f"{P}/students/{world['s2'].id}/profile", json=profile_body(roll_number="R1"),
                   headers=world["h_tpo"])
    assert r.status_code == 409
    # same roll number in another institute is fine
    r = client.put(f"{P}/students/{world['sb'].id}/profile", json=profile_body(roll_number="R1"),
                   headers=world["h_tpo_b"])
    assert r.status_code == 200


def test_profile_isolation_and_student_access(client, world, on):
    on(world["a"], world["b"])
    # another institute's student, a non-student, an unknown id -> 404
    for sid in (world["sb"].id, world["tpo"].id, 999999):
        assert client.get(f"{P}/students/{sid}", headers=world["h_tpo"]).status_code == 404
        r = client.put(f"{P}/students/{sid}/profile", json=profile_body(), headers=world["h_tpo"])
        assert r.status_code == 404
    # students cannot read or write staff routes
    assert client.get(f"{P}/students", headers=world["h_s1"]).status_code == 403
    assert client.put(f"{P}/students/{world['s1'].id}/profile", json=profile_body(),
                      headers=world["h_s1"]).status_code == 403
    put_profile(client, world["h_tpo"], world["s1"], placement_status="opted_out", status_note="Higher studies")
    me = client.get(f"{P}/me/profile", headers=world["h_s1"]).json()
    assert me["profile"]["placement_status"] == "opted_out" and me["department_name"] == "Computer Science"
    assert client.get(f"{P}/me/profile", headers=world["h_tpo"]).status_code == 403


def test_student_directory_filters_and_search(client, world, on, session):
    on(world["a"])
    put_profile(client, world["h_tpo"], world["s1"], roll_number="A-1", graduation_year=2027)
    put_profile(client, world["h_tpo"], world["s2"], roll_number="B-2", graduation_year=2028,
                placement_status="debarred")
    h = world["h_tpo"]
    names = lambda r: [x["full_name"] for x in r.json()]  # noqa: E731
    r = client.get(f"{P}/students", headers=h)
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "2"
    assert "sb@b.edu" not in r.text  # other institute never listed
    assert names(client.get(f"{P}/students?department_id={world['dept'].id}", headers=h)) == ["Asha"]
    assert names(client.get(f"{P}/students?graduation_year=2028", headers=h)) == ["Bala"]
    assert names(client.get(f"{P}/students?placement_status=active", headers=h)) == ["Asha"]
    assert names(client.get(f"{P}/students?q=b-2", headers=h)) == ["Bala"]
    # LIKE wildcards are literal
    assert names(client.get(f"{P}/students?q=%25", headers=h)) == []
    assert names(client.get(f"{P}/students?q=_", headers=h)) == []
    # placed filter
    d = mk_drive(session, world["a"])
    session.add(models.Application(tenant_id=world["a"].id, drive_id=d.id, student_id=world["s1"].id,
                                   status=models.ApplicationStatus.selected))
    session.commit()
    assert names(client.get(f"{P}/students?placed=true", headers=h)) == ["Asha"]
    assert names(client.get(f"{P}/students?placed=false", headers=h)) == ["Bala"]
    assert client.get(f"{P}/students?placement_status=placed", headers=h).status_code == 422


# --- drives: drafts, settings, lifecycle ------------------------------------------
def test_draft_drive_hidden_from_students_until_published(client, world, on, session):
    on(world["a"])
    r = client.post(f"{P}/drives", json={"company_name": "Zeta", "role_title": "Analyst",
                                         "settings": {"graduation_years": [2027]}}, headers=world["h_tpo"])
    assert r.status_code == 201, r.text
    body = r.json()
    did = body["drive"]["id"]
    assert body["settings"]["lifecycle"] == "draft" and body["drive"]["is_open"] is False
    assert body["settings"]["configured"] is True
    hs = world["h_s1"]
    assert did not in [x["id"] for x in client.get(f"{D}/open", headers=hs).json()]
    assert client.get(f"{D}/{did}", headers=hs).status_code == 404
    assert client.get(f"{D}/{did}/my-eligibility", headers=hs).status_code == 404
    assert client.get(f"{P}/drives/{did}/my-eligibility", headers=hs).status_code == 404
    assert client.post(f"{P}/drives/{did}/apply", headers=hs).status_code == 404
    assert client.post(f"{D}/{did}/apply", headers=hs).status_code == 404
    # staff still see it
    assert client.get(f"{D}/{did}", headers=world["h_tpo"]).status_code == 200

    r = client.post(f"{P}/drives/{did}/lifecycle", json={"to": "published"}, headers=world["h_tpo"])
    assert r.status_code == 200 and r.json()["lifecycle"] == "published" and r.json()["is_open"] is True
    assert did in [x["id"] for x in client.get(f"{D}/open", headers=hs).json()]
    assert len(audit_rows(session, "placement.drive_create")) == 1
    assert len(audit_rows(session, "placement.drive_lifecycle")) == 1


def test_lifecycle_transitions(client, world, on, session):
    on(world["a"])
    d = mk_drive(session, world["a"])  # legacy drive: effective "published"
    h = world["h_tpo"]
    g = client.get(f"{P}/drives/{d.id}/settings", headers=h).json()
    assert g["configured"] is False and g["lifecycle"] == "published"

    def go(to):
        return client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": to}, headers=h)

    assert go("completed").status_code == 409  # published -> completed not allowed
    assert go("closed").json()["lifecycle"] == "closed"
    assert go("published").json()["is_open"] is True
    assert go("closed").status_code == 200
    assert go("completed").json()["lifecycle"] == "completed"
    for to in ("published", "closed", "cancelled"):
        assert go(to).status_code == 409  # terminal
    r = client.put(f"{P}/drives/{d.id}/settings", json={}, headers=h)
    assert r.status_code == 409
    r = client.put(f"{P}/drives/{d.id}", json={"company_name": "X", "role_title": "Y"}, headers=h)
    assert r.status_code == 409
    assert client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "draft"}, headers=h).status_code == 422
    assert client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "closed", "note": "x"},
                       headers=h).status_code == 422  # unknown field
    # admin reads, cannot write; other institute 404
    assert client.get(f"{P}/drives/{d.id}/settings", headers=world["h_admin"]).status_code == 200
    assert client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "closed"},
                       headers=world["h_admin"]).status_code == 403
    on(world["a"], world["b"])
    assert client.get(f"{P}/drives/{d.id}/settings", headers=world["h_tpo_b"]).status_code == 404


def test_cannot_publish_after_window_closed(client, world, on, session):
    on(world["a"])
    past = datetime.now(timezone.utc) - timedelta(days=1)
    r = client.post(f"{P}/drives", json={"company_name": "Z", "role_title": "R", "settings": {
        "applications_open_at": iso(past - timedelta(days=2)), "applications_close_at": iso(past)}},
        headers=world["h_tpo"])
    did = r.json()["drive"]["id"]
    r = client.post(f"{P}/drives/{did}/lifecycle", json={"to": "published"}, headers=world["h_tpo"])
    assert r.status_code == 409


def test_settings_validation_and_department_isolation(client, world, on, session):
    on(world["a"])
    d = mk_drive(session, world["a"])
    h = world["h_tpo"]
    now = datetime.now(timezone.utc)
    bad = [
        {"applications_open_at": "2026-12-01T10:00:00"},  # naive
        {"applications_open_at": iso(now), "applications_close_at": iso(now - timedelta(hours=1))},
        {"academic_year": "2026-28"}, {"academic_year": "26-27"},
        {"graduation_years": [1999]}, {"graduation_years": [2020, 2021, 2022, 2023, 2024, 2025, 2026]},
        {"min_tenth_percent": 101}, {"max_active_backlogs": 100}, {"department_ids": [0]}, {"bogus": 1},
    ]
    for body in bad:
        assert client.put(f"{P}/drives/{d.id}/settings", json=body, headers=h).status_code == 422, body
    # another institute's department is rejected exactly like an unknown one
    r1 = client.put(f"{P}/drives/{d.id}/settings", json={"department_ids": [world["dept_b"].id]}, headers=h)
    r2 = client.put(f"{P}/drives/{d.id}/settings", json={"department_ids": [987654]}, headers=h)
    assert r1.status_code == r2.status_code == 422 and r1.json()["detail"] == r2.json()["detail"]
    out = put_settings(client, h, d, academic_year="2026-27", graduation_years=[2027, 2027],
                       department_ids=[world["dept"].id], exclude_placed=True)
    assert out["graduation_years"] == [2027] and out["department_ids"] == [world["dept"].id]
    assert out["lifecycle"] == "published" and out["configured"] is True
    out = put_settings(client, h, d, department_ids=[world["dept2"].id])
    assert out["department_ids"] == [world["dept2"].id] and out["exclude_placed"] is False
    assert len(audit_rows(session, "placement.drive_settings")) == 2


def test_update_drive_fields(client, world, on, session):
    on(world["a"])
    d = mk_drive(session, world["a"])
    r = client.put(f"{P}/drives/{d.id}", json={"company_name": "Acme", "role_title": "SDE II", "min_cgpa": 7},
                   headers=world["h_tpo"])
    assert r.status_code == 200 and r.json()["role_title"] == "SDE II" and r.json()["min_cgpa"] == 7
    row = audit_rows(session, "placement.drive_update")[0]
    assert "role_title" in row.summary and "min_cgpa" in row.summary and "SDE II" not in row.summary


# --- eligibility -----------------------------------------------------------------
def test_eligibility_legacy_parity(client, world, on, session):
    s1 = world["s1"]
    grades(session, s1, world["dept"], [(9.0, 4), (7.5, 3), (8.25, 2)])
    attendance(session, s1, world["sec"], present=7, absent=2, late=1)
    verified_skill(session, s1, "Python")
    verified_skill(session, s1, "SQL")
    d = mk_drive(session, world["a"], min_cgpa=7.0, min_attendance=75, required_skills="python, Docker")
    legacy_profile = legacy._student_profile(session, s1.id)
    from app.services import placement_management as svc

    m = svc.bulk_metrics(session, world["a"].id, [s1.id])[s1.id]
    assert m["cgpa"] == legacy_profile["cgpa"]
    assert m["attendance"] == legacy_profile["attendance"]
    assert sorted(m["verified_skills"]) == sorted(legacy_profile["verified_skills"])
    assert m["verified_projects"] == legacy_profile["verified_projects"]
    # all-students variant agrees too
    assert svc.bulk_metrics(session, world["a"].id, None)[s1.id] == m

    legacy_resp = client.get(f"{D}/{d.id}/my-eligibility", headers=world["h_s1"]).json()
    on(world["a"])
    v2 = client.get(f"{P}/drives/{d.id}/my-eligibility", headers=world["h_s1"]).json()
    assert v2["reasons"][:4] == legacy_resp["reasons"]
    assert v2["cgpa"] == legacy_resp["cgpa"] and v2["attendance"] == legacy_resp["attendance"]
    assert v2["eligible"] is False  # missing Docker
    assert v2["can_apply"] is False and "You do not meet every eligibility criterion." in v2["blockers"]


def test_eligibility_new_criteria(client, world, on, session):
    on(world["a"])
    s1, h = world["s1"], world["h_tpo"]
    d = mk_drive(session, world["a"])
    put_settings(client, h, d, graduation_years=[2027], min_tenth_percent=80, min_twelfth_percent=75,
                 max_active_backlogs=0, department_ids=[world["dept"].id])

    def verdict():
        r = client.get(f"{P}/drives/{d.id}/my-eligibility", headers=world["h_s1"]).json()
        return r["eligible"], {x["criterion"]: (x["passed"], x["detail"]) for x in r["reasons"]}

    ok, reasons = verdict()  # no profile: academic values "not recorded"
    assert not ok
    assert reasons["Graduation year"] == (False, "not recorded (need 2027)")
    assert reasons["10th %"][0] is False and "not recorded" in reasons["10th %"][1]
    assert reasons["Department"][0] is True
    put_profile(client, h, s1)
    ok, reasons = verdict()
    assert ok, reasons
    put_profile(client, h, s1, twelfth_percent=None, diploma_percent=78)
    ok, reasons = verdict()
    assert ok and reasons["12th / diploma %"] == (True, "diploma 78% (need >= 75%)")
    put_profile(client, h, s1, twelfth_percent=70, diploma_percent=90)  # 12th takes precedence
    assert verdict()[0] is False
    put_profile(client, h, s1, active_backlogs=1)
    assert verdict()[1]["Active backlogs"][0] is False
    put_profile(client, h, s1, graduation_year=2028)
    assert verdict()[1]["Graduation year"][0] is False
    put_profile(client, h, s1, placement_status="debarred")
    assert verdict()[1]["Placement status"] == (False, "debarred")
    # Bala is in Mechanical: department fails
    put_profile(client, h, world["s2"], roll_number="X2")
    rows = client.get(f"{P}/drives/{d.id}/eligibility", headers=h).json()
    bala = next(r for r in rows if r["full_name"] == "Bala")
    assert not bala["eligible"]
    assert any(x["criterion"] == "Department" and not x["passed"] for x in bala["reasons"])


def test_eligibility_list_isolation_headers_and_constant_queries(client, world, on, session, make_user):
    on(world["a"])
    d = mk_drive(session, world["a"])
    d_id = d.id  # read once: the test session must not query inside the window
    h = world["h_tpo"]

    def count_queries():
        n = {"q": 0, "s": []}

        def before(_conn, _cur, statement, *_a, **_k):
            n["q"] += 1
            n["s"].append(statement.split("FROM")[-1][:60])

        event.listen(dbmod.engine, "before_cursor_execute", before)
        try:
            r = client.get(f"{P}/drives/{d_id}/eligibility?limit=200", headers=h)
        finally:
            event.remove(dbmod.engine, "before_cursor_execute", before)
        assert r.status_code == 200
        return n["q"], r, n["s"]

    q_small, r, s_small = count_queries()
    assert r.headers["X-Total-Count"] == "2" and r.headers["X-Eligible-Count"] == "2"
    assert all(row["full_name"] != "sb" for row in r.json())
    for i in range(15):
        u = make_user(f"bulk{i}@a.edu", tenant=world["a"])
        grades(session, u, world["dept"], [(8.0, 3)])
        verified_skill(session, u, "Go")
    q_big, r, s_big = count_queries()
    assert r.headers["X-Total-Count"] == "17"
    assert q_big == q_small, (s_small, s_big)
    # legacy TPO eligibility route returns the same verdicts for a flagged institute
    legacy_rows = client.get(f"{D}/{d.id}/eligibility?limit=200", headers=h).json()
    assert {x["student_id"]: x["eligible"] for x in legacy_rows} == {
        x["student_id"]: x["eligible"] for x in r.json()
    }
    # admin may read; student may not
    assert client.get(f"{P}/drives/{d.id}/eligibility", headers=world["h_admin"]).status_code == 200
    assert client.get(f"{P}/drives/{d.id}/eligibility", headers=world["h_s1"]).status_code == 403


# --- apply -------------------------------------------------------------------------
@pytest.mark.parametrize("route", ["v2", "legacy"])
def test_apply_rules(client, world, on, session, route):
    on(world["a"])
    h, hs = world["h_tpo"], world["h_s1"]
    url = (lambda d: f"{P}/drives/{d.id}/apply") if route == "v2" else (lambda d: f"{D}/{d.id}/apply")
    now = datetime.now(timezone.utc)

    # not open yet
    d = mk_drive(session, world["a"])
    put_settings(client, h, d, applications_open_at=iso(now + timedelta(hours=1)))
    r = client.post(url(d), headers=hs)
    assert r.status_code == 400 and "not opened" in r.json()["detail"]
    # window closed
    put_settings(client, h, d, applications_open_at=iso(now - timedelta(days=2)),
                 applications_close_at=iso(now - timedelta(minutes=1)))
    r = client.post(url(d), headers=hs)
    assert r.status_code == 400 and "closed" in r.json()["detail"]
    # closed lifecycle
    put_settings(client, h, d)
    client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "closed"}, headers=h)
    assert client.post(url(d), headers=hs).status_code == 400
    client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "published"}, headers=h)
    # opted out
    put_profile(client, h, world["s1"], placement_status="opted_out")
    r = client.post(url(d), headers=hs)
    assert r.status_code == 403 and "Placement status" in r.json()["detail"]
    put_profile(client, h, world["s1"])
    # one-offer policy
    other = mk_drive(session, world["a"], company_name="Other")
    app_row = models.Application(tenant_id=world["a"].id, drive_id=other.id, student_id=world["s1"].id,
                                 status=models.ApplicationStatus.selected)
    session.add(app_row)
    session.commit()
    put_settings(client, h, d, exclude_placed=True)
    r = client.post(url(d), headers=hs)
    assert r.status_code == 403 and "already placed" in r.json()["detail"]
    put_settings(client, h, d, exclude_placed=False)
    # ok, then duplicate
    r = client.post(url(d), headers=hs)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"id", "drive_id", "student_id", "status", "note"}
    assert client.post(url(d), headers=hs).status_code == 409
    events = client.get(f"{P}/applications/{r.json()['id']}/history", headers=hs).json()
    assert [(e["from_status"], e["to_status"], e["actor_role"]) for e in events] == [(None, "applied", "student")]
    # only students apply; other institute 404
    assert client.post(url(d), headers=h).status_code == 403
    on(world["a"], world["b"])
    assert client.post(url(d), headers=world["h_sb"]).status_code == 404


def test_apply_unique_race_returns_409(client, world, on, session, monkeypatch):
    """Simulate the race: the pre-check sees no application, the insert hits
    the unique constraint."""
    d = mk_drive(session, world["a"])
    session.add(models.Application(tenant_id=world["a"].id, drive_id=d.id, student_id=world["s1"].id))
    session.commit()
    from sqlalchemy.orm import Session as OrmSession

    real_scalar = OrmSession.scalar
    state = {"hide": True}

    def scalar(self, stmt, *a, **k):
        text = str(stmt)
        if state["hide"] and "FROM applications" in text and "applications.student_id" in text:
            return None
        return real_scalar(self, stmt, *a, **k)

    monkeypatch.setattr(OrmSession, "scalar", scalar)
    # legacy route (flag off) - hardening applies regardless of the flag
    assert client.post(f"{D}/{d.id}/apply", headers=world["h_s1"]).status_code == 409
    on(world["a"])
    assert client.post(f"{P}/drives/{d.id}/apply", headers=world["h_s1"]).status_code == 409


# --- application status --------------------------------------------------------
def _applied(client, world, session):
    d = mk_drive(session, world["a"])
    r = client.post(f"{P}/drives/{d.id}/apply", headers=world["h_s1"])
    assert r.status_code == 201, r.text
    return d, r.json()["id"]


@pytest.mark.parametrize("route", ["v2", "legacy"])
def test_status_transitions_history_audit_notify(client, world, on, session, route):
    on(world["a"])
    d, aid = _applied(client, world, session)
    h = world["h_tpo"]
    url = f"{P}/applications/{aid}/status" if route == "v2" else f"{D}/applications/{aid}/status"

    def go(status_, note=None, **extra):
        return client.patch(url, json={"status": status_, "note": note, **extra}, headers=h)

    assert go("applied").status_code == 400
    assert go("shortlisted").status_code == 200
    assert go("shortlisted").status_code == 409  # same status
    r = go("selected", "Offer round cleared")
    assert r.status_code == 200 and r.json()["status"] == "selected"
    assert go("shortlisted").status_code == 409  # selected -> shortlisted not allowed
    assert go("rejected").status_code == 400  # correction needs a note
    assert go("rejected", "   ").status_code == 400  # whitespace is not a note
    assert go("rejected", "Background check failed").status_code == 200
    assert go("shortlisted", "Re-opened after appeal").status_code == 200

    hist = client.get(f"{P}/applications/{aid}/history", headers=h).json()
    assert [(e["from_status"], e["to_status"]) for e in hist] == [
        (None, "applied"), ("applied", "shortlisted"), ("shortlisted", "selected"),
        ("selected", "rejected"), ("rejected", "shortlisted"),
    ]
    rows = audit_rows(session, "placement.application_status")
    assert len(rows) == 4
    assert all("Asha" not in r.summary and "appeal" not in r.summary for r in rows)
    session.expire_all()
    notes = list(session.scalars(select(models.Notification).where(models.Notification.user_id == world["s1"].id)))
    assert len(notes) == 4
    # student sees own history; another student 404; admin reads; admin cannot write
    assert client.get(f"{P}/applications/{aid}/history", headers=world["h_s1"]).status_code == 200
    assert client.get(f"{P}/applications/{aid}/history", headers=world["h_s2"]).status_code == 404
    assert client.get(f"{P}/applications/{aid}/history", headers=world["h_admin"]).status_code == 200
    assert client.patch(f"{P}/applications/{aid}/status", json={"status": "rejected", "note": "x"},
                        headers=world["h_admin"]).status_code == 403
    on(world["a"], world["b"])
    assert client.patch(url, json={"status": "rejected", "note": "x"}, headers=world["h_tpo_b"]).status_code == 404
    assert client.get(f"{P}/applications/{aid}/history", headers=world["h_tpo_b"]).status_code == 404


def test_status_compare_and_set(client, world, on, session, monkeypatch):
    on(world["a"])
    _d, aid = _applied(client, world, session)
    h = world["h_tpo"]
    r = client.patch(f"{P}/applications/{aid}/status",
                     json={"status": "shortlisted", "expected_status": "shortlisted"}, headers=h)
    assert r.status_code == 409  # caller saw a stale status
    # Another writer moves the row between our read and our UPDATE.
    from app.services import placement_management as svc

    real = svc.require_application

    def stale(db, application_id, tenant_id):
        application = real(db, application_id, tenant_id)
        with dbmod.engine.begin() as conn:
            conn.execute(models.Application.__table__.update()
                         .where(models.Application.id == application_id).values(status="rejected"))
        return application

    monkeypatch.setattr(svc, "require_application", stale)
    r = client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"}, headers=h)
    assert r.status_code == 409 and "someone else" in r.json()["detail"]
    session.expire_all()
    assert len(list(session.scalars(select(models.PlacementApplicationEvent)))) == 1  # only "applied"


def test_cancelled_drive_freezes_applications(client, world, on, session):
    on(world["a"])
    d, aid = _applied(client, world, session)
    h = world["h_tpo"]
    assert client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "cancelled"}, headers=h).status_code == 200
    r = client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"}, headers=h)
    assert r.status_code == 409


def test_legacy_open_close_follows_lifecycle(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    d = mk_drive(session, world["a"])
    assert client.patch(f"{D}/{d.id}/status", json={"is_open": True}, headers=h).status_code == 200  # no-op
    r = client.patch(f"{D}/{d.id}/status", json={"is_open": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_open"] is False
    assert client.get(f"{P}/drives/{d.id}/settings", headers=h).json()["lifecycle"] == "closed"
    client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "completed"}, headers=h)
    assert client.patch(f"{D}/{d.id}/status", json={"is_open": True}, headers=h).status_code == 409


def test_flag_off_legacy_unchanged(client, world, session):
    """Flag off: legacy routes keep their exact behaviour (incl. any -> any
    status changes) and write no v2 rows."""
    d = mk_drive(session, world["a"])
    r = client.post(f"{D}/{d.id}/apply", headers=world["h_s1"])
    assert r.status_code == 201
    aid = r.json()["id"]
    h = world["h_tpo"]
    assert client.patch(f"{D}/applications/{aid}/status", json={"status": "selected"}, headers=h).status_code == 200
    assert client.patch(f"{D}/applications/{aid}/status", json={"status": "shortlisted"}, headers=h).status_code == 200
    assert client.patch(f"{D}/{d.id}/status", json={"is_open": False}, headers=h).status_code == 200
    session.expire_all()
    assert session.scalars(select(models.PlacementApplicationEvent)).first() is None
    assert session.scalars(select(models.PlacementDriveSettings)).first() is None


# --- hardening of existing routes --------------------------------------------------
def test_placement_analytics_is_tenant_filtered_in_app(client, world, session):
    mk_drive(session, world["a"], company_name="A-Co", package_lpa=10)
    db_ = mk_drive(session, world["b"], company_name="B-Co", package_lpa=50)
    session.add(models.Application(tenant_id=world["b"].id, drive_id=db_.id, student_id=world["sb"].id,
                                   status=models.ApplicationStatus.selected))
    session.commit()
    r = client.get("/v1/placement/analytics/overview", headers=world["h_tpo"]).json()
    assert r["total_drives"] == 1 and r["total_applications"] == 0
    assert r["highest_package"] == 10 and "B-Co" not in str(r)


def test_section_analytics_other_institute_404(client, world, session):
    sec_b = models.Section(tenant_id=world["b"].id, name="B-1", year=1, department_id=world["dept_b"].id)
    session.add(sec_b)
    session.commit()
    h = world["h_teacher"]
    assert client.get(f"/v1/analytics/section/{sec_b.id}", headers=h).status_code == 404
    assert client.get(f"/v1/analytics/section/{sec_b.id}/at-risk", headers=h).status_code == 404
    assert client.get(f"/v1/analytics/section/{world['sec'].id}", headers=h).status_code == 200


# --- schema-level guarantees ------------------------------------------------------------
def test_db_constraints(session, world):
    a = world["a"]
    session.add(models.StudentPlacementProfile(tenant_id=a.id, student_id=world["s1"].id, placement_status="placed"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    # (The composite same-institute FK is proven on Postgres; SQLite does not
    # enforce foreign keys in this app.)
    d = mk_drive(session, a)
    session.add(models.PlacementDriveSettings(tenant_id=a.id, drive_id=d.id, lifecycle="archived",
                                              exclude_placed=False))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- Phase 9 finalization: flag defaults + approved faculty visibility (P9-D7) ----------
def test_placement_flag_defaults_off():
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["PLACEMENT_MANAGEMENT_ENABLED"].default is False
    assert fields["PLACEMENT_MANAGEMENT_TENANT_IDS"].default == ""


def test_teacher_cannot_read_student_mock_interview_data(client, world, on, session, monkeypatch):
    """P9-D7 (approved option A): faculty may later see completion status only.
    Today no API gives a teacher any part of a student's interview - not the
    session, transcript, score, report, coding feedback or staff diagnostics -
    even with every related feature flag ON. Enforced on the server."""
    for name, value in [
        ("AI_MOCK_INTERVIEW_ENABLED", True), ("AI_MOCK_INTERVIEW_TENANT_IDS", ""),
        ("AI_MOCK_INTERVIEW_OPS_ENABLED", True),
        ("AI_MOCK_INTERVIEW_CODING_ENABLED", True),
        ("AI_MOCK_INTERVIEW_CODING_TENANT_IDS", str(world["a"].id)),
        ("AI_MOCK_INTERVIEW_CODING_AI_ENABLED", True),
    ]:
        monkeypatch.setattr(settings, name, value)
    on(world["a"])
    secret = "PRIVATE-ANSWER-7731"
    now = datetime.now(timezone.utc)
    s = models.MockInterviewSession(
        tenant_id=world["a"].id, student_id=world["s1"].id, interview_type="hr", mode="text",
        status="completed", difficulty="medium", language="en", duration_minutes=15,
        target_role="Engineer", started_at=now - timedelta(minutes=20), completed_at=now,
    )
    session.add(s)
    session.flush()
    session.add(models.MockInterviewTurn(tenant_id=world["a"].id, session_id=s.id, seq=1,
                                         question="Tell me about yourself", answer_text=secret))
    session.add(models.MockInterviewReport(
        tenant_id=world["a"].id, session_id=s.id, overall_score=87.25,
        category_scores_json={"communication": 87.25}, strengths_json=[secret],
        weaknesses_json=[], recommendations_json=[], rubric_version="v1",
    ))
    session.commit()
    sid = s.id
    mi = "/v1/mock-interview"
    reads = [
        ("get", f"{mi}/sessions/{sid}"), ("get", f"{mi}/sessions/{sid}/report"),
        ("post", f"{mi}/sessions/{sid}/report"), ("get", f"{mi}/sessions"),
        ("get", f"{mi}/sessions/active"), ("get", f"{mi}/history"),
        ("get", f"{mi}/sessions/{sid}/coding/feedback"),
        ("get", f"{mi}/admin/usage"), ("get", f"{mi}/admin/usage/students"),
        ("get", f"{mi}/admin/sessions/{sid}/diagnostics"),
        ("get", f"{P}/students"), ("get", f"{P}/students/{world['s1'].id}"),
    ]
    for method, path in reads:
        r = getattr(client, method)(path, headers=world["h_teacher"])
        assert r.status_code in (403, 404), (path, r.status_code)
        assert secret not in r.text and "87.25" not in r.text, path
    # Not vacuous: the owning student does get their own report.
    r = client.get(f"{mi}/sessions/{sid}/report", headers=world["h_s1"])
    assert r.status_code == 200 and "87.25" in r.text
