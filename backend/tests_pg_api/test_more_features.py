"""Phase 5P - previously untested workflows through the REAL /v1 API.

  P  student projects (group) + teacher per-member verification
  I  internships / OJT + staff verification
  D  doubt forum: question, answers, upvotes, accepted answer

Two synthetic institutes from the API-built `campus` fixture; every user logs
in through /v1/auth/login. AI proof scoring is MOCKED at ai_client's single
outbound chokepoint (no network). Assertion messages carry codes/ids only.
"""

from __future__ import annotations

import secrets

import pytest

from flowkit import EMAIL_DOMAIN, MARK, _d


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
@pytest.fixture
def mock_ai(monkeypatch):
    """Replace the AI-worker HTTP call with a fixed synthetic score."""
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


def _section2(t):
    r = t.admin.post(f"/v1/admin/departments/{t.dept}/sections", json={"name": "S2"})
    assert r.status_code == 201, _d(r)
    return r.json()["id"]


# ==========================================================================
# P - Projects
# ==========================================================================
def _project(owner, mates=(), title="Synthetic Project"):
    r = owner.post("/v1/projects", json={
        "title": title, "repo_url": "https://example.com/repo", "is_group": bool(mates),
        "members": [{"student_id": m, "contribution": "API layer"} for m in mates]})
    return r


def test_p_group_project_and_teacher_member_verification(campus, mock_ai, count_rows):
    a, b = campus.a, campus.b
    r = _project(a.student, mates=[a.student2.id])
    assert r.status_code == 201, _d(r)
    proj = r.json()
    members = {m["student_id"]: m for m in proj["members"]}
    assert set(members) == {a.student.id, a.student2.id} and proj["owner_id"] == a.student.id
    assert all(m["status"] == "pending" for m in members.values())
    assert mock_ai.count("/score/proof") == 2  # one mocked score per member
    assert count_rows(a.tenant, "project_members", "project_id = :p AND ai_score = 77",
                      {"p": proj["id"]}) == 2
    assert proj["id"] in {p["id"] for p in a.student2.get("/v1/projects/me").json()}
    queue = a.teacher.get("/v1/projects/queue").json()
    mine = {q["member_id"]: q for q in queue if q["project_id"] == proj["id"]}
    assert set(mine) == {members[a.student.id]["id"], members[a.student2.id]["id"]}
    assert not any(q["project_id"] == proj["id"] for q in b.teacher.get("/v1/projects/queue").json())
    v = a.teacher.patch(f"/v1/projects/members/{members[a.student.id]['id']}/decision",
                        json={"status": "verified", "review_note": "ok"})
    f = a.teacher.patch(f"/v1/projects/members/{members[a.student2.id]['id']}/decision",
                        json={"status": "flagged"})
    assert v.status_code == 200 and v.json()["status"] == "verified", _d(v)
    assert f.status_code == 200 and f.json()["status"] == "flagged", _d(f)
    for staff in (a.teacher, a.tpo):
        rows = staff.get(f"/v1/projects/student/{a.student.id}").json()
        assert any(x["project_id"] == proj["id"] and x["status"] == "verified" for x in rows), staff.role


def test_p_project_denials_and_cross_tenant(campus, mock_ai, count_rows):
    a, b = campus.a, campus.b
    for mates, code in (([b.student.id], 400), ([2_000_000_000], 400)):
        r = _project(a.student, mates=mates, title="Bad team")
        assert r.status_code == code, _d(r)
    assert count_rows(a.tenant, "projects", "title = 'Bad team'", {}) == 0
    proj = _project(a.student, mates=[a.student2.id]).json()
    mid = next(m["id"] for m in proj["members"] if m["student_id"] == a.student.id)
    url = f"/v1/projects/members/{mid}/decision"
    for who, code in ((a.student, 403), (a.tpo, 403), (a.admin, 403), (b.teacher, 404)):
        r = who.patch(url, json={"status": "verified"})
        assert r.status_code == code, (who.role, _d(r))
    assert a.teacher.patch(url, json={"status": "pending"}).status_code == 400
    assert b.teacher.get(f"/v1/projects/student/{a.student.id}").json() == []
    assert a.student.get("/v1/projects/queue").status_code == 403
    for who, code in ((a.student2, 403), (b.student, 404), (b.admin, 404)):
        r = who.delete(f"/v1/projects/{proj['id']}")
        assert r.status_code == code, (who.role, _d(r))
    assert a.student.delete(f"/v1/projects/{proj['id']}").status_code == 204
    assert count_rows(a.tenant, "project_members", "project_id = :p", {"p": proj["id"]}) == 0


