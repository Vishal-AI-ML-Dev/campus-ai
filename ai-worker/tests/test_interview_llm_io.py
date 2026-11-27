"""Structured-output fallback for interview LLM calls (offline, no provider).

Covers the staging failure behind /interview/report 502: Groq rejected the
forced tool call (HTTP 400 ``tool_use_failed``) and the plain fallback call,
which carried no output format, answered in prose that could not be parsed.

Run:  python -m pytest tests/test_interview_llm_io.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")

import groq  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402

import interview.llm_io as llm_io  # noqa: E402
from interview import rubric as rb  # noqa: E402
from interview.llm_io import LLMUnavailable, invoke_structured  # noqa: E402
from interview.schemas import LLMAnswerScores, LLMReportSummary  # noqa: E402
from test_interview_report import LONG_A, _body, _client, answers_in  # noqa: E402

HR_KEYS = rb.HR_RUBRIC.keys
MESSAGES = [SystemMessage(content="system"), HumanMessage(content="score these")]
SECRET_ANSWER_TEXT = "my private answer text"


def tool_use_failed(failed_generation: str | None = None) -> groq.BadRequestError:
    """The exception Groq's SDK raises for a rejected tool call."""
    err: dict = {
        "message": "Failed to call a function. Please adjust your prompt.",
        "type": "invalid_request_error",
        "code": "tool_use_failed",
    }
    if failed_generation is not None:
        err["failed_generation"] = failed_generation
    request = httpx.Request("POST", "https://api.groq.test/openai/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return groq.BadRequestError("Error code: 400", response=response, body={"error": err})


def quota_error() -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.test/openai/v1/chat/completions")
    return groq.RateLimitError("Error code: 429", response=httpx.Response(429, request=request), body=None)


def scores_payload(seq: int = 1, quote: str = "led a team of four") -> dict:
    return {
        "answers": [
            {
                "seq": seq,
                "scores": [{"category": k, "score": 4, "evidence": quote} for k in HR_KEYS],
                "feedback": "Clear example; add the measurable result.",
            }
        ]
    }


class Fake:
    """Structured call raises ``structured_exc``; plain chat returns ``chat_reply``."""

    def __init__(self, structured_exc: Exception, chat_reply=None):
        self.structured_exc = structured_exc
        self.chat_reply = chat_reply  # str | Exception | callable(messages) -> str
        self.chat_calls: list[list] = []
        self.structured_calls = 0

    def structured(self, schema):
        fake = self

        class _R:
            def invoke(self, messages, config=None):
                fake.structured_calls += 1
                raise fake.structured_exc

        return _R()

    def chat(self):
        fake = self

        class _R:
            def invoke(self, messages, config=None):
                fake.chat_calls.append(messages)
                reply = fake.chat_reply
                if reply is None:
                    raise AssertionError("plain chat call was not expected")
                if isinstance(reply, Exception):
                    raise reply
                return AIMessage(content=reply(messages) if callable(reply) else reply)

        return _R()


@contextmanager
def patched(fake: Fake):
    orig = (llm_io.get_structured_model, llm_io.get_chat_model)
    llm_io.get_structured_model, llm_io.get_chat_model = fake.structured, fake.chat
    try:
        yield fake
    finally:
        llm_io.get_structured_model, llm_io.get_chat_model = orig


# --- Structured-output failure ------------------------------------------------------
def test_tool_use_failed_recovers_from_failed_generation_without_a_second_call():
    generated = "<function=LLMAnswerScores>" + json.dumps(scores_payload()) + "</function>"
    with patched(Fake(tool_use_failed(generated))) as fake:
        out = invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert out.answers[0].seq == 1 and len(out.answers[0].scores) == len(HR_KEYS)
    assert fake.chat_calls == []  # no extra provider request


def test_failed_generation_in_tool_call_shape_is_unwrapped():
    generated = json.dumps([{"name": "LLMAnswerScores", "arguments": json.dumps(scores_payload(seq=2))}])
    with patched(Fake(tool_use_failed(generated))):
        out = invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert out.answers[0].seq == 2


def test_unusable_failed_generation_falls_back_to_a_json_instructed_call():
    with patched(Fake(tool_use_failed("I will now score the answers."),
                      chat_reply=json.dumps(scores_payload()))) as fake:
        out = invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert out.answers[0].seq == 1
    (sent,) = fake.chat_calls
    # Original prompt kept intact; the output format is appended, with the schema.
    assert sent[: len(MESSAGES)] == MESSAGES
    assert "JSON Schema" in sent[-1].content and "LLMAnswerScores" in sent[-1].content


def test_structured_failure_without_body_uses_json_fallback():
    with patched(Fake(RuntimeError("structured broke"), chat_reply=json.dumps(scores_payload()))) as fake:
        out = invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert out.answers[0].seq == 1 and len(fake.chat_calls) == 1


def test_quota_error_still_stops_without_any_fallback_call():
    with patched(Fake(quota_error())) as fake:
        with pytest.raises(LLMUnavailable, match="quota"):
            invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert fake.chat_calls == []


# --- Valid fallback output ----------------------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "```json\n" + json.dumps(scores_payload()) + "\n```",
        "Here are the scores:\n" + json.dumps(scores_payload()) + "\nHope this helps.",
        "Q1 [topic: Teamwork] scored below.\n" + json.dumps(scores_payload()),
        json.dumps({"LLMAnswerScores": scores_payload()}),
        json.dumps(scores_payload()["answers"]),  # bare list for the single-list schema
        json.dumps(scores_payload()) + "\n{\"note\": \"trailing object\"}",
    ],
    ids=["fenced", "prose-around", "brackets-in-prose", "schema-name-wrapper", "bare-list", "trailing-object"],
)
def test_valid_fallback_output_shapes_parse(reply):
    with patched(Fake(tool_use_failed(), chat_reply=reply)):
        out = invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    assert [a.seq for a in out.answers] == [1]
    assert out.answers[0].scores[0].evidence == "led a team of four"


