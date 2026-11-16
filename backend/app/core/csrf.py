"""CSRF protection: verify the browser-supplied origin of state-changing requests.

Why this exists
---------------
Browsers authenticate to this API with httpOnly cookies (``access_token`` /
``refresh_token``). In production those cookies default to ``SameSite=None``,
so a page on ANY site can make the victim's browser send them. CORS does not
stop that: it only hides the response, and "simple" requests (HTML form posts,
``text/plain`` / multipart bodies, body-less POSTs) are executed by the server
without a preflight. This check runs before any route.

Which credential the backend actually uses (why there is NO Authorization
exemption): ``get_current_user`` takes the token from ``Authorization`` only
when the scheme is ``Bearer``; any other scheme (``Basic x``) is ignored and
the access COOKIE authenticates instead. ``/auth/refresh`` and ``/auth/logout``
never read ``Authorization`` at all - they use the body token or the refresh
COOKIE. So the presence of an Authorization header does not mean cookies are
unused, and it never relaxes this check.

Policy (unsafe methods only: POST, PUT, PATCH, DELETE; every path)
------------------------------------------------------------------
1. ``Origin: null`` (sandboxed frames, opaque redirects, data: URLs) -> 403.
2. ``Origin`` present -> must be an allowed origin (CORS allow-list plus the
   API's own origin, for Swagger UI), else 403.
3. No ``Origin`` but a ``Referer`` -> the Referer's origin must be allowed.
4. Neither header ("no browser provenance"):
   a. ``Sec-Fetch-Site: cross-site`` -> 403.
   b. the request carries an auth cookie -> 403. A cookie-authenticated write
      must prove where it came from.
   c. otherwise -> allowed, but every ``Set-Cookie`` is removed from the
      response. Login / signup / refresh still return the token in the JSON
      body, so a non-browser client authenticates EXPLICITLY with
      ``Authorization: Bearer`` - and a request without browser provenance
      can never plant a cookie session (login CSRF).

Non-browser client contract: send ``Authorization: Bearer <token>`` and no
auth cookies (or, for a test client acting as a browser, an allowed Origin).

Safe methods (GET/HEAD/OPTIONS) are never checked; no GET route mutates state.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
AUTH_COOKIE_NAMES = ("access_token", "refresh_token")
_SAFE_FETCH_SITES = frozenset({"same-origin", "same-site", "none"})
_log = logging.getLogger("campus-ai")

# Decisions returned by ``csrf_decision``.
ALLOW = "allow"
ALLOW_NO_COOKIES = "allow-no-cookies"


def normalize_origin(value: str | None) -> str | None:
    """Return ``scheme://host[:port]`` in lower case, or None if not parseable.

    Default ports are dropped so ``https://a.example:443`` == ``https://a.example``.
    """
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host:
        return None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def csrf_decision(
    method: str,
    headers,
    has_auth_cookie: bool,
    allowed_origins: Iterable[str],
    own_origin: str | None = None,
) -> tuple[str, str | None]:
    """Return ``(decision, reason)``.

    decision is ``ALLOW``, ``ALLOW_NO_COOKIES`` (serve, but strip Set-Cookie)
    or ``"reject"`` with a short reason. ``headers`` is a case-insensitive
    mapping (Starlette ``Headers``); ``allowed_origins`` must be normalised.
    """
    if method.upper() not in UNSAFE_METHODS:
        return ALLOW, None

    allowed = set(allowed_origins)
    if own_origin:
        allowed.add(own_origin)

    origin = headers.get("origin")
    if origin is not None:
        if origin.strip().lower() == "null":
            return "reject", "opaque (null) origin"
        if normalize_origin(origin) in allowed:
            return ALLOW, None
        return "reject", "origin not allowed"

    referer = headers.get("referer")
    if referer:
        if normalize_origin(referer) in allowed:
            return ALLOW, None
        return "reject", "referer not allowed"

    fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in _SAFE_FETCH_SITES:
        return "reject", "cross-site request without origin"
    if has_auth_cookie:
        return "reject", "cookie-authenticated write without origin"
    return ALLOW_NO_COOKIES, None


def make_csrf_middleware(allowed_origins: Iterable[str]):
    """Build an ``@app.middleware("http")`` function enforcing ``csrf_decision``."""
    allowed = frozenset(
        o for o in (normalize_origin(x) for x in allowed_origins) if o
    )

    async def csrf_middleware(request: Request, call_next):
        decision, reason = csrf_decision(
            request.method,
            request.headers,
            any(request.cookies.get(n) is not None for n in AUTH_COOKIE_NAMES),
            allowed,
            own_origin=normalize_origin(str(request.base_url)),
        )
        if decision == ALLOW:
            return await call_next(request)
        if decision == ALLOW_NO_COOKIES:
            response = await call_next(request)
            # No browser provenance: never establish (or clear) a cookie
            # session. Removes every Set-Cookie header.
            if "set-cookie" in response.headers:
                del response.headers["set-cookie"]
            return response

        # Logged here because this middleware sits outside request logging.
        # Only method/path/reason - never header or cookie values.
        _log.warning(
            "csrf_blocked method=%s path=%s reason=%s",
            request.method,
            request.url.path,
            reason,
        )
        rid = request.headers.get("X-Request-ID") or ""
        # Same envelope shape as app.observability.errors, without echoing
        # the offending header back to the caller.
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Cross-site request blocked",
                "request_id": rid,
                "error": {"type": "csrf_error", "status": 403},
            },
        )

    return csrf_middleware