def test_p_recruiters_cannot_create_projects(campus, mock_ai, count_rows):
    """Projects are student verified-profile claims; an external recruiter
    account must not create them inside the institute's verification queue."""
    rec = _recruiter(campus, campus.a, "proj")
    r = _project(rec, title="Recruiter project")
    created = count_rows(campus.a.tenant, "projects", "owner_id = :u", {"u": rec.id})
    assert r.status_code == 403 and created == 0, (
        f"recruiter created a project: {_d(r)}; rows={created}")


# ==========================================================================
# I - Internships / OJT
# ==========================================================================
def _intern(student, org="Synthetic Org", role="Backend Intern", **extra):
    body = {"organization": org, "role_title": role, "internship_type": "internship",
            "mode": "remote", "start_date": "2031-05-01", "end_date": "2031-07-31", **extra}
    return student.post("/v1/internships", json=body)


def test_i_student_claims_and_staff_verify(campus):
    a, b = campus.a, campus.b
    r = _intern(a.student)
    assert r.status_code == 201 and r.json()["status"] == "pending", _d(r)
    one = r.json()
    other = _intern(a.student, org="Synthetic Org 2").json()
    assert _intern(a.student).status_code == 409  # same (org, role)
    assert _intern(a.student, org="X", start_date="2031-07-01", end_date="2031-06-01").status_code == 422
    q = {x["id"] for x in a.teacher.get("/v1/internships/queue").json()}
    assert {one["id"], other["id"]} <= q
    assert not ({one["id"], other["id"]} & {x["id"] for x in b.teacher.get("/v1/internships/queue").json()})
    v = a.teacher.patch(f"/v1/internships/{one['id']}/decision", json={"status": "verified"})
    f = a.tpo.patch(f"/v1/internships/{other['id']}/decision", json={"status": "flagged", "review_note": "proof?"})
    assert v.status_code == 200 and v.json()["status"] == "verified", _d(v)
    assert f.status_code == 200 and f.json()["status"] == "flagged", _d(f)
    mine = {x["id"]: x["status"] for x in a.student.get("/v1/internships/me").json()}
    assert mine == {one["id"]: "verified", other["id"]: "flagged"}
    staff_view = {x["id"] for x in a.tpo.get(f"/v1/internships/student/{a.student.id}").json()}
    assert {one["id"], other["id"]} <= staff_view


def test_i_internship_denials_and_cross_tenant(campus, count_rows):
    a, b = campus.a, campus.b
    it = _intern(a.student).json()
    url = f"/v1/internships/{it['id']}/decision"
    for who, code in ((a.student, 403), (a.student2, 403), (a.admin, 403), (b.teacher, 404), (b.tpo, 404)):
        r = who.patch(url, json={"status": "verified"})
        assert r.status_code == code, (who.role, _d(r))
    assert a.teacher.patch(url, json={"status": "pending"}).status_code == 400
    assert a.student.get("/v1/internships/queue").status_code == 403
    assert b.teacher.get(f"/v1/internships/student/{a.student.id}").json() == []
    for who, code in ((a.student2, 403), (b.student, 404), (b.admin, 404)):
        r = who.delete(f"/v1/internships/{it['id']}")
        assert r.status_code == code, (who.role, _d(r))
    assert a.student.delete(f"/v1/internships/{it['id']}").status_code == 204
    assert count_rows(a.tenant, "internships", "id = :i", {"i": it["id"]}) == 0


def test_i_recruiters_cannot_claim_internships(campus, count_rows):
    rec = _recruiter(campus, campus.a, "intern")
    r = _intern(rec)
    created = count_rows(campus.a.tenant, "internships", "student_id = :u", {"u": rec.id})
    assert r.status_code == 403 and created == 0, (
        f"recruiter claimed an internship: {_d(r)}; rows={created}")


# ==========================================================================
# D - Doubt forum
# ==========================================================================
def _doubt(user, section, title="Synthetic doubt"):
    return user.post("/v1/doubts", json={"section_id": section, "title": title, "body": "How?"})


