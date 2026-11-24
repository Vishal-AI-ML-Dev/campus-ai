"""Phase 9 review fixes M1 and L1 (SQLite, offline).

M1 - legacy ``/placement/analytics/overview`` excludes ``withdrawn``
applications from totals, applicants and selection-rate denominators, so the
funnel buckets and totals agree; numbers without withdrawn rows are unchanged.

L1 - withdrawal and every writer that can give the student a live offer or a
placement (recruiter extends an offer, student accepts an offer, TPO selects)
share one per-student lock (``lock_student_for_placement``: FOR UPDATE on the
student's users row) and re-check state AFTER acquiring it. SQLite has no row
locks, so the races are reproduced deterministically: the competing writer
commits exactly at the lock-acquisition point (i.e. it held the lock first),
and the test proves the re-check after the lock refuses the loser. True
blocking on Postgres is covered by the opt-in
``tests_pg_api/test_placement_offer_withdraw_lock_pg.py``.
"""

from __future__ import annotations

from sqlalchemy import select

import app.core.db as dbmod
import app.models as models
from app.services import placement_management as svc
from tests.test_placement_management import (  # noqa: F401 - fixtures
    D,
    P,
    _applied,
    audit_rows,
    mk_drive,
    on,
    world,
)

A = "/v1/placement/analytics/overview"


# --- M1 ------------------------------------------------------------------------------
def _app(session, world, drive, student, status):
    a = models.Application(tenant_id=world["a"].id, drive_id=drive.id, student_id=student.id, status=status)
    session.add(a)
    session.commit()
    return a


def test_m1_withdrawn_excluded_from_legacy_analytics(client, world, session, make_user):
    S = models.ApplicationStatus
    students = [make_user(f"m1-{i}@a.edu", tenant=world["a"]) for i in range(6)]
    d1 = mk_drive(session, world["a"], company_name="Alpha")
    d2 = mk_drive(session, world["a"], company_name="Beta")
    # drive 1: applied, shortlisted, selected, rejected, withdrawn, withdrawn
    for st, s in zip([S.applied, S.shortlisted, S.selected, S.rejected, S.withdrawn, S.withdrawn], students):
        _app(session, world, d1, s, st)
    # drive 2: selected, withdrawn (student 5 withdrew everywhere)
    _app(session, world, d2, students[0], S.selected)
    _app(session, world, d2, students[5], S.withdrawn)

    r = client.get(A, headers=world["h_tpo"])
    assert r.status_code == 200, r.text
    body = r.json()
    f = body["funnel"]
    assert (f["applied"], f["shortlisted"], f["selected"], f["rejected"]) == (1, 1, 2, 1)
    assert body["total_applications"] == 5 == sum(f.values())  # totals agree with the buckets
    assert body["unique_applicants"] == 4  # students 4 and 5 only withdrew
    rows = {d["company_name"]: d for d in body["drives"]}
    assert rows["Alpha"]["applicants"] == 4 and rows["Alpha"]["selection_rate"] == 25.0  # 1/4, not 1/6
    assert rows["Beta"]["applicants"] == 1 and rows["Beta"]["selection_rate"] == 100.0  # 1/1, not 1/2
    for row in rows.values():
        assert row["applicants"] >= row["shortlisted"] + row["selected"] + row["rejected"]
    comp = {c["company_name"]: c for c in body["companies"]}
    assert comp["Alpha"]["applicants"] == 4 and comp["Beta"]["applicants"] == 1


def test_m1_unchanged_without_withdrawn(client, world, session, make_user):
    S = models.ApplicationStatus
    students = [make_user(f"m1b-{i}@a.edu", tenant=world["a"]) for i in range(4)]
    d1 = mk_drive(session, world["a"], company_name="Gamma")
    d2 = mk_drive(session, world["a"], company_name="Delta")
    for st, s in zip([S.applied, S.shortlisted, S.selected, S.rejected], students):
        _app(session, world, d1, s, st)
    _app(session, world, d2, students[0], S.rejected)
    body = client.get(A, headers=world["h_tpo"]).json()
    assert body["total_applications"] == 5 and body["unique_applicants"] == 4
    rows = {d["company_name"]: d for d in body["drives"]}
    assert rows["Gamma"]["applicants"] == 4 and rows["Gamma"]["selection_rate"] == 25.0
    assert rows["Delta"]["applicants"] == 1 and rows["Delta"]["selection_rate"] == 0.0
    assert body["placed_students"] == 1


# --- L1 helpers ----------------------------------------------------------------------
def _company_for_drive(session, world, drive):
    rec = models.Recruiter(tenant_id=world["a"].id, company_name=drive.company_name,
                           status=models.RecruiterStatus.active)
    session.add(rec)
    session.commit()
    session.add(models.RecruiterUser(tenant_id=world["a"].id, recruiter_id=rec.id, user_id=world["rec"].id,
                                     is_primary=True))
    drive.recruiter_id = rec.id
    session.commit()
    return rec


def _shortlisted(client, world, session):
    d, aid = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"},
                        headers=world["h_tpo"]).status_code == 200
    return d, aid


