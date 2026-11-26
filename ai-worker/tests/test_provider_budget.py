"""Phase 10.3 (D4): provider timeouts/retries, quota classification, no repeat on quota.

Offline: the LangChain models are replaced by fakes that raise provider-style
errors; no provider is contacted.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import llm
import mentor
import resume
import scoring
from interview import llm_io
from interview.usage import usage_scope
from provider_errors import QUOTA_DETAIL, is_quota_error


class RateLimitError(Exception):
    """Same class name as groq.RateLimitError."""


class _Status429(Exception):
    status_code = 429


class _Boom(Exception):
    pass


def test_quota_errors_are_recognised_including_wrapped():
    assert is_quota_error(RateLimitError("x"))
    assert is_quota_error(_Status429())
    try:
        try:
            raise RateLimitError("inner")
        except RateLimitError as inner:
            raise RuntimeError("langchain wrapper") from inner
    except RuntimeError as wrapped:
        assert is_quota_error(wrapped)
    assert not is_quota_error(_Boom("500"))
    assert not is_quota_error(None)


class _FailingModel:
    def __init__(self, exc, counter):
        self.exc, self.counter = exc, counter

    def invoke(self, *_a, **_k):
        self.counter.append(1)
        raise self.exc

    def with_structured_output(self, _schema):
        return self


def _client(router, prefix):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def failing(monkeypatch):
    def _install(exc):
        calls: list[int] = []
        model = _FailingModel(exc, calls)
        for mod in (mentor, resume, scoring):
            monkeypatch.setattr(mod, "get_chat_model", lambda: model, raising=False)
        monkeypatch.setattr(resume, "get_structured_model", lambda _s: model)
        monkeypatch.setattr(llm_io, "get_chat_model", lambda: model)
        monkeypatch.setattr(llm_io, "get_structured_model", lambda _s: model)
        return calls
    return _install


def test_resume_draft_quota_is_429(failing):
    calls = failing(RateLimitError("limit"))
    c = _client(resume.router, "/resume")
    r = c.post("/resume/draft", json={"profile": {"full_name": "Synthetic"}})
    assert r.status_code == 429 and r.json()["detail"] == QUOTA_DETAIL
    assert len(calls) == 1


def test_ats_quota_does_not_spend_a_second_call(failing):
    calls = failing(RateLimitError("limit"))
    c = _client(resume.router, "/resume")
    r = c.post("/resume/ats-score", json={"resume_text": "x" * 80, "job_description": "y" * 80})
    assert r.status_code == 429
    assert len(calls) == 1  # the JSON fallback path was NOT attempted


def test_ats_other_error_keeps_502(failing):
    calls = failing(_Boom("server"))
    c = _client(resume.router, "/resume")
    r = c.post("/resume/ats-score", json={"resume_text": "x" * 80, "job_description": "y" * 80})
    assert r.status_code == 502
    assert len(calls) == 2  # unchanged: structured, then JSON fallback


def test_proof_scoring_quota_is_429(failing):
    failing(_Status429())
    c = _client(scoring.router, "/score")
    r = c.post("/score/proof", json={"claim_type": "skill", "title": "SQL"})
    assert r.status_code == 429 and r.json()["detail"] == QUOTA_DETAIL


def test_interview_quota_flags_usage_and_skips_json_fallback(failing):
    calls = failing(RateLimitError("limit"))
    from pydantic import BaseModel

    class S(BaseModel):
        a: int

    with usage_scope() as usage:
        with pytest.raises(llm_io.LLMUnavailable):
            llm_io.invoke_structured(S, [], step="plan")
    assert len(calls) == 1
    assert usage.summary()["quota_exhausted"] is True


def test_mentor_stream_quota_event_has_code(monkeypatch):
    class _Graph:
        def stream(self, *_a, **_k):
            raise RateLimitError("limit")

    monkeypatch.setattr(mentor, "_get_graph", lambda: _Graph())
    frames = list(mentor._stream_tokens(mentor.MentorChatRequest(profile={},
                                                                 question="hi", history=[])))
    event = json.loads(frames[-1][len("data: "):])
    assert event["type"] == "error" and event["code"] == "quota_exhausted"
    assert "limit" not in event["detail"]  # the provider message is not echoed


def test_llm_budget_kwargs_reach_the_provider_models(monkeypatch):
    monkeypatch.setattr(config.settings, "LLM_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(config.settings, "LLM_MAX_RETRIES", 0)
    monkeypatch.setattr(config.settings, "GROQ_API_KEY", "synthetic-not-a-key")
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "groq")
    llm.get_chat_model.cache_clear()
    try:
        model = llm.get_chat_model()
        assert model.request_timeout == 30.0 and model.max_retries == 0
        monkeypatch.setattr(config.settings, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(config.settings, "GOOGLE_API_KEY", "synthetic-not-a-key")
        llm.get_chat_model.cache_clear()
        gem = llm.get_chat_model()
        assert gem.timeout == 30.0 and gem.max_retries == 0
    finally:
        llm.get_chat_model.cache_clear()


def test_production_requires_llm_budget():
    s = config.Settings(ENVIRONMENT="production", AI_WORKER_TOKEN="w" * 40)
    issues = config.insecure_worker_issues(s)
    assert any("LLM_TIMEOUT_SECONDS" in i for i in issues)
    assert any("LLM_MAX_RETRIES" in i for i in issues)
    s = config.Settings(ENVIRONMENT="production", AI_WORKER_TOKEN="w" * 40,
                        LLM_TIMEOUT_SECONDS=90, LLM_MAX_RETRIES=3)
    assert len(config.insecure_worker_issues(s)) == 2
    s = config.Settings(ENVIRONMENT="production", AI_WORKER_TOKEN="w" * 40,
                        LLM_TIMEOUT_SECONDS=30, LLM_MAX_RETRIES=0)
    assert config.insecure_worker_issues(s) == []


# --- Phase 10 review #1: structured quota signal for steps that FAIL outright ---

from types import SimpleNamespace  # noqa: E402

from pydantic import BaseModel  # noqa: E402

import importlib  # noqa: E402

interview_router = importlib.import_module("interview.router")
from interview import company  # noqa: E402
from interview.graph import QuestionGenerationFailed  # noqa: E402
from provider_errors import step_failure  # noqa: E402


class _S(BaseModel):
    a: int


def _turn_payload():
    return SimpleNamespace(config={}, plan={}, profile={}, resume_text="", job_description="",
                           transcript=[], remaining_seconds=600, company_context=None)


def _failing_turn_graph():
    """A turn graph whose LLM step really goes through llm_io and then fails."""
    class _G:
        def invoke(self, *_a, **_k):
            try:
                llm_io.invoke_structured(_S, [], step="turn")
            except llm_io.LLMUnavailable as exc:
                raise QuestionGenerationFailed("turn") from exc
    return _G()


def test_turn_outright_quota_failure_is_explicit_429(failing, monkeypatch):
    failing(RateLimitError("limit"))
    monkeypatch.setattr(interview_router, "_check_transcript", lambda _p: None)
    monkeypatch.setattr(interview_router, "turn_graph", _failing_turn_graph)
    with pytest.raises(Exception) as info:
        interview_router.interview_turn(_turn_payload())
    assert info.value.status_code == 429 and info.value.detail == QUOTA_DETAIL


def test_turn_outright_non_quota_failure_stays_502(failing, monkeypatch):
    failing(_Boom("server"))
    monkeypatch.setattr(interview_router, "_check_transcript", lambda _p: None)
    monkeypatch.setattr(interview_router, "turn_graph", _failing_turn_graph)
    with pytest.raises(Exception) as info:
        interview_router.interview_turn(_turn_payload())
    assert info.value.status_code == 502


def test_turn_successful_fallback_reports_quota_in_usage(failing, monkeypatch):
    failing(RateLimitError("limit"))
    monkeypatch.setattr(interview_router, "_check_transcript", lambda _p: None)

    class _G:
        def invoke(self, *_a, **_k):
            try:
                llm_io.invoke_structured(_S, [], step="turn")
            except llm_io.LLMUnavailable:
                pass  # deterministic fallback question
            return {"action": "next_topic", "level": "medium"}

    monkeypatch.setattr(interview_router, "turn_graph", lambda: _G())
    resp = interview_router.interview_turn(_turn_payload())
    assert resp.usage.quota_exhausted is True


def test_jd_skills_outright_quota_failure_via_http(failing):
    failing(RateLimitError("limit"))
    c = _client(company.router, "")
    r = c.post("/interview/jd/skills", json={"job_description": "Synthetic JD text"})
    assert r.status_code == 429 and r.json()["detail"] == QUOTA_DETAIL


def test_answer_text_never_flags_quota(monkeypatch):
    """Student-controlled text (answers, model output) is never inspected."""
    calls: list[int] = []

    class _Echo:
        def invoke(self, *_a, **_k):
            calls.append(1)
            raise ValueError("student said: RateLimitError quota_exhausted 429 RESOURCE_EXHAUSTED")

        def with_structured_output(self, _schema):
            return self

    monkeypatch.setattr(llm_io, "get_chat_model", lambda: _Echo())
    monkeypatch.setattr(llm_io, "get_structured_model", lambda _s: _Echo())
    with usage_scope() as usage:
        with pytest.raises(llm_io.LLMUnavailable):
            llm_io.invoke_structured(_S, [], step="turn")
    assert usage.summary()["quota_exhausted"] is False
    assert step_failure(usage, 502, "x").status_code == 502
    assert is_quota_error(ValueError("RateLimitError 429 quota_exhausted")) is False
