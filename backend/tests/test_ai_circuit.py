"""Phase 7.5: AI circuit breaker + throttled background alerts (fake clock, offline)."""

from __future__ import annotations

import logging
import threading
import time

import httpx
import pytest
from sqlalchemy import select

import ai_client
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import AIUsageEvent
from app.observability import ai_alerts, alerts
from app.services import ai_circuit, ai_usage

BASE = "/v1/mock-interview"
TURN = {"action": "opening", "level": "medium", "provider": "fake",
        "question": {"seq": 1, "topic": "Introduction", "is_follow_up": False, "text": "Hi"}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def breaker(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ai_circuit, "_clock", clock)
    monkeypatch.setattr(ai_alerts, "_clock", clock)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", True)
    monkeypatch.setattr(settings, "AI_CIRCUIT_FAILURE_THRESHOLD", 5)
    monkeypatch.setattr(settings, "AI_CIRCUIT_ERROR_RATE", 0.5)
    monkeypatch.setattr(settings, "AI_CIRCUIT_MIN_CALLS", 10)
    monkeypatch.setattr(settings, "AI_CIRCUIT_WINDOW_SECONDS", 120)
    monkeypatch.setattr(settings, "AI_CIRCUIT_OPEN_SECONDS", 60)
    monkeypatch.setattr(settings, "AI_ALERT_THROTTLE_SECONDS", 300)
    ai_circuit._reset()
    ai_alerts._reset()
    yield clock
    ai_circuit._reset()
    ai_alerts._reset()


@pytest.fixture
def sent(monkeypatch):
    """Alerts handed to emit_alert (after the background thread ran)."""
    box: list[tuple[str, dict]] = []
    monkeypatch.setattr(alerts, "emit_alert", lambda name, message, severity="warning", **f: box.append((name, f)))

    def read():
        ai_alerts.flush()
        return list(box)

    return read


def fail(step="turn", n=1):
    for _ in range(n):
        assert ai_circuit.allow(step)
        ai_circuit.record(step, success=False)


# --- State machine -----------------------------------------------------------------
def test_opens_after_consecutive_failures_and_alerts_once(breaker, sent):
    fail(n=4)
    assert ai_circuit.allow("turn")
    ai_circuit.record("turn", success=False)  # 5th consecutive
    assert ai_circuit.snapshot()["turn"]["state"] == "open"
    assert not ai_circuit.allow("turn")
    assert ai_circuit.allow("plan")  # per step
    assert [n for n, _ in sent()] == ["ai_circuit_open"]
    assert sent()[0][1]["step"] == "turn"


def test_success_resets_the_consecutive_count(breaker):
    fail(n=4)
    ai_circuit.allow("turn")
    ai_circuit.record("turn", success=True)
    fail(n=4)
    assert ai_circuit.snapshot()["turn"]["state"] == "closed"


def test_error_rate_rule_opens_without_five_in_a_row(breaker):
    for i in range(10):
        ai_circuit.allow("turn")
        ai_circuit.record("turn", success=(i % 2 == 0))  # 50 % failures, max 1 in a row
    assert ai_circuit.snapshot()["turn"]["state"] == "open"


def test_old_failures_leave_the_window(breaker):
    for i in range(9):
        ai_circuit.allow("turn")
        ai_circuit.record("turn", success=(i % 2 == 0))
    breaker.t += 121  # window passes
    ai_circuit.allow("turn")
    ai_circuit.record("turn", success=False)
    assert ai_circuit.snapshot()["turn"]["state"] == "closed"


def test_half_open_allows_exactly_one_trial_then_closes_on_success(breaker, sent):
    fail(n=5)
    breaker.t += 59
    assert not ai_circuit.allow("turn")
    breaker.t += 1
    assert ai_circuit.allow("turn")          # the single trial
    assert ai_circuit.snapshot()["turn"]["state"] == "half_open"
    assert not ai_circuit.allow("turn")      # others refused while it runs
    ai_circuit.record("turn", success=True)
    assert ai_circuit.snapshot()["turn"]["state"] == "closed"
    assert ai_circuit.allow("turn")
    assert [n for n, _ in sent()] == ["ai_circuit_open", "ai_circuit_closed"]


def test_failed_trial_reopens(breaker):
    fail(n=5)
    breaker.t += 60
    assert ai_circuit.allow("turn")
    ai_circuit.record("turn", success=False)
    assert ai_circuit.snapshot()["turn"]["state"] == "open"
    assert not ai_circuit.allow("turn")
    assert ai_circuit.snapshot()["turn"]["retry_in_seconds"] == 60


def test_a_trial_that_never_reports_is_replaced(breaker):
    fail(n=5)
    breaker.t += 60
    assert ai_circuit.allow("turn")          # trial lost (request crashed)
    breaker.t += 60 + 150 - 1
    assert not ai_circuit.allow("turn")
    breaker.t += 1
    assert ai_circuit.allow("turn")


def test_straggler_results_while_open_are_ignored(breaker):
    fail(n=5)
    ai_circuit.record("turn", success=True)   # a call that started before opening
    assert ai_circuit.snapshot()["turn"]["state"] == "open"


@pytest.mark.parametrize(
    "outcome, status, failure",
    [("unreachable", None, True), ("timeout", None, True), ("provider_error", 502, True),
     ("worker_error", 500, True), ("worker_error", None, True), ("worker_error", 422, False),
     ("ok", 200, False), ("fallback", 200, False)],
)
def test_what_counts_as_failure(outcome, status, failure):
    assert ai_circuit.is_failure(outcome, status) is failure


@pytest.mark.parametrize("flag", ["AI_CIRCUIT_ENABLED", "AI_MOCK_INTERVIEW_ENABLED"])
def test_inert_when_disabled_or_master_flag_off(breaker, monkeypatch, flag):
    monkeypatch.setattr(settings, flag, False)
    fail(n=20)
    assert ai_circuit.allow("turn") and ai_circuit.snapshot() == {}


def test_concurrent_failures_open_once(breaker, sent):
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        for _ in range(5):
            if ai_circuit.allow("turn"):
                ai_circuit.record("turn", success=False)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ai_circuit.snapshot()["turn"]["state"] == "open"
    assert [n for n, _ in sent()] == ["ai_circuit_open"]


# --- Through the real worker client --------------------------------------------------
@pytest.fixture
def transport(monkeypatch):
    calls = []

    def install(outcome):
        def fake(method, url, *, headers=None, json=None, timeout=None):
            calls.append(url)
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome[0], json=outcome[1], request=httpx.Request(method, url))

        monkeypatch.setattr(ai_client.httpx, "request", fake)
        return calls

    return install


