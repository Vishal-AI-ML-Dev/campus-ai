"""Phase 9.2: placement management UI support, bulk status, withdrawal, import.

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §12.
Proves (SQLite, offline):

* flag off -> every new 9.2 route 404; recruiters 403; role matrix per route;
* staff drive list (lifecycle, counts, filters) and v2 applicant list
  (CURRENT eligibility v2, filters, isolation, constant query count);
* the legacy applicant list shows the v2 verdict for flagged institutes and is
  unchanged when the flag is off (known limitation of 9.1 closed);
* bulk shortlist/reject: dry-run writes nothing, per-record results, stale rows
  reported (never overwritten, incl. a race between validation and UPDATE),
  one event + audit + notification per success, cancelled drive frozen, limits;
* withdrawal (P9-D8): only applied/shortlisted, own application, final, blocked
  by an open/accepted offer or placement, race-safe, history + audit, staff can
  never set or leave `withdrawn` on any route (flag on or off);
* academic import: template, CSV + XLSX, server-side type/size/magic checks,
  row-level errors, duplicates, cross-institute, blanks never overwrite,
  hash-bound commit, one transaction, content-free audit;
* first publish notifies eligible students only.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

import app.core.db as dbmod
import app.models as models
from tests.test_placement_management import (  # noqa: F401 - fixtures
    D,
    P,
    _applied,
    audit_rows,
    mk_drive,
    on,
    put_profile,
    put_settings,
    verified_skill,
    world,
)

NEW_92_ROUTES = [
    ("get", "/status"), ("get", "/drives"), ("get", "/drives/1/applicants"),
    ("post", "/drives/1/applications/bulk-status"), ("post", "/applications/1/withdraw"),
    ("get", "/me/drives"), ("get", "/me/applications"), ("get", "/import/template"),
    ("post", "/import/preview"), ("post", "/import/commit"),
]


def _call(client, method, path, h):
    return getattr(client, method)(f"{P}{path}", headers=h, **({"json": {}} if method != "get" else {}))


# --- gate + RBAC ------------------------------------------------------------------
def test_92_routes_closed_when_flag_off(client, world):
    for who in ("h_tpo", "h_s1", "h_admin", "h_teacher"):
        for method, path in NEW_92_ROUTES:
            assert _call(client, method, path, world[who]).status_code == 404, (who, path)


def test_92_role_matrix(client, world, on):
    on(world["a"])
    for method, path in NEW_92_ROUTES:
        assert _call(client, method, path, world["h_rec"]).status_code == 403, path
    staff_paths = [("get", "/drives"), ("get", "/drives/1/applicants"), ("get", "/import/template"),
                   ("post", "/import/preview"), ("post", "/import/commit")]
    for method, path in staff_paths:
        for who in ("h_s1", "h_teacher"):
            assert _call(client, method, path, world[who]).status_code == 403, (who, path)
    for who in ("h_s1", "h_teacher", "h_admin"):  # bulk is TPO-only
        assert _call(client, "post", "/drives/1/applications/bulk-status", world[who]).status_code == 403
    for method, path in (("get", "/me/drives"), ("get", "/me/applications"), ("post", "/applications/1/withdraw")):
        for who in ("h_tpo", "h_admin", "h_teacher"):
            assert _call(client, method, path, world[who]).status_code == 403, (who, path)
    r = client.get(f"{P}/status", headers=world["h_teacher"])
    assert r.status_code == 200 and r.json() == {"enabled": True, "role": "teacher"}
    assert client.get(f"{P}/status", headers=world["h_tpo_b"]).status_code == 404


# --- staff drive list + applicants ---------------------------------------------------
def _create_draft(client, h, **settings):
    body = {"company_name": "Globex", "role_title": "Analyst", "settings": settings}
    r = client.post(f"{P}/drives", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["drive"]["id"]


def test_staff_drive_list_lifecycle_counts_filters(client, world, on, session):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    legacy = mk_drive(session, world["a"], company_name="Acme")
    mk_drive(session, world["b"], company_name="OtherInst")
    draft_id = _create_draft(client, h, academic_year="2026-27")
    assert client.post(f"{D}/{legacy.id}/apply", headers=world["h_s1"]).status_code == 201
    rows = client.get(f"{P}/drives", headers=h).json()
    assert {r["company_name"] for r in rows} == {"Acme", "Globex"}  # other institute absent
    by = {r["id"]: r for r in rows}
    assert by[legacy.id]["lifecycle"] == "published" and by[legacy.id]["configured"] is False
    assert by[legacy.id]["applications_total"] == 1 and by[legacy.id]["status_counts"] == {"applied": 1}
    assert by[draft_id]["lifecycle"] == "draft" and by[draft_id]["academic_year"] == "2026-27"
    assert [r["id"] for r in client.get(f"{P}/drives?lifecycle=draft", headers=h).json()] == [draft_id]
    assert [r["id"] for r in client.get(f"{P}/drives?academic_year=2026-27", headers=h).json()] == [draft_id]
    assert [r["id"] for r in client.get(f"{P}/drives?q=acm", headers=h).json()] == [legacy.id]
    assert client.get(f"{P}/drives?q=%25", headers=h).json() == []  # wildcard is literal
    assert client.get(f"{P}/drives?lifecycle=bogus", headers=h).status_code == 422
    assert client.get(f"{P}/drives", headers=world["h_admin"]).status_code == 200


def test_applicants_v2_current_eligibility_filters_isolation(client, world, on, session):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    d = mk_drive(session, world["a"])
    for who in ("h_s1", "h_s2"):
        assert client.post(f"{P}/drives/{d.id}/apply", headers=world[who]).status_code == 201
    put_profile(client, h, world["s1"], graduation_year=2027)
    put_profile(client, h, world["s2"], roll_number="ME/1", graduation_year=2026)
    # criterion added AFTER they applied: v2 list shows the current verdict
    put_settings(client, h, d, graduation_years=[2027])
    rows = client.get(f"{P}/drives/{d.id}/applicants", headers=h).json()
    by = {r["full_name"]: r for r in rows}
    assert by["Asha"]["eligible"] is True and by["Bala"]["eligible"] is False
    assert by["Bala"]["failed_criteria"] == ["Graduation year"]
    assert by["Bala"]["roll_number"] == "ME/1" and by["Asha"]["department_name"] == "Computer Science"
    assert [r["full_name"] for r in client.get(f"{P}/drives/{d.id}/applicants?eligible=false", headers=h).json()] == ["Bala"]
    assert [r["full_name"] for r in client.get(f"{P}/drives/{d.id}/applicants?q=me/1", headers=h).json()] == ["Bala"]
    r = client.get(f"{P}/drives/{d.id}/applicants?status=shortlisted", headers=h)
    assert r.json() == [] and r.headers["X-Status-Counts"] == "applied:2"
    assert client.get(f"{P}/drives/{d.id}/applicants", headers=world["h_admin"]).status_code == 200
    assert client.get(f"{P}/drives/{d.id}/applicants", headers=world["h_tpo_b"]).status_code == 404
    # legacy TPO applicant list now shows the same v2 verdict (shape unchanged)
    legacy = {r["full_name"]: r for r in client.get(f"{D}/{d.id}/applications", headers=h).json()}
    assert legacy["Asha"]["eligible"] is True and legacy["Bala"]["eligible"] is False
    assert set(legacy["Asha"]) == {
        "application_id", "student_id", "full_name", "status", "eligible", "cgpa", "attendance",
        "verified_skills", "verified_projects", "note", "contact_revealed",
    }


def test_legacy_applicant_list_unchanged_when_flag_off(client, world, session, monkeypatch):
    from app.core.config import settings

    d = mk_drive(session, world["a"])
    assert client.post(f"{D}/{d.id}/apply", headers=world["h_s1"]).status_code == 201
    monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_ENABLED", False)
    rows = client.get(f"{D}/{d.id}/applications", headers=world["h_tpo"]).json()
    assert rows[0]["eligible"] is True and rows[0]["status"] == "applied"


def test_applicants_constant_query_count(client, world, on, session, make_user, token_header):
    on(world["a"])
    h = world["h_tpo"]
    d = mk_drive(session, world["a"])

    def count():
        n = {"q": 0}

        def before(*_a, **_k):
            n["q"] += 1

        event.listen(dbmod.engine, "before_cursor_execute", before)
        try:
            r = client.get(f"{P}/drives/{d.id}/applicants?limit=200", headers=h)
        finally:
            event.remove(dbmod.engine, "before_cursor_execute", before)
        assert r.status_code == 200
        return n["q"], len(r.json())

    assert client.post(f"{P}/drives/{d.id}/apply", headers=world["h_s1"]).status_code == 201
    q1, n1 = count()
    for i in range(8):
        make_user(f"ap{i}@a.edu", tenant=world["a"])
        assert client.post(f"{P}/drives/{d.id}/apply", headers=token_header(f"ap{i}@a.edu")).status_code == 201
    q2, n2 = count()
    assert (n1, n2) == (1, 9) and q1 == q2


# --- bulk status -----------------------------------------------------------------------
def _three_applicants(client, world, session, make_user, token_header):
    d = mk_drive(session, world["a"])
    ids = []
    for who in ("h_s1", "h_s2"):
        r = client.post(f"{P}/drives/{d.id}/apply", headers=world[who])
        ids.append(r.json()["id"])
    make_user("s3@a.edu", tenant=world["a"])
    ids.append(client.post(f"{P}/drives/{d.id}/apply", headers=token_header("s3@a.edu")).json()["id"])
    return d, ids


def test_bulk_preview_then_apply_with_per_record_results(client, world, on, session, make_user, token_header):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    d, (a1, a2, a3) = _three_applicants(client, world, session, make_user, token_header)
    other = mk_drive(session, world["a"], company_name="Other")
    oid = client.post(f"{P}/drives/{other.id}/apply", headers=world["h_s1"]).json()["id"]
    # a2 is already shortlisted by someone else
    assert client.patch(f"{P}/applications/{a2}/status", json={"status": "shortlisted"}, headers=h).status_code == 200
    items = [{"application_id": a, "expected_status": "applied"} for a in (a1, a2, a3, oid, 999999)]
    url = f"{P}/drives/{d.id}/applications/bulk-status"

    preview = client.post(url, json={"to_status": "shortlisted", "items": items}, headers=h)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True and body["succeeded"] == 2 and body["failed"] == 3
    codes = {r["application_id"]: r["error_code"] for r in body["results"]}
    assert codes == {a1: None, a2: "changed", a3: None, oid: "not_found", 999999: "not_found"}
    session.expire_all()
    assert session.get(models.Application, a1).status == models.ApplicationStatus.applied  # nothing written

    r = client.post(url, json={"to_status": "shortlisted", "items": items, "dry_run": False}, headers=h)
    assert r.status_code == 200 and r.json()["succeeded"] == 2
    session.expire_all()
    assert session.get(models.Application, a1).status == models.ApplicationStatus.shortlisted
    assert session.get(models.Application, a3).status == models.ApplicationStatus.shortlisted
    assert session.get(models.Application, oid).status == models.ApplicationStatus.applied  # other drive
    bulk_events = list(session.scalars(select(models.PlacementApplicationEvent).where(
        models.PlacementApplicationEvent.to_status == "shortlisted")))
    assert sorted(e.application_id for e in bulk_events) == sorted([a1, a2, a3])  # a2 from the single change
    per_record = [x for x in audit_rows(session, "placement.application_status") if "(bulk)" in x.summary]
    assert len(per_record) == 2
    summary = audit_rows(session, "placement.bulk_status")
    assert len(summary) == 1 and "2 changed, 3 not changed" in summary[0].summary
    # same request again: nothing to do, nothing overwritten
    again = client.post(url, json={"to_status": "shortlisted", "items": items[:1], "dry_run": False}, headers=h)
    assert again.json()["results"][0]["error_code"] == "changed"
    # another institute's TPO: drive 404
    assert client.post(url, json={"to_status": "rejected", "items": items[:1]}, headers=world["h_tpo_b"]).status_code == 404


def test_bulk_rules_and_limits(client, world, on, session, make_user, token_header):
    on(world["a"])
    h = world["h_tpo"]
    d, (a1, a2, _a3) = _three_applicants(client, world, session, make_user, token_header)
    url = f"{P}/drives/{d.id}/applications/bulk-status"
    assert client.patch(f"{P}/applications/{a1}/status", json={"status": "rejected"}, headers=h).status_code == 200
    item = [{"application_id": a1, "expected_status": "rejected"}]
    r = client.post(url, json={"to_status": "shortlisted", "items": item}, headers=h).json()
    assert r["results"][0]["error_code"] == "note_required"
    r = client.post(url, json={"to_status": "shortlisted", "items": item, "note": "appeal", "dry_run": False}, headers=h)
    assert r.json()["succeeded"] == 1
    item2 = [{"application_id": a2, "expected_status": "applied"}]
    assert client.post(url, json={"to_status": "selected", "items": item2}, headers=h).status_code == 422
    assert client.post(url, json={"to_status": "withdrawn", "items": item2}, headers=h).status_code == 422
    dup = item2 * 2
    assert client.post(url, json={"to_status": "rejected", "items": dup}, headers=h).status_code == 422
    many = [{"application_id": i + 1, "expected_status": "applied"} for i in range(201)]
    assert client.post(url, json={"to_status": "rejected", "items": many}, headers=h).status_code == 422
    assert client.post(url, json={"to_status": "rejected", "items": []}, headers=h).status_code == 422
    assert client.post(f"{P}/drives/{d.id}/lifecycle", json={"to": "cancelled"}, headers=h).status_code == 200
    assert client.post(url, json={"to_status": "rejected", "items": item2}, headers=h).status_code == 409


def test_bulk_race_between_validation_and_update(client, world, on, session, make_user, token_header, monkeypatch):
    on(world["a"])
    h = world["h_tpo"]
    d, (a1, a2, _a3) = _three_applicants(client, world, session, make_user, token_header)
    from app.services import placement_management as svc

    real_now, fired = svc._now, {"done": False}

    def racing_now():
        if not fired["done"]:  # first UPDATE is being built: another TPO rejects a2
            fired["done"] = True
            with dbmod.engine.begin() as conn:
                conn.execute(models.Application.__table__.update()
                             .where(models.Application.id == a2).values(status="rejected"))
        return real_now()

    monkeypatch.setattr(svc, "_now", racing_now)
    items = [{"application_id": a, "expected_status": "applied"} for a in (a1, a2)]
    r = client.post(f"{P}/drives/{d.id}/applications/bulk-status",
                    json={"to_status": "shortlisted", "items": items, "dry_run": False}, headers=h)
    assert r.status_code == 200
    res = {x["application_id"]: x for x in r.json()["results"]}
    assert res[a1]["ok"] is True and res[a2]["ok"] is False and res[a2]["error_code"] == "changed"
    session.expire_all()
    assert session.get(models.Application, a2).status == models.ApplicationStatus.rejected  # not overwritten
    events = list(session.scalars(select(models.PlacementApplicationEvent).where(
        models.PlacementApplicationEvent.application_id == a2)))
    assert [e.to_status for e in events] == ["applied"]


# --- withdrawal (P9-D8) ---------------------------------------------------------------
def test_withdraw_rules_history_audit(client, world, on, session):
    on(world["a"], world["b"])
    d, aid = _applied(client, world, session)
    url = f"{P}/applications/{aid}/withdraw"
    assert client.post(url, headers=world["h_s2"]).status_code == 404  # not yours
    assert client.post(url, headers=world["h_sb"]).status_code == 404  # other institute
    assert client.post(url, json={"expected_status": "shortlisted"}, headers=world["h_s1"]).status_code == 409
    mine = client.get(f"{P}/me/applications", headers=world["h_s1"]).json()
    assert mine[0]["can_withdraw"] is True and mine[0]["withdraw_blocker"] is None
    r = client.post(url, json={"expected_status": "applied"}, headers=world["h_s1"])
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    assert client.post(url, headers=world["h_s1"]).status_code == 409  # final
    hist = client.get(f"{P}/applications/{aid}/history", headers=world["h_s1"]).json()
    assert [(e["from_status"], e["to_status"], e["actor_role"]) for e in hist][-1] == ("applied", "withdrawn", "student")
    rows = audit_rows(session, "placement.application_withdraw")
    assert len(rows) == 1 and "Asha" not in rows[0].summary
    mine = client.get(f"{P}/me/applications", headers=world["h_s1"]).json()
    assert mine[0]["can_withdraw"] is False and "already withdrew" in mine[0]["withdraw_blocker"]
    # staff can never move it out of withdrawn (v2 and legacy, flag on)
    for u in (f"{P}/applications/{aid}/status", f"{D}/applications/{aid}/status"):
        assert client.patch(u, json={"status": "shortlisted"}, headers=world["h_tpo"]).status_code == 409
    # re-applying to the same drive stays a duplicate (409)
    assert client.post(f"{P}/drives/{d.id}/apply", headers=world["h_s1"]).status_code == 409


def test_withdraw_from_shortlisted_but_not_after_selection(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    _d, aid = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"}, headers=h).status_code == 200
    assert client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"]).status_code == 200
    _d2, aid2 = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{aid2}/status", json={"status": "selected"}, headers=h).status_code == 200
    r = client.post(f"{P}/applications/{aid2}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and "can no longer be withdrawn" in r.json()["detail"]


def _offer(session, world, aid, status):
    rec = models.Recruiter(tenant_id=world["a"].id, company_name="Acme")
    session.add(rec)
    session.commit()
    app_ = session.get(models.Application, aid)
    session.add(models.Offer(tenant_id=world["a"].id, application_id=aid, recruiter_id=rec.id,
                             drive_id=app_.drive_id, student_id=app_.student_id, role_title="SDE", status=status))
    session.commit()


@pytest.mark.parametrize("offer_status,fragment", [
    (models.OfferStatus.extended, "open offer"), (models.OfferStatus.accepted, "accepted an offer"),
])
def test_withdraw_blocked_by_offer(client, world, on, session, offer_status, fragment):
    on(world["a"])
    _d, aid = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"},
                        headers=world["h_tpo"]).status_code == 200
    _offer(session, world, aid, offer_status)
    r = client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and fragment in r.json()["detail"]


def test_withdraw_blocked_once_placed_and_on_cancelled_drive(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    _d, placed_app = _applied(client, world, session)
    d2, aid = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{placed_app}/status", json={"status": "selected"}, headers=h).status_code == 200
    r = client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and "already placed" in r.json()["detail"]
    d3 = mk_drive(session, world["a"])
    aid3 = client.post(f"{P}/drives/{d3.id}/apply", headers=world["h_s2"]).json()["id"]
    assert client.post(f"{P}/drives/{d3.id}/lifecycle", json={"to": "cancelled"}, headers=h).status_code == 200
    r = client.post(f"{P}/applications/{aid3}/withdraw", headers=world["h_s2"])
    assert r.status_code == 409 and "cancelled" in r.json()["detail"]


def test_withdraw_race_with_shortlisting(client, world, on, session, monkeypatch):
    on(world["a"])
    _d, aid = _applied(client, world, session)
    from app.services import placement_management as svc

    real = svc.withdraw_blocker

    def racing(application, offer, placed, lifecycle):
        out = real(application, offer, placed, lifecycle)
        with dbmod.engine.begin() as conn:  # the TPO shortlists in between
            conn.execute(models.Application.__table__.update()
                         .where(models.Application.id == aid).values(status="shortlisted"))
        return out

    monkeypatch.setattr(svc, "withdraw_blocker", racing)
    r = client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and "someone else" in r.json()["detail"]
    session.expire_all()
    assert session.get(models.Application, aid).status == models.ApplicationStatus.shortlisted
    assert not audit_rows(session, "placement.application_withdraw")


def test_staff_can_never_set_withdrawn_even_with_flag_off(client, world, session):
    d = mk_drive(session, world["a"])
    aid = client.post(f"{D}/{d.id}/apply", headers=world["h_s1"]).json()["id"]
    r = client.patch(f"{D}/applications/{aid}/status", json={"status": "withdrawn"}, headers=world["h_tpo"])
    assert r.status_code == 400
    session.add(models.Application(tenant_id=world["a"].id, drive_id=d.id, student_id=world["s2"].id,
                                   status=models.ApplicationStatus.withdrawn))
    session.commit()
    wid = session.scalar(select(models.Application.id).where(models.Application.student_id == world["s2"].id))
    r = client.patch(f"{D}/applications/{wid}/status", json={"status": "shortlisted"}, headers=world["h_tpo"])
    assert r.status_code == 409


# --- student opportunities ------------------------------------------------------------
def test_student_opportunities_and_applications(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    open_d = mk_drive(session, world["a"], company_name="Open", required_skills="Go")
    mk_drive(session, world["a"], company_name="Closed", is_open=False)
    mk_drive(session, world["b"], company_name="OtherInst")
    _create_draft(client, h)  # draft: invisible
    verified_skill(session, world["s1"], "Go")
    rows = client.get(f"{P}/me/drives", headers=world["h_s1"]).json()
    assert [r["company_name"] for r in rows] == ["Open"]
    assert rows[0]["eligible"] is True and rows[0]["can_apply"] is True and rows[0]["window"] == "no_window"
    s2 = client.get(f"{P}/me/drives", headers=world["h_s2"]).json()[0]
    assert s2["eligible"] is False and s2["failed_criteria"] == ["Required skills"] and s2["can_apply"] is False
    names = {r["company_name"] for r in client.get(f"{P}/me/drives?scope=all", headers=world["h_s1"]).json()}
    assert names == {"Open", "Closed"}
    assert client.post(f"{P}/drives/{open_d.id}/apply", headers=world["h_s1"]).status_code == 201
    row = client.get(f"{P}/me/drives", headers=world["h_s1"]).json()[0]
    assert row["already_applied"] is True and row["application_status"] == "applied" and row["can_apply"] is False
    apps = client.get(f"{P}/me/applications", headers=world["h_s1"]).json()
    assert len(apps) == 1 and apps[0]["company_name"] == "Open" and apps[0]["lifecycle"] == "published"
    assert client.get(f"{P}/me/applications", headers=world["h_s2"]).json() == []


def test_first_publish_notifies_only_eligible_students(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    put_profile(client, h, world["s1"], graduation_year=2027)
    put_profile(client, h, world["s2"], roll_number="X/2", graduation_year=2026)
    did = _create_draft(client, h, graduation_years=[2027])
    assert client.post(f"{P}/drives/{did}/lifecycle", json={"to": "published"}, headers=h).status_code == 200
    session.expire_all()
    notes = list(session.scalars(select(models.Notification).where(models.Notification.type == "drive_published")))
    assert [n.user_id for n in notes] == [world["s1"].id]
    assert client.post(f"{P}/drives/{did}/lifecycle", json={"to": "closed"}, headers=h).status_code == 200
    assert client.post(f"{P}/drives/{did}/lifecycle", json={"to": "published"}, headers=h).status_code == 200
    session.expire_all()
    assert len(list(session.scalars(select(models.Notification).where(
        models.Notification.type == "drive_published")))) == 1  # re-publish does not re-notify
    assert "1 eligible students notified" in audit_rows(session, "placement.drive_lifecycle")[0].summary


# --- academic import -------------------------------------------------------------------
def _upload(client, h, content: bytes, name="marks.csv", ctype="text/csv", sha=None):
    files = {"file": (name, content, ctype)}
    if sha is None:
        return client.post(f"{P}/import/preview", files=files, headers=h)
    return client.post(f"{P}/import/commit", files=files, data={"file_sha256": sha}, headers=h)


def test_import_template(client, world, on):
    on(world["a"])
    r = client.get(f"{P}/import/template", headers=world["h_tpo"])
    assert r.status_code == 200 and r.text.strip() == (
        "email,roll_number,graduation_year,tenth_percent,twelfth_percent,diploma_percent,active_backlogs"
    )
    assert "attachment" in r.headers["content-disposition"]


def test_import_preview_rows_and_commit(client, world, on, session):
    on(world["a"], world["b"])
    h = world["h_tpo"]
    put_profile(client, h, world["s2"], roll_number="ME/9", graduation_year=2026, tenth_percent=70.0)
    csv_text = (
        "Email,Roll Number,graduation_year,tenth_percent,twelfth_percent,diploma_percent,active_backlogs,Notes\n"
        "S1@a.edu,CSE/1,2027,91.25,85,,0,secret remark\n"
        "s2@a.edu,,,,80%,,,\n"                        # blanks keep stored values
        "sb@b.edu,B/1,2027,,,,,\n"                    # other institute -> not found
        "nobody@a.edu,,2027,,,,,\n"
        "s1@a.edu,CSE/2,2027,,,,,\n"                  # duplicate student
        "bad-email,,2027,,,,,\n"
        ",ME/9,,,,,1,\n"                              # matched by existing roll number
        "teacher@a.edu,,2027,,,,,\n"                  # not a student
        ",,,,,,,\n"                                   # blank line ignored
    )
    for raw in ["s1@a.edu,CSE/1,1999,101,,,100\n", "s1@a.edu,bad roll!,abc,,,,1.5\n"]:
        bad = _upload(client, h, ("email,roll_number,graduation_year,tenth_percent,twelfth_percent,"
                                  "diploma_percent,active_backlogs\n" + raw).encode())
        assert bad.json()["rows"][0]["action"] == "error" and len(bad.json()["rows"][0]["errors"]) >= 3

    pv = _upload(client, h, csv_text.encode())
    assert pv.status_code == 200, pv.text
    body = pv.json()
    assert body["ignored_columns"] == ["notes"] and body["file_type"] == "csv"
    assert (body["total_rows"], body["valid_rows"], body["invalid_rows"]) == (8, 2, 6)
    rows = {r["row"]: r for r in body["rows"]}
    assert rows[2]["action"] == "create" and rows[2]["values"]["tenth_percent"] == 91.25
    assert rows[3]["action"] == "update" and rows[3]["changed_fields"] == ["twelfth_percent"]
    assert rows[4]["errors"] == ["No active student with this email in your institute"]
    assert rows[5]["action"] == "error"
    assert "already appears on row 2" in rows[6]["errors"][0]
    assert rows[7]["errors"] == ["Email is not valid"]
    # matched by roll number to s2, who is already on row 3 -> duplicate
    assert rows[8]["student_id"] == world["s2"].id and "already appears on row 3" in rows[8]["errors"][0]
    assert rows[9]["action"] == "error"
    session.expire_all()
    assert session.scalar(select(models.StudentPlacementProfile).where(
        models.StudentPlacementProfile.student_id == world["s1"].id)) is None  # preview writes nothing

    # commit requires the previewed file
    assert _upload(client, h, csv_text.encode() + b"\n", sha=body["file_sha256"]).status_code == 409
    done = _upload(client, h, csv_text.encode(), sha=body["file_sha256"])
    assert done.status_code == 200, done.text
    assert done.json() == {"file_sha256": body["file_sha256"], "total_rows": 8, "created": 1,
                           "updated": 1, "unchanged": 0, "invalid": 6}
    session.expire_all()
    p1 = session.scalar(select(models.StudentPlacementProfile).where(
        models.StudentPlacementProfile.student_id == world["s1"].id))
    p2 = session.scalar(select(models.StudentPlacementProfile).where(
        models.StudentPlacementProfile.student_id == world["s2"].id))
    assert (p1.roll_number, p1.graduation_year, p1.tenth_percent, p1.placement_status) == ("CSE/1", 2027, 91.25, "active")
    assert (p2.roll_number, p2.graduation_year, p2.tenth_percent, p2.twelfth_percent) == ("ME/9", 2026, 70.0, 80.0)
    audit = audit_rows(session, "placement.academic_import")
    assert len(audit) == 1
    assert "1 created, 1 updated" in audit[0].summary
    assert not any(x in audit[0].summary for x in ("s1@a.edu", "CSE/1", "91.25", "secret", "Asha"))
    # institute B sees nothing of A; A's student is "not found" for B
    rb = _upload(client, world["h_tpo_b"], b"email,graduation_year\ns1@a.edu,2027\n").json()
    assert rb["rows"][0]["errors"] == ["No active student with this email in your institute"]


def test_import_duplicate_roll_numbers(client, world, on, session, make_user):
    on(world["a"])
    h = world["h_tpo"]
    make_user("s3@a.edu", tenant=world["a"])
    put_profile(client, h, world["s2"], roll_number="TAKEN/1")
    body = _upload(client, h, b"email,roll_number\ns1@a.edu,R/1\ns3@a.edu,r/1\ns1@b.edu,x\n").json()
    assert "Roll number repeated on row 2" in body["rows"][1]["errors"]
    body = _upload(client, h, b"email,roll_number\ns1@a.edu,TAKEN/1\n").json()
    assert body["rows"][0]["errors"] == ["Another student of your institute already has this roll number"]


def _xlsx(rows: list[list[str]], doctype: bool = False) -> bytes:
    def cell(ref, v):
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'

    letters = "ABCDEFG"
    xml_rows = "".join(
        f'<row r="{i + 1}">' + "".join(cell(f"{letters[j]}{i + 1}", v) for j, v in enumerate(r)) + "</row>"
        for i, r in enumerate(rows)
    )
    head = '<?xml version="1.0"?>' + ('<!DOCTYPE x [<!ENTITY a "b">]>' if doctype else "")
    sheet = (head + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f"<sheetData>{xml_rows}</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_import_xlsx_and_file_validation(client, world, on):
    on(world["a"])
    h = world["h_tpo"]
    data = _xlsx([["email", "graduation_year", "active_backlogs"], ["s1@a.edu", "2027", "2"]])
    body = _upload(client, h, data, "marks.xlsx", XLSX).json()
    assert body["file_type"] == "xlsx" and body["rows"][0]["values"] == {"graduation_year": 2027, "active_backlogs": 2}
    # server-side checks: extension, content type, magic bytes, size, DOCTYPE, encoding, structure
    assert _upload(client, h, b"x", "marks.txt").status_code == 415
    assert _upload(client, h, b"email\n", "marks.csv", "image/png").status_code == 415
    assert _upload(client, h, b"not a zip", "marks.xlsx", XLSX).status_code == 415
    assert _upload(client, h, data, "marks.csv").status_code == 415  # zip disguised as CSV
    assert _upload(client, h, b"a" * (2 * 1024 * 1024 + 1)).status_code == 413
    evil = _xlsx([["email"], ["s1@a.edu"]], doctype=True)
    assert _upload(client, h, evil, "m.xlsx", XLSX).status_code == 400
    assert _upload(client, h, "email,graduation_year\n\xe9,1".encode("latin-1")).status_code == 400
    assert _upload(client, h, b"").status_code == 400
    assert _upload(client, h, b"name,city\nA,B\n").status_code == 400  # no email/roll column
    assert _upload(client, h, b"email\ns1@a.edu\n").status_code == 400  # no academic column
    big = "email,active_backlogs\n" + "x@a.edu,1\n" * 5001
    assert _upload(client, h, big.encode()).status_code == 400
    bad_hash = client.post(f"{P}/import/commit", files={"file": ("m.csv", b"email\n", "text/csv")},
                           data={"file_sha256": "zz"}, headers=h)
    assert bad_hash.status_code == 422


def test_import_admin_allowed_and_isolated(client, world, on, session):
    on(world["a"])
    body = _upload(client, world["h_admin"], b"email,graduation_year\ns2@a.edu,2028\n").json()
    assert body["to_create"] == 1
    done = _upload(client, world["h_admin"], b"email,graduation_year\ns2@a.edu,2028\n", sha=body["file_sha256"])
    assert done.json()["created"] == 1
    # re-import is idempotent
    again = _upload(client, world["h_admin"], b"email,graduation_year\ns2@a.edu,2028\n").json()
    assert again["unchanged"] == 1 and again["to_update"] == 0


def test_migration_enum_value_and_event_check(session, world):
    d = mk_drive(session, world["a"])
    app_ = models.Application(tenant_id=world["a"].id, drive_id=d.id, student_id=world["s1"].id,
                              status=models.ApplicationStatus.withdrawn)
    session.add(app_)
    session.commit()
    session.add(models.PlacementApplicationEvent(tenant_id=world["a"].id, application_id=app_.id,
                                                 from_status="applied", to_status="withdrawn"))
    session.commit()
    assert models.PlacementApplicationEvent.__table__.c.to_status is not None


def test_window_timestamps_still_required_aware(client, world, on, session):
    on(world["a"])
    d = mk_drive(session, world["a"])
    future = datetime.now(timezone.utc) + timedelta(days=1)
    put_settings(client, world["h_tpo"], d, applications_open_at=future.isoformat())
    row = [r for r in client.get(f"{P}/drives", headers=world["h_tpo"]).json() if r["id"] == d.id][0]
    assert row["window"] == "not_open_yet"
    assert client.get(f"{P}/me/drives", headers=world["h_s1"]).json()[0]["can_apply"] is False


def test_cors_exposes_pagination_and_count_headers_only():
    """Phase 9.2: a cross-origin frontend can read the count headers (additive)."""
    from fastapi.middleware.cors import CORSMiddleware

    from main import app

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    exposed = set(cors[0].kwargs["expose_headers"])
    assert exposed == {"X-Total-Count", "X-Request-ID", "X-Status-Counts", "X-Eligible-Count", "X-Completion-Counts"}
    assert cors[0].kwargs["allow_credentials"] is True
