"""Private-staging access gate (Phase 7B).

Staging runs on public free-tier URLs. Disabling signup is not enough to make
it private: login, password reset, the public lead forms and every API route
would still be reachable by anyone who finds the URL. When
``STAGING_GATE_TOKEN`` is set, this middleware refuses EVERY request (403)
unless it carries ``X-Staging-Gate: <token>``.

Who sends the header: only the staging frontend's server-side proxy
(frontend/src/proxy.ts), which itself sits behind Vercel Authentication. The
token never reaches the browser. Direct hits on the backend URL get 403.

Exempt: ``GET /health`` (dependency-free liveness for the host's health
check; it reveals nothing). CORS preflights are answered by the outer CORS
middleware before this gate runs and also reveal nothing.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

GATE_HEADER = "x-staging-gate"
EXEMPT = frozenset({("GET", "/health"), ("HEAD", "/health")})
_log = logging.getLogger("campus-ai")


def make_staging_gate_middleware(token: str):
    expected = token.encode("utf-8")

    async def staging_gate_middleware(request: Request, call_next):
        if (request.method, request.url.path) in EXEMPT:
            return await call_next(request)
        provided = request.headers.get(GATE_HEADER, "")
        if provided and hmac.compare_digest(provided.encode("utf-8"), expected):
            return await call_next(request)
        # Method + path only; never the header value.
        _log.warning("staging_gate_blocked method=%s path=%s",
                     request.method, request.url.path)
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    return staging_gate_middleware
