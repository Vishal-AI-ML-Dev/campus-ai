"""AI Mock Interview Phase 2: real-Postgres RLS and tenant-key checks.

Same gate and safety model as ``test_rls_postgres.py``: SKIPPED unless
``RLS_TEST_DATABASE_URL`` points at Postgres connected as the NOBYPASSRLS app
role; every write happens inside one transaction that is always rolled back,
with the tenant GUC set transaction-locally.
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

TABLES = ("mock_interview_sessions", "mock_interview_turns", "mock_interview_reports")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(_RLS_URL, poolclass=NullPool, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def conn(engine):
    """A connection inside a transaction that is always rolled back."""
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
    """Seed a tenant and one student (tenants/users are not under RLS)."""
    suffix = uuid.uuid4().hex[:8]
    tenant_id = conn.execute(
        text("INSERT INTO tenants (name, slug, is_active) VALUES (:n, :s, true) RETURNING id"),
        {"n": f"MI {label} {suffix}", "s": f"mi-{label}-{suffix}"},
    ).scalar()
    student_id = conn.execute(
        text(
            "INSERT INTO users (email, full_name, hashed_password, role, is_active, tenant_id) "
            "VALUES (:e, 'MI Student', 'x', 'student', true, :t) RETURNING id"
        ),
        {"e": f"mi-{label}-{suffix}@example.invalid", "t": tenant_id},
    ).scalar()
    return tenant_id, student_id


def _insert_session(conn, tenant_id, student_id):
    return conn.execute(
        text(
            "INSERT INTO mock_interview_sessions "
            "(tenant_id, student_id, interview_type, mode, status, difficulty, "
            " language, duration_minutes) "
            "VALUES (:t, :s, 'hr', 'text', 'setup', 'medium', 'en', 20) RETURNING id"
        ),
        {"t": tenant_id, "s": student_id},
    ).scalar()


def _insert_turn(conn, tenant_id, session_id, seq=1):
    conn.execute(
        text(
            "INSERT INTO mock_interview_turns (tenant_id, session_id, seq, question) "
            "VALUES (:t, :s, :q, 'Tell me about yourself.')"
        ),
        {"t": tenant_id, "s": session_id, "q": seq},
    )


def _insert_report(conn, tenant_id, session_id):
    conn.execute(
        text(
            "INSERT INTO mock_interview_reports "
            "(tenant_id, session_id, overall_score, category_scores_json, strengths_json, "
            " weaknesses_json, recommendations_json, rubric_version) "
            "VALUES (:t, :s, 70, '{}', '[]', '[]', '[]', 'v1')"
        ),
        {"t": tenant_id, "s": session_id},
    )


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
    session_id = _insert_session(conn, tenant_a, student_a)
    _insert_turn(conn, tenant_a, session_id)
    _insert_report(conn, tenant_a, session_id)
    assert _counts(conn) == {t: 1 for t in TABLES}

    _set_tenant(conn, tenant_b)
    assert _counts(conn) == {t: 0 for t in TABLES}
    # Updates and deletes from tenant B can't touch tenant A's rows either.
    assert conn.execute(
        text("UPDATE mock_interview_sessions SET status = 'failed' WHERE id = :i"),
        {"i": session_id},
    ).rowcount == 0
    assert conn.execute(
        text("DELETE FROM mock_interview_sessions WHERE id = :i"), {"i": session_id}
    ).rowcount == 0

    _set_tenant(conn, None)
    assert _counts(conn) == {t: 0 for t in TABLES}

    _set_tenant(conn, tenant_a)
    assert conn.execute(
        text("SELECT status FROM mock_interview_sessions WHERE id = :i"), {"i": session_id}
    ).scalar() == "setup"


def test_with_check_blocks_writing_for_another_tenant(conn):
    tenant_a, _ = _tenant_with_student(conn, "a")
    tenant_b, student_b = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    with pytest.raises(DBAPIError) as exc:
        _insert_session(conn, tenant_b, student_b)
    # 42501 + this message = the RLS WITH CHECK, not e.g. a missing grant.
    assert exc.value.orig.sqlstate == "42501"
    assert "row-level security" in str(exc.value.orig)


def test_child_cannot_point_at_another_tenants_session(conn):
    """FK checks ignore RLS, so the composite (session_id, tenant_id) key is
    what stops tenant B from attaching a turn/report to tenant A's session."""
    tenant_a, student_a = _tenant_with_student(conn, "a")
    tenant_b, _ = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    session_a = _insert_session(conn, tenant_a, student_a)

    _set_tenant(conn, tenant_b)
    for insert in (_insert_turn, _insert_report):
        savepoint = conn.begin_nested()
        with pytest.raises(IntegrityError):
            insert(conn, tenant_b, session_a)
        savepoint.rollback()


def test_session_student_must_be_in_same_tenant(conn):
    tenant_a, _ = _tenant_with_student(conn, "a")
    _, student_b = _tenant_with_student(conn, "b")
    _set_tenant(conn, tenant_a)
    with pytest.raises(IntegrityError):
        _insert_session(conn, tenant_a, student_b)


def test_check_constraints_enforced(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO mock_interview_sessions "
                "(tenant_id, student_id, interview_type, mode, status, difficulty, "
                " language, duration_minutes) "
                "VALUES (:t, :s, 'hr', 'text', 'setup', 'medium', 'fr', 20)"
            ),
            {"t": tenant_a, "s": student_a},
        )


def test_deleting_session_cascades_in_database(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    session_id = _insert_session(conn, tenant_a, student_a)
    _insert_turn(conn, tenant_a, session_id)
    _insert_report(conn, tenant_a, session_id)

    conn.execute(text("DELETE FROM mock_interview_sessions WHERE id = :i"), {"i": session_id})
    assert _counts(conn) == {t: 0 for t in TABLES}


def test_deleting_student_cascades(conn):
    tenant_a, student_a = _tenant_with_student(conn, "a")
    _set_tenant(conn, tenant_a)
    session_id = _insert_session(conn, tenant_a, student_a)
    _insert_turn(conn, tenant_a, session_id)
    _insert_report(conn, tenant_a, session_id)

    conn.execute(text("DELETE FROM users WHERE id = :i"), {"i": student_a})
    assert _counts(conn) == {t: 0 for t in TABLES}