def test_d_ask_answer_upvote_accept(campus):
    a = campus.a
    r = _doubt(a.student, a.section)
    assert r.status_code == 201 and r.json()["status"] == "open", _d(r)
    doubt = r.json()
    assert doubt["id"] in {d["id"] for d in a.student2.get("/v1/doubts/me").json()}
    t_ans = a.teacher.post(f"/v1/doubts/{doubt['id']}/answers", json={"body": "Teacher answer"})
    s_ans = a.student2.post(f"/v1/doubts/{doubt['id']}/answers", json={"body": "Peer answer"})
    assert t_ans.status_code == 201 and s_ans.status_code == 201
    t_id = t_ans.json()["id"]
    up = a.student.post(f"/v1/doubts/answers/{t_id}/upvote")
    assert up.status_code == 200 and up.json()["upvote_count"] == 1 and up.json()["viewer_has_upvoted"] is True
    again = a.student.post(f"/v1/doubts/answers/{t_id}/upvote")  # toggle off
    assert again.json()["upvote_count"] == 0 and again.json()["viewer_has_upvoted"] is False
    assert a.student.post(f"/v1/doubts/answers/{t_id}/upvote").json()["upvote_count"] == 1
    assert a.student2.post(f"/v1/doubts/answers/{t_id}/upvote").json()["upvote_count"] == 2
    acc = a.student.post(f"/v1/doubts/{doubt['id']}/answers/{t_id}/accept")
    assert acc.status_code == 200, _d(acc)
    detail = a.student.get(f"/v1/doubts/{doubt['id']}").json()
    assert detail["status"] == "resolved"
    flags = {x["id"]: x["is_accepted"] for x in detail["answers"]}
    assert flags == {t_id: True, s_ans.json()["id"]: False}
    staff_list = {d["id"] for d in a.teacher.get(f"/v1/doubts?section_id={a.section}").json()}
    assert doubt["id"] in staff_list


def test_d_section_role_and_cross_tenant_denials(campus):
    a, b = campus.a, campus.b
    sec2 = _section2(a)
    other_sec = _user(campus, a, "student", "student-s2", section=sec2)
    rec = _recruiter(campus, a, "doubt")
    doubt = _doubt(a.student, a.section).json()
    ans = a.teacher.post(f"/v1/doubts/{doubt['id']}/answers", json={"body": "x"}).json()
    # Posting outside your own section / without a section.
    for who in (a.student, rec):
        assert _doubt(who, sec2).status_code == 403, who.role
    assert _doubt(b.teacher, a.section).status_code == 404  # other institute's section
    # Same institute, other section (and recruiter): no access to the thread.
    for who in (other_sec, rec):
        assert who.get(f"/v1/doubts/{doubt['id']}").status_code == 403, who.role
        assert who.post(f"/v1/doubts/{doubt['id']}/answers", json={"body": "y"}).status_code == 403
        assert who.post(f"/v1/doubts/answers/{ans['id']}/upvote").status_code == 403
    assert doubt["id"] not in {d["id"] for d in other_sec.get("/v1/doubts/me").json()}
    # Other institute: 404 everywhere; the RLS-only /doubts/me feed stays empty.
    for who in (b.student, b.teacher, b.admin):
        assert who.get(f"/v1/doubts/{doubt['id']}").status_code == 404
        assert who.post(f"/v1/doubts/answers/{ans['id']}/upvote").status_code == 404
        assert who.post(f"/v1/doubts/{doubt['id']}/answers/{ans['id']}/accept").status_code == 404
        assert who.delete(f"/v1/doubts/{doubt['id']}").status_code == 404
    assert doubt["id"] not in {d["id"] for d in b.student.get("/v1/doubts/me").json()}
    assert doubt["id"] not in {d["id"] for d in b.teacher.get("/v1/doubts").json()}
    # Accept: not the asker (student) -> 403; staff may.
    assert a.student2.post(f"/v1/doubts/{doubt['id']}/answers/{ans['id']}/accept").status_code == 403
    assert a.teacher.post(f"/v1/doubts/{doubt['id']}/answers/{ans['id']}/accept").status_code == 200
    # Staff listing; delete: asker or admin only.
    assert a.student.get("/v1/doubts").status_code == 403
    for who in (a.student2, a.teacher):
        assert who.delete(f"/v1/doubts/{doubt['id']}").status_code == 403, who.role
    assert a.student.delete(f"/v1/doubts/{doubt['id']}").status_code == 204
    second = _doubt(a.student, a.section, title="Admin removes").json()
    assert a.admin.delete(f"/v1/doubts/{second['id']}").status_code == 204


