"""Phase 7.2: per-request LLM usage and latency from the interview engine.

Real LangChain runs (a BaseChatModel subclass whose AIMessage carries the
provider's usage metadata), so the TokenCounter goes through LangChain's real
callback machinery - including the report scorer's worker threads. Offline.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402

import interview.llm_io as llm_io  # noqa: E402
import llm_metrics  # noqa: E402
from interview import rubric as rb  # noqa: E402
from interview.router import router  # noqa: E402
from interview.schemas import (  # noqa: E402
    InterviewPlan,
    LLMAnswerScores,
    LLMPlan,
    LLMQuestion,
    LLMReportSummary,
    PlanTopic,
)
from interview.topics import INTRO_TOPIC  # noqa: E402
from interview.usage import StepUsage, extract_token_usage, usage_scope  # noqa: E402
from test_interview_engine import cfg  # noqa: E402
from test_interview_report import DOWN, LONG_B, _body, scorer  # noqa: E402

RESUME = "RESUME-SENTINEL-7f3a quantum basket weaving at Initech"


class UsageModel(BaseChatModel):
    """Real chat model; each call reports the configured usage metadata."""

    usage: dict | None = None
    delay: float = 0.0
    reply: str = "not json"

    @property
    def _llm_type(self) -> str:
        return "usage-spy"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.delay:
            time.sleep(self.delay)
        msg = AIMessage(content=self.reply)
        if self.usage is not None:
            msg.usage_metadata = {**self.usage, "total_tokens": sum(self.usage.values())}
        return ChatResult(generations=[ChatGeneration(message=msg)])


@contextmanager
def engine(script: dict, *, usage: dict | None = None, delay: float = 0.0, reply: str = "not json"):
    model = UsageModel(usage=usage, delay=delay, reply=reply)

    def structured(schema):
        def call(messages):
            model.invoke(messages)  # nested real run: inherits the TokenCounter
            item = script.get(schema, DOWN)
            if isinstance(item, Exception):
                raise item
            return item(messages) if callable(item) else item

        return RunnableLambda(call)

    saved = (llm_io.get_structured_model, llm_io.get_chat_model)
    llm_io.get_structured_model, llm_io.get_chat_model = structured, (lambda: model)
    try:
        yield model
    finally:
        llm_io.get_structured_model, llm_io.get_chat_model = saved


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _turn_body(**extra):
    plan = InterviewPlan(topics=[INTRO_TOPIC, PlanTopic(name="SQL", focus="joins")], question_budget=6)
    return {"config": cfg().model_dump(), "plan": plan.model_dump(), "transcript": [],
            "remaining_seconds": 600, **extra}


Q = LLMQuestion(preamble="", question="Tell me about yourself.")


# --- Extraction ------------------------------------------------------------------
def test_extract_from_usage_metadata_and_llm_output():
    msg = AIMessage(content="x")
    msg.usage_metadata = {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}
    r = SimpleNamespace(generations=[[SimpleNamespace(message=msg)]], llm_output=None)
    assert extract_token_usage(r) == (12, 5)
    r2 = SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content="x"))]],
                         llm_output={"token_usage": {"prompt_tokens": 30, "completion_tokens": 9}})
    assert extract_token_usage(r2) == (30, 9)


def test_extract_unknown_is_none_not_zero_and_ignores_junk():
    r = SimpleNamespace(generations=[[SimpleNamespace(message=AIMessage(content="x"))]], llm_output={})
    assert extract_token_usage(r) == (None, None)
    junk = SimpleNamespace(generations=[], llm_output={"token_usage": {"prompt_tokens": True,
                                                                        "completion_tokens": -4}})
    assert extract_token_usage(junk) == (None, None)
    zero = AIMessage(content="x")
    zero.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    rz = SimpleNamespace(generations=[[SimpleNamespace(message=zero)]], llm_output=None)
    assert extract_token_usage(rz) == (0, 0)


def test_step_usage_is_thread_safe_and_keeps_unknown_separate():
    u = StepUsage()
    u.call_finished(None, None)
    assert u.summary()["input_tokens"] is None and u.summary()["tokens_unreported"] == 1
    u.call_finished(0, 0)
    s = u.summary()
    assert (s["input_tokens"], s["output_tokens"], s["tokens_reported"]) == (0, 0, 1)


# --- Routes -----------------------------------------------------------------------
def test_turn_usage_counts_calls_tokens_latency_and_provider():
    with engine({LLMQuestion: Q}, usage={"input_tokens": 100, "output_tokens": 20}, delay=0.05):
        r = _client().post("/interview/turn", json=_turn_body(resume_text=RESUME))
    assert r.status_code == 200, r.text
    u = r.json()["usage"]
    assert u["llm_calls"] == 1 and u["failed_calls"] == 0 and u["fallback_used"] is False
    assert (u["input_tokens"], u["output_tokens"]) == (100, 20)
    assert u["tokens_reported"] == 1 and u["tokens_unreported"] == 0
    assert u["llm_latency_ms"] >= 50
    assert u["provider"] == "groq" and u["model"]
    assert RESUME.split()[0] not in json.dumps(u)


def test_missing_provider_usage_is_reported_as_unknown():
    with engine({LLMQuestion: Q}, usage=None):
        r = _client().post("/interview/turn", json=_turn_body())
    u = r.json()["usage"]
    assert u["llm_calls"] == 1
    assert u["input_tokens"] is None and u["output_tokens"] is None
    assert u["tokens_unreported"] == 1 and u["tokens_reported"] == 0


def test_plan_fallback_path_sets_fallback_flag_and_counts_every_call():
    body = {"config": cfg(interview_type="hr").model_dump(), "resume_text": RESUME}
    with engine({LLMPlan: DOWN}, usage={"input_tokens": 10, "output_tokens": 2}):
        r = _client().post("/interview/plan", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["planner"] == "fallback"
    u = r.json()["usage"]
    assert u["fallback_used"] is True
    # structured attempt + JSON fallback attempt, each a real model call
    assert u["llm_calls"] == 2 and u["input_tokens"] == 20 and u["output_tokens"] == 4


def test_failed_turn_returns_502_without_usage_and_leaves_no_scope():
    with engine({LLMQuestion: DOWN}, usage={"input_tokens": 1, "output_tokens": 1}):
        r = _client().post("/interview/turn", json=_turn_body())
    assert r.status_code == 502
    assert "usage" not in r.json()
    assert llm_io.current_usage() is None


def test_report_scorer_threads_add_to_the_same_usage():
    answers = [f"{LONG_B} case {i}" for i in range(9)]  # 3 scoring chunks in parallel
    script = {LLMAnswerScores: scorer(score=4, keys=rb.TECHNICAL_RUBRIC.keys), LLMReportSummary: DOWN}
    with engine(script, usage={"input_tokens": 7, "output_tokens": 3}):
        r = _client().post("/interview/report", json=_body(*answers, interview_type="technical"))
    assert r.status_code == 200, r.text
    u = r.json()["usage"]
    # 3 scorer chunks (threads) + summary structured + summary JSON fallback
    assert u["llm_calls"] == 5, u
    assert u["input_tokens"] == 35 and u["output_tokens"] == 15
    assert u["fallback_used"] is True  # the summary fell back to rule-based items


def test_usage_outside_a_router_scope_is_not_collected():
    with engine({LLMQuestion: Q}, usage={"input_tokens": 5, "output_tokens": 5}):
        out = llm_io.invoke_structured(LLMQuestion, [], step="question")
    assert out == Q and llm_io.current_usage() is None


def test_scope_isolates_requests():
    with usage_scope() as a:
        a.call_started()
        with usage_scope() as b:
            assert llm_io.current_usage() is b
        assert llm_io.current_usage() is a
    assert llm_io.current_usage() is None and a.llm_calls == 1


# --- Metrics ----------------------------------------------------------------------
def test_prometheus_step_and_token_metrics():
    if not llm_metrics._ENABLED:
        return
    from prometheus_client import REGISTRY

    def val(name, labels):
        return REGISTRY.get_sample_value(name, labels) or 0.0

    ok = {"step": "question", "provider": "groq", "model": llm_io.model_name(), "outcome": "ok"}
    fb = {**ok, "step": "plan", "outcome": "unusable"}
    tok = {"provider": "groq", "model": llm_io.model_name(), "direction": "input"}
    before = (val("mock_interview_llm_steps_total", ok), val("mock_interview_llm_steps_total", fb),
              val("mock_interview_llm_tokens_total", tok))
    with engine({LLMQuestion: Q}, usage={"input_tokens": 11, "output_tokens": 1}):
        _client().post("/interview/turn", json=_turn_body())
    with engine({LLMPlan: DOWN}, usage=None, reply="still not json"):
        _client().post("/interview/plan", json={"config": cfg(interview_type="hr").model_dump()})
    assert val("mock_interview_llm_steps_total", ok) == before[0] + 1
    assert val("mock_interview_llm_steps_total", fb) == before[1] + 1
    assert val("mock_interview_llm_tokens_total", tok) == before[2] + 11
    # No high-cardinality or content labels exist on these metrics.
    for metric in (llm_metrics.LLM_CALLS, llm_metrics.LLM_SECONDS, llm_metrics.LLM_TOKENS):
        assert set(metric._labelnames) <= {"step", "provider", "model", "outcome", "direction"}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASSED {name}")
