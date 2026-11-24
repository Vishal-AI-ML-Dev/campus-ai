"""Phase 9.3 + 9.4: campaigns, TPO dashboard, analytics, CSV export, preparation.

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §13-§14.
Proves (SQLite, offline):

* flag off -> every new route 404; recruiters 403; role matrix per route;
* campaigns: teacher owns own campaigns and assigns only students of sections
  they teach; TPO/admin see all; cross-institute 404; status rules; activation
  notifies; edits/assignments frozen after close; audit content-free;
* P9-D7 privacy: progress returns completion fields ONLY, no score/answer text
  leaks, and the progress/list SQL never touches reports or turns tables;
  completion = completed session inside the window (late flagged, type match);
* dashboard: genuine counts (drives by lifecycle, deadlines, applications,
  pending actions, campaign progress) and academic-year filter;
* analytics: distinct-student definitions (no double counting), filters,
  company/department/drive breakdowns, foreign ids 404;
* CSV export: role-restricted, audited, formula-injection safe;
* preparation: reuses the mock-interview gate and the drive's published
  company profile; prefill link; own completion only; drafts 404.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import event, select

import app.core.db as dbmod
import app.models as models
from app.core.config import settings
from tests.test_placement_management import (  # noqa: F401 - fixtures
    P,
    audit_rows,
    mk_drive,
    on,
    put_profile,
    put_settings,
    world,
)

NOW = datetime.now(timezone.utc)
NEW_ROUTES = [
    ("get", "/campaigns"), ("post", "/campaigns"), ("get", "/campaigns/sections"), ("get", "/campaigns/1"),
    ("put", "/campaigns/1"), ("post", "/campaigns/1/status"), ("get", "/campaigns/1/candidates"),
    ("post", "/campaigns/1/students"), ("delete", "/campaigns/1/students/1"), ("get", "/campaigns/1/progress"),
    ("get", "/me/campaigns"), ("get", "/dashboard"), ("get", "/analytics"), ("get", "/analytics/export?kind=summary"),
    ("get", "/analytics/options"), ("get", "/drives/1/preparation"),
]


def _call(client, method, path, h):
    kw = {"json": {}} if method in ("post", "put") else {}
    return getattr(client, method)(f"{P}{path}", headers=h, **kw)


def campaign_body(**kw):
    body = dict(title="Mock week", description="Practise before the drive", interview_type=None,
                starts_at=(NOW - timedelta(days=1)).isoformat(), due_at=(NOW + timedelta(days=7)).isoformat())
    body.update(kw)
    return body


def mk_campaign(client, h, **kw):
    r = client.post(f"{P}/campaigns", json=campaign_body(**kw), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def teach(session, world, teacher, section):
    session.add(models.TimetableEntry(tenant_id=world["a"].id, section_id=section.id, teacher_id=teacher.id,
                                      day_of_week=0, start_time=time(9), end_time=time(10)))
    session.commit()


def mk_session(session, student, *, completed_at, status="completed", itype="hr", secret=None):
    s = models.MockInterviewSession(tenant_id=student.tenant_id, student_id=student.id, interview_type=itype,
                                    mode="text", status=status, target_role="SDE", difficulty="medium",
                                    language="en", duration_minutes=15, completed_at=completed_at)
    session.add(s)
    session.commit()
    if secret:
        session.add(models.MockInterviewTurn(tenant_id=student.tenant_id, session_id=s.id, seq=1,
                                             question="Q?", answer_text=secret))
        session.add(models.MockInterviewReport(tenant_id=student.tenant_id, session_id=s.id, overall_score=87.5,
                                               category_scores_json={"x": 1}, strengths_json=[secret],
                                               weaknesses_json=[], recommendations_json=[], rubric_version="v1"))
        session.commit()
    return s


# --- gate + RBAC --------------------------------------------------------------------
def test_new_routes_closed_when_flag_off(client, world):
    for who in ("h_tpo", "h_s1", "h_admin", "h_teacher"):
        for method, path in NEW_ROUTES:
            assert _call(client, method, path, world[who]).status_code == 404, (who, path)


def test_role_matrix(client, world, on):
    on(world["a"])
    for method, path in NEW_ROUTES:
        assert _call(client, method, path, world["h_rec"]).status_code == 403, path
    for path in ("/dashboard", "/analytics", "/analytics/export?kind=summary", "/analytics/options"):
        for who in ("h_teacher", "h_s1"):
            assert client.get(f"{P}{path}", headers=world[who]).status_code == 403, (who, path)
        assert client.get(f"{P}{path}", headers=world["h_admin"]).status_code == 200, path
    for path in ("/campaigns", "/campaigns/sections"):
        assert client.get(f"{P}{path}", headers=world["h_s1"]).status_code == 403
    for who in ("h_tpo", "h_teacher", "h_admin"):
        assert client.get(f"{P}/me/campaigns", headers=world[who]).status_code == 403
        assert client.get(f"{P}/drives/1/preparation", headers=world[who]).status_code == 403


# --- campaigns -------------------------------------------------------------------------
def test_teacher_scoping_and_ownership(client, world, on, session, make_user, token_header):
    on(world["a"], world["b"])
    ht, htpo = world["h_teacher"], world["h_tpo"]
    teach(session, world, world["teacher"], world["sec"])  # teaches CSE-A (s1), not ME-A (s2)
    secs = client.get(f"{P}/campaigns/sections", headers=ht).json()
    assert [s["name"] for s in secs] == ["CSE-A"]
    assert len(client.get(f"{P}/campaigns/sections", headers=htpo).json()) == 2
    c = mk_campaign(client, ht)
    assert c["status"] == "draft" and c["is_mine"] is True and c["created_by_role"] == "teacher"
    url = f"{P}/campaigns/{c['id']}"
    cands = client.get(f"{url}/candidates", headers=ht).json()
    assert [x["full_name"] for x in cands] == ["Asha"]
    assert client.post(f"{url}/students", json={"student_ids": [world["s2"].id]}, headers=ht).status_code == 422
    assert client.post(f"{url}/students", json={"student_ids": [world["sb"].id]}, headers=ht).status_code == 422
    assert client.post(f"{url}/students", json={"section_ids": [world["sec2"].id]}, headers=ht).status_code == 422
    r = client.post(f"{url}/students", json={"section_ids": [world["sec"].id]}, headers=ht)
    assert r.json() == {"added": 1, "already_assigned": 0, "total_assigned": 1}
    r = client.post(f"{url}/students", json={"student_ids": [world["s1"].id]}, headers=ht)
    assert r.json() == {"added": 0, "already_assigned": 1, "total_assigned": 1}
    # teacher cannot link drives or use placement filters
    d = mk_drive(session, world["a"])
    assert client.post(f"{P}/campaigns", json=campaign_body(drive_id=d.id), headers=ht).status_code == 403
    assert client.get(f"{url}/candidates?drive_id={d.id}", headers=ht).status_code == 403
    assert client.get(f"{url}/candidates?graduation_year=2027", headers=ht).status_code == 403
    # TPO sees the teacher's campaign; teacher does not see the TPO's; other institute 404
    t = mk_campaign(client, htpo, title="TPO drive prep", drive_id=d.id)
    assert t["drive_label"] == "Acme — SDE"
    assert {x["id"] for x in client.get(f"{P}/campaigns", headers=htpo).json()} == {c["id"], t["id"]}
    assert [x["id"] for x in client.get(f"{P}/campaigns", headers=ht).json()] == [c["id"]]
    assert client.get(f"{P}/campaigns/{t['id']}", headers=ht).status_code == 404
    assert client.get(url, headers=world["h_tpo_b"]).status_code == 404
    assert client.post(f"{P}/campaigns", json=campaign_body(drive_id=999999), headers=htpo).status_code == 422
    # a second teacher of the same institute cannot see or change it
    make_user("t2@a.edu", role=models.UserRole.teacher, tenant=world["a"])
    h2 = token_header("t2@a.edu")
    assert client.get(url, headers=h2).status_code == 404
    assert client.post(f"{url}/students", json={"student_ids": [world["s1"].id]}, headers=h2).status_code == 404
    assert client.get(f"{url}/progress", headers=h2).status_code == 404
    assert client.get(f"{P}/campaigns", headers=h2).json() == []
    rows = audit_rows(session, "placement.campaign_assign")
    assert rows and all("Asha" not in r.summary for r in rows)


def test_teacher_without_sections_and_tpo_filters(client, world, on, session, token_header):
    on(world["a"])
    c = mk_campaign(client, world["h_teacher"])
    assert client.get(f"{P}/campaigns/{c['id']}/candidates", headers=world["h_teacher"]).json() == []
    tc = mk_campaign(client, world["h_tpo"])
    d = mk_drive(session, world["a"])
    assert client.post(f"{P}/drives/{d.id}/apply", headers=world["h_s2"]).status_code == 201
    url = f"{P}/campaigns/{tc['id']}/candidates"
    assert [x["full_name"] for x in client.get(f"{url}?drive_id={d.id}", headers=world["h_tpo"]).json()] == ["Bala"]
    assert client.get(f"{url}?drive_id={d.id}&application_status=shortlisted", headers=world["h_tpo"]).json() == []
    put_profile(client, world["h_tpo"], world["s1"], graduation_year=2027)
    assert [x["full_name"] for x in client.get(f"{url}?graduation_year=2027", headers=world["h_tpo"]).json()] == ["Asha"]
    assert [x["full_name"] for x in client.get(f"{url}?department_id={world['dept2'].id}", headers=world["h_tpo"]).json()] == ["Bala"]


def test_campaign_status_rules_notifications_and_edits(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    c = mk_campaign(client, h, interview_type="technical", target_role="Backend")
    url = f"{P}/campaigns/{c['id']}"
    client.post(f"{url}/students", json={"student_ids": [world["s1"].id, world["s2"].id]}, headers=h)

    def go(to):
        return client.post(f"{url}/status", json={"to": to}, headers=h)

    assert go("closed").status_code == 409  # draft -> closed not allowed
    assert go("active").status_code == 200
    session.expire_all()
    notes = list(session.scalars(select(models.Notification).where(models.Notification.type == "campaign_assigned")))
    assert sorted(n.user_id for n in notes) == sorted([world["s1"].id, world["s2"].id])
    assert "2 students notified" in audit_rows(session, "placement.campaign_status")[0].summary
    assert go("closed").status_code == 200
    assert client.put(url, json=campaign_body(), headers=h).status_code == 409
    assert client.post(f"{url}/students", json={"student_ids": [world["s1"].id]}, headers=h).status_code == 409
    assert client.delete(f"{url}/students/{world['s1'].id}", headers=h).status_code == 409
    assert go("archived").status_code == 200
    assert go("active").status_code == 409  # archived is terminal
    assert c["id"] not in [x["id"] for x in client.get(f"{P}/campaigns", headers=h).json()]
    assert c["id"] in [x["id"] for x in client.get(f"{P}/campaigns?include_archived=true", headers=h).json()]
    # validation
    bad = campaign_body(starts_at=(NOW + timedelta(days=2)).isoformat(), due_at=(NOW + timedelta(days=1)).isoformat())
    assert client.post(f"{P}/campaigns", json=bad, headers=h).status_code == 422
    assert client.post(f"{P}/campaigns", json=campaign_body(due_at="2030-01-01T00:00:00"), headers=h).status_code == 422
    assert client.post(f"{P}/campaigns", json=campaign_body(interview_type="video"), headers=h).status_code == 422
    past = mk_campaign(client, h, starts_at=(NOW - timedelta(days=9)).isoformat(),
                       due_at=(NOW - timedelta(days=2)).isoformat())
    assert client.post(f"{P}/campaigns/{past['id']}/status", json={"to": "active"}, headers=h).status_code == 409
    # unassign while draft
    d2 = mk_campaign(client, h)
    client.post(f"{P}/campaigns/{d2['id']}/students", json={"student_ids": [world["s1"].id]}, headers=h)
    assert client.delete(f"{P}/campaigns/{d2['id']}/students/{world['s1'].id}", headers=h).status_code == 204
    assert client.delete(f"{P}/campaigns/{d2['id']}/students/{world['s1'].id}", headers=h).status_code == 404


def test_progress_is_completion_only_and_never_reads_reports(client, world, on, session, make_user, token_header):
    on(world["a"])
    h = world["h_teacher"]
    teach(session, world, world["teacher"], world["sec"])
    s3 = make_user("s3@a.edu", tenant=world["a"], full_name="Chitra")
    s4 = make_user("s4@a.edu", tenant=world["a"], full_name="Dev")
    for u in (s3, s4):
        u.section_id = world["sec"].id
    session.commit()
    c = mk_campaign(client, h, interview_type="hr", starts_at=(NOW - timedelta(days=5)).isoformat(),
                    due_at=(NOW - timedelta(days=1)).isoformat())
    url = f"{P}/campaigns/{c['id']}"
    assert client.post(f"{url}/students", json={"section_ids": [world["sec"].id]}, headers=h).json()["added"] == 3
    secret = "SECRET-ANSWER-TEXT"
    mk_session(session, world["s1"], completed_at=NOW - timedelta(days=3), secret=secret)        # on time
    mk_session(session, s3, completed_at=NOW - timedelta(hours=2))                                # late
    mk_session(session, s4, completed_at=NOW - timedelta(days=9))                                 # before start
    mk_session(session, s4, completed_at=NOW - timedelta(days=3), itype="technical")              # wrong type
    mk_session(session, s4, completed_at=None, status="in_progress")                               # not completed

    statements: list[str] = []

    def spy(_conn, _cur, stmt, *_a, **_k):
        statements.append(stmt.lower())

    event.listen(dbmod.engine, "before_cursor_execute", spy)
    try:
        r = client.get(f"{url}/progress", headers=h)
        listing = client.get(f"{P}/campaigns", headers=h)
    finally:
        event.remove(dbmod.engine, "before_cursor_execute", spy)
    assert r.status_code == 200 and listing.status_code == 200
    rows = {x["full_name"]: x for x in r.json()}
    assert {k: v["completion"] for k, v in rows.items()} == {
        "Asha": "completed", "Chitra": "completed_late", "Dev": "pending"}
    assert all(set(x) == {"student_id", "full_name", "section_name", "completion", "completed_at", "assigned_at"}
               for x in r.json())
    assert r.headers["X-Completion-Counts"] == "pending:1,completed:1,completed_late:1"
    body = r.text + listing.text
    for leak in (secret, "87.5", "overall_score", "transcript", "feedback", "strengths"):
        assert leak not in body
    assert not any("mock_interview_reports" in s or "mock_interview_turns" in s for s in statements)
    assert listing.json()[0]["completed"] == 1 and listing.json()[0]["completed_late"] == 1
    filtered = client.get(f"{url}/progress?completion=pending", headers=h).json()
    assert [x["full_name"] for x in filtered] == ["Dev"]


def test_student_sees_own_campaigns_only(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    c = mk_campaign(client, h, title="For Asha")
    client.post(f"{P}/campaigns/{c['id']}/students", json={"student_ids": [world["s1"].id]}, headers=h)
    draft = mk_campaign(client, h, title="Still draft")
    client.post(f"{P}/campaigns/{draft['id']}/students", json={"student_ids": [world["s1"].id]}, headers=h)
    client.post(f"{P}/campaigns/{c['id']}/status", json={"to": "active"}, headers=h)
    mine = client.get(f"{P}/me/campaigns", headers=world["h_s1"]).json()
    assert [x["title"] for x in mine] == ["For Asha"] and mine[0]["completion"] == "pending"
    assert client.get(f"{P}/me/campaigns", headers=world["h_s2"]).json() == []


# --- dashboard ---------------------------------------------------------------------------
def test_dashboard_genuine_counts(client, world, on, session):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    soon = mk_drive(session, world["a"], company_name="Soon")
    put_settings(client, h, soon, applications_close_at=(NOW + timedelta(days=3)).isoformat(), academic_year="2026-27")
    closed = mk_drive(session, world["a"], company_name="Closed")
    mk_drive(session, world["b"], company_name="OtherInst")
    draft = client.post(f"{P}/drives", json={"company_name": "Draft", "role_title": "X"}, headers=h).json()
    a1 = client.post(f"{P}/drives/{closed.id}/apply", headers=world["h_s1"]).json()["id"]
    client.post(f"{P}/drives/{closed.id}/apply", headers=world["h_s2"])
    client.post(f"{P}/drives/{soon.id}/apply", headers=world["h_s1"])
    client.patch(f"{P}/applications/{a1}/status", json={"status": "selected"}, headers=h)
    client.post(f"{P}/drives/{closed.id}/lifecycle", json={"to": "closed"}, headers=h)
    put_profile(client, h, world["s2"], placement_status="opted_out")
    camp = mk_campaign(client, h)
    client.post(f"{P}/campaigns/{camp['id']}/students", json={"student_ids": [world["s1"].id]}, headers=h)
    client.post(f"{P}/campaigns/{camp['id']}/status", json={"to": "active"}, headers=h)
    mk_session(session, world["s1"], completed_at=NOW)

    d = client.get(f"{P}/dashboard", headers=h).json()
    assert d["drives"] == {"total": 3, "by_lifecycle": {"draft": 1, "published": 1, "closed": 1, "completed": 0,
                                                       "cancelled": 0}, "active": 1}
    assert [x["company_name"] for x in d["upcoming_deadlines"]] == ["Soon"]
    assert d["applications"]["total"] == 3 and d["applications"]["by_status"]["selected"] == 1
    assert d["shortlisting"] == {"decided": 1, "pending": 2, "decided_percentage": 33.3}
    assert d["students"] == {"registered": 2, "available_for_placement": 0, "placed": 1, "selected": 1,
                             "without_academic_records": 1}
    assert d["campaigns"] == {"active": 1, "assigned": 1, "completed": 1}
    kinds = {a["kind"]: a["count"] for a in d["pending_actions"]}
    assert kinds == {"drafts": 1, "awaiting_decision": 1, "profiles_missing": 1}
    yr = client.get(f"{P}/dashboard?academic_year=2026-27", headers=h).json()
    assert yr["drives"]["total"] == 1 and yr["applications"]["total"] == 1
    assert client.get(f"{P}/dashboard?academic_year=26-27", headers=h).status_code == 422
    assert draft["drive"]["id"]  # sanity
    b = client.get(f"{P}/dashboard", headers=world["h_tpo_b"]).json()
    assert b["drives"]["total"] == 1 and b["applications"]["total"] == 0


# --- analytics + export ------------------------------------------------------------------
def _analytics_world(client, world, session, make_user, token_header):
    h = world["h_tpo"]
    acme1 = mk_drive(session, world["a"], company_name="Acme")
    acme2 = mk_drive(session, world["a"], company_name="acme ", role_title="QA")
    globex = mk_drive(session, world["a"], company_name="=Globex")
    put_settings(client, h, globex, academic_year="2026-27")
    s3 = make_user("s3@a.edu", tenant=world["a"], full_name="Chitra")
    s3.section_id = world["sec"].id
    session.commit()
    ids = {}
    for who, hdr in (("s1", world["h_s1"]), ("s2", world["h_s2"]), ("s3", token_header("s3@a.edu"))):
        for d in (acme1, acme2, globex):
            ids[(who, d.id)] = client.post(f"{P}/drives/{d.id}/apply", headers=hdr).json()["id"]
    st = lambda aid, s, note=None: client.patch(f"{P}/applications/{aid}/status",  # noqa: E731
                                                json={"status": s, "note": note}, headers=h)
    st(ids[("s1", acme1.id)], "selected")
    st(ids[("s1", acme2.id)], "selected")            # same student selected twice -> counted once
    st(ids[("s2", acme1.id)], "shortlisted")
    st(ids[("s2", globex.id)], "shortlisted")
    rec = models.Recruiter(tenant_id=world["a"].id, company_name="=Globex")
    session.add(rec)
    session.commit()
    session.add(models.Offer(tenant_id=world["a"].id, application_id=ids[("s2", globex.id)], recruiter_id=rec.id,
                             drive_id=globex.id, student_id=world["s2"].id, role_title="SDE",
                             status=models.OfferStatus.accepted))
    session.commit()
    client.post(f"{P}/applications/{ids[('s3', globex.id)]}/withdraw", headers=token_header("s3@a.edu"))
    put_profile(client, h, world["s1"], graduation_year=2027)
    return acme1, acme2, globex


def test_analytics_definitions_no_double_counting(client, world, on, session, make_user, token_header):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    acme1, _acme2, globex = _analytics_world(client, world, session, make_user, token_header)
    a = client.get(f"{P}/analytics", headers=h).json()
    sm = a["summary"]
    assert sm["registered_students"] == 3 and sm["applications"] == 9 and sm["applicants"] == 3
    assert sm["applications_by_status"] == {"applied": 4, "shortlisted": 2, "selected": 2, "rejected": 0, "withdrawn": 1}
    assert sm["selected_students"] == 1 and sm["shortlisted_students"] == 1
    assert sm["placed_students"] == 2 and sm["offers"] == 1 and sm["offers_by_status"]["accepted"] == 1
    assert sm["placement_denominator"] == 3 and sm["placement_percentage"] == 66.7
    assert sm["eligible_students"] == 1  # active and not placed: Chitra
    comp = {c["company"].lower(): c for c in a["companies"]}
    assert comp["acme"]["drives"] == 2 and comp["acme"]["applicants"] == 3 and comp["acme"]["selected_students"] == 1
    assert comp["acme"]["placed_students"] == 1 and comp["=globex"]["placed_students"] == 1
    dept = {d["department"]: d for d in a["departments"]}
    assert dept["Computer Science"]["registered_students"] == 2 and dept["Mechanical"]["placed_students"] == 1
    # filters
    f = client.get(f"{P}/analytics?company=ACME", headers=h).json()["summary"]
    assert f["drives"] == 2 and f["applications"] == 6 and f["placed_students"] == 1
    f = client.get(f"{P}/analytics?academic_year=2026-27", headers=h).json()["summary"]
    assert f["drives"] == 1 and f["placed_students"] == 1
    f = client.get(f"{P}/analytics?department_id={world['dept2'].id}", headers=h).json()["summary"]
    assert f["registered_students"] == 1 and f["applications"] == 3
    f = client.get(f"{P}/analytics?graduation_year=2027", headers=h).json()["summary"]
    assert f["registered_students"] == 1 and f["placed_students"] == 1
    f = client.get(f"{P}/analytics?status=shortlisted", headers=h).json()["summary"]
    assert f["applications"] == 2 and f["placed_students"] == 2  # status filters application rows only
    f = client.get(f"{P}/analytics?drive_id={acme1.id}", headers=h).json()["summary"]
    assert f["eligible_basis"].startswith("eligibility v2") and f["eligible_students"] == 3
    # foreign / unknown references
    bdrive = mk_drive(session, world["b"])
    assert client.get(f"{P}/analytics?drive_id={bdrive.id}", headers=h).status_code == 404
    assert client.get(f"{P}/analytics?department_id={world['dept_b'].id}", headers=h).status_code == 404
    assert client.get(f"{P}/analytics?status=bogus", headers=h).status_code == 422
    assert client.get(f"{P}/analytics", headers=world["h_tpo_b"]).json()["summary"]["applications"] == 0
    opts = client.get(f"{P}/analytics/options", headers=h).json()
    assert opts["academic_years"] == ["2026-27"] and len(opts["drives"]) == 3
    assert globex.id in [d["id"] for d in opts["drives"]]


def test_csv_export_safe_audited(client, world, on, session, make_user, token_header):
    on(world["a"])
    h = world["h_tpo"]
    _analytics_world(client, world, session, make_user, token_header)
    world["s1"].full_name = "+cmd|' /C calc'!A0"
    session.commit()
    for kind in ("summary", "companies", "departments", "drives", "applications"):
        r = client.get(f"{P}/analytics/export?kind={kind}", headers=h)
        assert r.status_code == 200, (kind, r.text)
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        text = r.content.decode("utf-8-sig")
        for line in text.splitlines()[1:]:
            for cell in line.split(","):
                assert not cell.lstrip('"').startswith(("=", "+", "@")), (kind, line)
    apps = client.get(f"{P}/analytics/export?kind=applications", headers=h).content.decode("utf-8-sig")
    assert "'+cmd" in apps and "'=Globex" in apps
    assert "@" not in apps.replace("'@", "")  # no email addresses exported
    assert "Roll number" in apps.splitlines()[0]
    rows = audit_rows(session, "placement.export")
    assert len(rows) == 6 and all("Asha" not in r.summary and "cmd" not in r.summary for r in rows)  # 5 kinds + 1 extra
    assert client.get(f"{P}/analytics/export?kind=everything", headers=h).status_code == 422
    assert client.get(f"{P}/analytics/export?kind=applications", headers=world["h_teacher"]).status_code == 403


def test_safe_cell_unit():
    from app.services.placement_insights import safe_cell, to_csv

    assert safe_cell("=1+1") == "'=1+1" and safe_cell("-2") == "'-2" and safe_cell("@x") == "'@x"
    assert safe_cell("\tx") == "'\tx" and safe_cell(-2) == "-2" and safe_cell(None) == "" and safe_cell(True) == "yes"
    assert to_csv(["a"], [['x,"y"']]) == 'a\r\n"x,""y"""\r\n'


