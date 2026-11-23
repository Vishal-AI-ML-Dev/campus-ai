"""Phase 8.1: coding tables (migration e7a1c4d9b2f6) - constraints and keys on SQLite.

Real-Postgres RLS and cross-tenant key checks: test_mock_interview_coding_rls_postgres.py.
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

import app.core.db as db
import app.models as models
from app.models import MockInterviewCodeAttempt, MockInterviewCodeSubmission, MockInterviewSession


@pytest.fixture
def coding_session(session, make_user):
    student = make_user("m@x.edu")
    s = MockInterviewSession(tenant_id=student.tenant_id, student_id=student.id, interview_type="coding",
                             mode="text", status="in_progress", difficulty="medium", language="en",
                             duration_minutes=45)
    session.add(s)
    session.commit()
    return s


def _attempt(s, **overrides):
    values = dict(tenant_id=s.tenant_id, session_id=s.id, seq=1, problem_key="count-islands",
                  problem_version=1, status="pending")
    values.update(overrides)
    return MockInterviewCodeAttempt(**values)


def _submission(s, a, **overrides):
    values = dict(tenant_id=s.tenant_id, session_id=s.id, attempt_id=a.id, kind="graded",
                  language="python313", source="print(1)", source_sha256="0" * 64,
                  execution_id="11111111-1111-1111-1111-111111111111", status="not_executed")
    values.update(overrides)
    return MockInterviewCodeSubmission(**values)


def test_tables_registered_tenant_scoped_and_keyed_through_tenant():
    tables = models.Base.metadata.tables
    for name in ("mock_interview_code_attempts", "mock_interview_code_submissions"):
        assert "tenant_id" in tables[name].c
    fks = {tuple(sorted(c.name for c in fk.columns)): fk.referred_table.name
           for fk in tables["mock_interview_code_submissions"].foreign_key_constraints}
    assert fks[("session_id", "tenant_id")] == "mock_interview_sessions"
    assert fks[("attempt_id", "tenant_id")] == "mock_interview_code_attempts"
    attempt_fks = {tuple(sorted(c.name for c in fk.columns)) for fk in
                   tables["mock_interview_code_attempts"].foreign_key_constraints}
    assert ("session_id", "tenant_id") in attempt_fks


def test_defaults_and_roundtrip(session, coding_session):
    a = _attempt(coding_session)
    session.add(a)
    session.commit()
    assert (a.draft_rev, a.hints_used, a.runs_used, a.submissions_used) == (0, 0, 0, 0)
    sub = _submission(coding_session, a, group_results_json={"basic": [1, 2]})
    session.add(sub)
    session.commit()
    session.refresh(sub)
    assert sub.group_results_json == {"basic": [1, 2]} and sub.retry_count == 0


@pytest.mark.parametrize("overrides", [
    {"status": "running"}, {"language": "ruby"}, {"seq": 0}, {"hints_used": -1},
])
def test_attempt_check_constraints(session, coding_session, overrides):
    session.add(_attempt(coding_session, **overrides))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("overrides", [
    {"kind": "run"}, {"language": "javascript"}, {"status": "passed"}, {"verdict": "ok"},
])
def test_submission_check_constraints(session, coding_session, overrides):
    a = _attempt(coding_session)
    session.add(a)
    session.commit()
    session.add(_submission(coding_session, a, **overrides))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_unique_seq_execution_id_and_idempotency_key(session, coding_session):
    a = _attempt(coding_session)
    session.add(a)
    session.commit()
    session.add(_attempt(coding_session))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(_submission(coding_session, a, idempotency_key="k-1"))
    session.commit()
    for clash in ({"idempotency_key": "k-2"},  # same execution_id
                  {"idempotency_key": "k-1", "execution_id": "22222222-2222-2222-2222-222222222222"}):
        session.add(_submission(coding_session, a, **clash))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_session_and_attempt_deletes_cascade():
    """Declared ON DELETE CASCADE. The test SQLite has no foreign-key enforcement,
    so the actual delete is proven on Postgres (test_mock_interview_coding_rls_postgres)."""
    tables = models.Base.metadata.tables
    for name in ("mock_interview_code_attempts", "mock_interview_code_submissions"):
        for fk in tables[name].foreign_key_constraints:
            assert fk.ondelete == "CASCADE", (name, fk.name)


def test_indexes_present():
    insp = inspect(db.engine)
    names = {i["name"] for i in insp.get_indexes("mock_interview_code_submissions")}
    assert {"ix_mock_interview_code_submissions_tenant_created",
            "ix_mock_interview_code_submissions_attempt_created",
            "ix_mock_interview_code_submissions_status_created"} <= names
