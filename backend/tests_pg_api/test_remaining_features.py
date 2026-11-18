"""Phase 5Q - remaining backend features through the REAL /v1 API (Postgres/RLS).

Batch 1  skills + ECA decision-record deletion policy (admin-only, audited)
Batch 2  academics/results, analytics, calendar
Batch 3  file signing (STORAGE MOCKED), institute dashboard, leads/feedback,
         people, placement analytics
Batch 4  self-registration, user status, attendance summaries, bulk template

Two synthetic institutes from the API-built `campus` fixture; real logins.
External services: file storage (Supabase) is MOCKED at storage.* where noted
- never a real integration. No AI calls are made by these routes except skill
scoring, which is mocked at ai_client._worker_request.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from flowkit import EMAIL_DOMAIN, MARK, _d

ALIASES = ("/v1", "")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
@pytest.fixture
def mock_ai(monkeypatch):
    import ai_client

    class _Resp:
        def json(self):
            return {"score": 77}

    monkeypatch.setattr(ai_client, "_worker_request", lambda *a, **k: _Resp())


def _user(campus, t, role, label, section=None):
    email, pw = f"{label}-{campus.run}@{EMAIL_DOMAIN}", secrets.token_urlsafe(14)
    r = t.admin.post("/v1/admin/users", json={"email": email, "full_name": f"Synthetic {label}",
                                              "password": pw, "role": role})
    assert r.status_code == 201, _d(r)
    if section is not None:
        assert t.admin.patch(f"/v1/admin/users/{r.json()['id']}/section",
                             json={"section_id": section}).status_code == 200
    return campus.login(email, pw, label)


def _recruiter(campus, t, label):
    email, pw = f"rec-{label}-{campus.run}@{EMAIL_DOMAIN}", secrets.token_urlsafe(14)
    inv = t.tpo.post("/v1/recruiters/invite", json={
        "company_name": f"{MARK}-{campus.run} {label} Co", "email": email})
    assert inv.status_code == 201, _d(inv)
    acc = campus.new_client().post("/v1/recruiters/accept-invite", json={
        "token": inv.json()["token"], "full_name": "Rec", "password": pw})
    assert acc.status_code == 200, _d(acc)
    return campus.login(email, pw, f"recruiter-{label}")


def _audit(sql, tenant, ttype, tid):
    return sql("SELECT action, actor_id, tenant_id, summary FROM audit_logs "
               "WHERE target_type = :ty AND target_id = :id ORDER BY id",
               {"ty": ttype, "id": str(tid)}, tenant=tenant)


def _skill(student, name):
    r = student.post("/v1/skills", json={"name": name, "evidence_url": "https://example.com/p"})
    assert r.status_code == 201, _d(r)
    return r.json()


def _eca(student, title):
    r = student.post("/v1/eca", json={"title": title, "category": "sports"})
    assert r.status_code == 201, _d(r)
    return r.json()


# ==========================================================================
# Batch 1 - skills + ECA decision records
# ==========================================================================
def test_b1_eca_claim_and_review_flow(campus):
    a, b = campus.a, campus.b
    e = _eca(a.student, "Football captain")
    assert e["status"] == "pending"
    assert a.student.post("/v1/eca", json={"title": "Football captain", "category": "sports"}).status_code == 409
    assert e["id"] in {x["id"] for x in a.teacher.get("/v1/eca/queue").json()}
    assert e["id"] not in {x["id"] for x in b.teacher.get("/v1/eca/queue").json()}
    for who, code in ((a.student, 403), (a.admin, 403), (b.teacher, 404)):
        r = who.patch(f"/v1/eca/{e['id']}/decision", json={"status": "verified"})
        assert r.status_code == code, (who.role, _d(r))
    v = a.tpo.patch(f"/v1/eca/{e['id']}/decision", json={"status": "verified"})
    assert v.status_code == 200 and v.json()["status"] == "verified", _d(v)
    assert {x["id"]: x["status"] for x in a.student.get("/v1/eca/me").json()}[e["id"]] == "verified"
    assert e["id"] in {x["id"] for x in a.teacher.get(f"/v1/eca/student/{a.student.id}").json()}
    assert b.teacher.get(f"/v1/eca/student/{a.student.id}").json() == []


@pytest.mark.parametrize("kind", ["skill", "eca"])
def test_b1_owner_deletes_undecided_claim(campus, mock_ai, count_rows, kind):
    a, b = campus.a, campus.b
    for prefix in ALIASES:
        rec = _skill(a.student, f"Undecided {prefix or 'legacy'}") if kind == "skill" \
            else _eca(a.student, f"Undecided {prefix or 'legacy'}")
        url = f"{prefix}/{'skills' if kind == 'skill' else 'eca'}/{rec['id']}"
        assert a.student2.delete(url).status_code == 403
        assert b.student.delete(url).status_code == 404
        assert a.student.delete(url).status_code == 204
    table = "skills" if kind == "skill" else "extracurriculars"
    assert count_rows(a.tenant, table, "student_id = :u", {"u": a.student.id}) == 0


@pytest.mark.parametrize("kind,decision", [("skill", "verified"), ("skill", "flagged"),
                                           ("eca", "verified"), ("eca", "flagged")])
def test_b1_decided_claims_are_protected(campus, mock_ai, sql, kind, decision):
    a, b = campus.a, campus.b
    if kind == "skill":
        rec = _skill(a.student, f"Decided {decision}")
        assert a.teacher.patch(f"/v1/skills/{rec['id']}/decision", json={"status": decision}).status_code == 200
        base, table = "skills", "skills"
    else:
        rec = _eca(a.student, f"Decided {decision}")
        assert a.teacher.patch(f"/v1/eca/{rec['id']}/decision", json={"status": decision}).status_code == 200
        base, table = "eca", "extracurriculars"
    for prefix in ALIASES:
        for who, code in ((a.student, 403), (a.student2, 403), (a.teacher, 403), (a.tpo, 403),
                          (b.admin, 404), (b.student, 404)):
            r = who.delete(f"{prefix}/{base}/{rec['id']}")
            assert r.status_code == code, (prefix, who.role, _d(r))
    row = sql(f"SELECT status::text AS s, reviewed_by_id FROM {table} WHERE id = :i",
              {"i": rec["id"]}, tenant=a.tenant)
    assert len(row) == 1 and row[0]["s"] == decision and row[0]["reviewed_by_id"] == a.teacher.id
    assert _audit(sql, a.tenant, "skill" if kind == "skill" else "extracurricular", rec["id"]) == []


@pytest.mark.parametrize("kind", ["skill", "eca"])
def test_b1_admin_deletes_decided_claim_with_audit(campus, mock_ai, sql, count_rows, kind):
    a, b = campus.a, campus.b
    if kind == "skill":
        rec = _skill(a.student, "Admin removes")
        a.teacher.patch(f"/v1/skills/{rec['id']}/decision", json={"status": "verified"})
        url, table, ttype, action = f"/v1/skills/{rec['id']}", "skills", "skill", "skill.delete"
    else:
        rec = _eca(a.student, "Admin removes")
        a.teacher.patch(f"/v1/eca/{rec['id']}/decision", json={"status": "flagged"})
        url, table, ttype, action = f"/v1/eca/{rec['id']}", "extracurriculars", "extracurricular", "eca.delete"
    assert a.admin.delete(url).status_code == 204
    assert count_rows(a.tenant, table, "id = :i", {"i": rec["id"]}) == 0
    rows = _audit(sql, a.tenant, ttype, rec["id"])
    assert len(rows) == 1 and (rows[0]["action"], rows[0]["actor_id"], rows[0]["tenant_id"]) == (
        action, a.admin.id, a.tenant)
    assert "decision record" in rows[0]["summary"]
    assert _audit(sql, b.tenant, ttype, rec["id"]) == []
    assert any(x["target_id"] == str(rec["id"]) for x in a.admin.get(f"/v1/audit?action={action}").json())
    assert not any(x["target_id"] == str(rec["id"]) for x in b.admin.get(f"/v1/audit?action={action}").json())


@pytest.mark.parametrize("kind", ["skill", "eca"])
def test_b1_audit_failure_rolls_back_claim_deletion(campus, mock_ai, sql, count_rows, monkeypatch, kind):
    import app.routers.eca as eca_router
    import app.routers.skills as skills_router

    a = campus.a
    if kind == "skill":
        rec = _skill(a.student, "Atomic")
        a.teacher.patch(f"/v1/skills/{rec['id']}/decision", json={"status": "verified"})
        module, url, table, ttype = skills_router, f"/v1/skills/{rec['id']}", "skills", "skill"
    else:
        rec = _eca(a.student, "Atomic")
        a.teacher.patch(f"/v1/eca/{rec['id']}/decision", json={"status": "verified"})
        module, url, table, ttype = eca_router, f"/v1/eca/{rec['id']}", "extracurriculars", "extracurricular"

    def broken_audit(db, actor, action, summary, target_type=None, target_id=None):
        from app.models import AuditLog
        db.add(AuditLog(actor_id=actor.id, tenant_id=actor.tenant_id, actor_email=actor.email,
                        action="x" * 500, target_type=target_type,  # > String(60): DB error
                        target_id=str(target_id), summary=summary))

    monkeypatch.setattr(module, "record_audit", broken_audit)
    assert a.admin.delete(url).status_code == 500
    assert count_rows(a.tenant, table, "id = :i", {"i": rec["id"]}) == 1
    assert _audit(sql, a.tenant, ttype, rec["id"]) == []


# ==========================================================================
# Batch 2 - academics/results, analytics, calendar
# ==========================================================================
def _subject(teacher, dept, code="CS501", credits=4, semester=5, expect=201):
    r = teacher.post("/v1/academics/subjects", json={"department_id": dept, "name": f"Subject {code}",
                                                     "code": code, "credits": credits, "semester": semester})
    assert r.status_code == expect, _d(r)
    return r.json() if expect == 201 else None


def test_b2_subjects_results_and_student_summary(campus):
    a, b = campus.a, campus.b
    s1 = _subject(a.teacher, a.dept, "CS501", credits=4)
    s2 = _subject(a.teacher, a.dept, "CS502", credits=2)
    _subject(a.teacher, a.dept, "CS501", expect=409)
    assert s1["id"] in {x["id"] for x in a.student.get("/v1/academics/subjects").json()}
    assert s1["id"] not in {x["id"] for x in b.student.get("/v1/academics/subjects").json()}
    r1 = a.teacher.post("/v1/academics/results", json={"student_id": a.student.id, "subject_id": s1["id"],
                                                       "marks_obtained": 92, "max_marks": 100})
    r2 = a.teacher.post("/v1/academics/results", json={"student_id": a.student.id, "subject_id": s2["id"],
                                                       "marks_obtained": 75, "max_marks": 100})
    assert r1.status_code == 200 and r1.json()["grade_point"] == 10.0, _d(r1)
    assert r2.status_code == 200 and r2.json()["grade_point"] == 8.0, _d(r2)
    upd = a.teacher.post("/v1/academics/results", json={"student_id": a.student.id, "subject_id": s2["id"],
                                                        "marks_obtained": 85, "max_marks": 100})
    assert upd.status_code == 200 and upd.json()["id"] == r2.json()["id"] and upd.json()["grade_point"] == 9.0
    mine = {x["subject_id"]: x["grade_point"] for x in a.student.get("/v1/academics/me/results").json()}
    assert mine == {s1["id"]: 10.0, s2["id"]: 9.0}
    summary = a.student.get("/v1/academics/me/summary").json()
    assert summary["total_credits"] == 6 and summary["cgpa"] == round((10 * 4 + 9 * 2) / 6, 2)
    lst = a.teacher.get(f"/v1/academics/subjects/{s1['id']}/results").json()
    assert [x["student_id"] for x in lst] == [a.student.id]
    assert a.student2.get("/v1/academics/me/results").json() == []


def test_b2_academics_denials_and_cross_tenant(campus):
    a, b = campus.a, campus.b
    s1 = _subject(a.teacher, a.dept)
    for who in (a.student, a.tpo, a.admin):
        _subject(who, a.dept, "XX1", expect=403)
    _subject(b.teacher, a.dept, "XX2", expect=404)          # other institute's dept
    body = {"student_id": a.student.id, "subject_id": s1["id"], "marks_obtained": 50, "max_marks": 100}
    for who in (a.student, a.tpo):
        assert who.post("/v1/academics/results", json=body).status_code == 403
    assert b.teacher.post("/v1/academics/results", json=body).status_code == 404          # foreign subject
    assert a.teacher.post("/v1/academics/results", json={**body, "student_id": b.student.id}).status_code == 404
    assert b.teacher.get(f"/v1/academics/subjects/{s1['id']}/results").status_code == 404
    assert a.student.get(f"/v1/academics/subjects/{s1['id']}/results").status_code == 403


def test_b2_results_only_for_students(campus, count_rows):
    """A result row is a student's grade: recording one for a teacher/admin
    account of the same institute must be rejected."""
    a = campus.a
    s1 = _subject(a.teacher, a.dept)
    r = a.teacher.post("/v1/academics/results", json={"student_id": a.tpo.id, "subject_id": s1["id"],
                                                      "marks_obtained": 50, "max_marks": 100})
    assert r.status_code in (404, 422), _d(r)
    assert count_rows(a.tenant, "results", "student_id = :u", {"u": a.tpo.id}) == 0


def test_b2_results_marks_cannot_exceed_max(campus, count_rows):
    a = campus.a
    s1 = _subject(a.teacher, a.dept)
    r = a.teacher.post("/v1/academics/results", json={"student_id": a.student.id, "subject_id": s1["id"],
                                                      "marks_obtained": 150, "max_marks": 100})
    assert r.status_code in (400, 422), _d(r)
    assert count_rows(a.tenant, "results", "student_id = :u", {"u": a.student.id}) == 0


def test_b2_analytics_section_and_me(campus):
    a, b = campus.a, campus.b
    a.teacher.post("/v1/attendance/mark", json={"section_id": a.section, "date": "2031-02-02",
                                                "records": [{"student_id": a.student.id, "status": "absent"},
                                                            {"student_id": a.student2.id, "status": "present"}]})
    for staff in (a.teacher, a.tpo, a.admin):
        r = staff.get(f"/v1/analytics/section/{a.section}")
        assert r.status_code == 200, (staff.role, _d(r))
        ar = staff.get(f"/v1/analytics/section/{a.section}/at-risk")
        assert ar.status_code == 200 and {x["student_id"] for x in ar.json()} <= {a.student.id, a.student2.id}
    for who in (b.teacher, b.admin):
        assert who.get(f"/v1/analytics/section/{a.section}").status_code == 404
        assert who.get(f"/v1/analytics/section/{a.section}/at-risk").status_code == 404
    assert a.student.get(f"/v1/analytics/section/{a.section}").status_code == 403
    me = a.student.get("/v1/analytics/me")
    assert me.status_code == 200 and me.json()["student_id"] == a.student.id
    assert me.json()["attendance_pct"] == 0.0


def test_b2_calendar_audience_and_denials(campus):
    a, b = campus.a, campus.b
    day = str(date.today() + timedelta(days=30))
    r_all = a.admin.post("/v1/calendar", json={"title": "Fest", "event_date": day, "audience": "all"})
    r_stu = a.admin.post("/v1/calendar", json={"title": "Exam", "event_date": day,
                                               "category": "exam", "audience": "student"})
    assert r_all.status_code == 201 and r_stu.status_code == 201, (_d(r_all), _d(r_stu))
    ev_all, ev_stu = r_all.json()["id"], r_stu.json()["id"]
    assert {ev_all, ev_stu} <= {x["id"] for x in a.student.get("/v1/calendar").json()}
    teacher_view = {x["id"] for x in a.teacher.get("/v1/calendar?upcoming=true").json()}
    assert ev_all in teacher_view and ev_stu not in teacher_view
    assert {ev_all, ev_stu} <= {x["id"] for x in a.admin.get("/v1/calendar").json()}
    assert not ({ev_all, ev_stu} & {x["id"] for x in b.student.get("/v1/calendar").json()})
    for who in (a.teacher, a.tpo, a.student):
        assert who.post("/v1/calendar", json={"title": "x", "event_date": day}).status_code == 403
        assert who.delete(f"/v1/calendar/{ev_all}").status_code == 403
    assert b.admin.delete(f"/v1/calendar/{ev_all}").status_code == 404
    bad = a.admin.post("/v1/calendar", json={"title": "x", "event_date": day,
                                             "end_date": str(date.today())})
    assert bad.status_code == 400, _d(bad)
    assert a.admin.delete(f"/v1/calendar/{ev_all}").status_code == 204


def test_b2_recruiters_do_not_see_internal_calendar(campus):
    """Same principle as the approved announcement rule: recruiters are
    external accounts and should not see the institute's internal calendar."""
    rec = _recruiter(campus, campus.a, "cal")
    ev = campus.a.admin.post("/v1/calendar", json={"title": "Internal", "audience": "all",
                                                   "event_date": str(date.today() + timedelta(days=5))}).json()
    seen = ev["id"] in {x["id"] for x in rec.get("/v1/calendar").json()}
    assert not seen, "recruiter sees the institute's internal 'all' calendar event"


