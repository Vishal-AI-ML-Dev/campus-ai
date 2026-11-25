"""Phase 10.3 - AI governance on every AI path (owner decisions D4, OQ-2).

Offline (SQLite): the AI worker is replaced at the HTTP layer (``httpx.request``
/ ``httpx.stream``), so these tests exercise the real ``ai_client`` retry
policy, the governance checks, the usage ledger and the platform counter.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

import ai_client
import app.core.db as db
import app.models as models
from app.core.config import ai_governance_issues, settings
from app.services import ai_circuit, ai_governance, ai_usage
from app.services import mock_interview_ai


@pytest.fixture(autouse=True)
def _reset_governance():
    ai_governance._reset()
    ai_circuit._reset()
    yield
    ai_governance._reset()
    ai_circuit._reset()


class FakeWorker:
    """Scripted AI worker: each call pops the next reply (status, body) or exception."""

    def __init__(self, monkeypatch, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []
        monkeypatch.setattr(httpx, "request", self._request)

    def _request(self, method, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.calls.append((method, url))
        reply = self.replies.pop(0) if self.replies else (200, {})
        request = httpx.Request(method, url)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body, request=request)


def _on(monkeypatch, **overrides):
    monkeypatch.setattr(settings, "AI_GOVERNANCE_ENABLED", True)
    monkeypatch.setattr(settings, "AI_CONCURRENCY_WAIT_SECONDS", 0.0)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


def _ledger():
    s = db.SessionLocal()
    try:
        return s.query(models.AIUsageEvent).order_by(models.AIUsageEvent.id).all()
    finally:
        s.close()


def _messages():
    s = db.SessionLocal()
    try:
        return s.query(models.MentorMessage).count()
    finally:
        s.close()


def _ask(client, headers, **extra):
    return client.post("/mentor/chat", json={"question": "What next?"},
                       headers={**headers, **extra})


MENTOR_OK = (200, {"answer": "Learn SQL.", "provider": "test"})


# --------------------------------------------------------------------------- flag off


def test_flag_off_keeps_legacy_behaviour(client, make_user, token_header, monkeypatch):
    """Governance off: legacy 3-attempt retry on 5xx, no ledger rows (unchanged)."""
    worker = FakeWorker(monkeypatch, (500, {}), (500, {}), MENTOR_OK)
    monkeypatch.setattr(ai_client._worker_request.retry, "sleep", lambda _s: None)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 200, resp.text
    assert len(worker.calls) == 3
    assert _ledger() == []


def test_kill_switch_works_with_governance_off(client, make_user, token_header, monkeypatch):
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    monkeypatch.setattr(settings, "AI_KILL_SWITCH", True)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == {"type": "ai_unavailable", "status": 503, "reason": "kill_switch"}
    assert "Everything else keeps working" in body["detail"]
    assert worker.calls == []
    assert _messages() == 0  # refused before anything was written


def test_disabled_feature_only_blocks_that_feature(client, make_user, token_header, monkeypatch):
    FakeWorker(monkeypatch, MENTOR_OK)
    monkeypatch.setattr(settings, "AI_DISABLED_FEATURES", "ats, resume")
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    ats = client.post("/resume/ats-score", json={"resume_text": "x" * 60, "job_description": "y" * 60},
                      headers=h)
    assert ats.status_code == 503 and ats.json()["error"]["reason"] == "feature_disabled"
    assert _ask(client, h).status_code == 200
    # Non-AI features keep working.
    assert client.get("/mentor/conversations", headers=h).status_code == 200


# --------------------------------------------------------------------------- governed calls


def test_governed_success_writes_ledger_and_platform_counter(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, (200, {"answer": "ok", "provider": "test",
                                            "usage": {"input_tokens": 30, "output_tokens": 12}}))
    user = make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 200 and resp.json()["answer"] == "ok"
    assert len(worker.calls) == 1
    (row,) = _ledger()
    assert (row.feature, row.step, row.outcome, row.user_id) == ("mentor", "mentor_chat", "ok", user.id)
    assert (row.input_tokens, row.output_tokens) == (30, 12)
    assert ai_governance.platform_usage_today() == {"requests": 1, "tokens": 42}


@pytest.mark.parametrize("failure", [
    (500, {}),
    (502, {"detail": "LLM call failed"}),
    httpx.ReadTimeout("slow"),
])
def test_failed_call_is_not_repeated_and_still_counts(client, make_user, token_header, monkeypatch, failure):
    """A 5xx / read timeout may already have spent an LLM call: never repeated."""
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, failure, MENTOR_OK)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 502
    assert len(worker.calls) == 1
    (row,) = _ledger()
    assert row.outcome in ("worker_error", "provider_error", "timeout")
    assert ai_governance.platform_usage_today()["requests"] == 1  # cannot bypass the cap


def test_unreachable_worker_is_retried_once(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(ai_client._worker_request.retry, "sleep", lambda _s: None)
    worker = FakeWorker(monkeypatch, httpx.ConnectError("down"), MENTOR_OK)
    make_user("s@x.edu")
    assert _ask(client, token_header("s@x.edu")).status_code == 200
    assert len(worker.calls) == 2


def test_provider_quota_starts_cooldown(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    alerts = []
    monkeypatch.setattr(ai_governance.ai_alerts, "alert", lambda name, *a, **k: alerts.append(name))
    worker = FakeWorker(monkeypatch, (429, {"detail": "AI provider quota exhausted"}), MENTOR_OK)
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    first = _ask(client, h)
    assert first.status_code == 503 and first.json()["error"]["reason"] == "provider_quota"
    assert "ai_quota_exhausted" in alerts
    second = _ask(client, h)
    assert second.status_code == 503 and second.json()["error"]["reason"] == "provider_quota"
    assert int(second.headers["Retry-After"]) > 0
    assert len(worker.calls) == 1  # the cooldown made no provider call
    assert ai_governance.cooldown_remaining() > 0
    # Non-AI features keep working during the cooldown.
    assert client.get("/mentor/conversations", headers=h).status_code == 200


def test_user_daily_cap(client, make_user, token_header, monkeypatch):
    _on(monkeypatch, AI_USER_DAILY_REQUEST_MAX="2")
    worker = FakeWorker(monkeypatch, MENTOR_OK, MENTOR_OK, MENTOR_OK)
    make_user("a@x.edu")
    make_user("b@x.edu")
    ha, hb = token_header("a@x.edu"), token_header("b@x.edu")
    assert _ask(client, ha).status_code == 200
    assert _ask(client, ha).status_code == 200
    third = _ask(client, ha)
    assert third.status_code == 429 and third.json()["error"]["reason"] == "user_requests"
    assert "Retry-After" in third.headers
    assert _ask(client, hb).status_code == 200  # another student is unaffected
    assert len(worker.calls) == 3
    assert [r.outcome for r in _ledger()].count("quota_blocked") == 0  # precheck refusal: no call


def test_tenant_daily_cap_is_per_institute(client, make_user, make_tenant, token_header, monkeypatch):
    _on(monkeypatch, AI_TENANT_DAILY_REQUEST_MAX="2")
    FakeWorker(monkeypatch, MENTOR_OK, MENTOR_OK, MENTOR_OK)
    other = make_tenant("other")
    make_user("a@x.edu")
    make_user("b@x.edu")
    make_user("c@o.edu", tenant=other)
    assert _ask(client, token_header("a@x.edu")).status_code == 200
    assert _ask(client, token_header("b@x.edu")).status_code == 200
    blocked = _ask(client, token_header("a@x.edu"))
    assert blocked.status_code == 429 and blocked.json()["error"]["reason"] == "tenant_requests"
    assert _ask(client, token_header("c@o.edu")).status_code == 200


def test_platform_request_cap_across_institutes(client, make_user, make_tenant, token_header, monkeypatch):
    _on(monkeypatch, AI_PLATFORM_DAILY_REQUEST_MAX="2")
    worker = FakeWorker(monkeypatch, MENTOR_OK, MENTOR_OK, MENTOR_OK)
    other = make_tenant("other")
    make_user("a@x.edu")
    make_user("c@o.edu", tenant=other)
    assert _ask(client, token_header("a@x.edu")).status_code == 200
    assert _ask(client, token_header("c@o.edu")).status_code == 200
    blocked = _ask(client, token_header("c@o.edu"))
    assert blocked.status_code == 503 and blocked.json()["error"]["reason"] == "platform_requests"
    assert len(worker.calls) == 2
    assert _ledger()[-1].outcome == "quota_blocked"


def test_platform_token_cap(client, make_user, token_header, monkeypatch):
    _on(monkeypatch, AI_PLATFORM_DAILY_TOKEN_MAX="40")
    worker = FakeWorker(monkeypatch, (200, {"answer": "a", "provider": "t",
                                            "usage": {"input_tokens": 30, "output_tokens": 20}}), MENTOR_OK)
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    assert _ask(client, h).status_code == 200
    blocked = _ask(client, h)
    assert blocked.status_code == 503 and blocked.json()["error"]["reason"] == "platform_tokens"
    assert len(worker.calls) == 1


def test_circuit_refusal_does_not_consume_platform_budget(client, make_user, token_header, monkeypatch):
    """Found in the 10.9 outage drill: breaker refusals must refund the reservation."""
    _on(monkeypatch, AI_CIRCUIT_FAILURE_THRESHOLD=2, AI_CIRCUIT_MIN_CALLS=100)
    worker = FakeWorker(monkeypatch, (502, {}), (502, {}))
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    assert [_ask(client, h).status_code for _ in range(2)] == [502, 502]
    assert ai_governance.platform_usage_today()["requests"] == 2
    refused = _ask(client, h)
    assert refused.status_code == 503 and refused.json()["error"]["reason"] == "circuit_open"
    assert len(worker.calls) == 2
    assert ai_governance.platform_usage_today()["requests"] == 2  # refunded


def _open_mentor_breaker(monkeypatch, step):
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", True)
    monkeypatch.setattr(settings, "AI_CIRCUIT_FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(settings, "AI_CIRCUIT_OPEN_SECONDS", 60)
    assert ai_circuit.allow(step)
    ai_circuit.record(step, success=False)
    assert ai_circuit.snapshot()[step]["state"] == "open"


def test_open_breaker_refuses_mentor_chat_before_saving(client, make_user, token_header, monkeypatch):
    """Release fix: an open breaker no longer leaves a saved, unanswered question."""
    _on(monkeypatch)
    _open_mentor_breaker(monkeypatch, "mentor_chat")
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "circuit_open"
    assert resp.headers["Retry-After"] == "60"
    assert _messages() == 0
    assert worker.calls == []
    assert ai_governance.platform_usage_today()["requests"] == 0
    assert ai_circuit.snapshot()["mentor_chat"]["state"] == "open"  # precheck changed nothing


def test_open_breaker_refuses_mentor_stream_before_saving(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    _open_mentor_breaker(monkeypatch, "mentor_stream")
    calls = []
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: calls.append(a) or _fake_stream([]))
    make_user("s@x.edu")
    resp = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=token_header("s@x.edu"))
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "circuit_open"
    assert _messages() == 0
    assert calls == []


def test_precheck_does_not_consume_the_half_open_trial(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    _open_mentor_breaker(monkeypatch, "mentor_chat")
    breaker = ai_circuit._breakers["mentor_chat"]
    breaker.opened_at -= 61  # open period over: next allow() starts the trial
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 200  # the route's own trial call went through
    assert len(worker.calls) == 1 and _messages() == 2
    assert ai_circuit.snapshot()["mentor_chat"]["state"] == "closed"


def test_closed_breaker_keeps_mentor_behaviour(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(settings, "AI_CIRCUIT_ENABLED", True)
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 200 and resp.json()["answer"] == "Learn SQL."
    assert len(worker.calls) == 1 and _messages() == 2


def test_breaker_opening_after_precheck_is_still_refused_and_refunded(
        client, make_user, token_header, monkeypatch):
    """Documented race: precheck passes, then ``allow`` refuses (question already saved)."""
    _on(monkeypatch)
    _open_mentor_breaker(monkeypatch, "mentor_chat")
    import app.routers.mentor as mentor_router
    monkeypatch.setattr(mentor_router.ai_circuit, "would_refuse", lambda step: False)
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "circuit_open"
    assert worker.calls == []
    assert ai_governance.platform_usage_today()["requests"] == 0  # refunded
    assert _messages() == 1  # known limitation: the saved question stays unanswered


def test_counter_failure_fails_closed(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, MENTOR_OK)

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ai_governance, "_ensure_platform_row", _boom)
    make_user("s@x.edu")
    resp = _ask(client, token_header("s@x.edu"))
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "platform_unavailable"
    assert worker.calls == []


def test_concurrency_cap_refuses_when_busy(client, make_user, token_header, monkeypatch):
    _on(monkeypatch, AI_MAX_CONCURRENT_CALLS=1)
    worker = FakeWorker(monkeypatch, MENTOR_OK)
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    slot = ai_governance._semaphore()
    assert slot.acquire(timeout=0)
    try:
        resp = _ask(client, h)
    finally:
        slot.release()
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "busy"
    assert worker.calls == []


def test_one_inflight_call_per_user(monkeypatch, make_user):
    _on(monkeypatch)
    user = make_user("s@x.edu")
    with ai_governance.admit("mentor", tenant_id=user.tenant_id, user_id=user.id):
        with pytest.raises(ai_governance.AIRefused) as info:
            with ai_governance.admit("ats", tenant_id=user.tenant_id, user_id=user.id):
                pass
    assert info.value.reason == "user_busy" and info.value.status_code == 429
    # Released afterwards.
    with ai_governance.admit("ats", tenant_id=user.tenant_id, user_id=user.id):
        pass


# --------------------------------------------------------------------------- idempotency


def test_idempotency_key_replays_without_second_call(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, MENTOR_OK, MENTOR_OK)
    make_user("s@x.edu")
    h = {**token_header("s@x.edu"), "Idempotency-Key": "chat-0001-abcdef"}
    first = _ask(client, h)
    second = _ask(client, h)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(worker.calls) == 1
    assert _messages() == 2  # one user + one assistant message, not four


def test_idempotency_key_is_validated_and_released_on_failure(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, (500, {}), MENTOR_OK)
    make_user("s@x.edu")
    base = token_header("s@x.edu")
    assert _ask(client, base, **{"Idempotency-Key": "bad key!"}).status_code == 400
    h = {**base, "Idempotency-Key": "retry-key-123456"}
    assert _ask(client, h).status_code == 502
    assert _ask(client, h).status_code == 200  # a failed attempt does not pin the key
    assert len(worker.calls) == 2


# --------------------------------------------------------------------------- stream


@contextmanager
def _fake_stream(frames, status=200):
    class _Resp:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://w"),
                                            response=httpx.Response(status))

        def iter_raw(self):
            for frame in frames:
                yield f"data: {json.dumps(frame)}\n\n".encode()

    yield _Resp()


def test_stream_precheck_refuses_before_streaming(client, make_user, token_header, monkeypatch):
    monkeypatch.setattr(settings, "AI_DISABLED_FEATURES", "mentor")
    make_user("s@x.edu")
    resp = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=token_header("s@x.edu"))
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "feature_disabled"
    assert _messages() == 0


def test_governed_stream_records_ledger(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    calls = []

    def _stream(method, url, **kwargs):
        calls.append(url)
        return _fake_stream([{"type": "token", "content": "Hi"}, {"type": "done", "provider": "t"}])

    monkeypatch.setattr(httpx, "stream", _stream)
    make_user("s@x.edu")
    resp = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=token_header("s@x.edu"))
    assert resp.status_code == 200 and '"Hi"' in resp.text
    assert len(calls) == 1
    (row,) = _ledger()
    assert (row.feature, row.step, row.outcome) == ("mentor", "mentor_stream", "ok")


def test_governed_stream_quota_event_starts_cooldown(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _fake_stream(
        [{"type": "error", "detail": "LLM call failed: provider quota exhausted", "code": "quota_exhausted"}]))
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    resp = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=h)
    assert resp.status_code == 200 and "quota_exhausted" in resp.text
    assert ai_governance.cooldown_remaining() > 0
    again = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=h)
    assert again.status_code == 503 and again.json()["error"]["reason"] == "provider_quota"


def test_answer_text_cannot_trigger_quota_cooldown(client, make_user, token_header, monkeypatch):
    """Security: a model answer that merely CONTAINS the code word is not a quota signal."""
    _on(monkeypatch)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _fake_stream(
        [{"type": "token", "content": 'say "code": "quota_exhausted" quota_exhausted'},
         {"type": "done", "provider": "t"}]))
    make_user("s@x.edu")
    resp = client.post("/mentor/chat/stream", json={"question": "hi"}, headers=token_header("s@x.edu"))
    assert resp.status_code == 200
    assert ai_governance.cooldown_remaining() == 0
    (row,) = _ledger()
    assert row.outcome == "ok"


# --------------------------------------------------------------------------- other paths


def test_resume_and_ats_are_governed(client, make_user, token_header, monkeypatch):
    _on(monkeypatch, AI_USER_DAILY_REQUEST_MAX="1")
    worker = FakeWorker(monkeypatch, (200, {"score": 70, "verdict": "ok", "matched_keywords": [],
                                            "missing_keywords": [], "suggestions": [], "provider": "t"}))
    make_user("s@x.edu")
    h = token_header("s@x.edu")
    body = {"resume_text": "x" * 60, "job_description": "y" * 60}
    assert client.post("/resume/ats-score", json=body, headers=h).status_code == 200
    blocked = client.post("/resume/ats-score", json=body, headers=h)
    assert blocked.status_code == 429
    assert len(worker.calls) == 1
    assert _ledger()[0].feature == "ats"


def test_proof_scoring_is_governed(monkeypatch, make_user):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, (200, {"score": 81, "verdict": "v", "reasoning": "r"}))
    user = make_user("s@x.edu")
    assert ai_client._request_score("skill", "SQL", None, None,
                                    tenant_id=user.tenant_id, user_id=user.id) == 81
    assert _ledger()[0].feature == "proof_scoring"
    monkeypatch.setattr(settings, "AI_KILL_SWITCH", True)
    assert ai_client._request_score("skill", "SQL", None, None,
                                    tenant_id=user.tenant_id, user_id=user.id) is None
    assert len(worker.calls) == 1
    # Without an institute context the governed path fails closed (no call).
    monkeypatch.setattr(settings, "AI_KILL_SWITCH", False)
    assert ai_client._request_score("skill", "SQL", None, None) is None
    assert len(worker.calls) == 1


def test_interview_calls_respect_kill_switch_and_platform_cap(monkeypatch, make_user):
    user = make_user("s@x.edu")
    worker = FakeWorker(monkeypatch, (200, {"plan": {}, "provider": "t"}), (200, {"plan": {}, "provider": "t"}))
    monkeypatch.setattr(settings, "AI_DISABLED_FEATURES", "mock_interview")
    with ai_usage.usage_context(user.tenant_id, user.id):
        with pytest.raises(mock_interview_ai.InterviewEngineUnavailable) as info:
            mock_interview_ai.plan_interview({})
    assert info.value.refused == "feature_disabled" and info.value.circuit_open
    assert worker.calls == []
    assert _ledger()[0].outcome == "quota_blocked"

    monkeypatch.setattr(settings, "AI_DISABLED_FEATURES", "")
    _on(monkeypatch, AI_PLATFORM_DAILY_REQUEST_MAX="1")
    with ai_usage.usage_context(user.tenant_id, user.id):
        assert mock_interview_ai.plan_interview({})["plan"] == {}
        with pytest.raises(mock_interview_ai.InterviewEngineUnavailable) as info:
            mock_interview_ai.plan_interview({})
    assert info.value.refused == "platform_requests"
    assert len(worker.calls) == 1


def test_interview_quota_signal_from_usage_block(monkeypatch, make_user):
    _on(monkeypatch)
    user = make_user("s@x.edu")
    FakeWorker(monkeypatch, (200, {"plan": {}, "provider": "t", "usage": {"quota_exhausted": True}}))
    with ai_usage.usage_context(user.tenant_id, user.id):
        mock_interview_ai.plan_interview({})
    assert ai_governance.cooldown_remaining() > 0


def test_interview_outright_quota_failure_starts_cooldown(monkeypatch, make_user):
    """Review #1: the worker's explicit 429 for a FAILED step arms the shared pause."""
    _on(monkeypatch)
    user = make_user("s@x.edu")
    worker = FakeWorker(monkeypatch, (429, {"detail": "AI provider quota exhausted"}),
                        (200, {"action": "end", "provider": "t"}))
    with ai_usage.usage_context(user.tenant_id, user.id):
        with pytest.raises(mock_interview_ai.InterviewEngineUnavailable):
            mock_interview_ai.interview_turn({})
        assert ai_governance.cooldown_remaining() > 0
        with pytest.raises(mock_interview_ai.InterviewEngineUnavailable) as info:
            mock_interview_ai.interview_turn({})
    assert info.value.refused == "provider_quota"
    assert len(worker.calls) == 1  # the pause made no second provider call
    first = _ledger()[0]
    assert (first.outcome, first.http_status, first.error_class) == (
        "provider_error", 429, "ProviderQuotaExhausted")


