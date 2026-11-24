"""Phase 9.1: placement management tables on real Postgres (RLS, composite FK).

Same gate and safety model as ``test_mock_interview_coding_rls_postgres.py``:
SKIPPED unless ``RLS_TEST_DATABASE_URL`` points at a LOCAL THROWAWAY Postgres
connected as the NOBYPASSRLS app role, migrated to head; every write happens
inside one transaction that is always rolled back, with the tenant GUC set
transaction-locally. Never point it at staging or production.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

_RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    "postgresql" not in _RLS_URL,
    reason="RLS_TEST_DATABASE_URL not set to a Postgres app-role URL.",
)

TABLES = (
    "student_placement_profiles",
    "placement_drive_settings",
    "placement_drive_departments",
    "placement_application_events",
)


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


def _set_tenant(conn, tenant_id):
    conn.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": "" if tenant_id is None else str(tenant_id)},
    )


def _tenant_with_student(conn, label):
    suffix = uuid.uuid4().hex[:8]
    tenant_id = conn.execute(
        text("INSERT INTO tenants (name, slug, is_active) VALUES (:n, :s, true) RETURNING id"),
        {"n": f"P9 {label} {suffix}", "s": f"p9-{label}-{suffix}"},
    ).scalar()
    student_id = conn.execute(
        text(
            "INSERT INTO users (email, full_name, hashed_password, role, is_active, tenant_id) "
            "VALUES (:e, 'P9 Student', 'x', 'student', true, :t) RETURNING id"
        ),
        {"e": f"p9-{label}-{suffix}@example.invalid", "t": tenant_id},
    ).scalar()
    return tenant_id, student_id


def _seed(conn, tenant_id, student_id):
    """One row in every new table for this tenant (GUC must be set)."""
    dept = conn.execute(
        text("INSERT INTO departments (tenant_id, name, code) VALUES (:t, :n, :c) RETURNING id"),
        {"t": tenant_id, "n": f"D{uuid.uuid4().hex[:6]}", "c": uuid.uuid4().hex[:6]},
    ).scalar()
    drive = conn.execute(
        text(
            "INSERT INTO drives (tenant_id, company_name, role_title, min_cgpa, min_attendance, "
            " min_verified_projects, is_open) VALUES (:t, 'Acme', 'SDE', 0, 0, 0, false) RETURNING id"
        ),
        {"t": tenant_id},
    ).scalar()
    app_id = conn.execute(
        text(
            "INSERT INTO applications (tenant_id, drive_id, student_id, status, contact_revealed, "
            " recruiter_decision) VALUES (:t, :d, :s, 'applied', false, 'pending') RETURNING id"
        ),
        {"t": tenant_id, "d": drive, "s": student_id},
    ).scalar()
    conn.execute(
        text(
            "INSERT INTO student_placement_profiles (tenant_id, student_id, placement_status) "
            "VALUES (:t, :s, 'active')"
        ),
        {"t": tenant_id, "s": student_id},
    )
    conn.execute(
        text(
            "INSERT INTO placement_drive_settings (tenant_id, drive_id, lifecycle, exclude_placed) "
            "VALUES (:t, :d, 'draft', false)"
        ),
        {"t": tenant_id, "d": drive},
    )
    conn.execute(
        text("INSERT INTO placement_drive_departments (tenant_id, drive_id, department_id) VALUES (:t, :d, :x)"),
        {"t": tenant_id, "d": drive, "x": dept},
    )
    conn.execute(
        text(
            "INSERT INTO placement_application_events (tenant_id, application_id, to_status) "
            "VALUES (:t, :a, 'applied')"
        ),
        {"t": tenant_id, "a": app_id},
    )
    return drive


def _counts(conn):
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}


def test_tables_have_forced_rls_and_policy(engine):
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "  (SELECT count(*) FROM pg_policies p "
                "   WHERE p.tablename = c.relname AND p.policyname = 'tenant_isolation' "
                "     AND p.cmd = 'ALL' "
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


def test_rows_visible_only_to_own_tenant(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    _seed(conn, tenant_a, student_a)
    assert _counts(conn) == {t: 1 for t in TABLES}

    _set_tenant(conn, tenant_b)
    assert _counts(conn) == {t: 0 for t in TABLES}
    for table in TABLES:
        assert conn.execute(text(f"UPDATE {table} SET tenant_id = tenant_id")).rowcount == 0
        assert conn.execute(text(f"DELETE FROM {table}")).rowcount == 0

    _set_tenant(conn, None)
    assert _counts(conn) == {t: 0 for t in TABLES}


def test_cannot_write_rows_for_another_tenant(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_b)
    with pytest.raises(DBAPIError):
        conn.execute(
            text(
                "INSERT INTO student_placement_profiles (tenant_id, student_id, placement_status) "
                "VALUES (:t, :s, 'active')"
            ),
            {"t": tenant_a, "s": student_a},
        )


def test_profile_composite_fk_rejects_foreign_student(conn):
    tenant_a, _ = _tenant_with_student(conn, "a")
    _tenant_b, student_b = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO student_placement_profiles (tenant_id, student_id, placement_status) "
                "VALUES (:t, :s, 'active')"
            ),
            {"t": tenant_a, "s": student_b},
        )


def test_deleting_drive_cascades_settings_departments(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    drive = _seed(conn, tenant_a, student_a)
    conn.execute(text("DELETE FROM drives WHERE id = :d"), {"d": drive})
    counts = _counts(conn)
    assert counts["placement_drive_settings"] == 0
    assert counts["placement_drive_departments"] == 0
    assert counts["placement_application_events"] == 0  # via applications cascade
    assert counts["student_placement_profiles"] == 1
