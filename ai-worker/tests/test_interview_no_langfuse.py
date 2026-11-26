"""Phase 7.1 (R1): the interview engine never reaches Langfuse or any tracer.

Unlike the engine tests' FakeLLM (which bypasses LangChain), these tests run a
REAL LangChain chat model and real runnables, so LangChain's callback
managers do their normal work. Langfuse is simulated as fully configured: the
worker's tracing module holds a live callback handler, exactly as after
``init_tracing`` with LANGFUSE_* keys. LangSmith environment tracing is also
switched on. Every interview route - success, LLM failure, JSON-fallback retry
and 502 paths - must produce ZERO callback events and attach no handler or
tracer to any LLM call. Control tests prove the harness would detect a leak.

Offline: no network, no keys.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

import interview.llm_io as llm_io  # noqa: E402
import tracing  # noqa: E402
from interview.router import interview_turn, router  # noqa: E402
from interview.schemas import (  # noqa: E402
    InterviewPlan,
    LLMAnswerScores,
    LLMEvaluation,
    LLMPlan,
    LLMQuestion,
    LLMReportSummary,
    PlanTopic,
    TurnRequest,
)
from interview.topics import INTRO_TOPIC  # noqa: E402
from test_interview_engine import cfg  # noqa: E402
from test_interview_report import DOWN, LONG_A, _body, scorer  # noqa: E402

RESUME_SENTINEL = "RESUME-SENTINEL-7f3a quantum basket weaving at Initech"
JD_SENTINEL = "JD-SENTINEL-91bc we need a senior unicorn wrangler"
ANSWER_SENTINEL = "ANSWER-SENTINEL-c0de I optimised the flux capacitor by 88 percent"


class Recorder(BaseCallbackHandler):
    """Stands in for the Langfuse CallbackHandler; records every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any, Any]] = []

    def _rec(self, name, kwargs):
        self.events.append((name, kwargs.get("run_id"), kwargs.get("parent_run_id")))

    def on_chat_model_start(self, serialized, messages, **kw):
        self._rec("chat_model_start", kw)

    def on_llm_start(self, serialized, prompts, **kw):
        self._rec("llm_start", kw)

    def on_llm_end(self, response, **kw):
        self._rec("llm_end", kw)

    def on_llm_error(self, error, **kw):
        self._rec("llm_error", kw)

    def on_chain_start(self, serialized, inputs, **kw):
        self._rec("chain_start", kw)

    def on_chain_end(self, outputs, **kw):
        self._rec("chain_end", kw)

    def on_chain_error(self, error, **kw):
        self._rec("chain_error", kw)


class SpyChatModel(BaseChatModel):
    """Real LangChain chat model; records the handlers attached to each call."""

    reply: str = "this is not json"
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "spy"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        handlers = list(run_manager.handlers) if run_manager is not None else []
        self.seen.append([type(h).__name__ for h in handlers])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


@contextmanager
def langfuse_configured_and_spy_llm(script: dict, *, langsmith_env: bool = True):
    """Simulate configured Langfuse + LangSmith env tracing; route the
    interview's LLM factories through a real chat model."""
    recorder = Recorder()
    model = SpyChatModel(seen=[])

    def structured(schema):
        def call(messages):
            model.invoke(messages)  # a real, nested LangChain model run
            item = script.get(schema, DOWN)
            if isinstance(item, Exception):
                raise item
            return item(messages) if callable(item) else item

        return RunnableLambda(call)

    saved = (tracing._enabled, tracing._handler, llm_io.get_structured_model, llm_io.get_chat_model)
    env_saved = {k: os.environ.get(k) for k in _LS_ENV}
    tracing._enabled, tracing._handler = True, recorder
    llm_io.get_structured_model = structured
    llm_io.get_chat_model = lambda: model
    if langsmith_env:
        os.environ.update(_LS_ENV)
    _clear_langsmith_env_cache()
    try:
        yield recorder, model
    finally:
        tracing._enabled, tracing._handler, llm_io.get_structured_model, llm_io.get_chat_model = saved
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _clear_langsmith_env_cache()


_LS_ENV = {
    "LANGSMITH_TRACING": "true",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGSMITH_API_KEY": "fake-key-not-used",
    "LANGSMITH_ENDPOINT": "http://127.0.0.1:9",
}


def _clear_langsmith_env_cache() -> None:
    from langsmith import utils as ls_utils

    for fn in (getattr(ls_utils, "get_env_var", None), getattr(ls_utils, "get_tracer_project", None)):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# Phase 7.2: the only handler an interview LLM call may carry is the in-process
# token counter (reads provider usage numbers, keeps no text, sends nothing).
ALLOWED_HANDLERS = {"TokenCounter"}


def _assert_untraced(recorder: Recorder, model: SpyChatModel, *, min_llm_calls: int) -> None:
    assert recorder.events == [], f"tracer received {len(recorder.events)} events"
    assert len(model.seen) >= min_llm_calls, "the scenario did not reach the LLM"
    assert all(set(h) <= ALLOWED_HANDLERS for h in model.seen), (
        f"handlers attached to LLM calls: {model.seen}"
    )


def _plan() -> dict:
    return InterviewPlan(
        topics=[INTRO_TOPIC, PlanTopic(name="SQL", focus="joins")], question_budget=6
    ).model_dump()