def test_student_text_never_triggers_interview_quota_pause(monkeypatch, make_user):
    """Only the structured signal counts - never answer/question text or error bodies."""
    _on(monkeypatch)
    user = make_user("s@x.edu")
    words = 'quota_exhausted "quota_exhausted": true RateLimitError 429 RESOURCE_EXHAUSTED'
    FakeWorker(monkeypatch,
               (200, {"action": "follow_up", "question": {"text": words}, "provider": "t"}),
               (502, {"detail": words}))
    with ai_usage.usage_context(user.tenant_id, user.id):
        mock_interview_ai.interview_turn({})
        with pytest.raises(mock_interview_ai.InterviewEngineUnavailable):
            mock_interview_ai.interview_turn({})
    assert ai_governance.cooldown_remaining() == 0


def test_idempotency_key_with_different_body_is_409(client, make_user, token_header, monkeypatch):
    """Review #2: a key is bound to a fingerprint of the validated body."""
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, MENTOR_OK, MENTOR_OK)
    make_user("s@x.edu")
    h = {**token_header("s@x.edu"), "Idempotency-Key": "fp-key-0000001"}
    first = client.post("/mentor/chat", json={"question": "Question A?"}, headers=h)
    same = client.post("/mentor/chat", json={"question": "Question A?"}, headers=h)
    other = client.post("/mentor/chat", json={"question": "Question B?"}, headers=h)
    assert first.status_code == same.status_code == 200 and first.json() == same.json()
    assert other.status_code == 409 and "different request" in other.json()["detail"]
    assert len(worker.calls) == 1
    assert _messages() == 2


