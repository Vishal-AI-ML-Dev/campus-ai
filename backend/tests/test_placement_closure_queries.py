"""Phase 9 closure L4: placement query counts (no N+1) with unchanged results.

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §20.
* campaign list / dashboard: campaign counts take a constant number of queries
  and equal the per-campaign reference rule (`completion_map`), for many
  campaigns, interview types, windows and late completions;
* legacy TPO applicant list (`GET /drives/{id}/applications`, flag on): query
  count independent of the number of applicants; foreign-institute rows still
  skipped;
* analytics: a drive filter reads only offers on drives in scope; numbers
  unchanged; another institute's offers are never read;
* every query stays institute-scoped (a session/offer of institute B with an
  A student id never counts for A).
"""

# ruff: noqa: F811 - pytest fixtures are imported from test_placement_management and re-bound as arguments
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

import app.core.db as dbmod
import app.models as models
from app.services import placement_campaigns as svc
from app.services import placement_insights as ins
from tests.test_placement_management import (  # noqa: F401 - fixtures
    D,
    P,
    mk_drive,
    on,
    world,
)

NOW = datetime.now(timezone.utc)


class _Counter:
    def __init__(self):
        self.n = 0
        self.statements: list[str] = []

    def __call__(self, _conn, _cursor, statement, *_a):
        self.n += 1
        self.statements.append(statement)


def _count(fn):
    c = _Counter()
    event.listen(dbmod.engine, "before_cursor_execute", c)
    try:
        result = fn()
    finally:
        event.remove(dbmod.engine, "before_cursor_execute", c)
    return c, result


def _students(session, tenant, n, tag):
    users = [models.User(email=f"{tag}{i}@q.edu", full_name=f"{tag} {i}", hashed_password="x",
                         role=models.UserRole.student, tenant_id=tenant.id) for i in range(n)]
    session.add_all(users)
    session.commit()
    return users


def _campaign(session, tenant, creator, i, interview_type=None, start_days=10, due_days=5, status="active"):
    c = models.PlacementCampaign(tenant_id=tenant.id, title=f"C{i}", status=status, interview_type=interview_type,
                                 starts_at=NOW - timedelta(days=start_days), due_at=NOW + timedelta(days=due_days),
                                 created_by_id=creator.id, created_by_role="tpo")
    session.add(c)
    session.commit()
    return c


def _assign(session, c, users):
    session.add_all([models.PlacementCampaignStudent(tenant_id=c.tenant_id, campaign_id=c.id, student_id=u.id)
                     for u in users])
    session.commit()


def _session(session, tenant_id, student_id, itype, days_ago, status="completed"):
    session.add(models.MockInterviewSession(tenant_id=tenant_id, student_id=student_id, interview_type=itype,
                                            status=status, completed_at=NOW - timedelta(days=days_ago)))


def _reference(session, campaigns):
    """The pre-L4 per-campaign rule, campaign by campaign."""
    out = {}
    for c in campaigns:
        ids = list(svc.assigned_ids(session, c))
        done = svc.completion_map(session, c, ids)
        on_time = sum(1 for at in done.values() if svc.completion_of(c, at) == "completed")
        out[c.id] = (len(ids), on_time, len(done) - on_time)
    return out


@pytest.fixture
def campaign_world(session, world):
    a, b = world["a"], world["b"]
    sa = _students(session, a, 12, "qa")
    sb = _students(session, b, 4, "qb")
    specs = [  # (interview_type, start_days_ago, due_in_days)
        (None, 10, 5), ("hr", 10, 5), ("technical", 3, 5), (None, 1, 5), ("hr", 20, -8), (None, 30, -25),
    ]
    camps = [_campaign(session, a, world["tpo"], i, t, s, d) for i, (t, s, d) in enumerate(specs)]
    for i, c in enumerate(camps):
        _assign(session, c, sa[i: i + 7])
    cb = _campaign(session, b, world["tpo_b"], 99)
    _assign(session, cb, sb)
    # A sessions: various types, times (before start, in window, after due => late), statuses.
    for i, u in enumerate(sa):
        _session(session, a.id, u.id, "hr", 2 + (i % 5))
        _session(session, a.id, u.id, "technical", 12 + i)
        if i % 3 == 0:
            _session(session, a.id, u.id, "hr", 0, status="abandoned")
        if i % 4 == 0:
            _session(session, a.id, u.id, "technical", 15)  # after C4's due date => late
    # Institute B rows that reuse A student ids must never count for A.
    for u in sa:
        _session(session, b.id, u.id, "hr", 1)
        _session(session, b.id, u.id, "technical", 1)
    for u in sb:
        _session(session, b.id, u.id, "hr", 1)
    session.commit()
    return dict(a_campaigns=camps, b_campaign=cb, sa=sa, sb=sb)


