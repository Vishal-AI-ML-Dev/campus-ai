"""Phase 8.1: server-side limits hold under races (decision P8-D6).

SQLite serializes writers, so the races are reproduced deterministically: the
request reads the attempt, then another writer changes it before the atomic
conditional UPDATE runs. The real-thread version runs on Postgres
(tests_pg_api/test_mock_interview_coding_pg.py).
"""

# Fixtures are shared by importing them from the sessions module; ruff reads
# the fixture parameters as redefinitions of those imports.
# ruff: noqa: F811

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

import app.models as models
from app.core.rate_limit import limiter
from app.services import mock_interview_coding as coding
from tests.test_mock_interview_coding_sessions import (  # noqa: F401 (fixtures)
    PY,
    create_coding,
    draft,
    flag,
    gate,
    no_engine,
    no_rate_limit,
    started_coding,
    stu,
    submit,
)
from tests.test_mock_interview_sessions import BASE


def _attempt(session, sid, seq=1):
    session.expire_all()
    return session.scalar(
        select(models.MockInterviewCodeAttempt).where(
            models.MockInterviewCodeAttempt.session_id == sid,
            models.MockInterviewCodeAttempt.seq == seq,
        )
    )


def _race(monkeypatch, session, sid, **values):
    """Change the attempt right after the service has read it."""
    real = coding.attempt

    def attempt_then_race(db, s, seq):
        a = real(db, s, seq)
        session.execute(
            update(models.MockInterviewCodeAttempt)
            .where(models.MockInterviewCodeAttempt.id == a.id)
            .values(**values)
        )
        session.commit()
        return a

    monkeypatch.setattr(coding, "attempt", attempt_then_race)


def test_submit_cannot_exceed_three_when_another_request_wins(client, gate, session, monkeypatch):
    _, h = gate
    sid, _ = started_coding(client, h)
    _race(monkeypatch, session, sid, submissions_used=3)
    r = submit(client, h, sid)
    assert r.status_code == 409 and r.json()["detail"] == coding.MSG_GRADED_LIMIT
    session.expire_all()
    assert session.scalar(select(models.MockInterviewCodeSubmission.id)) is None  # rolled back


def test_submit_refused_while_another_submission_is_in_flight(client, gate, session, monkeypatch):
    _, h = gate
    sid, _ = started_coding(client, h)
    _race(monkeypatch, session, sid, in_flight_submission_id=999)
    assert submit(client, h, sid).status_code == 409
    assert _attempt(session, sid).submissions_used == 0


def test_submit_after_problem_closed_concurrently_is_refused(client, gate, session, monkeypatch):
    _, h = gate
    sid, _ = started_coding(client, h)
    _race(monkeypatch, session, sid, status="closed")
    r = submit(client, h, sid)
    assert r.status_code == 409 and r.json()["detail"] == coding.MSG_PROBLEM_CLOSED


def test_draft_from_a_stale_tab_is_refused(client, gate, session, monkeypatch):
    _, h = gate
    sid, _ = started_coding(client, h)
    _race(monkeypatch, session, sid, draft_rev=5)
    r = draft(client, h, sid, rev=0)
    assert r.status_code == 409 and r.json()["detail"] == coding.MSG_DRAFT_STALE


def test_hint_cannot_exceed_the_cap_when_another_request_wins(client, gate, session, monkeypatch):
    _, h = gate
    sid, _ = started_coding(client, h, difficulty="hard")
    _race(monkeypatch, session, sid, hints_used=2)
    r = client.post(f"{BASE}/sessions/{sid}/coding/attempts/1/hints", headers=h)
    assert r.status_code == 409 and r.json()["detail"] == coding.MSG_NO_HINTS
    assert _attempt(session, sid).hints_used == 2


def test_end_wins_over_a_late_submission(client, gate, session):
    """Finish closes every attempt in the same transaction as the session."""
    user, h = gate
    sid, _ = started_coding(client, h)
    s = session.get(models.MockInterviewSession, sid)
    coding.end(session, s, abandon=True)
    with pytest.raises(HTTPException) as exc:
        coding.submit(session, user, session.get(models.MockInterviewSession, sid), 1, "python313", PY, "late-key-1")
    assert exc.value.status_code == 409


def test_runner_failures_and_rejections_never_count(client, gate, session):
    """error / rejected rows (8.6) don't use up the weekly per-problem cap."""
    user, h = gate
    sid, _ = started_coding(client, h, duration_minutes=30, coding={"problem_key": "count-inversions"})
    a = _attempt(session, sid)
    for i in range(coding.WEEKLY_PROBLEM_GRADED_CAP):
        session.add(models.MockInterviewCodeSubmission(
            tenant_id=a.tenant_id, session_id=sid, attempt_id=a.id, kind="graded", language="c11",
            source="x", source_sha256=f"{i:064d}", execution_id=f"00000000-0000-0000-0000-{i:012d}",
            status="error" if i % 2 else "rejected"))
    session.commit()
    assert submit(client, h, sid).status_code == 202


def test_submission_rate_limit_is_three_per_minute_per_problem(client, gate, monkeypatch):
    """slowapi keys a limit by caller AND request path, i.e. per problem here
    (the same as the per-session AI-step limits). Retries with the same key
    use no submission, so only the rate limit can refuse the 4th request."""
    _, h = gate
    sid, _ = started_coding(client, h, difficulty="easy", duration_minutes=45)
    monkeypatch.setattr(limiter, "enabled", True)
    codes = [submit(client, h, sid, key="rate-key-1").status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]
    assert submit(client, h, sid, seq=2, key="rate-key-2").status_code == 202  # another problem