def test_idempotency_keys_are_isolated_per_user(client, make_user, token_header, monkeypatch):
    _on(monkeypatch)
    worker = FakeWorker(monkeypatch, (200, {"answer": "for A", "provider": "t"}),
                        (200, {"answer": "for B", "provider": "t"}))
    make_user("a@x.edu")
    make_user("b@x.edu")
    key = {"Idempotency-Key": "shared-key-0001"}
    ra = client.post("/mentor/chat", json={"question": "Same?"}, headers={**token_header("a@x.edu"), **key})
    rb = client.post("/mentor/chat", json={"question": "Same?"}, headers={**token_header("b@x.edu"), **key})
    assert ra.json()["answer"] == "for A" and rb.json()["answer"] == "for B"
    assert len(worker.calls) == 2


def test_request_fingerprint_is_canonical_and_stores_no_body():
    from app.routers.resume import AtsScoreRequest

    a = AtsScoreRequest(resume_text="x" * 60, job_description="y" * 60)
    b = AtsScoreRequest(job_description="y" * 60, resume_text="x" * 60)
    assert ai_governance.request_fingerprint(a) == ai_governance.request_fingerprint(b)
    assert ai_governance.request_fingerprint({"k": 1, "j": 2}) == ai_governance.request_fingerprint({"j": 2, "k": 1})
    fp = ai_governance.request_fingerprint(a)
    assert len(fp) == 64 and "xxxx" not in fp