# --- preparation (9.4) --------------------------------------------------------------------
def test_preparation_reuses_mock_interview(client, world, on, session, monkeypatch):
    on(world["a"])
    d = mk_drive(session, world["a"], company_name="Acme & Co", role_title="Data Analyst")
    url = f"{P}/drives/{d.id}/preparation"
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", False)
    r = client.get(url, headers=world["h_s1"]).json()
    assert r["practice_available"] is False and r["practice_link"] is None and r["company_profile"] is None
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", str(world["a"].id))
    session.add(models.MockInterviewCompanyProfile(
        tenant_id=world["a"].id, drive_id=d.id, name="Acme profile", role_title="Data Analyst",
        role_template_key="data_analyst", status="published"))
    session.commit()
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", False)
    r = client.get(url, headers=world["h_s1"]).json()
    assert r["practice_available"] is True and r["company_profile"] is None
    assert r["practice_link"] == "/dashboard/mock-interview?prep_role=Data+Analyst&prep_company=Acme+%26+Co"
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_COMPANY_ENABLED", True)
    mk_session(session, world["s1"], completed_at=NOW)
    mk_session(session, world["s2"], completed_at=NOW)
    r = client.get(url, headers=world["h_s1"]).json()
    assert r["company_profile"]["name"] == "Acme profile"
    assert r["practice_link"].endswith(f"&prep_profile={r['company_profile']['id']}")
    assert r["my_completed_practice"] == 1  # own sessions only
    # campaign linked to the drive shows up for the assigned student
    c = mk_campaign(client, world["h_tpo"], drive_id=d.id)
    client.post(f"{P}/campaigns/{c['id']}/students", json={"student_ids": [world["s1"].id]}, headers=world["h_tpo"])
    client.post(f"{P}/campaigns/{c['id']}/status", json={"to": "active"}, headers=world["h_tpo"])
    r = client.get(url, headers=world["h_s1"]).json()
    assert [x["completion"] for x in r["campaigns"]] == ["completed"]
    assert client.get(url, headers=world["h_s2"]).json()["campaigns"] == []
    # drafts and other institutes are invisible
    draft = client.post(f"{P}/drives", json={"company_name": "D", "role_title": "X"}, headers=world["h_tpo"]).json()
    assert client.get(f"{P}/drives/{draft['drive']['id']}/preparation", headers=world["h_s1"]).status_code == 404
    on(world["a"], world["b"])
    assert client.get(url, headers=world["h_sb"]).status_code == 404


def test_campaign_tables_constraints(session, world):
    c = models.PlacementCampaign(tenant_id=world["a"].id, title="x", status="draft", created_by_role="tpo",
                                 starts_at=NOW, due_at=NOW + timedelta(days=1))
    session.add(c)
    session.commit()
    session.add(models.PlacementCampaignStudent(tenant_id=world["a"].id, campaign_id=c.id, student_id=world["s1"].id))
    session.commit()
    session.add(models.PlacementCampaignStudent(tenant_id=world["a"].id, campaign_id=c.id, student_id=world["s1"].id))
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    bad = models.PlacementCampaign(tenant_id=world["a"].id, title="x", status="bogus", created_by_role="tpo",
                                   starts_at=NOW, due_at=NOW + timedelta(days=1))
    session.add(bad)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
