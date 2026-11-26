"""Phase 7.10: manual, audited 400-day retention purge (P7-D5). SQLite test DB only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import AIUsageEvent, AuditLog, MockInterviewSession
from scripts import purge_ai_usage as purge


@pytest.fixture
def seeded(make_user, make_tenant):
    a = make_user("purge-a@x.edu")
    other = make_tenant("purge-b")
    b = make_user("purge-b@x.edu", tenant=other)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for user, ages in ((a, (401, 450, 399, 1)), (b, (500, 2))):
            for i, age in enumerate(ages):
                db.add(AIUsageEvent(event_id=f"{user.id}-{i}", tenant_id=user.tenant_id, user_id=user.id,
                                    feature="mock_interview", step="turn", outcome="ok", latency_ms=1,
                                    created_at=now - timedelta(days=age)))
        db.add(MockInterviewSession(tenant_id=a.tenant_id, student_id=a.id, interview_type="hr", mode="text",
                                    status="completed", difficulty="medium", language="en",
                                    duration_minutes=15, created_at=now - timedelta(days=900),
                                    updated_at=now - timedelta(days=900)))
        db.commit()
    return a, b


def counts():
    with SessionLocal() as db:
        return (db.scalar(select(func.count(AIUsageEvent.id))),
                db.scalar(select(func.count(MockInterviewSession.id))),
                db.scalars(select(AuditLog).where(AuditLog.action == "ai_usage.purge")).all())


def test_dry_run_is_the_default_and_changes_nothing(seeded, capsys):
    assert purge.main([]) == 0
    out = capsys.readouterr().out
    assert "TOTAL: 3" in out and "Dry run" in out
    rows, sessions, audits = counts()
    assert (rows, sessions, len(audits)) == (6, 1, 0)


@pytest.mark.parametrize("argv", [["--apply"], ["--apply", "--expected-count", "2"]])
def test_apply_needs_the_exact_expected_count(seeded, argv):
    assert purge.main(argv) == 3
    assert counts()[0] == 6


def test_apply_deletes_only_expired_ledger_rows_and_audits_each_institute(seeded):
    a, b = seeded
    assert purge.main(["--apply", "--expected-count", "3"]) == 0
    rows, sessions, audits = counts()
    assert rows == 3 and sessions == 1  # recent rows and the old session survive
    by_tenant = {x.tenant_id: x for x in audits}
    assert set(by_tenant) == {a.tenant_id, b.tenant_id}
    assert all(x.actor_email == "system:purge_ai_usage" and x.actor_id is None for x in audits)
    assert "Deleted 2 AI usage events" in by_tenant[a.tenant_id].summary
    assert "Deleted 1 AI usage events" in by_tenant[b.tenant_id].summary
    assert "retention 400 days" in by_tenant[a.tenant_id].summary
    with SessionLocal() as db:
        ages = [(datetime.now(timezone.utc) - e.created_at.replace(tzinfo=timezone.utc)).days
                for e in db.scalars(select(AIUsageEvent))]
    assert max(ages) < 400


def test_tenant_scope_and_minimum_retention(seeded, capsys):
    a, _ = seeded
    assert purge.main(["--days", "399"]) == 2
    assert purge.main(["--tenant-id", str(a.tenant_id), "--apply", "--expected-count", "2"]) == 0
    assert counts()[0] == 4 and len(counts()[2]) == 1
    assert purge.main(["--days", "1000"]) == 0
    assert "TOTAL: 0" in capsys.readouterr().out
