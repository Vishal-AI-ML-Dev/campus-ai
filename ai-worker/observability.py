"""Observability: structured request logging + (optional) Sentry error tracking.

Why this exists
---------------
Once the product is live, unhandled errors and slow requests are otherwise
*invisible* - you only hear about them when a user complains. This module gives
you two cheap, always-on signals:

1. **Structured access log** - one JSON line per HTTP request (method, path,
   status, duration_ms, request_id). Log shippers (Render / HF Space logs, Logtail, etc.)
   can parse these directly, and the per-request `request_id` lets you trace a
   single call end-to-end.
2. **Sentry** - captures every unhandled exception with a full stack trace +
   request context, and a sample of performance traces.

Sentry is OPTIONAL: it only initialises when `SENTRY_DSN` is set. With no DSN
(local dev, CI, or before you sign up) the app runs exactly as before and the
`sentry_sdk` package is never even imported. Turn it on later by setting
`SENTRY_DSN` in the host env - no code change needed.

Location:
    E:\\campus-ai\\backend\\observability.py   (identical copy in ai-worker/)
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Holds the current request's id so log lines emitted anywhere during the
# request (not just the access log) can be correlated.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Mock Interview Phase 7.1: id of ONE backend -> AI-worker call, created by the
# backend before the call and sent as X-AI-Invocation-ID (kept across retries
# of that call). Set by the backend around the call and by the worker's
# correlation middleware; added to every JSON log line while set.
invocation_id_ctx: ContextVar[str] = ContextVar("invocation_id", default="-")

# Correlation ids are opaque tokens. Anything else (too long, spaces, quotes,
# control characters, ...) is rejected so a client can't inject arbitrary or
# sensitive text into logs, headers or error reports. At least one letter or
# digit is required, so the context default "-" is never taken for an id.
_SAFE_ID = re.compile(r"^(?=[^A-Za-z0-9]*[A-Za-z0-9])[A-Za-z0-9._-]{1,64}$")


def safe_correlation_id(value: str | None) -> str | None:
    """Return ``value`` when it is a safe correlation id, else None."""
    if value and _SAFE_ID.fullmatch(value):
        return value
    return None


def new_correlation_id() -> str:
    """A fresh random correlation id (32 hex chars, no content)."""
    return uuid.uuid4().hex


class _JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON (easy to grep/ship/parse)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        invocation_id = invocation_id_ctx.get()
        if invocation_id != "-":
            payload["invocation_id"] = invocation_id
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    """Route the root logger through a single JSON stdout handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn's own access log is noisy + unstructured; our middleware already
    # logs every request, so silence the duplicate.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


def init_sentry(
    dsn: str | None,
    environment: str = "development",
    release: str | None = None,
) -> bool:
    """Initialise Sentry IFF a DSN is configured. Returns True when enabled.

    Imports `sentry_sdk` lazily so the dependency is only needed in
    environments that actually set a DSN.
    """
    global _sentry_on
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(**sentry_options(dsn, environment, release))
    _sentry_on = True
    return True


# True once init_sentry() has run with a DSN; lets the middlewares tag the
# per-request Sentry scope without importing sentry_sdk when it is unused.
_sentry_on = False


def tag_sentry_scope(key: str, value: str) -> None:
    """Attach a SAFE correlation id to the current request's Sentry scope.

    Unhandled errors are reported by Starlette's outer error middleware, after
    the logging middleware has reset its context var, so the id must live on
    the (per-request) Sentry scope. No-op unless Sentry is on.
    """
    if not _sentry_on or not safe_correlation_id(value):
        return
    try:
        import sentry_sdk

        sentry_sdk.set_tag(key, value)
    except Exception:  # noqa: BLE001 - tagging must never break a request
        pass


# Sentry integrations that instrument LLM / AI SDKs (module, class), from the
# auto-enabling list of sentry-sdk 2.63.0. LangChain and LangGraph would wrap
# every LangChain call (including mock-interview calls); while LangChain is
# active the SDK suppresses OpenAI / Anthropic / Google GenAI, so disabling
# LangChain alone would switch those on - all of them are disabled.
_AI_INTEGRATIONS = (
    ("langchain", "LangchainIntegration"),
    ("langgraph", "LanggraphIntegration"),
    ("google_genai", "GoogleGenAIIntegration"),
    ("openai", "OpenAIIntegration"),
    ("anthropic", "AnthropicIntegration"),
    ("huggingface_hub", "HuggingfaceHubIntegration"),
    ("cohere", "CohereIntegration"),
    ("litellm", "LiteLLMIntegration"),
    ("openai_agents", "OpenAIAgentsIntegration"),
    ("pydantic_ai", "PydanticAIIntegration"),
    ("mcp", "MCPIntegration"),
)


