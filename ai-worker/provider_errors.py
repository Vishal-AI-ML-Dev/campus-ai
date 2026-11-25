"""Provider error classification (Phase 10.3, owner decision D4).

``is_quota_error(exc)`` recognises "the provider's quota / rate limit is
exhausted" (HTTP 429, Groq ``RateLimitError``, Google ``ResourceExhausted`` /
``RESOURCE_EXHAUSTED``), also when wrapped by LangChain (``__cause__`` /
``__context__`` chain). The worker then answers **429** with a fixed detail
instead of a generic 502, so the backend can stop sending new AI calls for a
cooldown period instead of retrying into a wall.

Only the exception CLASS and a status code are inspected - never a prompt.
The provider's message is not returned to the caller.
"""

from __future__ import annotations

from fastapi import HTTPException

QUOTA_DETAIL = "AI provider quota exhausted"
_QUOTA_CLASS_NAMES = frozenset({"RateLimitError", "ResourceExhausted", "TooManyRequests"})


def _one(exc: BaseException) -> bool:
    if type(exc).__name__ in _QUOTA_CLASS_NAMES:
        return True
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if value == 429 or (isinstance(value, str) and value.upper() == "RESOURCE_EXHAUSTED"):
            return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 429


def is_quota_error(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if _one(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def quota_http_error() -> HTTPException:
    return HTTPException(status_code=429, detail=QUOTA_DETAIL)


def step_failure(usage: object, status_code: int, detail: str) -> HTTPException:
    """The error for an interview step that failed outright (Phase 10 review #1).

    If the provider reported quota exhaustion during this step (``usage`` was
    flagged by ``llm_io`` from the provider exception CLASS/STATUS - never from
    any text), answer the explicit 429 quota signal so the backend starts its
    shared quota pause; otherwise the step's usual error.
    """
    if usage is not None and getattr(usage, "quota_exhausted", False) is True:
        return quota_http_error()
    return HTTPException(status_code=status_code, detail=detail)
