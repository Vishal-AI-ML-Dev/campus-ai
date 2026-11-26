"""AI Mock Interview Phase 2: ORM models for sessions, turns and reports.

Runs on the throwaway SQLite test DB (schema from ``create_all``). SQLite does
enforce CHECK and UNIQUE constraints, so those are tested here; foreign keys,
RLS and the composite tenant key are proven on Postgres in
``test_mock_interview_rls_postgres.py``.
"""

import pytest
from sqlalchemy import ForeignKeyConstraint, inspect
from sqlalchemy.exc import IntegrityError

from app import models
from app.models import (
    MockInterviewReport,
    MockInterviewSession,
    MockInterviewTurn,
    UserRole,
)
from app.models.mock_interview import (
    DIFFICULTY_VALUES,
    INTERVIEW_TYPE_VALUES,
    LANGUAGE_VALUES,
    MODE_VALUES,
)

NEW_TABLES = (
    "mock_interview_sessions",
    "mock_interview_turns",
    "mock_interview_reports",
)


@pytest.fixture
def student(make_tenant, make_user):
    tenant = make_tenant(slug="mi-models")
    return make_user("mi.student@test.dev", role=UserRole.student, tenant=tenant)


def _session(db, student, **overrides) -> MockInterviewSession:
    row = MockInterviewSession(
        tenant_id=student.tenant_id,
        student_id=student.id,
        interview_type=overrides.pop("interview_type", "hr"),
        **overrides,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _report(session_row, **overrides) -> MockInterviewReport:
    values = {
        "tenant_id": session_row.tenant_id,
        "session_id": session_row.id,
        "overall_score": 72.5,
        "rubric_version": "v1",
    }
    values.update(overrides)
    return MockInterviewReport(**values)


def test_models_are_registered_and_tenant_scoped():
    for name in NEW_TABLES:
        table = models.Base.metadata.tables[name]
        assert not table.c.tenant_id.nullable
        assert "tenants" in {fk.column.table.name for fk in table.c.tenant_id.foreign_keys}


@pytest.mark.parametrize("table", ["mock_interview_turns", "mock_interview_reports"])
def test_children_reference_session_through_tenant(table):
    """Turns/reports must point at (session id, tenant id), not just the id."""
    composite = [
        c
        for c in models.Base.metadata.tables[table].constraints
        if isinstance(c, ForeignKeyConstraint)
        and c.referred_table.name == "mock_interview_sessions"
    ]
    assert len(composite) == 1
    fk = composite[0]
    assert [c.name for c in fk.columns] == ["session_id", "tenant_id"]
    assert [e.column.name for e in fk.elements] == ["id", "tenant_id"]
    assert fk.ondelete == "CASCADE"


def test_session_student_is_tied_to_tenant():
    """A session's student must be a user of the same tenant."""
    table = models.Base.metadata.tables["mock_interview_sessions"]
    fks = [
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint) and c.referred_table.name == "users"
    ]
    assert len(fks) == 1
    assert [c.name for c in fks[0].columns] == ["student_id", "tenant_id"]
    assert [e.column.name for e in fks[0].elements] == ["id", "tenant_id"]