# --------------------------------------------------------------------------- production gate


def test_production_requires_governance(monkeypatch):
    s = settings.model_copy()
    s.AI_GOVERNANCE_ENABLED = False
    assert any("AI_GOVERNANCE_ENABLED" in i for i in ai_governance_issues(s))
    s.AI_GOVERNANCE_ENABLED = True
    s.AI_PLATFORM_DAILY_REQUEST_MAX = ""
    s.AI_PLATFORM_DAILY_TOKEN_MAX = "0"
    issues = ai_governance_issues(s)
    assert any("AI_PLATFORM_DAILY_REQUEST_MAX" in i for i in issues)
    assert any("AI_PLATFORM_DAILY_TOKEN_MAX" in i for i in issues)
    s.AI_PLATFORM_DAILY_REQUEST_MAX = "500"
    s.AI_PLATFORM_DAILY_TOKEN_MAX = "400000"
    s.AI_USER_DAILY_REQUEST_MAX = "abc"
    s.AI_DISABLED_FEATURES = "mentr"
    issues = ai_governance_issues(s)
    assert any("AI_USER_DAILY_REQUEST_MAX" in i for i in issues)
    assert any("AI_DISABLED_FEATURES" in i for i in issues)
    s.AI_USER_DAILY_REQUEST_MAX = "20"
    s.AI_DISABLED_FEATURES = "mentor"
    assert ai_governance_issues(s) == []


def test_refusal_messages_are_content_free():
    for reason in ("kill_switch", "provider_quota", "user_requests", "busy", "platform_requests"):
        refusal = ai_governance.AIRefused("mentor", reason)
        assert "@" not in refusal.message and len(refusal.message) < 160