# ==========================================================================
# Batch 3 - files (MOCKED storage), institute dashboard, leads, people, placement analytics
# ==========================================================================
@pytest.fixture
def mock_storage(monkeypatch):
    """MOCKED Supabase storage: records signed paths, returns fake URLs."""
    import storage

    signed: list[str] = []
    monkeypatch.setattr(storage, "storage_enabled", lambda: True)
    monkeypatch.setattr(storage, "create_signed_upload_url", lambda path: (
        signed.append(path) or {"path": path, "upload_url": "https://storage.invalid/u", "token": "mock"}))
    monkeypatch.setattr(storage, "create_signed_download_url", lambda path, exp=None: (
        signed.append(path) or "https://storage.invalid/d"))
    return signed


def test_b3_file_signing_is_tenant_scoped_mocked_storage(campus, mock_storage):
    a, b = campus.a, campus.b
    up = a.student.post("/v1/files/sign-upload", json={"filename": "proof.pdf", "kind": "proof"})
    assert up.status_code == 200, _d(up)
    path = up.json()["path"]
    assert path.startswith(f"{a.tenant}/proof/{a.student.id}/") and path.endswith("proof.pdf")
    assert a.student.post("/v1/files/sign-upload", json={"filename": "x.pdf", "kind": "weird"}).status_code == 400
    assert a.student.post("/v1/files/sign-upload", json={"filename": "x.exe", "kind": "proof"}).status_code == 415
    # Phase 5R: an UNATTACHED upload is no longer signable by prefix alone -
    # not even inside the same institute (authorized cases: test_files_authz.py).
    assert a.teacher.post("/v1/files/sign-download", json={"path": path}).status_code == 403
    # Leading "/" is stripped server-side, so "/{A}/..." must still be refused for B.
    for p in (path, f"{a.tenant}/../{b.tenant}/x.pdf", f"/{a.tenant}/proof/1/x.pdf"):
        assert b.student.post("/v1/files/sign-download", json={"path": p}).status_code == 403, p
    assert mock_storage == [path]  # only the upload signing reached (mocked) storage