def test_campaign_counts_match_reference_and_institute_scope(session, world, campaign_world):
    camps = campaign_world["a_campaigns"]
    ref = _reference(session, camps)
    got = svc.counts_for(session, camps)
    assert got == ref
    assert any(v[2] for v in got.values()), "fixture must exercise late completions"
    assert any(v[1] for v in got.values()) and any(v[0] > v[1] + v[2] for v in got.values())
    # Without B's look-alike rows A's numbers are identical (B rows ignored).
    session.execute(models.MockInterviewSession.__table__.delete().where(
        models.MockInterviewSession.tenant_id == world["b"].id))
    session.commit()
    assert svc.counts_for(session, camps) == ref
    assert svc.counts_for(session, [campaign_world["b_campaign"]]) == {campaign_world["b_campaign"].id: (4, 0, 0)}


def test_campaign_counts_constant_queries(session, world, campaign_world):
    ids = [c.id for c in campaign_world["a_campaigns"]]
    # Load the campaigns in one query first, as the list route does (the test
    # session is shared with the app, so committed objects would lazy-refresh).
    session.expire_all()
    camps = list(session.scalars(select(models.PlacementCampaign).where(models.PlacementCampaign.id.in_(ids))))
    c1, _ = _count(lambda: svc.counts_for(session, camps[:1]))
    c6, _ = _count(lambda: svc.counts_for(session, camps))
    assert c1.n == c6.n == 2
    # Only completion columns of the interview tables are read.
    joined = " ".join(c6.statements).lower()
    assert "mock_interview_reports" not in joined and "mock_interview_turns" not in joined
    assert svc.counts_for(session, []) == {}


def test_campaign_list_and_dashboard_query_count_independent_of_campaigns(client, world, on, session):
    on(world["a"])
    h = world["h_tpo"]
    sa = _students(session, world["a"], 6, "ql")

    def add(k):
        for i in range(k):
            c = _campaign(session, world["a"], world["tpo"], i, ["hr", None][i % 2])
            _assign(session, c, sa[: 2 + i % 4])

    def measure():
        session.expire_all()
        cl, r1 = _count(lambda: client.get(f"{P}/campaigns?limit=50", headers=h))
        cd, r2 = _count(lambda: client.get(f"{P}/dashboard", headers=h))
        assert r1.status_code == 200 and r2.status_code == 200
        return cl.n, cd.n, len(r1.json())

    add(2)
    l2, d2, n2 = measure()
    add(9)
    l11, d11, n11 = measure()
    assert (n2, n11) == (2, 11)
    assert l2 == l11 and d2 == d11