def _raw(stmt):
    """A competing writer's committed change on its own connection."""
    with dbmod.engine.begin() as conn:
        conn.execute(stmt)


def _at_lock(monkeypatch, competitor):
    """Run `competitor` once, exactly when the lock is requested: it models the
    other writer having held the per-student lock and committed first."""
    real = svc.lock_student_for_placement
    calls = []

    def hooked(db, tenant_id, student_id):
        calls.append((tenant_id, student_id))
        if len(calls) == 1:
            competitor()
        return real(db, tenant_id, student_id)

    monkeypatch.setattr(svc, "lock_student_for_placement", hooked)
    return calls


def _withdraw_events(session, aid):
    session.expire_all()
    return list(session.scalars(select(models.PlacementApplicationEvent).where(
        models.PlacementApplicationEvent.application_id == aid,
        models.PlacementApplicationEvent.to_status == "withdrawn")))


# --- L1 A: offer creation wins -------------------------------------------------------
def test_l1_offer_created_first_blocks_withdrawal(client, world, on, session, monkeypatch):
    on(world["a"])
    d, aid = _shortlisted(client, world, session)
    rec = _company_for_drive(session, world, d)
    calls = _at_lock(monkeypatch, lambda: _raw(models.Offer.__table__.insert().values(
        tenant_id=world["a"].id, application_id=aid, recruiter_id=rec.id, drive_id=d.id,
        student_id=world["s1"].id, role_title="SDE", status="extended")))
    r = client.post(f"{P}/applications/{aid}/withdraw", json={"expected_status": "shortlisted"}, headers=world["h_s1"])
    assert r.status_code == 409 and "open offer" in r.json()["detail"]
    assert calls == [(world["a"].id, world["s1"].id)]
    session.expire_all()
    assert session.get(models.Application, aid).status == models.ApplicationStatus.shortlisted  # still active
    assert _withdraw_events(session, aid) == [] and audit_rows(session, "placement.application_withdraw") == []


# --- L1 B: withdrawal wins -----------------------------------------------------------
def test_l1_withdrawal_first_blocks_offer(client, world, on, session):
    on(world["a"])
    d, aid = _shortlisted(client, world, session)
    _company_for_drive(session, world, d)
    assert client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"]).status_code == 200
    r = client.post("/v1/recruiters/me/offers", json={"application_id": aid}, headers=world["h_rec"])
    assert r.status_code == 403
    session.expire_all()
    assert session.scalar(select(models.Offer).where(models.Offer.application_id == aid)) is None
    assert len(_withdraw_events(session, aid)) == 1
    assert len(audit_rows(session, "placement.application_withdraw")) == 1
    assert audit_rows(session, "recruiter.offer_extend") == []


def test_l1_offer_rechecks_application_after_lock(client, world, on, session, monkeypatch):
    """The recruiter read 'shortlisted', then the withdrawal held the lock and
    committed first: after locking, the offer path must see 'withdrawn'."""
    on(world["a"])
    d, aid = _shortlisted(client, world, session)
    _company_for_drive(session, world, d)
    calls = _at_lock(monkeypatch, lambda: _raw(models.Application.__table__.update()
                                               .where(models.Application.id == aid).values(status="withdrawn")))
    r = client.post("/v1/recruiters/me/offers", json={"application_id": aid}, headers=world["h_rec"])
    assert r.status_code == 403
    assert calls == [(world["a"].id, world["s1"].id)]
    session.expire_all()
    assert session.scalar(select(models.Offer).where(models.Offer.application_id == aid)) is None
    assert audit_rows(session, "recruiter.offer_extend") == []