def disabled_ai_integrations() -> list[Any]:
    """Classes for ``sentry_sdk.init(disabled_integrations=...)``.

    A Sentry integration module raises ``DidNotEnable`` on import when its
    library is not installed; such an integration can never activate, so it
    is skipped. In the AI worker this disables langchain, langgraph,
    google_genai and huggingface_hub; the backend has none of these libraries.
    """
    import importlib

    classes = []
    for module, name in _AI_INTEGRATIONS:
        try:
            mod = importlib.import_module(f"sentry_sdk.integrations.{module}")
            classes.append(getattr(mod, name))
        except Exception:  # noqa: BLE001 - DidNotEnable / ImportError: not installed
            continue
    return classes


def sentry_options(dsn: str, environment: str, release: str | None) -> dict[str, Any]:
    """Keyword arguments for ``sentry_sdk.init`` (privacy-safe; Phase 7.1).

    Verified against sentry-sdk 2.63.0, whose defaults capture stack-frame
    local variables and JSON request bodies (up to ~10 KB) even with
    ``send_default_pii=False``. Both are switched off here, and every event,
    transaction and breadcrumb is reduced to an allow-list by ``scrub_event`` /
    ``scrub_breadcrumb`` before it can leave the process.

    Sentry's auto-enabled AI integrations are switched off with the SDK's
    ``disabled_integrations`` option (see ``disabled_ai_integrations``).
    """
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    return dict(
        dsn=dsn,
        environment=environment,
        release=release,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        # No Sentry instrumentation of LLM calls (prompts, responses, graph
        # state): the AI worker's interview prompts hold resumes, job
        # descriptions and answers. Defence in depth on top of the scrubbing.
        disabled_integrations=disabled_ai_integrations(),
        # 10% of requests get a performance trace - enough to spot slow routes
        # without blowing the free-tier quota. Tune via taste/quota.
        traces_sample_rate=0.1,
        # Never ship personally identifiable info (emails, tokens, cookies, IPs).
        send_default_pii=False,
        # Frame locals can hold answers, resumes, job descriptions, tokens.
        include_local_variables=False,
        # Never attach request bodies (interview answers, worker payloads).
        max_request_body_size="never",
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
    )


# --- Sentry event scrubbing (allow-lists; anything not listed is dropped) -----
REDACTED = "[redacted]"
_SAFE_REQUEST_HEADERS = frozenset(
    {"content-type", "content-length", "accept", "user-agent", "x-request-id"}
)
_SAFE_CONTEXTS = frozenset({"runtime", "os", "trace", "app", "device"})
_SAFE_TRACE_KEYS = frozenset(
    {"trace_id", "span_id", "parent_span_id", "op", "status", "origin", "type"}
)
_SAFE_SPAN_DATA = frozenset(
    {
        "http.method",
        "http.request.method",
        "http.response.status_code",
        "http.status_code",
        "status_code",
        "reason",
        "db.system",
        "db.operation",
        "thread.id",
        "thread.name",
    }
)
_SAFE_BREADCRUMB_DATA = frozenset(
    {"method", "status_code", "reason", "http.method", "http.response.status_code"}
)