# --- Interview routes -----------------------------------------------------------
def test_plan_route_success_and_fallback_send_nothing():
    body = {
        "config": cfg(interview_type="technical").model_dump(),
        "resume_text": RESUME_SENTINEL,
        "job_description": JD_SENTINEL,
    }
    # LLMPlan fails -> JSON fallback retry also fails -> deterministic plan.
    with langfuse_configured_and_spy_llm({LLMPlan: DOWN}) as (rec, model):
        r = _client().post("/interview/plan", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["planner"] == "fallback"
    _assert_untraced(rec, model, min_llm_calls=3)  # jd + planner + fallback retries


def test_turn_route_opening_success_sends_nothing():
    body = {"config": cfg().model_dump(), "plan": _plan(), "resume_text": RESUME_SENTINEL,
            "job_description": JD_SENTINEL, "transcript": [], "remaining_seconds": 600}
    q = LLMQuestion(preamble="", question="Tell me about yourself.")
    with langfuse_configured_and_spy_llm({LLMQuestion: q}) as (rec, model):
        r = _client().post("/interview/turn", json=body)
    assert r.status_code == 200, r.text
    _assert_untraced(rec, model, min_llm_calls=1)


def test_turn_route_with_answer_evaluation_sends_nothing():
    body = {"config": cfg().model_dump(), "plan": _plan(), "transcript": [
        {"seq": 1, "topic": INTRO_TOPIC.name, "question": "Introduce yourself.",
         "answer": ANSWER_SENTINEL}], "remaining_seconds": 600}
    script = {
        LLMEvaluation: LLMEvaluation(coverage="full", depth=3, evidence="flux capacitor",
                                     follow_up_recommended=False, follow_up_angle="",
                                     difficulty_signal=0),
        LLMQuestion: LLMQuestion(preamble="", question="Explain a SQL join."),
    }
    with langfuse_configured_and_spy_llm(script) as (rec, model):
        r = _client().post("/interview/turn", json=body)
    assert r.status_code == 200, r.text
    _assert_untraced(rec, model, min_llm_calls=2)


def test_turn_route_failure_and_retry_paths_send_nothing():
    body = {"config": cfg().model_dump(), "plan": _plan(), "transcript": [], "remaining_seconds": 600,
            "resume_text": RESUME_SENTINEL}
    # Structured call fails, JSON fallback fails, the generator retries -> 502.
    with langfuse_configured_and_spy_llm({LLMQuestion: DOWN}) as (rec, model):
        r = _client().post("/interview/turn", json=body)
    assert r.status_code == 502
    _assert_untraced(rec, model, min_llm_calls=2)


def test_report_route_success_and_failure_send_nothing():
    with langfuse_configured_and_spy_llm(
        {LLMAnswerScores: scorer(score=4), LLMReportSummary: DOWN}
    ) as (rec, model):
        ok = _client().post("/interview/report", json=_body(LONG_A, ANSWER_SENTINEL))
    assert ok.status_code == 200, ok.text
    _assert_untraced(rec, model, min_llm_calls=2)

    with langfuse_configured_and_spy_llm({LLMAnswerScores: DOWN}) as (rec, model):
        bad = _client().post("/interview/report", json=_body(LONG_A, ANSWER_SENTINEL))
    assert bad.status_code == 502
    _assert_untraced(rec, model, min_llm_calls=1)


def test_parent_callbacks_are_not_inherited_by_interview_steps():
    """Even when the engine runs under a traced parent runnable, no child run
    (graph, node or LLM) reports to the parent's handler."""
    payload = TurnRequest(config=cfg(), plan=InterviewPlan(**_plan()), transcript=[],
                          remaining_seconds=600)
    q = LLMQuestion(preamble="", question="Tell me about yourself.")
    # LangSmith env off: the parent run itself (test code, outside the guard)
    # would otherwise try to upload. Inheritance works the same for any handler.
    with langfuse_configured_and_spy_llm({LLMQuestion: q}, langsmith_env=False) as (rec, model):
        parent = RunnableLambda(lambda p: interview_turn(p))
        parent.invoke(payload, config={"callbacks": [rec]})
    children = [e for e in rec.events if e[2] is not None]
    assert children == [], f"interview runs reported to a parent tracer: {children}"
    assert {e[0] for e in rec.events} <= {"chain_start", "chain_end"}  # the parent itself
    assert model.seen and all(set(h) <= ALLOWED_HANDLERS for h in model.seen)


# --- Guards ---------------------------------------------------------------------
def test_trace_config_refuses_interview_names_even_when_enabled():
    rec = Recorder()
    saved = (tracing._enabled, tracing._handler)
    tracing._enabled, tracing._handler = True, rec
    try:
        assert tracing.trace_config("interview_question", provider="groq") == {}
        assert tracing.trace_config("Interview-anything") == {}
        # Unrelated features keep their tracing behaviour.
        assert tracing.trace_config("mentor_generate")["callbacks"] == [rec]
    finally:
        tracing._enabled, tracing._handler = saved


def test_llm_io_does_not_use_trace_config():
    import inspect

    src = inspect.getsource(llm_io)
    assert "trace_config" not in src.replace("``tracing.trace_config``", "")


# --- Controls: the harness detects a leak when tracing is NOT suppressed -------
def test_control_harness_detects_langfuse_handler():
    # LangSmith env off here: outside the guard it would really try to upload.
    with langfuse_configured_and_spy_llm({}, langsmith_env=False) as (rec, model):
        model.invoke("hello", config=tracing.trace_config("mentor_generate"))
    assert rec.events, "control failed: recorder saw nothing"
    assert model.seen and "Recorder" in model.seen[0]


def test_control_env_tracing_is_on_outside_the_guard():
    from langchain_core.tracers.context import _tracing_v2_is_enabled

    from interview.no_tracing import tracing_disabled

    with langfuse_configured_and_spy_llm({}):
        assert _tracing_v2_is_enabled(), "control failed: env tracing not active"
        with tracing_disabled():
            assert not _tracing_v2_is_enabled()
        assert _tracing_v2_is_enabled()