# --- L1 C: acceptance / placement races -----------------------------------------------
def test_l1_offer_accepted_elsewhere_blocks_withdrawal(client, world, on, session, monkeypatch):
    on(world["a"])
    dx, x = _shortlisted(client, world, session)
    dy, y = _shortlisted(client, world, session)
    rec = _company_for_drive(session, world, dy)
    _raw(models.Offer.__table__.insert().values(
        tenant_id=world["a"].id, application_id=y, recruiter_id=rec.id, drive_id=dy.id,
        student_id=world["s1"].id, role_title="SDE", status="extended"))
    # The acceptance of Y held the lock and committed just before the withdrawal of X.
    _at_lock(monkeypatch, lambda: _raw(models.Offer.__table__.update()
                                       .where(models.Offer.application_id == y).values(status="accepted")))
    r = client.post(f"{P}/applications/{x}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and "already placed" in r.json()["detail"]
    session.expire_all()
    assert session.get(models.Application, x).status == models.ApplicationStatus.shortlisted
    assert _withdraw_events(session, x) == []


def test_l1_selection_elsewhere_blocks_withdrawal(client, world, on, session, monkeypatch):
    on(world["a"])
    _dx, x = _shortlisted(client, world, session)
    _dy, y = _shortlisted(client, world, session)
    _at_lock(monkeypatch, lambda: _raw(models.Application.__table__.update()
                                       .where(models.Application.id == y).values(status="selected")))
    r = client.post(f"{P}/applications/{x}/withdraw", headers=world["h_s1"])
    assert r.status_code == 409 and "already placed" in r.json()["detail"]
    session.expire_all()
    assert session.get(models.Application, x).status == models.ApplicationStatus.shortlisted


def test_l1_accept_rechecks_offer_after_lock(client, world, on, session, monkeypatch):
    on(world["a"])
    d, aid = _shortlisted(client, world, session)
    rec = _company_for_drive(session, world, d)
    _raw(models.Offer.__table__.insert().values(
        tenant_id=world["a"].id, application_id=aid, recruiter_id=rec.id, drive_id=d.id,
        student_id=world["s1"].id, role_title="SDE", status="extended"))
    session.expire_all()
    offer_id = session.scalar(select(models.Offer.id).where(models.Offer.application_id == aid))
    # The recruiter withdrew the offer under the lock just before the student's acceptance.
    _at_lock(monkeypatch, lambda: _raw(models.Offer.__table__.update()
                                       .where(models.Offer.id == offer_id).values(status="withdrawn")))
    r = client.patch(f"/v1/recruiters/offers/{offer_id}/respond", json={"accept": True}, headers=world["h_s1"])
    assert r.status_code == 409
    session.expire_all()
    assert session.get(models.Offer, offer_id).status == models.OfferStatus.withdrawn


def test_l1_final_state_never_withdrawn_with_live_offer(client, world, on, session):
    """Sequential end-to-end: whichever completes first, no withdrawn application
    ever carries an extended or accepted offer."""
    on(world["a"])
    d, aid = _shortlisted(client, world, session)
    _company_for_drive(session, world, d)
    r = client.post("/v1/recruiters/me/offers", json={"application_id": aid}, headers=world["h_rec"])
    assert r.status_code == 201, r.text
    assert client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"]).status_code == 409
    offer_id = r.json()["id"]
    assert client.patch(f"/v1/recruiters/offers/{offer_id}/respond", json={"accept": True},
                        headers=world["h_s1"]).status_code == 200
    assert client.post(f"{P}/applications/{aid}/withdraw", headers=world["h_s1"]).status_code == 409
    session.expire_all()
    bad = session.execute(
        select(models.Application.id).join(models.Offer, models.Offer.application_id == models.Application.id)
        .where(models.Application.status == models.ApplicationStatus.withdrawn,
               models.Offer.status.in_([models.OfferStatus.extended, models.OfferStatus.accepted]))
    ).all()
    assert bad == []
    assert len(audit_rows(session, "recruiter.offer_extend")) == 1
    assert audit_rows(session, "placement.application_withdraw") == []


# --- L1 D: every competing writer participates in the same lock -------------------------
def test_l1_all_competing_writers_take_the_student_lock(client, world, on, session, monkeypatch):
    on(world["a"])
    seen: list[tuple[str, int]] = []
    real = svc.lock_student_for_placement

    def spy(db, tenant_id, student_id):
        seen.append((tenant_id, student_id))
        return real(db, tenant_id, student_id)

    monkeypatch.setattr(svc, "lock_student_for_placement", spy)
    tid, sid = world["a"].id, world["s1"].id
    d, aid = _applied(client, world, session)
    assert client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted"},
                        headers=world["h_tpo"]).status_code == 200                       # TPO status change
    _company_for_drive(session, world, d)
    offer = client.post("/v1/recruiters/me/offers", json={"application_id": aid}, headers=world["h_rec"])
    assert offer.status_code == 201                                                      # recruiter offer
    assert client.patch(f"/v1/recruiters/offers/{offer.json()['id']}/respond", json={"accept": True},
                        headers=world["h_s1"]).status_code == 200                       # student accepts
    _d2, aid2 = _applied(client, world, session)
    assert client.post(f"{P}/applications/{aid2}/withdraw", headers=world["h_s1"]).status_code == 409  # withdraw
    _d3, aid3 = _applied(client, world, session)
    assert client.patch(f"{D}/applications/{aid3}/status", json={"status": "selected"},
                        headers=world["h_tpo"]).status_code == 200                       # legacy route (flagged)
    assert seen == [(tid, sid)] * 5


def test_l1_status_change_never_reinterprets_a_concurrent_decision(client, world, on, session, monkeypatch):
    """Another staff member rejected the application while this request waited
    for the lock: the request (no expected_status, even with a note that would
    allow a correction) must get 409 and must not override that decision."""
    on(world["a"])
    _d, aid = _applied(client, world, session)
    _at_lock(monkeypatch, lambda: _raw(models.Application.__table__.update()
                                       .where(models.Application.id == aid).values(status="rejected")))
    r = client.patch(f"{P}/applications/{aid}/status", json={"status": "shortlisted", "note": "override?"},
                     headers=world["h_tpo"])
    assert r.status_code == 409 and "someone else" in r.json()["detail"]
    session.expire_all()
    assert session.get(models.Application, aid).status == models.ApplicationStatus.rejected
    assert audit_rows(session, "placement.application_status") == []