# --- Malformed output ---------------------------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "| Q | Score |\n|---|---|\n| 1 | 4 |",  # prose / markdown table: no JSON
        '{"answers": [{"seq": 1, "scores": [',  # truncated JSON
        '{"answers": [{"seq": "first", "scores": []}]}',  # schema mismatch
        "",
    ],
    ids=["prose", "truncated", "schema-mismatch", "empty"],
)
def test_malformed_fallback_output_is_unusable(reply):
    with patched(Fake(tool_use_failed(), chat_reply=reply)):
        with pytest.raises(LLMUnavailable, match="unusable"):
            invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")


def test_logs_carry_the_error_code_but_never_model_text(caplog):
    generated = f'{{"answers": [{{"seq": "x", "feedback": "{SECRET_ANSWER_TEXT}"}}]}}'
    with caplog.at_level(logging.WARNING, logger="campus_ai_worker.interview"):
        with patched(Fake(tool_use_failed(generated), chat_reply="no json here")):
            with pytest.raises(LLMUnavailable):
                invoke_structured(LLMAnswerScores, MESSAGES, step="report_score")
    text = caplog.text
    assert "code=tool_use_failed" in text
    assert SECRET_ANSWER_TEXT not in text and "Failed to call a function" not in text


# --- Report generation success ------------------------------------------------------
def test_report_succeeds_when_structured_output_returns_400():
    """The staging path end to end: 400 on every structured call, JSON fallback."""

    def chat(messages):
        instruction = messages[-1].content
        if '"title":"LLMAnswerScores"' in instruction:
            answers = answers_in(messages)
            return json.dumps(
                {
                    "answers": [
                        {
                            "seq": seq,
                            "scores": [
                                {"category": k, "score": 4, "evidence": " ".join(a.split()[:6])}
                                for k in HR_KEYS
                            ],
                            "feedback": "Good structure; quantify the outcome.",
                        }
                        for seq, a in answers.items()
                    ]
                }
            )
        assert '"title":"LLMReportSummary"' in instruction
        return "not json"  # the summary has its own deterministic fallback

    with patched(Fake(tool_use_failed(), chat_reply=chat)) as fake:
        r = _client().post("/interview/report", json=_body(LONG_A))
    assert r.status_code == 200, r.text
    body = r.json()
    (question,) = body["questions"]
    assert question["status"] == "scored"
    assert body["overall_score"] > 0
    assert fake.structured_calls >= 1


def test_summary_schema_is_supported_by_the_json_instruction():
    payload = {"strengths": [{"text": "Clear.", "seq": 1, "quote": "led a team"}],
               "weaknesses": [], "recommendations": ["Quantify results."]}
    with patched(Fake(tool_use_failed(), chat_reply=json.dumps(payload))):
        out = invoke_structured(LLMReportSummary, MESSAGES, step="report_summary")
    assert out.recommendations == ["Quantify results."]