def test_allowed_values_match_catalog(client, make_tenant, make_user, token_header, monkeypatch):
    """The DB constraints accept exactly what /catalog offers."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    tenant = make_tenant(slug="mi-catalog")
    make_user("mi.catalog@test.dev", role=UserRole.student, tenant=tenant)
    resp = client.get("/v1/mock-interview/catalog", headers=token_header("mi.catalog@test.dev"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert tuple(body["interview_types"]) == INTERVIEW_TYPE_VALUES
    assert tuple(body["modes"]) == MODE_VALUES
    assert tuple(body["difficulties"]) == DIFFICULTY_VALUES
    assert tuple(body["languages"]) == LANGUAGE_VALUES
    for minutes in body["durations_minutes"]:
        assert 5 <= minutes <= 120


def test_session_defaults_and_json_roundtrip(session, student):
    plan = {"topics": ["intro", "projects"], "max_questions": 6, "note": "नमस्ते"}
    row = _session(session, student, plan_json=plan, job_description="JD text")
    assert row.mode == "text"
    assert row.status == "setup"
    assert row.difficulty == "medium"
    assert row.language == "en"
    assert row.duration_minutes == 20
    assert row.resume_id is None and row.campaign_id is None
    assert row.created_at is not None and row.updated_at is not None
    session.expire_all()
    assert session.get(MockInterviewSession, row.id).plan_json == plan


def test_turns_are_ordered_and_inherit_tenant(session, student):
    row = _session(session, student)
    # Appended out of order; the relationship sorts by seq.
    row.turns.append(MockInterviewTurn(seq=2, question="Second?", is_follow_up=True))
    row.turns.append(MockInterviewTurn(seq=1, question="First?"))
    session.commit()
    session.expire_all()

    turns = session.get(MockInterviewSession, row.id).turns
    assert [t.seq for t in turns] == [1, 2]
    assert all(t.tenant_id == student.tenant_id for t in turns)
    assert turns[0].is_follow_up is False and turns[1].is_follow_up is True
    assert turns[0].answer_text is None and turns[0].answer_source is None


def test_turn_answer_and_eval_roundtrip(session, student):
    row = _session(session, student)
    evaluation = {"score": 7, "evidence": "I led the migration", "category": "ownership"}
    row.turns.append(
        MockInterviewTurn(
            seq=1,
            question="Tell me about a project.",
            answer_text="I led the migration of our app.",
            answer_source="typed",
            turn_eval_json=evaluation,
        )
    )
    session.commit()
    session.expire_all()
    assert session.get(MockInterviewSession, row.id).turns[0].turn_eval_json == evaluation


def test_report_is_one_to_one_with_list_defaults(session, student):
    row = _session(session, student)
    session.add(_report(row, category_scores_json={"communication": 8}))
    session.commit()
    session.expire_all()

    report = session.get(MockInterviewSession, row.id).report
    assert report is not None
    assert report.category_scores_json == {"communication": 8}
    assert report.strengths_json == []
    assert report.weaknesses_json == []
    assert report.recommendations_json == []

    session.add(_report(row, overall_score=10))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_duplicate_turn_seq_rejected(session, student):
    row = _session(session, student)
    session.add_all(
        [
            MockInterviewTurn(tenant_id=row.tenant_id, session_id=row.id, seq=1, question="A"),
            MockInterviewTurn(tenant_id=row.tenant_id, session_id=row.id, seq=1, question="B"),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"interview_type": "gd"},
        {"mode": "phone"},
        {"status": "paused"},
        {"difficulty": "extreme"},
        {"language": "fr"},
        {"duration_minutes": 4},
        {"duration_minutes": 121},
    ],
)
def test_session_check_constraints(session, student, overrides):
    with pytest.raises(IntegrityError):
        _session(session, student, **overrides)
    session.rollback()


@pytest.mark.parametrize(
    "overrides",
    [{"seq": 0}, {"answer_source": "voice"}],
)
def test_turn_check_constraints(session, student, overrides):
    row = _session(session, student)
    values = {"tenant_id": row.tenant_id, "session_id": row.id, "seq": 1, "question": "Q"}
    values.update(overrides)
    session.add(MockInterviewTurn(**values))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("score", [-0.1, 100.1])
def test_report_score_range(session, student, score):
    row = _session(session, student)
    session.add(_report(row, overall_score=score))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.parametrize("score", [0, 100])
def test_report_score_bounds_allowed(session, student, score):
    row = _session(session, student)
    session.add(_report(row, overall_score=score))
    session.commit()


def test_deleting_session_removes_turns_and_report(session, student):
    row = _session(session, student)
    row.turns.append(MockInterviewTurn(seq=1, question="Q"))
    session.add(_report(row))
    session.commit()
    session_id = row.id

    session.delete(session.get(MockInterviewSession, session_id))
    session.commit()
    assert session.query(MockInterviewTurn).filter_by(session_id=session_id).count() == 0
    assert session.query(MockInterviewReport).filter_by(session_id=session_id).count() == 0


def test_indexes_present(session):
    insp = inspect(session.get_bind())
    names = {ix["name"] for ix in insp.get_indexes("mock_interview_sessions")}
    assert {
        "ix_mock_interview_sessions_tenant_id",
        "ix_mock_interview_sessions_student_created",
    } <= names
