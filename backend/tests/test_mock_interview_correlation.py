"""Phase 7.1: correlation ids on backend -> AI worker calls (SQLite, offline).

The HTTP transport (``httpx.request`` as used by ``ai_client._worker_request``)
is replaced by a recorder, so these tests see the exact headers the worker
would receive. Proves: an invocation id is created before the call, the same
ids are sent on a retry, every call gets a new invocation id, the API
request's X-Request-ID reaches the worker, malformed ids are replaced, the
worker token can't be overridden, ids carry no content, and the JSON logs
carry the ids.
"""

from __future__ import annotations

import io
import json
import logging
import re

import httpx
import pytest

import ai_client
import app.services.mock_interview_ai as engine
from app.core.config import settings
from app.observability.observability import (
    _JsonFormatter,
    invocation_id_ctx,
    request_id_ctx,
)

BASE = "/v1/mock-interview"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
ANSWER_SENTINEL = "ANSWER-SENTINEL-c0de I optimised the flux capacitor"


class Transport:
    """Scripted stand-in for httpx.request; records every attempt."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "json": json})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body, request=httpx.Request(method, url))


@pytest.fixture
def transport(monkeypatch):
    def install(*outcomes):
        t = Transport(*outcomes)
        monkeypatch.setattr(ai_client.httpx, "request", t)
        return t

    return install


@pytest.fixture
def json_logs():
    """Capture the mock-interview logger through the real JSON formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    log = logging.getLogger("campus_ai.mock_interview")
    log.addHandler(handler)
    try:
        yield lambda: [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]
    finally:
        log.removeHandler(handler)


TURN_OK = (200, {"action": "opening", "level": "medium", "provider": "fake",
                 "question": {"seq": 1, "topic": "Introduction", "is_follow_up": False, "text": "Hi"}})
PLAN_OK = (200, {"plan": {"version": 1, "topics": [{"name": "Introduction", "source": "intro"}],
                          "question_budget": 4, "planner": "llm"}, "provider": "fake"})


# --- Service level ------------------------------------------------------------
def test_invocation_id_is_created_before_the_call_and_kept_on_retry(transport):
    t = transport(httpx.ConnectError("refused"), TURN_OK)
    token = request_id_ctx.set("req-abc123")
    try:
        engine.interview_turn({"transcript": [], "answer": ANSWER_SENTINEL})
    finally:
        request_id_ctx.reset(token)
    assert len(t.calls) == 2  # one connect-error retry
    first, second = (c["headers"] for c in t.calls)
    assert HEX32.match(first["X-AI-Invocation-ID"])
    assert first["X-AI-Invocation-ID"] == second["X-AI-Invocation-ID"]
    assert first["X-Request-ID"] == second["X-Request-ID"] == "req-abc123"
    # The payload is sent unchanged (no correlation fields added to the body).
    assert t.calls[0]["json"] == {"transcript": [], "answer": ANSWER_SENTINEL}
    assert invocation_id_ctx.get() == "-"  # reset after the call


def test_each_call_gets_a_new_invocation_id(transport):
    t = transport(TURN_OK)
    engine.interview_turn({"transcript": []})
    engine.interview_turn({"transcript": []})
    ids = [c["headers"]["X-AI-Invocation-ID"] for c in t.calls]
    assert len(ids) == 2 and ids[0] != ids[1]


def test_outside_a_request_the_invocation_id_doubles_as_request_id(transport):
    t = transport(TURN_OK)
    engine.interview_turn({"transcript": []})
    h = t.calls[0]["headers"]
    assert h["X-Request-ID"] == h["X-AI-Invocation-ID"]


def test_malformed_request_id_in_context_is_not_forwarded(transport):
    t = transport(TURN_OK)
    token = request_id_ctx.set("bad id with spaces " + ANSWER_SENTINEL)
    try:
        engine.interview_turn({"transcript": []})
    finally:
        request_id_ctx.reset(token)
    h = t.calls[0]["headers"]
    assert h["X-Request-ID"] == h["X-AI-Invocation-ID"]
    assert ANSWER_SENTINEL not in json.dumps(h)