# ==========================================================================
# Phase 5P fixes - regression tests
# ==========================================================================
ALIASES = ("/v1", "")  # versioned + deprecated unversioned mount


def _audit(sql, tenant, target_type, target_id):
    return sql("SELECT action, actor_id, tenant_id, summary FROM audit_logs "
               "WHERE target_type = :ty AND target_id = :id ORDER BY id",
               {"ty": target_type, "id": str(target_id)}, tenant=tenant)


def test_fix_only_students_create_projects_and_internships(campus, mock_ai, count_rows):
    a = campus.a
    rec = _recruiter(campus, a, "createfix")
    staff_ids = [a.teacher.id, a.tpo.id, a.admin.id, rec.id]
    for prefix in ALIASES:
        for who in (a.teacher, a.tpo, a.admin, rec):
            p = who.post(f"{prefix}/projects", json={"title": "x", "is_group": False, "members": []})
            i = who.post(f"{prefix}/internships", json={"organization": "x", "role_title": "y",
                                                         "internship_type": "internship"})
            assert p.status_code == 403 and i.status_code == 403, (prefix, who.role, _d(p), _d(i))
    assert count_rows(a.tenant, "projects", "owner_id = ANY(:ids)", {"ids": staff_ids}) == 0
    assert count_rows(a.tenant, "internships", "student_id = ANY(:ids)", {"ids": staff_ids}) == 0
    # Students still create, on either alias.
    assert a.student.post("/projects", json={"title": "Legacy alias", "is_group": False,
                                             "members": []}).status_code == 201
    assert _intern(a.student, org="Alias Org").status_code == 201


def test_fix_mixed_status_group_project_is_protected(campus, mock_ai, sql, count_rows):
    """One member verified, the owner's contribution still pending: the WHOLE
    project is a decision record - nobody but an admin may delete it."""
    a, b = campus.a, campus.b
    proj = _project(a.student, mates=[a.student2.id]).json()
    mate = next(m["id"] for m in proj["members"] if m["student_id"] == a.student2.id)
    assert a.teacher.patch(f"/v1/projects/members/{mate}/decision",
                           json={"status": "verified"}).status_code == 200
    for prefix in ALIASES:
        for who, code in ((a.student, 403), (a.student2, 403), (a.teacher, 403), (a.tpo, 403),
                          (b.admin, 404), (b.student, 404)):
            r = who.delete(f"{prefix}/projects/{proj['id']}")
            assert r.status_code == code, (prefix, who.role, _d(r))
    rows = sql("SELECT student_id, status::text AS s FROM project_members WHERE project_id = :p "
               "ORDER BY student_id", {"p": proj["id"]}, tenant=a.tenant)
    assert {r["student_id"]: r["s"] for r in rows} == {a.student.id: "pending", a.student2.id: "verified"}
    assert _audit(sql, a.tenant, "project", proj["id"]) == []


def test_fix_decided_internships_are_protected(campus, sql):
    a, b = campus.a, campus.b
    ver = _intern(a.student, org="Verified Org").json()
    flg = _intern(a.student, org="Flagged Org").json()
    assert a.teacher.patch(f"/v1/internships/{ver['id']}/decision", json={"status": "verified"}).status_code == 200
    assert a.tpo.patch(f"/v1/internships/{flg['id']}/decision", json={"status": "flagged"}).status_code == 200
    for it, st in ((ver, "verified"), (flg, "flagged")):
        for prefix in ALIASES:
            for who, code in ((a.student, 403), (a.teacher, 403), (a.tpo, 403), (b.admin, 404)):
                r = who.delete(f"{prefix}/internships/{it['id']}")
                assert r.status_code == code, (prefix, st, who.role, _d(r))
        row = sql("SELECT status::text AS s, reviewed_by_id FROM internships WHERE id = :i",
                  {"i": it["id"]}, tenant=a.tenant)
        assert len(row) == 1 and row[0]["s"] == st and row[0]["reviewed_by_id"] is not None