def test_legacy_applicant_list_constant_queries_and_skips_foreign_rows(client, world, on, session, make_user,
                                                                       token_header):
    on(world["a"])
    h = world["h_tpo"]
    d = mk_drive(session, world["a"])

    def measure():
        session.expire_all()  # same identity-map state for both measurements (shared test session)
        c, r = _count(lambda: client.get(f"{D}/{d.id}/applications?limit=200", headers=h))
        assert r.status_code == 200, r.text
        return c.n, r.json()

    assert client.post(f"{D}/{d.id}/apply", headers=world["h_s1"]).status_code == 201
    q1, rows1 = measure()
    for i in range(9):
        make_user(f"la{i}@a.edu", tenant=world["a"])
        assert client.post(f"{D}/{d.id}/apply", headers=token_header(f"la{i}@a.edu")).status_code == 201
    # A corrupt row pointing at another institute's student is skipped, as before.
    session.add(models.Application(tenant_id=world["a"].id, drive_id=d.id, student_id=world["sb"].id,
                                   status=models.ApplicationStatus.applied))
    session.commit()
    q10, rows10 = measure()
    assert (len(rows1), len(rows10)) == (1, 10)
    assert q1 == q10
    assert world["sb"].id not in {r["student_id"] for r in rows10}
    assert {r["full_name"] for r in rows10} >= {"Asha"}


def test_legacy_applicant_list_flag_off_unchanged_shape(client, world, session):
    d = mk_drive(session, world["a"])
    assert client.post(f"{D}/{d.id}/apply", headers=world["h_s1"]).status_code == 201
    rows = client.get(f"{D}/{d.id}/applications", headers=world["h_tpo"]).json()
    assert [(r["full_name"], r["status"], r["eligible"]) for r in rows] == [("Asha", "applied", True)]


def _offer(session, tenant, app_row, status, recruiter_id):
    session.add(models.Offer(tenant_id=tenant.id, application_id=app_row.id, recruiter_id=recruiter_id,
                             drive_id=app_row.drive_id, student_id=app_row.student_id, role_title="SDE",
                             status=status))


def test_analytics_drive_filter_reads_only_in_scope_offers(session, world):
    a, b = world["a"], world["b"]
    sa = _students(session, a, 9, "qo")
    sb = _students(session, b, 3, "qob")
    drives = [mk_drive(session, a, company_name=f"Co{i}") for i in range(3)]
    db_ = mk_drive(session, b, company_name="Co0")
    apps = []
    for i, u in enumerate(sa):
        for d in drives[: 1 + i % 3]:
            st = [models.ApplicationStatus.selected, models.ApplicationStatus.shortlisted][i % 2]
            row = models.Application(tenant_id=a.id, drive_id=d.id, student_id=u.id, status=st)
            session.add(row)
            apps.append(row)
    for u in sb:
        session.add(models.Application(tenant_id=b.id, drive_id=db_.id, student_id=u.id,
                                       status=models.ApplicationStatus.selected))
    session.commit()
    statuses = list(models.OfferStatus)
    for i, row in enumerate(apps):
        _offer(session, a, row, statuses[i % len(statuses)], world["tpo"].id)
    for row in session.scalars(select(models.Application).where(models.Application.tenant_id == b.id)):
        _offer(session, b, row, models.OfferStatus.accepted, world["tpo_b"].id)
    session.commit()

    loaded: list[int] = []

    def on_load(target, _ctx):
        loaded.append(target.tenant_id)

    def offers_for(f):
        loaded.clear()
        event.listen(models.Offer, "load", on_load)
        try:
            scope = ins.load_scope(session, a.id, f)
        finally:
            event.remove(models.Offer, "load", on_load)
        session.expunge_all()
        return scope, list(loaded)

    full, loaded_all = offers_for(ins.Filters())
    assert set(loaded_all) == {a.id} and len(loaded_all) == len(apps)  # never B's offers
    for d in drives:
        scope, loaded_d = offers_for(ins.Filters(drive_id=d.id))
        on_drive = [o for o in full.offers if o.drive_id == d.id]
        assert sorted(o.id for o in scope.offers) == sorted(o.id for o in on_drive)
        assert len(loaded_d) == len(on_drive) < len(apps)  # only in-scope offers read
    scope, _ = offers_for(ins.Filters(company="co1"))
    assert {o.drive_id for o in scope.offers} == {drives[1].id}
    # Summary numbers per drive add up to the unfiltered totals.
    total = ins.analytics(session, a.id, ins.Filters())["summary"]["offers"]
    per = sum(ins.analytics(session, a.id, ins.Filters(drive_id=d.id))["summary"]["offers"] for d in drives)
    assert total == per == len(apps)
