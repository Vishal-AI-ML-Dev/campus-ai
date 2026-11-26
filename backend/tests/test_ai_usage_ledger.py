"""Phase 7.3: AI usage ledger (SQLite, offline; the worker HTTP layer is faked).

Proves R3/R4/R5 and acceptance criteria 1-3 and 7: every interview AI call -
success, fallback, worker 5xx, timeout, unreachable, invalid response - writes
exactly ONE content-free row keyed by the invocation id sent to the worker;
duplicates are ignored; a failing ledger never changes the student's result;
failed writes go to the RQ queue when one exists.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

import ai_client
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import AIUsageEvent
from app.observability.observability import request_id_ctx
from app.services import ai_circuit, ai_usage

BASE = "/v1/mock-interview"
SENTINEL = "ANSWER-SENTINEL-c0de I optimised the flux capacitor"
RESUME = "RESUME-SENTINEL-7f3a quantum basket weaving"

USAGE = {"provider": "groq", "model": "test-model", "llm_calls": 2, "failed_calls": 0,
         "fallback_used": False, "input_tokens": 120, "output_tokens": 30,
         "tokens_reported": 2, "tokens_unreported": 0, "llm_latency_ms": 840}
TURN = {"action": "opening", "level": "medium", "provider": "fake",
        "question": {"seq": 1, "topic": "Introduction", "is_follow_up": False, "text": "Hi"}}
PLAN = {"plan": {"version": 1, "topics": [{"name": "Introduction", "source": "intro"}],
                 "question_budget": 4, "planner": "llm"}, "provider": "fake"}


class Transport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _breaker_off(monkeypatch):
    # These tests are about the ledger; the breaker has its own tests.
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", False)
    ai_circuit._reset()


@pytest.fixture
def transport(monkeypatch):
    def install(*outcomes):
        t = Transport(*outcomes)
        monkeypatch.setattr(ai_client.httpx, "request", t)
        return t

    return install


@pytest.fixture
def tenant_user(make_user):
    return make_user("ledger@x.edu")


def _rows() -> list[AIUsageEvent]:
    with SessionLocal() as db:
        return list(db.scalars(select(AIUsageEvent).order_by(AIUsageEvent.id)))


def _row_text(row: AIUsageEvent) -> str:
    return json.dumps({c.name: getattr(row, c.name) for c in row.__table__.columns}, default=str)


def _call(user, fn=engine.interview_turn, payload=None, session_id=7):
    with ai_usage.usage_context(user.tenant_id, user.id, subject_type="mock_interview_session",
                                subject_id=session_id):
        return fn(payload or {"transcript": [], "answer": SENTINEL, "resume_text": RESUME})


# --- One row per outcome -------------------------------------------------------
def test_success_row_has_ids_tokens_and_no_usage_in_returned_data(transport, tenant_user):
    t = transport((200, {**TURN, "usage": USAGE}))
    token = request_id_ctx.set("req-ledger-1")
    try:
        data = _call(tenant_user)
    finally:
        request_id_ctx.reset(token)
    assert "usage" not in data and data["action"] == "opening"
    [row] = _rows()
    assert row.event_id == t.calls[0]["headers"]["X-AI-Invocation-ID"]
    assert row.request_id == "req-ledger-1"
    assert (row.tenant_id, row.user_id) == (tenant_user.tenant_id, tenant_user.id)
    assert (row.feature, row.step, row.outcome) == ("mock_interview", "turn", "ok")
    assert (row.subject_type, row.subject_id) == ("mock_interview_session", "7")
    assert (row.provider, row.model, row.llm_calls) == ("groq", "test-model", 2)
    assert (row.input_tokens, row.output_tokens, row.tokens_unreported) == (120, 30, 0)
    assert row.http_status == 200 and row.latency_ms >= 0 and row.llm_latency_ms == 840
    assert row.cost_micros is None and row.price_version is None  # no price table


def test_fallback_usage_is_recorded_as_fallback(transport, tenant_user):
    transport((200, {**TURN, "usage": {**USAGE, "fallback_used": True}}))
    _call(tenant_user)
    assert _rows()[0].outcome == "fallback"


def test_unknown_tokens_stay_null_not_zero(transport, tenant_user):
    transport((200, {**TURN, "usage": {**USAGE, "input_tokens": None, "output_tokens": None,
                                        "tokens_reported": 0, "tokens_unreported": 2}}))
    _call(tenant_user)
    row = _rows()[0]
    assert row.input_tokens is None and row.output_tokens is None and row.tokens_unreported == 2


def test_old_worker_without_usage_block(transport, tenant_user):
    transport((200, TURN))
    _call(tenant_user)
    row = _rows()[0]
    assert row.outcome == "ok" and row.llm_calls is None and row.input_tokens is None


@pytest.mark.parametrize(
    "outcome, expected, status",
    [
        ((502, {"detail": "x"}), "provider_error", 502),
        ((500, {"detail": "x"}), "worker_error", 500),
        ((422, {"detail": "x"}), "worker_error", 422),
        (httpx.ReadTimeout("slow"), "timeout", None),
        (httpx.ConnectError("refused"), "unreachable", None),
        ((200, {"action": "nonsense"}), "worker_error", 200),
    ],
)
def test_every_failure_writes_exactly_one_row(transport, tenant_user, outcome, expected, status):
    t = transport(outcome)
    with pytest.raises(engine.InterviewEngineUnavailable) as err:
        _call(tenant_user)
    rows = _rows()
    assert len(rows) == 1, "exactly one row per invocation, retries included"
    row = rows[0]
    assert (row.outcome, row.http_status) == (expected, status)
    assert row.event_id == err.value.invocation_id == t.calls[0]["headers"]["X-AI-Invocation-ID"]
    assert row.error_class and SENTINEL not in row.error_class


def test_connect_retry_is_still_one_row(transport, tenant_user):
    t = transport(httpx.ConnectError("refused"), (200, TURN))
    _call(tenant_user)
    assert len(t.calls) == 2 and len(_rows()) == 1 and _rows()[0].outcome == "ok"


def test_each_step_and_path_is_named(transport, tenant_user):
    transport((200, PLAN))
    _call(tenant_user, fn=engine.plan_interview)
    transport((200, {"questions": [], "rubric_version": "hr-v1"}))
    _call(tenant_user, fn=engine.generate_report)
    assert [r.step for r in _rows()] == ["plan", "report"]


# --- Idempotency and failure isolation ------------------------------------------
def test_duplicate_event_ids_are_ignored(tenant_user):
    ctx = ai_usage.UsageContext(tenant_id=tenant_user.tenant_id, user_id=tenant_user.id)
    event = ai_usage.build_event(ctx=ctx, event_id="dup-1", step="turn", outcome="ok", latency_ms=5)
    assert ai_usage.record(event) == "ok"
    assert ai_usage.record(event) == "duplicate"
    assert ai_usage.record_event_job(event) is False
    assert len(_rows()) == 1


def test_a_failing_ledger_never_changes_the_students_result(transport, tenant_user, monkeypatch):
    transport((200, TURN))

    def boom(event):
        raise RuntimeError("database down")

    monkeypatch.setattr(ai_usage, "record_event", boom)
    assert _call(tenant_user)["action"] == "opening"


def test_failed_write_without_redis_retries_once_then_counts_lost(tenant_user, monkeypatch):
    calls = []

    def flaky(event):
        calls.append(1)
        raise RuntimeError("locked")

    monkeypatch.setattr(ai_usage, "record_event", flaky)
    ctx = ai_usage.UsageContext(tenant_id=tenant_user.tenant_id, user_id=tenant_user.id)
    event = ai_usage.build_event(ctx=ctx, event_id="lost-1", step="turn", outcome="ok", latency_ms=1)
    assert ai_usage.record(event) == "lost" and len(calls) == 2


def test_failed_write_is_queued_when_redis_is_configured(tenant_user, monkeypatch):
    class FakeQueue:
        def __init__(self):
            self.jobs = []

        def enqueue(self, func, args=(), **options):
            self.jobs.append((func, args, options))

    queue = FakeQueue()
    real = ai_usage.record_event
    monkeypatch.setattr(ai_usage.jobs, "get_queue", lambda: queue)
    monkeypatch.setattr(ai_usage, "record_event", lambda e: (_ for _ in ()).throw(RuntimeError("x")))
    ctx = ai_usage.UsageContext(tenant_id=tenant_user.tenant_id, user_id=tenant_user.id)
    event = ai_usage.build_event(ctx=ctx, event_id="queued-1", step="turn", outcome="ok", latency_ms=1)
    assert ai_usage.record(event) == "queued"
    [(func, args, options)] = queue.jobs
    assert func is ai_usage.record_event_job and args == (event,) and "retry" in options
    # The worker runs the job (twice, e.g. an RQ retry after a timeout): one row.
    monkeypatch.setattr(ai_usage, "record_event", real)
    assert func(*args) is True and func(*args) is False
    assert len(_rows()) == 1


def test_no_context_writes_nothing(transport):
    transport((200, TURN))
    engine.interview_turn({"transcript": []})
    assert _rows() == []


# --- Content-free ----------------------------------------------------------------
def test_no_interview_content_reaches_the_ledger(transport, tenant_user):
    noisy_usage = {**USAGE, "provider": "groq", "model": "m", "note": SENTINEL, "prompt": RESUME}
    transport((200, {**TURN, "usage": noisy_usage}))
    _call(tenant_user)
    transport(httpx.ConnectError(SENTINEL))
    with pytest.raises(engine.InterviewEngineUnavailable):
        _call(tenant_user)
    for row in _rows():
        text = _row_text(row)
        assert SENTINEL.split()[0] not in text and RESUME.split()[0] not in text


# --- API level -------------------------------------------------------------------
@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


def test_start_writes_plan_and_turn_rows_for_the_session(client, make_user, token_header, flag, transport):
    user = make_user("api-ledger@x.edu")
    h = token_header("api-ledger@x.edu")
    created = client.post(f"{BASE}/sessions", headers=h,
                          json={"interview_type": "hr", "target_role": "Engineer", "duration_minutes": 15})
    sid = created.json()["id"]
    transport((200, {**PLAN, "usage": USAGE}), (200, {**TURN, "usage": USAGE}))
    r = client.post(f"{BASE}/sessions/{sid}/start", headers={**h, "X-Request-ID": "api-ledger-req"})
    assert r.status_code == 200, r.text
    assert "usage" not in r.text  # never exposed to the student
    rows = _rows()
    assert [x.step for x in rows] == ["plan", "turn"]
    assert all(x.subject_id == str(sid) and x.user_id == user.id for x in rows)
    assert all(x.request_id == "api-ledger-req" for x in rows)
    assert len({x.event_id for x in rows}) == 2
