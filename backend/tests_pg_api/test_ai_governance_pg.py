"""Phase 10.3 AI governance on real Postgres as the NOBYPASSRLS ``app_user``.

Proves what SQLite cannot:
  * ``ai_platform_usage_daily`` has RLS ENABLED + FORCED with the
    ``ai_platform_counter`` policy: without the transaction GUC a client (e.g. a
    Supabase Data API role) sees no row and cannot insert or reset one;
  * the platform request reservation is atomic under concurrency: N parallel
    admissions against a cap of K reserve exactly K;
  * institute caps are counted from the RLS-protected ledger per institute.

Synthetic data only; today's counter row is removed before and after each test.
Skipped unless RLS_TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest

GUC = "SELECT set_config('app.ai_platform_counter', 'on', true)"


@pytest.fixture
def clean_counter(aux_engine):
    from sqlalchemy import text

    def wipe():
        with aux_engine.begin() as conn:
            conn.execute(text(GUC))
            conn.execute(text("DELETE FROM ai_platform_usage_daily WHERE day = :d"),
                         {"d": datetime.now(timezone.utc).date()})

    wipe()
    yield
    wipe()


@pytest.fixture
def governance(monkeypatch):
    from app.core.config import settings
    from app.services import ai_governance

    monkeypatch.setattr(settings, "AI_GOVERNANCE_ENABLED", True)
    monkeypatch.setattr(settings, "AI_CONCURRENCY_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(settings, "AI_MAX_CONCURRENT_CALLS", 50)
    ai_governance._reset()
    yield settings
    ai_governance._reset()


def test_pg_counter_table_rls_is_forced(sql):
    rows = sql(
        "SELECT c.relrowsecurity AS on_, c.relforcerowsecurity AS forced, "
        "(SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname "
        " AND p.policyname = 'ai_platform_counter') AS policies "
        "FROM pg_class c WHERE c.relname = 'ai_platform_usage_daily'"
    )
    assert rows == [{"on_": True, "forced": True, "policies": 1}]


def test_pg_counter_invisible_and_unwritable_without_guc(aux_engine, sql, clean_counter, governance):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.services import ai_governance

    with ai_governance.admit("mentor", tenant_id=None, user_id=None):
        pass
    assert ai_governance.platform_usage_today()["requests"] == 1
    # Same role, no GUC: nothing visible, nothing writable.
    assert sql("SELECT count(*) AS n FROM ai_platform_usage_daily") == [{"n": 0}]
    assert sql("UPDATE ai_platform_usage_daily SET requests = 0 RETURNING day") == []
    with pytest.raises(DBAPIError):
        with aux_engine.begin() as conn:
            conn.execute(text("INSERT INTO ai_platform_usage_daily (day, requests, tokens) "
                              "VALUES ('2099-01-01', 0, 0)"))
    assert ai_governance.platform_usage_today()["requests"] == 1


def test_pg_platform_reservation_is_atomic(clean_counter, governance, monkeypatch):
    from app.services import ai_governance

    monkeypatch.setattr(governance, "AI_PLATFORM_DAILY_REQUEST_MAX", "7")
    admitted, refused = [], []
    barrier = threading.Barrier(20)

    def one():
        barrier.wait()
        try:
            with ai_governance.admit("ats", tenant_id=None, user_id=None):
                admitted.append(1)
        except ai_governance.AIRefused as exc:
            refused.append(exc.reason)

    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(admitted) == 7
    assert refused == ["platform_requests"] * 13
    assert ai_governance.platform_usage_today()["requests"] == 7


def test_pg_tenant_cap_counts_only_own_institute(campus, clean_counter, governance, monkeypatch):
    from app.services import ai_governance, ai_usage

    a, b = campus.a, campus.b
    for _ in range(2):
        ai_usage.record(ai_usage.build_event(
            ctx=ai_usage.UsageContext(tenant_id=a.tenant, user_id=a.student.id, feature="mentor"),
            event_id=f"pg-gov-{uuid.uuid4().hex}", step="mentor_chat", outcome="ok", latency_ms=1))
    monkeypatch.setattr(governance, "AI_TENANT_DAILY_REQUEST_MAX", "2")
    with pytest.raises(ai_governance.AIRefused) as info:
        ai_governance.precheck("mentor", tenant_id=a.tenant, user_id=a.student.id)
    assert info.value.reason == "tenant_requests"
    ai_governance.precheck("mentor", tenant_id=b.tenant, user_id=b.student.id)  # other institute ok
