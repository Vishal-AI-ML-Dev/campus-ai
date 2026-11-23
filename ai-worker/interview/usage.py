"""Per-request LLM usage for the interview engine (Mock Interview Phase 7.2).

What is collected (numbers and identifiers only, never text):
  * ``llm_calls``      - LLM invocations started (structured + JSON-fallback)
  * ``failed_calls``   - invocations that raised or returned unusable output
  * ``fallback_used``  - True when any step fell back (JSON fallback or a
                         deterministic fallback after ``LLMUnavailable``)
  * ``input_tokens`` / ``output_tokens`` - provider-reported counts; ``None``
                         when the provider reported nothing (unknown is NOT 0)
  * ``tokens_reported`` / ``tokens_unreported`` - calls with / without counts
  * ``llm_latency_ms`` - summed wall time of the LLM steps

How: ``usage_scope()`` (entered by the interview router) puts a thread-safe
``StepUsage`` into a context variable; ``llm_io`` attaches a ``TokenCounter``
callback to each interview LLM call. The callback is in-process only: it reads
the provider's usage metadata from the finished call and never stores, logs or
sends prompts or completions. It is the only callback interview calls carry
(Langfuse, LangSmith and Sentry stay excluded, Phase 7.1). The report scorer's
threads run in a copy of the request context (Phase 7.1), so they add to the
same ``StepUsage``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler

import llm_metrics
from config import settings


class StepUsage:
    """Thread-safe accumulator for one worker request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.failed_calls = 0
        self.fallback_used = False
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.tokens_reported = 0
        self.tokens_unreported = 0
        self.llm_latency_ms = 0.0

    def call_started(self) -> None:
        with self._lock:
            self.llm_calls += 1

    def call_finished(self, input_tokens: int | None, output_tokens: int | None) -> None:
        with self._lock:
            if input_tokens is None and output_tokens is None:
                self.tokens_unreported += 1
                return
            self.tokens_reported += 1
            if input_tokens is not None:
                self.input_tokens = (self.input_tokens or 0) + input_tokens
            if output_tokens is not None:
                self.output_tokens = (self.output_tokens or 0) + output_tokens

    def call_failed(self) -> None:
        with self._lock:
            self.failed_calls += 1

    def mark_fallback(self) -> None:
        with self._lock:
            self.fallback_used = True

    def add_latency(self, ms: float) -> None:
        with self._lock:
            self.llm_latency_ms += ms

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "provider": provider_name(),
                "model": model_name(),
                "llm_calls": self.llm_calls,
                "failed_calls": self.failed_calls,
                "fallback_used": self.fallback_used,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "tokens_reported": self.tokens_reported,
                "tokens_unreported": self.tokens_unreported,
                "llm_latency_ms": int(round(self.llm_latency_ms)),
            }


_current: ContextVar[StepUsage | None] = ContextVar("interview_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[StepUsage]:
    """Collect usage for the LLM calls made inside this block."""
    usage = StepUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def current_usage() -> StepUsage | None:
    return _current.get()


def provider_name() -> str:
    return (settings.LLM_PROVIDER or "").strip().lower()[:40]


def model_name() -> str | None:
    """Configured model id (configuration, not content)."""
    provider = provider_name()
    if provider == "groq":
        return (settings.GROQ_MODEL or "")[:80] or None
    if provider == "gemini":
        return (settings.GEMINI_MODEL or "")[:80] or None
    return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number >= 0 else None


def extract_token_usage(response: Any) -> tuple[int | None, int | None]:
    """Provider-reported (input, output) token counts of one finished call.

    Reads LangChain's standard ``AIMessage.usage_metadata`` first, then the
    provider-specific ``llm_output['token_usage']`` (OpenAI-style keys). Only
    numbers are read. ``(None, None)`` when nothing was reported.
    """
    found_in = found_out = None
    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list or []:
            meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if isinstance(meta, dict):
                i = _non_negative_int(meta.get("input_tokens"))
                o = _non_negative_int(meta.get("output_tokens"))
                if i is not None:
                    found_in = (found_in or 0) + i
                if o is not None:
                    found_out = (found_out or 0) + o
    if found_in is not None or found_out is not None:
        return found_in, found_out
    llm_output = getattr(response, "llm_output", None)
    token_usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
    if isinstance(token_usage, dict):
        i = _non_negative_int(token_usage.get("prompt_tokens", token_usage.get("input_tokens")))
        o = _non_negative_int(
            token_usage.get("completion_tokens", token_usage.get("output_tokens"))
        )
        if i is not None or o is not None:
            return i, o
    return None, None


class TokenCounter(BaseCallbackHandler):
    """Counts calls and provider-reported tokens. Keeps no prompt or output."""

    raise_error = False
    run_inline = True

    def __init__(self, usage: StepUsage) -> None:
        self._usage = usage

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # noqa: D401
        self._usage.call_started()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._usage.call_started()

    def on_llm_end(self, response, **kwargs) -> None:
        input_tokens, output_tokens = extract_token_usage(response)
        self._usage.call_finished(input_tokens, output_tokens)
        llm_metrics.observe_tokens(provider_name(), model_name(), input_tokens, output_tokens)

    def on_llm_error(self, error, **kwargs) -> None:
        self._usage.call_failed()