def test_open_breaker_skips_the_worker_and_writes_a_circuit_open_row(breaker, transport, make_user):
    user = make_user("circuit@x.edu")
    calls = transport((502, {"detail": "x"}))
    with ai_usage.usage_context(user.tenant_id, user.id):
        for _ in range(5):
            with pytest.raises(engine.InterviewEngineUnavailable):
                engine.interview_turn({"transcript": []})
        before = len(calls)
        started = time.perf_counter()
        with pytest.raises(engine.InterviewEngineUnavailable) as err:
            engine.interview_turn({"transcript": []})
        assert time.perf_counter() - started < 0.1
    assert err.value.circuit_open is True and len(calls) == before  # no worker call
    with SessionLocal() as db:
        outcomes = [r.outcome for r in db.scalars(select(AIUsageEvent).order_by(AIUsageEvent.id))]
    assert outcomes == ["provider_error"] * 5 + ["circuit_open"]


def test_student_gets_the_usual_503_quickly_when_open(client, make_user, token_header, breaker, transport, monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")
    make_user("cb-student@x.edu")
    h = token_header("cb-student@x.edu")
    sid = client.post(f"{BASE}/sessions", headers=h,
                      json={"interview_type": "hr", "target_role": "Engineer", "duration_minutes": 15}).json()["id"]
    transport(httpx.ConnectError("refused"))
    for _ in range(5):
        ai_circuit.allow("plan")
        ai_circuit.record("plan", success=False)
    started = time.perf_counter()
    r = client.post(f"{BASE}/sessions/{sid}/start", headers=h)
    elapsed = time.perf_counter() - started
    assert r.status_code == 503 and "Nothing you typed was lost" in r.text
    assert elapsed < 2.0  # no 60-120 s wait (request overhead only)


# --- Alerts ---------------------------------------------------------------------------
def test_alerts_are_throttled_per_name_and_key(breaker, sent):
    assert ai_alerts.alert("x", "m", key="a")
    assert not ai_alerts.alert("x", "m", key="a")
    assert ai_alerts.alert("x", "m", key="b")
    breaker.t += 300
    assert ai_alerts.alert("x", "m", key="a")
    assert len(sent()) == 3


def test_slow_or_failing_alert_delivery_never_blocks_or_raises(breaker, monkeypatch):
    def slow_and_broken(*a, **k):
        time.sleep(0.5)
        raise RuntimeError("webhook down")

    monkeypatch.setattr(alerts, "emit_alert", slow_and_broken)
    started = time.perf_counter()
    assert ai_alerts.alert("slow", "m") is True
    fail(n=5)  # opening also alerts
    assert time.perf_counter() - started < 0.2
    ai_alerts.flush()  # the background thread swallowed both failures


def test_webhook_failure_log_never_contains_the_url(monkeypatch, caplog):
    secret_url = "https://hooks.example.invalid/services/T000/B000/SECRET-TOKEN-123"
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", secret_url)

    def boom(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        alerts.emit_alert("t", "m")
    from app.observability.observability import _JsonFormatter

    lines = [_JsonFormatter().format(r) for r in caplog.records]  # what is shipped
    failed = [ln for ln in lines if "alert webhook failed" in ln]
    assert failed and '"error": "ConnectError"' in failed[0]
    assert all("SECRET-TOKEN-123" not in ln and "hooks.example.invalid" not in ln for ln in lines)


# --- Release fix: read-only precheck for routes (mentor) ---------------------------


def test_would_refuse_is_read_only_and_matches_allow(breaker):
    assert ai_circuit.would_refuse("mentor_chat") is False
    assert "mentor_chat" not in ai_circuit.snapshot()  # no breaker created
    fail("mentor_chat", 5)
    assert ai_circuit.would_refuse("mentor_chat") is True
    breaker.t += 61  # open period over: allow() would start the half-open trial
    assert ai_circuit.would_refuse("mentor_chat") is False
    assert ai_circuit.snapshot()["mentor_chat"]["state"] == "open"  # not moved to half_open
    assert ai_circuit.allow("mentor_chat") is True  # the trial is still available
    assert ai_circuit.would_refuse("mentor_chat") is True  # trial in flight
    ai_circuit.record("mentor_chat", True)
    assert ai_circuit.would_refuse("mentor_chat") is False


def test_would_refuse_inert_when_inactive(breaker, monkeypatch):
    fail("mentor_chat", 5)
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", False)
    assert ai_circuit.would_refuse("mentor_chat") is False
