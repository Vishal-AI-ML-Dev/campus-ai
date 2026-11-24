"""Phase 8.3 + 8.4: code reviews and company profiles on real Postgres.

Same gate and safety model as ``test_mock_interview_coding_rls_postgres.py``:
SKIPPED unless ``RLS_TEST_DATABASE_URL`` points at Postgres connected as the
NOBYPASSRLS app role; every write happens inside one transaction that is
always rolled back, with the tenant GUC set transaction-locally.

Proves: forced RLS + exact ``tenant_isolation`` policy on both tables; rows
visible / writable only for the own tenant; composite tenant keys stop a
review from pointing at another institute's session or attempt; cascades;
review-only feedback can never hold a score or claim verification (CHECK).
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.test_mock_interview_coding_rls_postgres import (  # noqa: F401 - fixtures
    _attempt,
    _session,
    _set_tenant,
    _tenant_with_student,
    conn,
    engine,
)

_RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    "postgresql" not in _RLS_URL,
    reason="RLS_TEST_DATABASE_URL not set to a Postgres app-role URL.",
)

TABLES = ("mock_interview_code_reviews", "mock_interview_company_profiles")


def _review(conn, tenant_id, session_id, attempt_id, **extra):  # noqa: F811
    cols = {"rubric_version": "coding-v1-unverified", "status": "ai_estimated",
            "verification": "not_verified", "code_quality_score": 3, **extra}
    names = ", ".join(cols)
    params = ", ".join(f":{k}" for k in cols)
    return conn.execute(
        text(
            f"INSERT INTO mock_interview_code_reviews (tenant_id, session_id, attempt_id, {names}) "
            f"VALUES (:t, :s, :a, {params}) RETURNING id"
        ),
        {"t": tenant_id, "s": session_id, "a": attempt_id, **cols},
    ).scalar()


def _profile(conn, tenant_id, status="draft"):  # noqa: F811
    return conn.execute(
        text(
            "INSERT INTO mock_interview_company_profiles "
            "(tenant_id, name, role_title, role_template_key, status) "
            "VALUES (:t, 'Acme', 'SDE', 'sde_fresher', :st) RETURNING id"
        ),
        {"t": tenant_id, "st": status},
    ).scalar()


def _counts(conn):  # noqa: F811
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES}


def test_tables_have_forced_rls_and_policy(engine):  # noqa: F811
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


def test_rows_visible_only_to_own_tenant(conn):  # noqa: F811
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    r = _review(conn, tenant_a, s, a)
    p = _profile(conn, tenant_a, "published")
    assert _counts(conn) == {t: 1 for t in TABLES}

    _set_tenant(conn, tenant_b)
    assert _counts(conn) == {t: 0 for t in TABLES}
    assert conn.execute(
        text("UPDATE mock_interview_company_profiles SET status = 'archived' WHERE id = :i"), {"i": p}
    ).rowcount == 0
    assert conn.execute(
        text("UPDATE mock_interview_code_reviews SET code_quality_score = 5 WHERE id = :i"), {"i": r}
    ).rowcount == 0
    _set_tenant(conn, None)
    assert _counts(conn) == {t: 0 for t in TABLES}


def test_with_check_blocks_writing_for_another_tenant(conn):  # noqa: F811
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    _set_tenant(conn, tenant_b)
    for insert in (lambda: _profile(conn, tenant_a), lambda: _review(conn, tenant_a, s, a)):
        savepoint = conn.begin_nested()
        with pytest.raises(DBAPIError) as exc:
            insert()
        assert exc.value.orig.sqlstate == "42501"
        savepoint.rollback()


def test_reviews_cannot_point_at_another_tenants_rows(conn):  # noqa: F811
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, student_b = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    session_a = _session(conn, tenant_a, student_a)
    attempt_a = _attempt(conn, tenant_a, session_a)
    _set_tenant(conn, tenant_b)
    session_b = _session(conn, tenant_b, student_b)
    attempt_b = _attempt(conn, tenant_b, session_b)
    for insert in (
        lambda: _review(conn, tenant_b, session_a, attempt_b),
        lambda: _review(conn, tenant_b, session_b, attempt_a),
    ):
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError):
            insert()
        savepoint.rollback()


def test_cascades_and_drive_set_null(conn):  # noqa: F811
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    _review(conn, tenant_a, s, a)
    conn.execute(text("DELETE FROM mock_interview_sessions WHERE id = :i"), {"i": s})
    assert conn.execute(text("SELECT count(*) FROM mock_interview_code_reviews")).scalar() == 0
    drive_id = conn.execute(
        text(
            "INSERT INTO drives (tenant_id, company_name, role_title, min_cgpa, min_attendance, "
            " min_verified_projects, is_open) VALUES (:t, 'Acme', 'SDE', 0, 0, 0, true) RETURNING id"
        ),
        {"t": tenant_a},
    ).scalar()
    p = _profile(conn, tenant_a)
    conn.execute(text("UPDATE mock_interview_company_profiles SET drive_id = :d WHERE id = :i"),
                 {"d": drive_id, "i": p})
    conn.execute(text("DELETE FROM drives WHERE id = :d"), {"d": drive_id})
    assert conn.execute(
        text("SELECT drive_id FROM mock_interview_company_profiles WHERE id = :i"), {"i": p}
    ).scalar() is None


def test_review_only_feedback_can_never_hold_a_score(conn):  # noqa: F811
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    for seq, extra in enumerate(
        (
            {"problem_score": 12.0},
            {"verification": "verified"},
            {"code_quality_score": 6},
            {"status": "passed"},
            {"rubric_version": "coding-v2"},
        ),
        start=1,
    ):
        a = _attempt(conn, tenant_a, s, seq=seq)
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError):
            _review(conn, tenant_a, s, a, **extra)
        savepoint.rollback()
    p = _profile(conn, tenant_a)
    savepoint = conn.begin_nested()
    with pytest.raises(IntegrityError):
        conn.execute(text("UPDATE mock_interview_company_profiles SET status = 'deleted' WHERE id = :i"),
                     {"i": p})
    savepoint.rollback()