def test_b3_file_signing_without_storage_config_is_503(campus):
    """REAL (unmocked) behaviour in this env: storage is not configured."""
    r = campus.a.student.post("/v1/files/sign-upload", json={"filename": "proof.pdf", "kind": "proof"})
    assert r.status_code == 503, _d(r)


def test_b3_institute_dashboard_counts_only_own_institute(campus):
    a, b = campus.a, campus.b
    for who in (a.teacher, a.tpo, a.student):
        assert who.get("/v1/institute/dashboard").status_code == 403
    users = a.admin.get("/v1/institute/dashboard").json()["users"]
    # campus A has: admin, teacher, tpo, student, student2 (B has the same).
    assert (users["total"], users["students"], users["teachers"], users["tpos"], users["admins"]) == (5, 2, 1, 1, 1), users
    assert users["students_with_section"] == 2


def test_b3_placement_analytics_scoped_to_institute(campus):
    a, b = campus.a, campus.b
    d = a.tpo.post("/v1/drives", json={"company_name": "Synthetic Co", "role_title": "Eng"}).json()
    assert a.student.post(f"/v1/drives/{d['id']}/apply").status_code == 201
    for who in (a.teacher, a.student):
        assert who.get("/v1/placement/analytics/overview").status_code == 403
    ov = a.tpo.get("/v1/placement/analytics/overview").json()
    assert (ov["total_drives"], ov["total_applications"], ov["total_active_students"]) == (1, 1, 2), ov
    ovb = b.admin.get("/v1/placement/analytics/overview").json()
    assert (ovb["total_drives"], ovb["total_applications"], ovb["total_active_students"]) == (0, 0, 2), ovb


