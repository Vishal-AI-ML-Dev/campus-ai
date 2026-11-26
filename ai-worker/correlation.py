"""Correlation of backend -> worker calls (Mock Interview Phase 7.1).

The backend creates one invocation id per AI call, before the call, and sends
it as ``X-AI-Invocation-ID`` (plus the originating ``X-Request-ID``, which the
shared request-logging middleware already honours). This middleware puts the
invocation id into the logging context, so every JSON log line written while
the call runs carries ``invocation_id``, tags the request's Sentry scope with
it and echoes it back as a response header.

Ids that are not safe tokens (<=64 chars of [A-Za-z0-9._-]) are ignored, never
logged. The ids are random; they carry no interview content.
"""

from __future__ import annotations

from observability import invocation_id_ctx, safe_correlation_id, tag_sentry_scope

INVOCATION_HEADER = "X-AI-Invocation-ID"


async def invocation_id_middleware(request, call_next):
    """ASGI middleware. Register AFTER ``request_logging_middleware`` so it
    wraps it and the access-log line also carries the invocation id."""
    invocation_id = safe_correlation_id(request.headers.get(INVOCATION_HEADER))
    if invocation_id is None:
        return await call_next(request)
    token = invocation_id_ctx.set(invocation_id)
    tag_sentry_scope("invocation_id", invocation_id)
    try:
        response = await call_next(request)
    finally:
        invocation_id_ctx.reset(token)
    response.headers[INVOCATION_HEADER] = invocation_id
    return response
