"""Phase 9.2-9.4 on real Postgres: campaign tables RLS, 'withdrawn' enum value,
compare-and-set under two concurrent connections.

Same gate and safety model as ``test_placement_management_rls_postgres.py``:
SKIPPED unless ``RLS_TEST_DATABASE_URL`` points at a LOCAL THROWAWAY Postgres
connected as the NOBYPASSRLS app role, migrated to head (``e1a3c5b7d9f4``).
Every write happens inside transactions that are always rolled back. Never
point it at staging or production.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

from tests.test_placement_management_rls_postgres import _seed, _set_tenant, _tenant_with_student

_RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    "postgresql" not in _RLS_URL,
    reason="RLS_TEST_DATABASE_URL not set to a Postgres app-role URL.",
)

TABLES = ("placement_campaigns", "placement_campaign_students")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_RLS_URL, poolclass=NullPool, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


def _campaign(conn, tenant_id, student_id):
    cid = conn.execute(
        text(
            "INSERT INTO placement_campaigns (tenant_id, title, status, starts_at, due_at, created_by_role) "
            "VALUES (:t, 'Prep', 'draft', now(), now() + interval '7 days', 'tpo') RETURNING id"
        ),
        {"t": tenant_id},
    ).scalar()
    conn.execute(
        text("INSERT INTO placement_campaign_students (tenant_id, campaign_id, student_id) VALUES (:t, :c, :s)"),
        {"t": tenant_id, "c": cid, "s": student_id},
    )
    return cid


def _counts(conn):
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}


def test_campaign_tables_have_forced_rls_and_policy(engine):
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "  (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname "
                "     AND p.policyname = 'tenant_isolation' AND p.cmd = 'ALL' "
                "     AND p.qual = '(tenant_id = app.current_tenant_id())' "
                "     AND p.with_check = '(tenant_id = app.current_tenant_id())') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:t)"
            ),
            {"t": list(TABLES)},
        ).all()
    assert sorted(r[0] for r in rows) == sorted(TABLES)
    for name, enabled, forced, policies in rows:
        assert enabled and forced, f"{name}: RLS not enabled+forced"
        assert policies == 1, f"{name}: tenant_isolation policy missing or altered"


def test_campaign_rows_isolated(conn):
    tenant_a, student_a = _tenant_with_student(conn, "ca")
    tenant_b, _ = _tenant_with_student(conn, "cb")
    _set_tenant(conn, tenant_a)
    _campaign(conn, tenant_a, student_a)
    assert _counts(conn) == {t: 1 for t in TABLES}
    _set_tenant(conn, tenant_b)
    assert _counts(conn) == {t: 0 for t in TABLES}
    for table in TABLES:
        assert conn.execute(text(f"DELETE FROM {table}")).rowcount == 0
    with pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO placement_campaigns (tenant_id, title, status, starts_at, due_at, created_by_role) "
                "VALUES (:t, 'x', 'draft', now(), now() + interval '1 day', 'tpo')"
            ),
            {"t": tenant_a},
        )


def test_campaign_student_composite_fk_rejects_foreign_student(conn):
    tenant_a, student_a = _tenant_with_student(conn, "fa")
    _tb, student_b = _tenant_with_student(conn, "fb")
    _set_tenant(conn, tenant_a)
    cid = _campaign(conn, tenant_a, student_a)
    with pytest.raises(IntegrityError):
        conn.execute(
            text("INSERT INTO placement_campaign_students (tenant_id, campaign_id, student_id) VALUES (:t, :c, :s)"),
            {"t": tenant_a, "c": cid, "s": student_b},
        )


def test_withdrawn_enum_value_and_event_check(conn):
    tenant_a, student_a = _tenant_with_student(conn, "wa")
    _set_tenant(conn, tenant_a)
    _seed(conn, tenant_a, student_a)
    app_id = conn.execute(text("SELECT id FROM applications WHERE student_id = :s"), {"s": student_a}).scalar()
    assert conn.execute(
        text("UPDATE applications SET status = 'withdrawn' WHERE id = :a AND status = 'applied'"), {"a": app_id}
    ).rowcount == 1
    conn.execute(
        text(
            "INSERT INTO placement_application_events (tenant_id, application_id, from_status, to_status) "
            "VALUES (:t, :a, 'applied', 'withdrawn')"
        ),
        {"t": tenant_a, "a": app_id},
    )
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO placement_application_events (tenant_id, application_id, to_status) "
                "VALUES (:t, :a, 'bogus')"
            ),
            {"t": tenant_a, "a": app_id},
        )


def test_compare_and_set_blocks_a_concurrent_writer(engine):
    """Two connections: A moves applied -> shortlisted (uncommitted). B's
    compare-and-set cannot change the row (it is invisible/locked to B; a lock
    timeout bounds any wait), and A's own stale compare-and-set matches 0 rows."""
    suffix = uuid.uuid4().hex[:6]
    with engine.connect() as a, engine.connect() as b:
        ta = a.begin()
        tb = b.begin()
        try:
            tenant, student = _tenant_with_student(a, f"cc{suffix}")
            _set_tenant(a, tenant)
            _seed(a, tenant, student)
            app_id = a.execute(text("SELECT id FROM applications WHERE student_id = :s"), {"s": student}).scalar()
            cas = "UPDATE applications SET status = :to WHERE id = :a AND status = 'applied'"
            assert a.execute(text(cas), {"to": "shortlisted", "a": app_id}).rowcount == 1
            # B cannot see A's uncommitted tenant, so B can never touch the row either way.
            _set_tenant(b, tenant)
            b.execute(text("SET LOCAL lock_timeout = '300ms'"))
            n = b.execute(text(cas), {"to": "withdrawn", "a": app_id}).rowcount
            assert n == 0
            # Within A, the stale compare-and-set now matches nothing.
            assert a.execute(text(cas), {"to": "withdrawn", "a": app_id}).rowcount == 0
        finally:
            tb.rollback()
            ta.rollback()