def test_b3_leads_and_feedback(campus, sql):
    a, b = campus.a, campus.b
    try:
        _leads_flow(campus, sql, a, b)
    finally:
        # leads/feedback are not tenant-cascaded: remove this test's rows.
        sql("DELETE FROM leads WHERE email LIKE :e", {"e": f"%@{EMAIL_DOMAIN}"})
        sql("DELETE FROM feedback WHERE message = 'Synthetic feedback'")


def _leads_flow(campus, sql, a, b):
    anon = campus.new_client()
    lead = anon.post("/v1/leads", json={"name": "Synthetic Prospect", "email": f"lead-{campus.run}@{EMAIL_DOMAIN}",
                                        "institute": "Prospect College", "message": "Demo please"})
    assert lead.status_code == 201 and lead.json()["tenant_id"] is None, _d(lead)
    lid = lead.json()["id"]
    fb = anon.post("/v1/feedback", json={"message": "Synthetic feedback", "rating": 5})
    assert fb.status_code == 201, _d(fb)
    for who in (a.teacher, a.tpo, a.student):
        assert who.get("/v1/leads").status_code == 403 and who.get("/v1/feedback").status_code == 403
    # Phase 5R: unclaimed public leads are hidden from - and unclaimable by -
    # every institute admin (future platform-operator workflow).
    for adm in (a.admin, b.admin):
        assert lid not in {x["id"] for x in adm.get("/v1/leads?limit=200").json()}
        assert adm.patch(f"/v1/leads/{lid}/claim").status_code == 404
        assert adm.patch(f"/v1/leads/{lid}/handled").status_code == 404
    # A lead ALREADY claimed into A (set up directly; no API can claim now).
    sql("UPDATE leads SET tenant_id = :t WHERE id = :i", {"t": a.tenant, "i": lid})
    assert lid in {x["id"] for x in a.admin.get("/v1/leads?limit=200").json()}
    c = a.admin.patch(f"/v1/leads/{lid}/claim")  # idempotent for own lead
    assert c.status_code == 200 and c.json()["tenant_id"] == a.tenant, _d(c)
    assert lid not in {x["id"] for x in b.admin.get("/v1/leads?limit=200").json()}
    assert b.admin.patch(f"/v1/leads/{lid}/claim").status_code == 404
    assert b.admin.patch(f"/v1/leads/{lid}/handled").status_code == 404
    h = a.admin.patch(f"/v1/leads/{lid}/handled")
    assert h.status_code == 200 and h.json()["handled"] is True