def test_failure_keeps_ids_logs_them_and_exposes_them_on_the_exception(transport, json_logs):
    t = transport(httpx.ConnectError("refused"))
    token = request_id_ctx.set("req-fail-1")
    try:
        with pytest.raises(engine.InterviewEngineUnavailable) as err:
            engine.interview_turn({"transcript": [], "answer": ANSWER_SENTINEL})
    finally:
        request_id_ctx.reset(token)
    assert len(t.calls) == 2
    sent = {c["headers"]["X-AI-Invocation-ID"] for c in t.calls}
    assert sent == {err.value.invocation_id}
    lines = json_logs()
    warn = [ln for ln in lines if "worker call" in ln["msg"]]
    assert warn and warn[0]["invocation_id"] == err.value.invocation_id
    assert warn[0]["request_id"] == "req-fail-1"
    assert ANSWER_SENTINEL not in json.dumps(lines)


@pytest.mark.parametrize("outcome", [(502, {"detail": "x"}), httpx.ReadTimeout("slow")])
def test_no_retry_after_a_possible_llm_run_still_one_id(transport, outcome):
    t = transport(outcome)
    with pytest.raises(engine.InterviewEngineUnavailable) as err:
        engine.interview_turn({"transcript": []})
    assert len(t.calls) == 1  # policy unchanged: no double LLM spend
    assert t.calls[0]["headers"]["X-AI-Invocation-ID"] == err.value.invocation_id


def test_worker_token_wins_over_a_same_named_extra_header(transport, monkeypatch):
    monkeypatch.setattr(settings, "AI_WORKER_TOKEN", "t" * 40)
    t = transport(TURN_OK)
    ai_client._worker_request(
        "POST", "/interview/turn", json={}, timeout=httpx.Timeout(5),
        headers={"X-Worker-Token": "attacker", "X-Request-ID": "r1"},
    )
    assert t.calls[0]["headers"]["X-Worker-Token"] == "t" * 40
    assert t.calls[0]["headers"]["X-Request-ID"] == "r1"


def test_worker_request_without_headers_is_unchanged(transport, monkeypatch):
    monkeypatch.setattr(settings, "AI_WORKER_TOKEN", "")
    t = transport(TURN_OK)
    ai_client._worker_request("POST", "/x", json={}, timeout=httpx.Timeout(5))
    assert t.calls[0]["headers"] == {}


# --- API level ----------------------------------------------------------------
@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_ENABLED", True)
    monkeypatch.setattr(settings, "AI_MOCK_INTERVIEW_TENANT_IDS", "")


def _student_header(make_user, token_header):
    make_user("corr@x.edu")
    return token_header("corr@x.edu")


def _create(client, h):
    r = client.post(f"{BASE}/sessions", headers=h,
                    json={"interview_type": "hr", "target_role": "Software Engineer",
                          "duration_minutes": 15})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_api_request_id_reaches_the_worker_for_every_step(client, make_user, token_header, flag, transport):
    h = _student_header(make_user, token_header)
    sid = _create(client, h)
    t = transport(PLAN_OK, TURN_OK)
    r = client.post(f"{BASE}/sessions/{sid}/start", headers={**h, "X-Request-ID": "e2e-req-1"})
    assert r.status_code == 200, r.text
    assert r.headers["x-request-id"] == "e2e-req-1"
    assert [c["url"].rsplit("/", 1)[-1] for c in t.calls] == ["plan", "turn"]
    assert all(c["headers"]["X-Request-ID"] == "e2e-req-1" for c in t.calls)
    invocations = [c["headers"]["X-AI-Invocation-ID"] for c in t.calls]
    assert all(HEX32.match(i) for i in invocations) and invocations[0] != invocations[1]
    assert "X-AI-Invocation-ID" not in r.headers  # not exposed to the browser


def test_api_replaces_a_malformed_client_request_id(client, make_user, token_header, flag, transport):
    h = _student_header(make_user, token_header)
    sid = _create(client, h)
    t = transport(PLAN_OK, TURN_OK)
    bad = "x" * 65
    r = client.post(f"{BASE}/sessions/{sid}/start", headers={**h, "X-Request-ID": bad})
    assert r.status_code == 200, r.text
    rid = r.headers["x-request-id"]
    assert rid != bad and len(rid) <= 64
    assert all(c["headers"]["X-Request-ID"] == rid for c in t.calls)


@pytest.mark.parametrize(
    "value, ok",
    [("req-1", True), ("a", True), ("A.b_c-9", True), ("x" * 64, True),
     ("-", False), ("...", False), ("x" * 65, False), ("a b", False), ('a"b', False),
     ("a\n", False), ("é", False), ("", False), (None, False),
     (ANSWER_SENTINEL, False)],
)
def test_safe_correlation_id(value, ok):
    from app.observability.observability import safe_correlation_id

    assert (safe_correlation_id(value) is not None) is ok
    assert safe_correlation_id(value) in (value, None)
