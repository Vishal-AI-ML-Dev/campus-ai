"""Structured LLM calls with a JSON fallback (same approach as resume._ats_invoke).

1. Ask the provider for schema-bound output (function calling / JSON mode).
2. If that fails or returns junk, make a plain call and extract the first JSON
   object from the text.
3. If that also fails, raise ``LLMUnavailable``; each graph node decides
   whether it has a deterministic fallback or must fail the request.

Errors are logged by class only (never prompt text, resumes or answers).

Tracing (Phase 7.1, R1): interview calls are NEVER traced. They do not use
``tracing.trace_config`` and run inside ``no_tracing.tracing_disabled()`` -
see ``interview/no_tracing.py``. The only callback they may carry is the
in-process ``usage.TokenCounter`` (Phase 7.2), which reads provider token
counts and keeps no text; it is attached only inside a router usage scope.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

import llm_metrics
from interview.no_tracing import tracing_disabled, untraced_config
from interview.usage import TokenCounter, current_usage, model_name, provider_name
from llm import get_chat_model, get_structured_model

logger = logging.getLogger("campus_ai_worker.interview")

M = TypeVar("M", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """The provider failed or returned unusable output for this step."""


def _extract_json(text: str) -> Any:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def invoke_structured(schema: type[M], messages: list[Any], *, step: str) -> M:
    # Inside the guard so it also holds in the report scorer's worker threads.
    with tracing_disabled():
        return _measured(schema, messages, step=step)


def _measured(schema: type[M], messages: list[Any], *, step: str) -> M:
    """Phase 7.2: time the step and record its outcome; behaviour unchanged."""
    usage = current_usage()
    cfg = untraced_config() if usage is None else {"callbacks": [TokenCounter(usage)]}
    started = time.perf_counter()
    outcome = "provider_error"
    try:
        result, outcome = _invoke_structured(schema, messages, step=step, cfg=cfg)
        return result
    except LLMUnavailable as exc:
        outcome = "unusable" if "unusable" in str(exc) else "provider_error"
        if usage is not None:
            usage.mark_fallback()  # the graph falls back, or the request fails
        raise
    finally:
        seconds = time.perf_counter() - started
        if usage is not None:
            usage.add_latency(seconds * 1000)
            if outcome == "json_fallback":
                usage.mark_fallback()
        llm_metrics.observe_step(step, provider_name(), model_name(), outcome, seconds)


def _invoke_structured(
    schema: type[M], messages: list[Any], *, step: str, cfg: dict[str, Any]
) -> tuple[M, str]:
    try:
        result = get_structured_model(schema).invoke(messages, config=cfg)
        if isinstance(result, schema):
            return result, "ok"
        if isinstance(result, dict):
            return schema.model_validate(result), "ok"
    except Exception as exc:  # noqa: BLE001 - fall through to the JSON path
        logger.warning("interview %s: structured output failed (%s)", step, type(exc).__name__)

    try:
        raw = get_chat_model().invoke(messages, config=cfg)
        return schema.model_validate(_extract_json(str(raw.content))), "json_fallback"
    except (ValidationError, ValueError) as exc:
        logger.warning("interview %s: unparseable model output (%s)", step, type(exc).__name__)
        raise LLMUnavailable(f"{step}: unusable model output") from exc
    except Exception as exc:  # noqa: BLE001 - provider/SDK/network error
        logger.warning("interview %s: provider call failed (%s)", step, type(exc).__name__)
        raise LLMUnavailable(f"{step}: provider call failed") from exc