def test_b3_people_student_roster(campus):
    a, b = campus.a, campus.b
    for staff in (a.teacher, a.tpo, a.admin):
        ids = {u["id"] for u in staff.get("/v1/people/students?limit=200").json()}
        assert ids == {a.student.id, a.student2.id}, staff.role
    sec = {u["id"] for u in a.teacher.get(f"/v1/people/students?section_id={a.section}").json()}
    assert sec == {a.student.id, a.student2.id}
    assert b.teacher.get(f"/v1/people/students?section_id={a.section}").status_code == 404
    assert a.student.get("/v1/people/students").status_code == 403


# ==========================================================================
# Batch 4 - self-registration, user status, attendance summaries, template
# ==========================================================================
def test_b4_self_registration_creates_student_in_slug_tenant(campus, sql):
    a, b = campus.a, campus.b
    c = campus.new_client()
    email, pw = f"selfreg-{campus.run}@{EMAIL_DOMAIN}", secrets.token_urlsafe(12)
    r = c.post("/v1/auth/register", json={"email": email, "full_name": "Self Reg", "password": pw,
                                          "tenant_slug": a.slug, "role": "admin", "tenant_id": b.tenant})
    assert r.status_code == 201 and r.json()["role"] == "student", _d(r)
    assert sql("SELECT tenant_id, role::text AS r FROM users WHERE email = :e", {"e": email}) == [
        {"tenant_id": a.tenant, "r": "student"}]
    assert c.post("/v1/auth/register", json={"email": email, "full_name": "x", "password": pw,
                                             "tenant_slug": a.slug}).status_code == 409
    assert c.post("/v1/auth/register", json={"email": f"x-{campus.run}@{EMAIL_DOMAIN}", "full_name": "x",
                                             "password": pw, "tenant_slug": f"{MARK}-nope"}).status_code == 404
    sql("UPDATE tenants SET is_active = false WHERE id = :t", {"t": b.tenant})
    try:
        assert c.post("/v1/auth/register", json={"email": f"y-{campus.run}@{EMAIL_DOMAIN}", "full_name": "y",
                                                 "password": pw, "tenant_slug": b.slug}).status_code == 404
    finally:
        sql("UPDATE tenants SET is_active = true WHERE id = :t", {"t": b.tenant})
    # Self-registration creates a PENDING (inactive) account: no login until approved.
    assert r.json()["is_active"] is False, _d(r)
    assert campus.new_client().post("/v1/auth/login", data={"username": email, "password": pw}).status_code == 401
    uid = r.json()["id"]
    assert r.json()["approval_pending"] is True, _d(r)
    assert b.admin.post(f"/v1/admin/users/{uid}/approve").status_code == 404  # other institute
    assert a.teacher.post(f"/v1/admin/users/{uid}/approve").status_code == 403
    # The generic status toggle cannot activate a pending request.
    assert a.admin.patch(f"/v1/admin/users/{uid}/status", json={"is_active": True}).status_code == 409
    assert a.admin.post(f"/v1/admin/users/{uid}/approve").status_code == 200
    me = campus.login(email, pw, "selfreg")
    assert me.role == "student"


