"""Structured LLM calls with a JSON fallback (same approach as resume._ats_invoke).

1. Ask the provider for schema-bound output (function calling / JSON mode).
2. If that fails: first try the provider's rejected generation (Groq returns
   it with a 400 ``tool_use_failed``, no extra request); otherwise make a plain
   call that carries the JSON Schema as an instruction, and parse the first
   JSON value in the text that validates.
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
from functools import lru_cache
from typing import Any, TypeVar, get_origin

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

import llm_metrics
from interview.no_tracing import tracing_disabled, untraced_config
from interview.usage import TokenCounter, current_usage, model_name, provider_name
from llm import get_chat_model, get_structured_model
from provider_errors import is_quota_error

logger = logging.getLogger("campus_ai_worker.interview")

M = TypeVar("M", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """The provider failed or returned unusable output for this step."""


_MAX_JSON_CANDIDATES = 200
_TOOL_WRAPPER_KEYS = ("arguments", "parameters")


def _json_values(text: str) -> list[Any]:
    """Every JSON object/array that decodes at some '{' or '[' in ``text``.

    Tolerates prose, markdown fences and ``<function=...>`` wrappers around the
    JSON, which a plain-text answer (or Groq's ``failed_generation``) often has.
    """
    decoder = json.JSONDecoder()
    values: list[Any] = []
    tried = 0
    i = 0
    while i < len(text) and tried < _MAX_JSON_CANDIDATES:
        if text[i] in "{[":
            tried += 1
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            if isinstance(value, (dict, list)) and value:
                values.append(value)
            i = end
        else:
            i += 1
    return values


def _unwrap(schema: type[BaseModel], data: Any) -> Any:
    """Peel tool-call / schema-name wrappers models put around the payload."""
    fields = set(schema.model_fields)
    for _ in range(4):
        if (
            isinstance(data, list)
            and len(data) == 1
            and isinstance(data[0], dict)
            and "name" in data[0]
            and any(k in data[0] for k in _TOOL_WRAPPER_KEYS)
        ):
            data = data[0]  # [{"name": ..., "arguments": {...}}]
            continue
        if not isinstance(data, dict) or fields & data.keys():
            break
        inner = next((data[k] for k in (*_TOOL_WRAPPER_KEYS, schema.__name__) if k in data), None)
        if inner is None and len(data) == 1:
            inner = next(iter(data.values()))
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except ValueError:
                break
        if not isinstance(inner, (dict, list)):
            break
        data = inner
    if isinstance(data, list) and len(fields) == 1:
        (only,) = fields
        if get_origin(schema.model_fields[only].annotation) is list:
            data = {only: data}  # a bare list for a single-list schema
    return data


def _parse_output(schema: type[M], text: str) -> M:
    """First JSON value in ``text`` that validates against ``schema``."""
    last: Exception = ValueError("no JSON object in model output")
    for value in _json_values(text):
        try:
            return schema.model_validate(_unwrap(schema, value))
        except ValidationError as exc:
            last = exc
    raise last


def _extract_json(text: str) -> Any:
    """Kept for callers/tests: the first JSON object or array in ``text``."""
    values = _json_values(text)
    if not values:
        raise ValueError("no JSON object in model output")
    return values[0]


@lru_cache(maxsize=64)
def _json_instruction(schema: type[BaseModel]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    return (
        "Return your answer as ONE JSON object that matches this JSON Schema. "
        "Output only the JSON: no markdown, no code fences, no text before or "
        "after it. Escape any double quotes inside strings.\n"
        f"JSON Schema: {schema_json}"
    )


def _json_messages(schema: type[BaseModel], messages: list[Any]) -> list[Any]:
    """The plain-call prompt: the original messages plus the output format.

    The structured call carries the schema in its tool definition; a plain call
    has no tool, so without this the model answers in prose (unparseable).
    """
    return [*messages, HumanMessage(content=_json_instruction(schema))]


def _error_code(exc: BaseException) -> str:
    """Provider error code (e.g. Groq ``tool_use_failed``) for logs - never text."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        code = err.get("code") if isinstance(err, dict) else None
        if isinstance(code, str):
            safe = "".join(ch for ch in code if ch.isalnum() or ch in "_-.")[:40]
            if safe:
                return safe
    status = getattr(exc, "status_code", None)
    return str(status) if isinstance(status, int) else "-"


def _failed_generation(exc: BaseException) -> str | None:
    """Groq returns the rejected tool-call text with a 400 ``tool_use_failed``."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return None
    err = body.get("error", body)
    text = err.get("failed_generation") if isinstance(err, dict) else None
    return text if isinstance(text, str) and text.strip() else None


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
        logger.warning(
            "interview %s: structured output failed (%s, code=%s)",
            step,
            type(exc).__name__,
            _error_code(exc),
        )
        # Phase 10.3: the quota is exhausted - a JSON-path retry would only
        # spend (or be refused) again. Flag it for the backend and stop here.
        if is_quota_error(exc):
            _flag_quota()
            raise LLMUnavailable(f"{step}: provider quota exhausted") from exc
        # Groq rejects a tool call whose arguments break the JSON Schema (400
        # tool_use_failed) but returns what the model generated. It is often
        # valid after unwrapping; using it saves a second provider request.
        generated = _failed_generation(exc)
        if generated is not None:
            try:
                return _parse_output(schema, generated), "json_fallback"
            except (ValidationError, ValueError):
                logger.warning("interview %s: failed_generation unusable, retrying as JSON", step)

    try:
        raw = get_chat_model().invoke(_json_messages(schema, messages), config=cfg)
        return _parse_output(schema, str(raw.content)), "json_fallback"
    except (ValidationError, ValueError) as exc:
        logger.warning("interview %s: unparseable model output (%s)", step, type(exc).__name__)
        raise LLMUnavailable(f"{step}: unusable model output") from exc
    except Exception as exc:  # noqa: BLE001 - provider/SDK/network error
        logger.warning("interview %s: provider call failed (%s)", step, type(exc).__name__)
        if is_quota_error(exc):
            _flag_quota()
        raise LLMUnavailable(f"{step}: provider call failed") from exc


def _flag_quota() -> None:
    usage = current_usage()
    if usage is not None:
        usage.mark_quota_exhausted()