def test_fix_owner_still_deletes_undecided_records(campus, mock_ai, count_rows):
    a = campus.a
    proj = _project(a.student, mates=[a.student2.id], title="Undecided").json()
    it = _intern(a.student, org="Undecided Org").json()
    assert a.student2.delete(f"/v1/projects/{proj['id']}").status_code == 403  # not owner
    assert a.student2.delete(f"/v1/internships/{it['id']}").status_code == 403
    assert a.student.delete(f"/v1/projects/{proj['id']}").status_code == 204
    assert a.student.delete(f"/internships/{it['id']}").status_code == 204    # legacy alias
    assert count_rows(a.tenant, "projects", "id = :i", {"i": proj["id"]}) == 0
    assert count_rows(a.tenant, "project_members", "project_id = :i", {"i": proj["id"]}) == 0
    assert count_rows(a.tenant, "internships", "id = :i", {"i": it["id"]}) == 0


def test_fix_admin_deletes_decided_records_with_audit(campus, mock_ai, sql, count_rows):
    a, b = campus.a, campus.b
    proj = _project(a.student, mates=[a.student2.id], title="Admin removes").json()
    m = proj["members"][0]["id"]
    assert a.teacher.patch(f"/v1/projects/members/{m}/decision", json={"status": "flagged"}).status_code == 200
    it = _intern(a.student, org="Admin Org").json()
    assert a.teacher.patch(f"/v1/internships/{it['id']}/decision", json={"status": "verified"}).status_code == 200
    assert a.admin.delete(f"/v1/projects/{proj['id']}").status_code == 204
    assert a.admin.delete(f"/v1/internships/{it['id']}").status_code == 204
    assert count_rows(a.tenant, "projects", "id = :i", {"i": proj["id"]}) == 0
    assert count_rows(a.tenant, "project_members", "project_id = :i", {"i": proj["id"]}) == 0
    assert count_rows(a.tenant, "internships", "id = :i", {"i": it["id"]}) == 0
    for ttype, tid, action in (("project", proj["id"], "project.delete"),
                               ("internship", it["id"], "internship.delete")):
        rows = _audit(sql, a.tenant, ttype, tid)
        assert len(rows) == 1, (ttype, rows)
        assert (rows[0]["action"], rows[0]["actor_id"], rows[0]["tenant_id"]) == (action, a.admin.id, a.tenant)
        assert "decision record" in rows[0]["summary"]
        assert _audit(sql, b.tenant, ttype, tid) == []
        assert any(x["target_id"] == str(tid) for x in a.admin.get(f"/v1/audit?action={action}").json())
        assert not any(x["target_id"] == str(tid) for x in b.admin.get(f"/v1/audit?action={action}").json())


@pytest.mark.parametrize("kind", ["project", "internship"])
def test_fix_audit_failure_rolls_back_deletion(campus, mock_ai, sql, count_rows, monkeypatch, kind):
    """If the audit row cannot be written, the decided record must survive."""
    import app.routers.internship as internship_router
    import app.routers.projects as projects_router

    a = campus.a
    if kind == "project":
        rec = _project(a.student, mates=[a.student2.id], title="Atomic").json()
        mid = rec["members"][0]["id"]
        assert a.teacher.patch(f"/v1/projects/members/{mid}/decision", json={"status": "verified"}).status_code == 200
        module, url, table = projects_router, f"/v1/projects/{rec['id']}", "projects"
    else:
        rec = _intern(a.student, org="Atomic Org").json()
        assert a.teacher.patch(f"/v1/internships/{rec['id']}/decision", json={"status": "verified"}).status_code == 200
        module, url, table = internship_router, f"/v1/internships/{rec['id']}", "internships"

    def broken_audit(db, actor, action, summary, target_type=None, target_id=None):
        from app.models import AuditLog
        db.add(AuditLog(actor_id=actor.id, tenant_id=actor.tenant_id, actor_email=actor.email,
                        action="x" * 500, target_type=target_type,  # > String(60): DB error
                        target_id=str(target_id), summary=summary))

    monkeypatch.setattr(module, "record_audit", broken_audit)
    r = a.admin.delete(url)
    assert r.status_code == 500, _d(r)
    assert count_rows(a.tenant, table, "id = :i", {"i": rec["id"]}) == 1  # rolled back
    if kind == "project":
        assert count_rows(a.tenant, "project_members", "project_id = :i", {"i": rec["id"]}) == 2
    assert _audit(sql, a.tenant, kind, rec["id"]) == []