def test_b4_admin_toggles_user_status(campus):
    a, b = campus.a, campus.b
    url = f"/v1/admin/users/{a.student2.id}/status"
    for who, code in ((a.teacher, 403), (a.tpo, 403), (a.student, 403), (b.admin, 404)):
        assert who.patch(url, json={"is_active": False}).status_code == code, who.role
    assert a.admin.patch(f"/v1/admin/users/{a.admin.id}/status", json={"is_active": False}).status_code == 400
    off = a.admin.patch(url, json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert a.student2.get("/v1/auth/me").status_code == 401             # live session rejected
    relog = campus.new_client().post("/v1/auth/login", data={"username": a.student2.email,
                                                             "password": a.student2.password})
    assert relog.status_code == 401
    on = a.admin.patch(url, json={"is_active": True})
    assert on.status_code == 200 and on.json()["is_active"] is True
    assert campus.login(a.student2.email, a.student2.password, "reactivated").id == a.student2.id


def test_b4_attendance_summaries(campus):
    a, b = campus.a, campus.b
    for day, s1, s2 in (("2031-03-01", "present", "absent"), ("2031-03-02", "late", "present"),
                        ("2031-03-03", "absent", "present")):
        r = a.teacher.post("/v1/attendance/mark", json={"section_id": a.section, "date": day, "records": [
            {"student_id": a.student.id, "status": s1}, {"student_id": a.student2.id, "status": s2}]})
        assert r.status_code == 200, _d(r)
    s = a.student.get("/v1/attendance/me/summary").json()
    assert (s["total"], s["present"], s["late"], s["absent"]) == (3, 1, 1, 1)
    assert s["percentage"] == round(2 / 3 * 100, 1) or abs(s["percentage"] - 66.67) < 0.1, s
    sec = a.teacher.get(f"/v1/attendance/section/{a.section}?date=2031-03-02")
    assert sec.status_code == 200 and len(sec.json()) == 2
    assert {x["status"] for x in sec.json()} == {"late", "present"}
    assert b.teacher.get(f"/v1/attendance/section/{a.section}").status_code == 404
    assert a.student.get(f"/v1/attendance/section/{a.section}").status_code == 403
    assert b.student.get("/v1/attendance/me/summary").json()["total"] == 0


def test_b4_bulk_import_template(campus):
    a = campus.a
    r = a.admin.get("/v1/admin/users/bulk-template")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "full_name,email,role,password,section_id"
    assert all(line.split(",")[2] in {"student", "teacher", "tpo", "admin"} for line in lines[1:])
    for who in (a.teacher, a.tpo, a.student):
        assert who.get("/v1/admin/users/bulk-template").status_code == 403
