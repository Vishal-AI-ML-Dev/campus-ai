"""Phase 9 review L1 on real Postgres: withdrawal blocks on the shared
per-student lock and re-checks after it (opt-in; skipped without
RLS_TEST_DATABASE_URL - see conftest.py for the fail-closed safety gate).

A second connection holds ``SELECT ... FOR UPDATE`` on the student's users row
(the lock every offer / acceptance / selection writer takes). The withdrawal
request, run in a thread, must BLOCK on that lock - detected from
``pg_stat_activity`` (a state condition, not a timing assumption). The holder
then commits the competing change; the withdrawal resumes, sees it and is
refused with 409. Synthetic data only; tenants are removed by the fixture.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text


@pytest.fixture
def flagged(monkeypatch, campus):
    from app.core.config import settings  # imported lazily, like the other files here

    monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_ENABLED", True)
    monkeypatch.setattr(settings, "PLACEMENT_MANAGEMENT_TENANT_IDS", f"{campus.a.tenant},{campus.b.tenant}")
    return campus


def _shortlisted_application(a, name):
    r = a.tpo.post("/v1/placement/drives", json={"company_name": name, "role_title": "SDE"})
    assert r.status_code == 201, r.text
    d = r.json()["drive"]["id"]
    assert a.tpo.post(f"/v1/placement/drives/{d}/lifecycle", json={"to": "published"}).status_code == 200
    r = a.student.post(f"/v1/placement/drives/{d}/apply")
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert a.tpo.patch(f"/v1/placement/applications/{aid}/status", json={"status": "shortlisted"}).status_code == 200
    return d, aid


def _wait_until_blocked(aux_engine, deadline_s=30.0):
    """Wait for a backend of this database to be waiting on a lock."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        with aux_engine.connect() as c:
            n = c.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' AND state = 'active'"
            )).scalar()
        if n:
            return
        time.sleep(0.05)
    raise AssertionError("the withdrawal never blocked on the student lock")


def _withdraw_in_thread(a, aid):
    out = {}

    def run():
        out["r"] = a.student.post(f"/v1/placement/applications/{aid}/withdraw")

    t = threading.Thread(target=run)
    t.start()
    return t, out


def test_offer_committed_under_lock_blocks_withdrawal(flagged, aux_engine, sql):
    a = flagged.a
    d, aid = _shortlisted_application(a, "LockCo")
    rec = sql("INSERT INTO recruiters (tenant_id, company_name, status, created_at) "
              "VALUES (:t, 'LockCo', 'active', now()) RETURNING id", {"t": a.tenant}, tenant=a.tenant)[0]["id"]
    with aux_engine.connect() as holder:
        tx = holder.begin()
        holder.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(a.tenant)})
        holder.execute(text("SELECT id FROM users WHERE id = :s FOR UPDATE"), {"s": a.student.id})
        t, out = _withdraw_in_thread(a, aid)
        _wait_until_blocked(aux_engine)
        assert "r" not in out  # still waiting for the lock
        holder.execute(text(
            "INSERT INTO offers (tenant_id, application_id, recruiter_id, drive_id, student_id, role_title, "
            "status, created_at) VALUES (:t, :a, :r, :d, :s, 'SDE', 'extended', now())"),
            {"t": a.tenant, "a": aid, "r": rec, "d": d, "s": a.student.id})
        tx.commit()
    t.join(60)
    assert out["r"].status_code == 409 and "open offer" in out["r"].json()["detail"]
    st = sql("SELECT status::text AS s FROM applications WHERE id = :a", {"a": aid}, tenant=a.tenant)[0]["s"]
    assert st == "shortlisted"
    assert sql("SELECT count(*) AS n FROM placement_application_events WHERE application_id = :a "
               "AND to_status = 'withdrawn'", {"a": aid}, tenant=a.tenant)[0]["n"] == 0


def test_selection_committed_under_lock_blocks_withdrawal(flagged, aux_engine, sql):
    a = flagged.a
    _dx, x = _shortlisted_application(a, "LockX")
    _dy, y = _shortlisted_application(a, "LockY")
    with aux_engine.connect() as holder:
        tx = holder.begin()
        holder.execute(text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(a.tenant)})
        holder.execute(text("SELECT id FROM users WHERE id = :s FOR UPDATE"), {"s": a.student.id})
        t, out = _withdraw_in_thread(a, x)
        _wait_until_blocked(aux_engine)
        assert "r" not in out
        holder.execute(text("UPDATE applications SET status = 'selected' WHERE id = :y"), {"y": y})
        tx.commit()
    t.join(60)
    assert out["r"].status_code == 409 and "already placed" in out["r"].json()["detail"]
    st = sql("SELECT status::text AS s FROM applications WHERE id = :a", {"a": x}, tenant=a.tenant)[0]["s"]
    assert st == "shortlisted"
