"""Phase 8.1: coding tables on real Postgres (RLS, composite tenant keys, cascades).

Same gate and safety model as ``test_mock_interview_rls_postgres.py``: SKIPPED
unless ``RLS_TEST_DATABASE_URL`` points at Postgres connected as the
NOBYPASSRLS app role; every write happens inside one transaction that is
always rolled back, with the tenant GUC set transaction-locally.
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

TABLES = ("mock_interview_code_attempts", "mock_interview_code_submissions")


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
        {"n": f"MIC {label} {suffix}", "s": f"mic-{label}-{suffix}"},
    ).scalar()
    student_id = conn.execute(
        text(
            "INSERT INTO users (email, full_name, hashed_password, role, is_active, tenant_id) "
            "VALUES (:e, 'MIC Student', 'x', 'student', true, :t) RETURNING id"
        ),
        {"e": f"mic-{label}-{suffix}@example.invalid", "t": tenant_id},
    ).scalar()
    return tenant_id, student_id


def _session(conn, tenant_id, student_id):
    return conn.execute(
        text(
            "INSERT INTO mock_interview_sessions "
            "(tenant_id, student_id, interview_type, mode, status, difficulty, language, duration_minutes) "
            "VALUES (:t, :s, 'coding', 'text', 'in_progress', 'medium', 'en', 45) RETURNING id"
        ),
        {"t": tenant_id, "s": student_id},
    ).scalar()


def _attempt(conn, tenant_id, session_id, seq=1):
    return conn.execute(
        text(
            "INSERT INTO mock_interview_code_attempts "
            "(tenant_id, session_id, seq, problem_key, problem_version, status) "
            "VALUES (:t, :s, :q, 'count-islands', 1, 'pending') RETURNING id"
        ),
        {"t": tenant_id, "s": session_id, "q": seq},
    ).scalar()


def _submission(conn, tenant_id, session_id, attempt_id):
    return conn.execute(
        text(
            "INSERT INTO mock_interview_code_submissions "
            "(tenant_id, session_id, attempt_id, kind, language, source, source_sha256, "
            " execution_id, status) "
            "VALUES (:t, :s, :a, 'graded', 'python313', 'print(1)', :h, :e, 'not_executed') RETURNING id"
        ),
        {"t": tenant_id, "s": session_id, "a": attempt_id, "h": "0" * 64, "e": str(uuid.uuid4())},
    ).scalar()


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
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    _submission(conn, tenant_a, s, a)
    assert _counts(conn) == {t: 1 for t in TABLES}

    _set_tenant(conn, tenant_b)
    assert _counts(conn) == {t: 0 for t in TABLES}
    assert conn.execute(
        text("UPDATE mock_interview_code_attempts SET submissions_used = 99 WHERE id = :i"), {"i": a}
    ).rowcount == 0
    assert conn.execute(text("DELETE FROM mock_interview_code_submissions")).rowcount == 0
    _set_tenant(conn, None)
    assert _counts(conn) == {t: 0 for t in TABLES}


def test_with_check_blocks_writing_for_another_tenant(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    _set_tenant(conn, tenant_b)
    with pytest.raises(DBAPIError) as exc:
        _attempt(conn, tenant_a, s)
    assert exc.value.orig.sqlstate == "42501"


def test_children_cannot_point_at_another_tenants_rows(conn):
    """FK checks ignore RLS: the composite (…, tenant_id) keys do the stopping."""
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, student_b = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    session_a = _session(conn, tenant_a, student_a)
    attempt_a = _attempt(conn, tenant_a, session_a)
    _set_tenant(conn, tenant_b)
    session_b = _session(conn, tenant_b, student_b)
    attempt_b = _attempt(conn, tenant_b, session_b)
    for insert in (
        lambda: _attempt(conn, tenant_b, session_a, seq=2),
        lambda: _submission(conn, tenant_b, session_a, attempt_b),
        lambda: _submission(conn, tenant_b, session_b, attempt_a),
    ):
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError):
            insert()
        savepoint.rollback()


def test_deleting_a_session_cascades_to_attempts_and_submissions(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    _submission(conn, tenant_a, s, a)
    conn.execute(text("DELETE FROM mock_interview_sessions WHERE id = :i"), {"i": s})
    assert _counts(conn) == {t: 0 for t in TABLES}


def test_check_constraints_enforced(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    s = _session(conn, tenant_a, student_a)
    a = _attempt(conn, tenant_a, s)
    for sql in (
        "UPDATE mock_interview_code_attempts SET status = 'running' WHERE id = :i",
        "UPDATE mock_interview_code_attempts SET submissions_used = -1 WHERE id = :i",
    ):
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError):
            conn.execute(text(sql), {"i": a})
        savepoint.rollback()
