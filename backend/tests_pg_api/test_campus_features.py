"""Phase 5L - remaining core campus features through the REAL /v1 API.

Workflows (each: same-tenant success + role denials + cross-tenant denials):
  L  leave / OD requests and staff approval or rejection
  T  timetable creation and student viewing
  M  study-hub materials creation and viewing
  A  announcements and their notification fan-out; notification read state

Uses the API-built `campus` fixture (two synthetic institutes, every user
logged in via /v1/auth/login with its own cookie session). Recruiters are
onboarded through invite -> public accept -> real login. Nothing external is
called; assertion messages carry only codes / ids / server details.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from flowkit import EMAIL_DOMAIN, MARK, _d


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _extra_user(campus, t, role, label):
    """Admin-created account (role != recruiter) + real login."""
    email, pw = f"{label}-{campus.run}@{EMAIL_DOMAIN}", secrets.token_urlsafe(14)
    r = t.admin.post("/v1/admin/users", json={
        "email": email, "full_name": f"Synthetic {label}", "password": pw, "role": role})
    assert r.status_code == 201, _d(r)
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


def _second_section(t):
    r = t.admin.post(f"/v1/admin/departments/{t.dept}/sections", json={"name": "S2"})
    assert r.status_code == 201, _d(r)
    return r.json()["id"]


def _leave(student, start, end=None, *, kind="leave", category="medical", title="Fever"):
    r = student.post("/v1/leave", json={
        "request_type": kind, "category": category, "title": title,
        "start_date": str(start), "end_date": str(end or start)})
    assert r.status_code == 201, _d(r)
    return r.json()


# ==========================================================================
# L - Leave / OD
# ==========================================================================
def test_l_student_applies_and_teacher_approves_with_attendance_condoned(campus):
    a, b = campus.a, campus.b
    day = date(2031, 4, 7)
    # Teacher marks the student absent; an approved leave must condone it.
    m = a.teacher.post("/v1/attendance/mark", json={
        "section_id": a.section, "date": str(day),
        "records": [{"student_id": a.student.id, "status": "absent"}]})
    assert m.status_code == 200, _d(m)
    req = _leave(a.student, day)
    assert req["status"] == "pending" and req["student_id"] == a.student.id
    assert req["section_id"] == a.section and req["days"] == 1
    inbox = a.teacher.get("/v1/leave?status_filter=pending")
    assert inbox.status_code == 200 and req["id"] in {x["id"] for x in inbox.json()}
    assert req["id"] not in {x["id"] for x in b.teacher.get("/v1/leave").json()}
    d = a.teacher.patch(f"/v1/leave/{req['id']}/decision",
                        json={"status": "approved", "review_note": "get well"})
    assert d.status_code == 200 and d.json()["status"] == "approved", _d(d)
    assert d.json()["reviewed_by_id"] == a.teacher.id
    again = a.teacher.patch(f"/v1/leave/{req['id']}/decision", json={"status": "rejected"})
    assert again.status_code == 409, _d(again)
    mine = {x["id"]: x for x in a.student.get("/v1/leave/me").json()}
    assert mine[req["id"]]["status"] == "approved"
    att = [x for x in a.student.get("/v1/attendance/me").json() if x["date"] == str(day)]
    assert len(att) == 1 and att[0]["status"] == "absent" and att[0]["condoned"] is True


def test_l_rejection_and_cancellation(campus):
    a = campus.a
    rej = _leave(a.student, date(2031, 4, 8), title="Personal", category="personal")
    r = a.admin.patch(f"/v1/leave/{rej['id']}/decision", json={"status": "rejected"})
    assert r.status_code == 200 and r.json()["status"] == "rejected", _d(r)
    assert a.student.post(f"/v1/leave/{rej['id']}/cancel").status_code == 409
    pend = _leave(a.student, date(2031, 4, 9))
    assert a.student2.post(f"/v1/leave/{pend['id']}/cancel").status_code == 403
    c = a.student.post(f"/v1/leave/{pend['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled", _d(c)
    assert a.teacher.patch(f"/v1/leave/{pend['id']}/decision",
                           json={"status": "approved"}).status_code == 409


def test_l_role_and_cross_tenant_denials(campus, count_rows):
    a, b = campus.a, campus.b
    req = _leave(a.student, date(2031, 4, 10))
    url = f"/v1/leave/{req['id']}/decision"
    for who, code in ((a.student, 403), (a.student2, 403), (a.tpo, 403), (b.teacher, 404), (b.admin, 404)):
        r = who.patch(url, json={"status": "approved"})
        assert r.status_code == code, (who.role, _d(r))
    for who, code in ((a.student, 403), (b.student, 403)):
        assert who.get("/v1/leave").status_code == code, who.role  # staff inbox
    assert a.student2.get(f"/v1/leave/{req['id']}").status_code == 403
    for who in (b.student, b.teacher):
        assert who.get(f"/v1/leave/{req['id']}").status_code == 404
        assert who.post(f"/v1/leave/{req['id']}/cancel").status_code == 404
        assert who.delete(f"/v1/leave/{req['id']}").status_code == 404
    assert a.student2.delete(f"/v1/leave/{req['id']}").status_code == 403
    assert a.teacher.get(f"/v1/leave/{req['id']}").status_code == 200  # staff may read
    # Validation: end before start, wrong category for the type.
    for body in ({"request_type": "leave", "category": "medical", "title": "x",
                  "start_date": "2031-04-12", "end_date": "2031-04-11"},
                 {"request_type": "leave", "category": "fest", "title": "x",
                  "start_date": "2031-04-12", "end_date": "2031-04-12"}):
        assert a.student.post("/v1/leave", json=body).status_code == 422
    assert count_rows(a.tenant, "leave_requests", "status = 'pending'", {}) == 1


def test_l_bulk_od_only_own_tenant_students(campus):
    a, b = campus.a, campus.b
    ids = [a.student.id, a.student2.id, b.student.id, a.teacher.id]
    body = {"student_ids": ids, "category": "fest", "title": "Synthetic Fest",
            "start_date": "2031-05-01", "end_date": "2031-05-02"}
    for who in (a.student, a.tpo):
        assert who.post("/v1/leave/bulk-od", json=body).status_code == 403, who.role
    r = a.teacher.post("/v1/leave/bulk-od", json=body)
    assert r.status_code == 201, _d(r)
    out = r.json()
    assert out["created"] == 2 and sorted(out["skipped"]) == sorted([b.student.id, a.teacher.id])
    assert {e["student_id"] for e in out["entries"]} == {a.student.id, a.student2.id}
    assert all(e["status"] == "approved" and e["days"] == 2 for e in out["entries"])
    assert b.student.get("/v1/leave/me").json() == []


def test_l_recruiters_cannot_file_leave_or_od(campus, count_rows):
    """Leave/OD rows describe a student's absence; an external recruiter
    account must not be able to create them in the institute."""
    rec = _recruiter(campus, campus.a, "leave")
    r = rec.post("/v1/leave", json={"request_type": "leave", "category": "personal",
                                    "title": "x", "start_date": "2031-06-01",
                                    "end_date": "2031-06-01"})
    created = count_rows(campus.a.tenant, "leave_requests", "student_id = :u", {"u": rec.id})
    assert r.status_code == 403 and created == 0, (
        f"recruiter filed a leave request: {_d(r)}; rows created={created}")


# ==========================================================================
# T - Timetable
# ==========================================================================
def _slot(staff, section, *, day=0, start="09:00", end="10:00", teacher_id=None, expect=201):
    body = {"section_id": section, "day_of_week": day, "start_time": start,
            "end_time": end, "room": "R101"}
    if teacher_id is not None:
        body["teacher_id"] = teacher_id
    r = staff.post("/v1/timetable", json=body)
    assert r.status_code == expect, _d(r)
    return r.json() if expect == 201 else None


def test_t_teacher_creates_slot_and_students_view_their_section(campus):
    a, b = campus.a, campus.b
    slot = _slot(a.teacher, a.section, teacher_id=a.teacher.id)
    assert slot["section_id"] == a.section
    for s in (a.student, a.student2):
        mine = s.get("/v1/timetable/me")
        assert mine.status_code == 200 and [x["id"] for x in mine.json()] == [slot["id"]]
        assert s.get(f"/v1/timetable/{slot['id']}").status_code == 200
    assert slot["id"] in {x["id"] for x in a.teacher.get("/v1/timetable/teaching").json()}
    assert slot["id"] in {x["id"] for x in a.admin.get(f"/v1/timetable?section_id={a.section}").json()}
    assert b.student.get("/v1/timetable/me").json() == []
    _slot(a.teacher, a.section, start="09:00", end="09:30", expect=409)  # clash
    _slot(a.teacher, a.section, start="11:00", end="10:00", expect=422)  # times


def test_t_role_and_cross_tenant_denials(campus):
    a, b = campus.a, campus.b
    slot = _slot(a.teacher, a.section, day=1)
    sec2 = _second_section(a)
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/section",
                         json={"section_id": sec2}).status_code == 200
    rec = _recruiter(campus, a, "tt")
    for who in (a.student, a.tpo, rec):
        _slot(who, a.section, day=2, expect=403)
    _slot(b.teacher, a.section, day=2, expect=404)                 # other institute's section
    _slot(a.teacher, a.section, day=2, teacher_id=b.teacher.id, expect=404)  # foreign teacher
    _slot(a.teacher, a.section, day=2, teacher_id=a.student.id, expect=422)  # not a teacher
    assert a.student2.get(f"/v1/timetable/{slot['id']}").status_code == 403  # other section
    assert rec.get(f"/v1/timetable/{slot['id']}").status_code == 403
    for who in (b.student, b.teacher):
        assert who.get(f"/v1/timetable/{slot['id']}").status_code == 404
    assert b.teacher.patch(f"/v1/timetable/{slot['id']}", json={"room": "X"}).status_code == 404
    assert b.admin.delete(f"/v1/timetable/{slot['id']}").status_code == 404
    other_teacher = _extra_user(campus, a, "teacher", "teacher2-a")
    assert other_teacher.patch(f"/v1/timetable/{slot['id']}", json={"room": "X"}).status_code == 403
    ok = a.admin.patch(f"/v1/timetable/{slot['id']}", json={"room": "R202"})
    assert ok.status_code == 200 and ok.json()["room"] == "R202"
    assert a.student.get("/v1/timetable").status_code == 403  # staff listing


# ==========================================================================
# M - Study-hub materials
# ==========================================================================
def _material(staff, section, title="Synthetic Notes", expect=201):
    r = staff.post("/v1/materials", json={"section_id": section, "title": title,
                                          "content": "Synthetic content", "category": "notes"})
    assert r.status_code == expect, _d(r)
    return r.json() if expect == 201 else None


def test_m_teacher_publishes_and_section_students_view(campus):
    a, b = campus.a, campus.b
    mat = _material(a.teacher, a.section)
    for s in (a.student, a.student2):
        assert mat["id"] in {x["id"] for x in s.get("/v1/materials/me").json()}
        one = s.get(f"/v1/materials/{mat['id']}")
        assert one.status_code == 200 and one.json()["content"] == "Synthetic content"
    assert mat["id"] in {x["id"] for x in a.teacher.get(f"/v1/materials?section_id={a.section}").json()}
    assert b.student.get("/v1/materials/me").json() == []
    assert mat["id"] not in {x["id"] for x in b.teacher.get("/v1/materials").json()}
    r = a.teacher.post("/v1/materials", json={"section_id": a.section, "title": "Empty"})
    assert r.status_code == 400, _d(r)  # needs content or link


def test_m_role_and_cross_tenant_denials(campus):
    a, b = campus.a, campus.b
    mat = _material(a.teacher, a.section)
    sec2 = _second_section(a)
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/section",
                         json={"section_id": sec2}).status_code == 200
    rec = _recruiter(campus, a, "mat")
    for who in (a.student, a.tpo, rec):
        _material(who, a.section, expect=403)
    _material(b.teacher, a.section, expect=404)  # other institute's section
    assert a.student2.get(f"/v1/materials/{mat['id']}").status_code == 403
    assert mat["id"] not in {x["id"] for x in a.student2.get("/v1/materials/me").json()}
    assert rec.get(f"/v1/materials/{mat['id']}").status_code == 403
    for who in (b.student, b.teacher):
        assert who.get(f"/v1/materials/{mat['id']}").status_code == 404
    assert b.admin.delete(f"/v1/materials/{mat['id']}").status_code == 404
    other_teacher = _extra_user(campus, a, "teacher", "teacher3-a")
    assert other_teacher.delete(f"/v1/materials/{mat['id']}").status_code == 403
    assert a.student.get("/v1/materials").status_code == 403  # staff listing
    assert a.teacher.delete(f"/v1/materials/{mat['id']}").status_code == 204
    assert a.student.get(f"/v1/materials/{mat['id']}").status_code == 404


# ==========================================================================
# A - Announcements + notifications
# ==========================================================================
def _announce(admin, audience="all", title="Synthetic notice"):
    r = admin.post("/v1/announcements", json={"title": title, "body": "b", "audience": audience})
    assert r.status_code == 201, _d(r)
    return r.json()


def _notes(actor, unread_only=False):
    r = actor.get(f"/v1/notifications{'?unread_only=true' if unread_only else ''}")
    assert r.status_code == 200, _d(r)
    return r.json()


def test_a_admin_announces_to_audience_and_notifications_fan_out(campus):
    a, b = campus.a, campus.b
    ann = _announce(a.admin, audience="student", title="Students only")
    for s in (a.student, a.student2):
        assert ann["id"] in {x["id"] for x in s.get("/v1/announcements").json()}
        assert any(n["title"] == "Students only" and n["type"] == "announcement"
                   for n in _notes(s))
    for who in (a.teacher, a.tpo):
        assert ann["id"] not in {x["id"] for x in who.get("/v1/announcements").json()}
        assert not any(n["title"] == "Students only" for n in _notes(who))
    assert ann["id"] in {x["id"] for x in a.admin.get("/v1/announcements").json()}
    assert not any(n["title"] == "Students only" for n in _notes(a.admin))  # poster excluded
    for who in (b.student, b.admin):
        assert ann["id"] not in {x["id"] for x in who.get("/v1/announcements").json()}
        assert not any(n["title"] == "Students only" for n in _notes(who))
    everyone = _announce(a.admin, audience="all", title="Everyone")
    assert everyone["id"] in {x["id"] for x in a.teacher.get("/v1/announcements").json()}


def test_a_announcement_role_and_cross_tenant_denials(campus):
    a, b = campus.a, campus.b
    ann = _announce(a.admin)
    for who in (a.teacher, a.tpo, a.student):
        r = who.post("/v1/announcements", json={"title": "x", "body": "y"})
        assert r.status_code == 403, (who.role, _d(r))
        assert who.delete(f"/v1/announcements/{ann['id']}").status_code == 403
    assert b.admin.delete(f"/v1/announcements/{ann['id']}").status_code == 404
    assert a.admin.post("/v1/announcements", json={
        "title": "x", "body": "y", "audience": "recruiter"}).status_code == 422
    assert a.admin.delete(f"/v1/announcements/{ann['id']}").status_code == 204
    assert ann["id"] not in {x["id"] for x in a.student.get("/v1/announcements").json()}


def test_a_notification_read_state_is_per_user(campus):
    a, b = campus.a, campus.b
    _announce(a.admin, audience="student", title="N1")
    _announce(a.admin, audience="student", title="N2")
    s1, s2 = a.student, a.student2
    assert s1.get("/v1/notifications/unread-count").json()["unread"] == 2
    n1 = next(n for n in _notes(s1) if n["title"] == "N1")
    # Nobody else can mark s1's notification read.
    for who in (s2, a.admin, b.student):
        r = who.post(f"/v1/notifications/{n1['id']}/read")
        assert r.status_code == 404, (who.role, _d(r))
    ok = s1.post(f"/v1/notifications/{n1['id']}/read")
    assert ok.status_code == 200 and ok.json()["is_read"] is True
    assert s1.get("/v1/notifications/unread-count").json()["unread"] == 1
    assert [n["title"] for n in _notes(s1, unread_only=True)] == ["N2"]
    assert s2.post("/v1/notifications/read-all").json()["updated"] == 2
    assert s1.get("/v1/notifications/unread-count").json()["unread"] == 1  # unaffected
    assert b.student.get("/v1/notifications/unread-count").json()["unread"] == 0


def test_a_recruiters_do_not_receive_internal_announcements(campus):
    """Recruiters are external company accounts: institute-wide ('all')
    announcements must not be listed to them or fanned out as notifications."""
    rec = _recruiter(campus, campus.a, "ann")
    ann = _announce(campus.a.admin, audience="all", title="Internal notice")
    listed = ann["id"] in {x["id"] for x in rec.get("/v1/announcements").json()}
    notified = any(n["title"] == "Internal notice" for n in _notes(rec))
    assert not listed and not notified, (
        f"recruiter sees 'all' announcement={listed}; got notification={notified}")


# ==========================================================================
# Phase 5L fixes - regression tests
# ==========================================================================
ALIASES = ("/v1", "")  # versioned + deprecated unversioned mount


def test_fix_only_students_submit_leave_on_both_aliases(campus, count_rows):
    a = campus.a
    rec = _recruiter(campus, a, "leavefix")
    body = {"request_type": "leave", "category": "personal", "title": "x",
            "start_date": "2031-07-01", "end_date": "2031-07-01"}
    for prefix in ALIASES:
        for who in (a.teacher, a.tpo, a.admin, rec):
            r = who.post(f"{prefix}/leave", json=body)
            assert r.status_code == 403, (prefix, who.role, _d(r))
    staff_ids = [a.teacher.id, a.tpo.id, a.admin.id, rec.id]
    assert count_rows(a.tenant, "leave_requests", "student_id = ANY(:ids)", {"ids": staff_ids}) == 0
    # Students still submit, on either alias; staff bulk OD still works.
    _leave(a.student, date(2031, 7, 2))
    r = a.student2.post("/leave", json={**body, "start_date": "2031-07-03", "end_date": "2031-07-03"})
    assert r.status_code == 201, _d(r)
    od = a.teacher.post("/v1/leave/bulk-od", json={
        "student_ids": [a.student.id], "category": "sports", "title": "Meet",
        "start_date": "2031-07-04", "end_date": "2031-07-04"})
    assert od.status_code == 201 and od.json()["created"] == 1, _d(od)


def test_fix_students_cannot_delete_decision_records(campus, sql):
    a = campus.a
    approved = _leave(a.student, date(2031, 8, 1))
    rejected = _leave(a.student, date(2031, 8, 2))
    approved_then_cancelled = _leave(a.student, date(2031, 8, 3))
    for req, decision in ((approved, "approved"), (rejected, "rejected"),
                          (approved_then_cancelled, "approved")):
        assert a.teacher.patch(f"/v1/leave/{req['id']}/decision",
                               json={"status": decision}).status_code == 200
    # Approved -> cancelled via the (preserved) admin path; the decision is
    # still recorded. Students may no longer cancel approved requests.
    c = a.admin.post(f"/v1/leave/{approved_then_cancelled['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    for prefix in ALIASES:
        for req in (approved, rejected, approved_then_cancelled):
            r = a.student.delete(f"{prefix}/leave/{req['id']}")
            assert r.status_code == 403, (prefix, req["id"], _d(r))
    ids = [approved["id"], rejected["id"], approved_then_cancelled["id"]]
    kept = sql("SELECT id, status::text AS s FROM leave_requests WHERE id = ANY(:ids) ORDER BY id",
               {"ids": ids}, tenant=a.tenant)
    assert [(k["id"], k["s"]) for k in kept] == [
        (approved["id"], "approved"), (rejected["id"], "rejected"),
        (approved_then_cancelled["id"], "cancelled")]
    mine = {x["id"]: x["status"] for x in a.student.get("/v1/leave/me").json()}
    assert all(i in mine for i in ids)


def test_fix_students_still_cancel_and_delete_undecided_own_requests(campus, count_rows):
    a, b = campus.a, campus.b
    pend = _leave(a.student, date(2031, 9, 1))
    cancelled = _leave(a.student, date(2031, 9, 2))
    # Same-tenant success: cancel own pending, delete own undecided rows.
    c = a.student.post(f"/v1/leave/{cancelled['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    # Unauthorized roles / other students / other institute cannot touch them.
    assert a.student2.post(f"/v1/leave/{pend['id']}/cancel").status_code == 403
    for who, code in ((a.student2, 403), (a.teacher, 403), (a.tpo, 403), (b.student, 404), (b.admin, 404)):
        r = who.delete(f"/v1/leave/{pend['id']}")
        assert r.status_code == code, (who.role, _d(r))
    assert a.student.delete(f"/v1/leave/{pend['id']}").status_code == 204
    assert a.student.delete(f"/v1/leave/{cancelled['id']}").status_code == 204
    assert count_rows(a.tenant, "leave_requests", "id = ANY(:ids)",
                      {"ids": [pend["id"], cancelled["id"]]}) == 0


def test_fix_admin_governance_delete_preserved(campus, count_rows):
    """Unchanged existing behaviour: an institute admin may still remove a
    decided request (explicit governance action, not a student self-erase)."""
    a = campus.a
    req = _leave(a.student, date(2031, 9, 5))
    assert a.teacher.patch(f"/v1/leave/{req['id']}/decision", json={"status": "approved"}).status_code == 200
    assert a.admin.delete(f"/v1/leave/{req['id']}").status_code == 204
    assert count_rows(a.tenant, "leave_requests", "id = :i", {"i": req["id"]}) == 0


def test_fix_recruiters_excluded_internal_roles_keep_announcements(campus):
    a, b = campus.a, campus.b
    rec = _recruiter(campus, a, "annfix")
    ann_all = _announce(a.admin, audience="all", title="All hands")
    for aud in ("student", "teacher", "tpo"):
        _announce(a.admin, audience=aud, title=f"Only {aud}")
    for prefix in ALIASES:
        r = rec.get(f"{prefix}/announcements")
        assert r.status_code == 200 and r.json() == [], (prefix, _d(r))
    assert rec.get("/v1/notifications/unread-count").json()["unread"] == 0
    assert _notes(rec) == []
    # Internal roles keep "all" (listing + notification); poster excluded.
    for who in (a.student, a.teacher, a.tpo):
        assert ann_all["id"] in {x["id"] for x in who.get("/v1/announcements").json()}, who.role
        assert any(n["title"] == "All hands" for n in _notes(who)), who.role
    assert ann_all["id"] in {x["id"] for x in a.admin.get("/v1/announcements").json()}
    assert not any(n["title"] == "All hands" for n in _notes(b.student))


def test_fix_pre_existing_recruiter_notifications_are_kept_not_deleted(campus, sql):
    """A notification delivered to a recruiter BEFORE the 5L fix (simulated by
    a direct synthetic insert) is never deleted. Updated for the approved
    follow-up policy: it is now HIDDEN from the recruiter (list, unread count,
    mark-read) and left unmodified in storage."""
    a = campus.a
    rec = _recruiter(campus, a, "oldnote")
    nid = sql("INSERT INTO notifications (tenant_id, user_id, type, title, is_read) "
              "VALUES (:t, :u, 'announcement', 'Pre-fix notice', false) RETURNING id",
              {"t": a.tenant, "u": rec.id}, tenant=a.tenant)[0]["id"]
    _announce(a.admin, audience="all", title="Post-fix notice")
    assert _notes(rec) == []
    assert rec.get("/v1/notifications/unread-count").json()["unread"] == 0
    assert rec.post(f"/v1/notifications/{nid}/read").status_code == 404
    assert sql("SELECT count(*) AS n, bool_and(NOT is_read) AS unread FROM notifications "
               "WHERE id = :i", {"i": nid}, tenant=a.tenant)[0] == {"n": 1, "unread": True}


# ==========================================================================
# Leave-cancellation rule - regression tests
# ==========================================================================
def _row(sql, tenant, req_id):
    return sql("SELECT status::text AS status, reviewed_by_id, review_note, reviewed_at "
               "FROM leave_requests WHERE id = :i", {"i": req_id}, tenant=tenant)[0]


def test_cancel_student_may_cancel_own_pending(campus, sql):
    a = campus.a
    for prefix in ALIASES:
        req = _leave(a.student, date(2031, 10, 1 if prefix else 2))
        r = a.student.post(f"{prefix}/leave/{req['id']}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled", (prefix, _d(r))
        row = _row(sql, a.tenant, req["id"])
        assert row["status"] == "cancelled" and row["reviewed_at"] is None


@pytest.mark.parametrize("decision,code", [("approved", 403), ("rejected", 409)])
def test_cancel_student_cannot_cancel_decided_request(campus, sql, decision, code):
    """Own but decided: denied, and the decision + record are unchanged."""
    a = campus.a
    req = _leave(a.student, date(2031, 10, 5))
    d = a.teacher.patch(f"/v1/leave/{req['id']}/decision",
                        json={"status": decision, "review_note": "synthetic decision"})
    assert d.status_code == 200, _d(d)
    before = _row(sql, a.tenant, req["id"])
    for prefix in ALIASES:
        r = a.student.post(f"{prefix}/leave/{req['id']}/cancel")
        assert r.status_code == code, (prefix, _d(r))
    after = _row(sql, a.tenant, req["id"])
    assert after == before and after["status"] == decision
    assert after["reviewed_by_id"] == a.teacher.id and after["review_note"] == "synthetic decision"
    mine = {x["id"]: x["status"] for x in a.student.get("/v1/leave/me").json()}
    assert mine[req["id"]] == decision


def test_cancel_non_owner_and_cross_tenant_denied(campus, sql):
    a, b = campus.a, campus.b
    req = _leave(a.student, date(2031, 10, 8))
    for who, code in ((a.student2, 403), (a.teacher, 403), (a.tpo, 403),
                      (b.student, 404), (b.teacher, 404), (b.admin, 404)):
        r = who.post(f"/v1/leave/{req['id']}/cancel")
        assert r.status_code == code, (who.role, _d(r))
    assert _row(sql, a.tenant, req["id"])["status"] == "pending"


def test_cancel_staff_workflow_preserved(campus, sql):
    """Unchanged staff paths: an admin may cancel an approved request, and the
    teacher who raised a bulk OD may cancel that (approved) OD row."""
    a = campus.a
    req = _leave(a.student, date(2031, 10, 10))
    assert a.teacher.patch(f"/v1/leave/{req['id']}/decision", json={"status": "approved"}).status_code == 200
    r = a.admin.post(f"/v1/leave/{req['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", _d(r)
    assert _row(sql, a.tenant, req["id"])["reviewed_by_id"] == a.teacher.id  # decision kept
    od = a.teacher.post("/v1/leave/bulk-od", json={
        "student_ids": [a.student.id], "category": "sports", "title": "Meet",
        "start_date": "2031-10-12", "end_date": "2031-10-12"}).json()["entries"][0]
    assert a.student.post(f"/v1/leave/{od['id']}/cancel").status_code == 403  # student: approved
    r = a.teacher.post(f"/v1/leave/{od['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", _d(r)


# ==========================================================================
# Decided-record deletion (admin only, audited) - regression tests
# ==========================================================================
def _audit(sql, tenant, req_id):
    return sql("SELECT action, actor_id, target_type, tenant_id, summary FROM audit_logs "
               "WHERE target_type = 'leave_request' AND target_id = :t ORDER BY id",
               {"t": str(req_id)}, tenant=tenant)


def _decided_bulk_od(t, student_id, day):
    r = t.teacher.post("/v1/leave/bulk-od", json={
        "student_ids": [student_id], "category": "sports", "title": "Meet",
        "start_date": day, "end_date": day})
    assert r.status_code == 201, _d(r)
    return r.json()["entries"][0]


def test_del_teacher_cannot_delete_own_bulk_od_but_can_cancel(campus, sql, count_rows):
    a = campus.a
    od = _decided_bulk_od(a, a.student.id, "2031-11-01")
    assert od["applied_by_id"] == a.teacher.id and od["status"] == "approved"
    for prefix in ALIASES:
        r = a.teacher.delete(f"{prefix}/leave/{od['id']}")
        assert r.status_code == 403, (prefix, _d(r))
    c = a.teacher.post(f"/v1/leave/{od['id']}/cancel")  # still allowed
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert a.teacher.delete(f"/v1/leave/{od['id']}").status_code == 403  # cancelled != erasable
    assert count_rows(a.tenant, "leave_requests", "id = :i", {"i": od["id"]}) == 1
    assert _audit(sql, a.tenant, od["id"]) == []


def test_del_only_admin_deletes_decided_records(campus, count_rows):
    a, b = campus.a, campus.b
    od = _decided_bulk_od(a, a.student.id, "2031-11-02")
    req = _leave(a.student, date(2031, 11, 3))
    assert a.teacher.patch(f"/v1/leave/{req['id']}/decision", json={"status": "rejected"}).status_code == 200
    for rid in (od["id"], req["id"]):
        for who, code in ((a.student, 403), (a.student2, 403), (a.teacher, 403), (a.tpo, 403),
                          (b.admin, 404), (b.teacher, 404)):
            r = who.delete(f"/v1/leave/{rid}")
            assert r.status_code == code, (rid, who.role, _d(r))
    assert count_rows(a.tenant, "leave_requests", "id = ANY(:ids)", {"ids": [od["id"], req["id"]]}) == 2


def test_del_admin_delete_is_audited_in_same_tenant(campus, sql, count_rows):
    a, b = campus.a, campus.b
    od = _decided_bulk_od(a, a.student.id, "2031-11-04")
    r = a.admin.delete(f"/v1/leave/{od['id']}")
    assert r.status_code == 204, _d(r)
    assert count_rows(a.tenant, "leave_requests", "id = :i", {"i": od["id"]}) == 0
    rows = _audit(sql, a.tenant, od["id"])
    assert len(rows) == 1
    row = rows[0]
    assert (row["action"], row["actor_id"], row["tenant_id"]) == ("leave.delete", a.admin.id, a.tenant)
    assert "decision record" in row["summary"] and f"#{od['id']}" in row["summary"]
    assert _audit(sql, b.tenant, od["id"]) == []  # never visible to another institute
    # Visible through the admin audit API of the SAME institute only.
    mine = a.admin.get("/v1/audit?action=leave.delete").json()
    assert any(x["target_id"] == str(od["id"]) for x in mine)
    assert not any(x["target_id"] == str(od["id"]) for x in b.admin.get("/v1/audit?action=leave.delete").json())
    assert a.teacher.get("/v1/audit").status_code == 403


def test_del_admin_delete_and_audit_are_atomic(campus, sql, count_rows, monkeypatch):
    """If the audit row cannot be written, the deletion must not happen."""
    import app.routers.leaveod as leaveod

    a = campus.a
    od = _decided_bulk_od(a, a.student.id, "2031-11-05")

    def broken_audit(db, actor, action, summary, target_type=None, target_id=None):
        from app.models import AuditLog
        db.add(AuditLog(actor_id=actor.id, tenant_id=actor.tenant_id, actor_email=actor.email,
                        action="x" * 500, target_type=target_type,  # > String(60): DB error
                        target_id=str(target_id), summary=summary))

    monkeypatch.setattr(leaveod, "record_audit", broken_audit)
    r = a.admin.delete(f"/v1/leave/{od['id']}")
    assert r.status_code == 500, _d(r)
    assert count_rows(a.tenant, "leave_requests", "id = :i", {"i": od["id"]}) == 1  # rolled back
    assert _audit(sql, a.tenant, od["id"]) == []


def test_del_student_still_deletes_own_undecided_without_audit(campus, sql, count_rows):
    a = campus.a
    req = _leave(a.student, date(2031, 11, 6))
    assert a.student.delete(f"/v1/leave/{req['id']}").status_code == 204
    assert count_rows(a.tenant, "leave_requests", "id = :i", {"i": req["id"]}) == 0
    assert _audit(sql, a.tenant, req["id"]) == []


# ==========================================================================
# Recruiter notification filtering - regression tests
# ==========================================================================
def _seed_note(sql, t, user_id, ntype, title):
    return sql("INSERT INTO notifications (tenant_id, user_id, type, title, is_read) "
               "VALUES (:t, :u, :ty, :ti, false) RETURNING id",
               {"t": t.tenant, "u": user_id, "ty": ntype, "ti": title}, tenant=t.tenant)[0]["id"]


def test_note_recruiter_sees_other_types_not_announcements(campus, sql):
    a = campus.a
    rec = _recruiter(campus, a, "notefilter")
    hidden = _seed_note(sql, a, rec.id, "announcement", "Old internal notice")
    visible = _seed_note(sql, a, rec.id, "generic", "Company account update")
    for prefix in ALIASES:
        notes = rec.get(f"{prefix}/notifications").json()
        assert [n["id"] for n in notes] == [visible], prefix
        assert rec.get(f"{prefix}/notifications?unread_only=true").json()[0]["id"] == visible
        assert rec.get(f"{prefix}/notifications/unread-count").json()["unread"] == 1
    assert rec.post(f"/v1/notifications/{hidden}/read").status_code == 404
    ok = rec.post(f"/v1/notifications/{visible}/read")
    assert ok.status_code == 200 and ok.json()["is_read"] is True
    stored = sql("SELECT id, is_read FROM notifications WHERE id = ANY(:ids) ORDER BY id",
                 {"ids": [hidden, visible]}, tenant=a.tenant)
    assert {r["id"]: r["is_read"] for r in stored} == {hidden: False, visible: True}


def test_note_mark_all_read_never_touches_hidden_rows(campus, sql):
    a = campus.a
    rec = _recruiter(campus, a, "readall")
    hidden = _seed_note(sql, a, rec.id, "announcement", "Old internal notice")
    visible = _seed_note(sql, a, rec.id, "generic", "Visible")
    r = rec.post("/v1/notifications/read-all")
    assert r.status_code == 200 and r.json()["updated"] == 1
    stored = {x["id"]: (x["is_read"], x["read_at"]) for x in sql(
        "SELECT id, is_read, read_at FROM notifications WHERE id = ANY(:ids)",
        {"ids": [hidden, visible]}, tenant=a.tenant)}
    assert stored[hidden] == (False, None)
    assert stored[visible][0] is True
    assert rec.get("/v1/notifications/unread-count").json()["unread"] == 0


def test_note_internal_users_keep_announcement_notifications(campus, sql):
    a, b = campus.a, campus.b
    _announce(a.admin, audience="all", title="Internal all")
    for who in (a.student, a.teacher, a.tpo):
        notes = _notes(who)
        n = next(x for x in notes if x["title"] == "Internal all")
        assert n["type"] == "announcement"
        before = who.get("/v1/notifications/unread-count").json()["unread"]
        assert who.post(f"/v1/notifications/{n['id']}/read").status_code == 200
        assert who.get("/v1/notifications/unread-count").json()["unread"] == before - 1
    # Cross-user / cross-tenant: cannot read or mark another user's rows.
    s_note = next(x for x in _notes(a.student) if x["title"] == "Internal all")
    rec = _recruiter(campus, a, "noteiso")
    for who in (a.student2, rec, b.student, b.admin):
        assert who.post(f"/v1/notifications/{s_note['id']}/read").status_code == 404
