"""Global exception handlers - one uniform JSON error shape for every failure.

Why this exists
---------------
Without these handlers, an unhandled exception leaks Starlette's default plain
"Internal Server Error" (no request id, inconsistent shape), and different error
kinds (HTTPException, validation, 500) return different JSON. That makes the
frontend guess and makes production bugs hard to trace.

Every error now returns the SAME shape and always carries the request id (also
echoed as the ``X-Request-ID`` header), so a user can quote one id in a bug
report and you can grep the exact log line:

    {
      "detail": <human-readable message | validation error list>,
      "request_id": "abc123def456",
      "error": {"type": "http_error" | "validation_error" | "internal_error",
                "status": 500}
    }

``detail`` is kept as the top-level, human-readable key FastAPI clients already
expect (the frontend reads it), so this stays backwards compatible. Any
more-specific handler registered elsewhere (e.g. slowapi's RateLimitExceeded)
still wins via Starlette's MRO lookup.

Location:
    E:\\campus-ai\\backend\\errors.py   (identical copy in ai-worker/)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

try:  # backend: package layout (app.observability); worker: flat modules
    from .observability import request_id_ctx
except ImportError:  # pragma: no cover - ai-worker flat-module fallback
    from observability import request_id_ctx

logger = logging.getLogger("campus.error")


def _request_id(request: Request) -> str:
    """Best-effort current request id (state -> incoming header -> context var).

    The catch-all 500 handler runs OUTSIDE the request-logging middleware, after
    that middleware has already reset the context var - so we read the id from
    ``request.state`` first (set by the middleware), then fall back to an
    incoming header, then the context var.
    """
    rid = getattr(request.state, "request_id", None)
    if rid:
        return str(rid)
    return request.headers.get("X-Request-ID") or request_id_ctx.get()


def _error_response(
    *, status_code: int, detail: object, request_id: str, error_type: str
) -> JSONResponse:
    """Build the one-and-only uniform error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "request_id": request_id,
            "error": {"type": error_type, "status": status_code},
        },
        headers={"X-Request-ID": request_id},
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Preserve the HTTPException status + detail, wrap in the uniform envelope."""
    rid = _request_id(request)
    response = _error_response(
        status_code=exc.status_code,
        detail=exc.detail,
        request_id=rid,
        error_type="http_error",
    )
    # Preserve any auth challenge / custom headers (e.g. WWW-Authenticate on 401).
    if exc.headers:
        for key, value in exc.headers.items():
            response.headers.setdefault(key, value)
    return response


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 with the standard validation error list under ``detail``."""
    rid = _request_id(request)
    return _error_response(
        status_code=422,
        detail=jsonable_encoder(exc.errors()),
        request_id=rid,
        error_type="validation_error",
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all: never leak internals - return a clean, traceable 500.

    The request-logging middleware already logged the full traceback for this
    request (with the correct request id), so we do not re-log here.
    """
    rid = _request_id(request)
    return _error_response(
        status_code=500,
        detail="Internal server error",
        request_id=rid,
        error_type="internal_error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install the uniform handlers. Call once, after creating the app."""
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
