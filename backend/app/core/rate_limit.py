"""Rate limiting for abuse-prone PUBLIC endpoints (slowapi).

Why this exists
---------------
The unauthenticated routes are the obvious abuse targets:
  * POST /auth/login            -> credential stuffing / password brute force
  * POST /auth/register         -> spam / fake-account floods
  * POST /leads, /feedback      -> marketing-form spam bots
  * POST /recruiters/accept-invite -> invite-token guessing

slowapi gives per-client request quotas with almost no code.

Storage: in-memory vs Redis
---------------------------
By default slowapi keeps counters IN-MEMORY, which is only correct for a single
instance: each replica would count separately, so N replicas effectively let
through N x the quota. As soon as `REDIS_URL` is configured we point slowapi's
`storage_uri` at Redis so every instance shares one counter (the horizontal-
scale fix). With no `REDIS_URL` set we keep the zero-infra in-memory store,
which is fine for local dev, tests, and a single Render dyno.

Why per-IP (and the campus-NAT caveat)
--------------------------------------
We key on the client IP. Behind a reverse proxy the socket peer is the proxy,
so set TRUSTED_PROXY_HOPS to the number of proxies that append to
`X-Forwarded-For`; only those appended entries are trusted (see client_ip).

NOTE: a whole college can sit behind ONE public IP (NAT). So we deliberately do
NOT set a global default limit (that would throttle an entire campus together)
and we keep the login/register ceilings generous. The per-IP limits here are a
first line of defense against bots; true per-account brute-force protection
(lockout / exponential backoff keyed on the email) is a later hardening step.
"""

from __future__ import annotations

import logging

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings

logger = logging.getLogger("campus_ai.rate_limit")


def client_ip(request: Request) -> str:
    """Client IP for rate limiting, trusting only CONFIGURED proxy hops.

    Phase 6 fix: the left-most ``X-Forwarded-For`` entry is whatever the
    client sent, so keying on it let anyone pick a fresh "IP" per request and
    bypass every limit. Each trusted proxy APPENDS the address it saw, so with
    ``TRUSTED_PROXY_HOPS = N`` the N-th entry from the right is the address the
    outermost trusted proxy received the request from. With N = 0 (default)
    the header is ignored and the socket peer is used. If the header has fewer
    entries than N (request did not pass through all proxies) we also fall
    back to the socket peer.
    """
    hops = settings.TRUSTED_PROXY_HOPS
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for") or ""
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return get_remote_address(request)


_storage_uri: str | None = settings.REDIS_URL or None
if _storage_uri:
    logger.info("Rate limiter using shared Redis storage.")
else:
    logger.info(
        "Rate limiter using in-memory storage "
        "(set REDIS_URL to share counters across instances)."
    )

# Shared limiter. No default_limits on purpose (see campus-NAT note above);
# limits are applied explicitly via @limiter.limit(...) on individual routes.
# headers_enabled => responses carry X-RateLimit-Limit / -Remaining / -Reset
# so clients (and we) can see the quota state.
limiter = Limiter(
    key_func=client_ip,
    headers_enabled=True,
    storage_uri=_storage_uri,
)