def _strip_url(url: object) -> str | None:
    """Scheme + host + path only: no query string, fragment or userinfo."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _scrub_request(request: object) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    out: dict[str, Any] = {}
    if isinstance(request.get("method"), str):
        out["method"] = request["method"]
    url = _strip_url(request.get("url"))
    if url is not None:
        out["url"] = url
    headers = request.get("headers")
    if isinstance(headers, dict):
        kept = {}
        for name, value in headers.items():
            if not isinstance(name, str) or name.lower() not in _SAFE_REQUEST_HEADERS:
                continue
            if name.lower() == "x-request-id":
                value = safe_correlation_id(value if isinstance(value, str) else None) or REDACTED
            kept[name] = value
        out["headers"] = kept
    # data, cookies, query_string, env and anything else are dropped.
    return out


def _scrub_exception(exc_block: object) -> None:
    values = exc_block.get("values") if isinstance(exc_block, dict) else None
    if not isinstance(values, list):
        return
    for exc in values:
        if not isinstance(exc, dict):
            continue
        # The exception TYPE and stack are kept; the message is not, because it
        # can echo input (e.g. pydantic "input_value='<answer>'").
        if "value" in exc:
            exc["value"] = REDACTED
        mech = exc.get("mechanism")
        if isinstance(mech, dict):
            mech.pop("data", None)
            mech.pop("meta", None)
        stack = exc.get("stacktrace")
        frames = stack.get("frames") if isinstance(stack, dict) else None
        if isinstance(frames, list):
            for frame in frames:
                if isinstance(frame, dict):
                    frame.pop("vars", None)


def _scrub_spans(spans: object) -> None:
    if not isinstance(spans, list):
        return
    for span in spans:
        if not isinstance(span, dict):
            continue
        data = span.get("data")
        if isinstance(data, dict):
            span["data"] = {k: v for k, v in data.items() if k in _SAFE_SPAN_DATA}
        span.pop("tags", None)
        desc = span.get("description")
        if isinstance(desc, str) and "://" in desc:
            method, _, url = desc.partition(" ")
            stripped = _strip_url(url) if url else _strip_url(method)
            span["description"] = f"{method} {stripped}" if url and stripped else (stripped or "")


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reduce a Sentry error/transaction event to non-sensitive diagnostics.

    Kept: exception types and stack frames (file, function, line, source
    context), request method + path, the correlation ids as tags, level,
    environment, release, runtime/os contexts, trace ids, timings.
    Dropped: exception messages, frame variables, request bodies, query
    strings, cookies and non-allow-listed headers, user, extra, unknown
    contexts, formatted log messages and their arguments, span data.
    """
    try:
        if "request" in event:
            event["request"] = _scrub_request(event.get("request"))
        event.pop("user", None)
        event.pop("extra", None)
        _scrub_exception(event.get("exception"))
        for thread in (event.get("threads") or {}).get("values", []) or []:
            if isinstance(thread, dict):
                _scrub_exception({"values": [thread]})
        logentry = event.get("logentry")
        if isinstance(logentry, dict):
            # Keep the format string ("worker call %s failed: %s"), drop values.
            logentry.pop("params", None)
            logentry.pop("formatted", None)
        if "message" in event and isinstance(logentry, dict) and logentry.get("message"):
            event["message"] = logentry["message"]
        elif "message" in event:
            event["message"] = REDACTED
        contexts = event.get("contexts")
        if isinstance(contexts, dict):
            kept = {k: v for k, v in contexts.items() if k in _SAFE_CONTEXTS}
            trace = kept.get("trace")
            if isinstance(trace, dict):
                kept["trace"] = {k: v for k, v in trace.items() if k in _SAFE_TRACE_KEYS}
            event["contexts"] = kept
        _scrub_spans(event.get("spans"))
        crumbs = event.get("breadcrumbs")
        values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
        if isinstance(values, list):
            cleaned = [c for c in (scrub_breadcrumb(b) for b in values) if c is not None]
            if isinstance(crumbs, dict):
                crumbs["values"] = cleaned
            else:
                event["breadcrumbs"] = cleaned
        tags = event.get("tags")
        tags = dict(tags) if isinstance(tags, dict) else {}
        # Ids from the logging context fill in only when the per-request scope
        # tag is missing; "-" is the context default (no id), not an id.
        for key, var in (("request_id", request_id_ctx), ("invocation_id", invocation_id_ctx)):
            value = var.get()
            if key not in tags and value != "-" and safe_correlation_id(value):
                tags[key] = value
        event["tags"] = tags
        return event
    except Exception:  # noqa: BLE001 - if scrubbing fails, send nothing
        return None  # type: ignore[return-value]


def scrub_breadcrumb(
    crumb: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Keep a breadcrumb's shape (type, category, level, time, HTTP status)
    but never its message or free-form data (log text, SQL params, bodies)."""
    try:
        if not isinstance(crumb, dict):
            return None
        out = {
            k: crumb[k]
            for k in ("type", "category", "level", "timestamp")
            if k in crumb
        }
        data = crumb.get("data")
        if isinstance(data, dict):
            kept = {k: v for k, v in data.items() if k in _SAFE_BREADCRUMB_DATA}
            url = _strip_url(data.get("url"))
            if url is not None:
                kept["url"] = url
            if kept:
                out["data"] = kept
        return out
    except Exception:  # noqa: BLE001 - drop the breadcrumb rather than leak
        return None


_access_logger = logging.getLogger("campus.request")


async def request_logging_middleware(request, call_next):
    """ASGI middleware: assign a request_id, time the request, log one line.

    Register with: `app.middleware("http")(request_logging_middleware)`.
    Echoes the id back as the `X-Request-ID` response header so a client/user
    can quote it in a bug report and you can find the exact log line.
    """
    # Phase 7.1: a client-supplied id is honoured only when it is a safe token
    # (<=64 chars of [A-Za-z0-9._-]); otherwise a fresh id is used.
    rid = safe_correlation_id(request.headers.get("X-Request-ID")) or uuid.uuid4().hex[:12]
    token = request_id_ctx.set(rid)
    # Also stash on request.state so exception handlers (which may run OUTSIDE
    # this middleware, e.g. the catch-all 500 handler) can read the id even
    # after the context var has been reset.
    request.state.request_id = rid
    tag_sentry_scope("request_id", rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        _access_logger.exception(
            "request_failed",
            extra={
                "extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": duration_ms,
                }
            },
        )
        request_id_ctx.reset(token)
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    _access_logger.info(
        "request",
        extra={
            "extra_fields": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            }
        },
    )
    response.headers["X-Request-ID"] = rid
    request_id_ctx.reset(token)
    return response
