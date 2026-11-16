"""HTTP security headers for every API response (Phase 7).

The API serves JSON only (plus Swagger/ReDoc in non-production), so the
policy is deliberately strict: nothing returned by the API may be rendered as
a page, framed, sniffed into another type, or cached by an intermediary.

Always set (unless the route already set the header):
  * X-Content-Type-Options: nosniff
  * X-Frame-Options: DENY            (legacy twin of frame-ancestors)
  * Referrer-Policy: no-referrer
  * Cross-Origin-Opener-Policy: same-origin
  * Permissions-Policy: camera=(), microphone=(), geolocation=()
  * Cache-Control: no-store          (authenticated JSON must not be cached)
  * Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; ...
    - EXCEPT on the interactive docs pages (/docs, /redoc, oauth2 redirect),
      which load Swagger/ReDoc assets from a CDN and run inline script. Those
      routes only exist outside production (see main.py), so production
      responses always carry the strict CSP.

HSTS (Strict-Transport-Security) is OPT-IN via HSTS_MAX_AGE > 0 and must only
be enabled once the API is served exclusively over HTTPS on its final domain:
a browser that has seen it refuses plain HTTP to that host for max-age seconds.
includeSubDomains is a separate opt-in (HSTS_INCLUDE_SUBDOMAINS) because it
affects every subdomain of the API host. Browsers ignore HSTS received over
plain HTTP, so it is harmless on a local http:// stack, but we still do not
send it unless configured.
"""

from __future__ import annotations

from fastapi import Request

API_CSP = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def hsts_value(max_age: int, include_subdomains: bool) -> str | None:
    """Return the Strict-Transport-Security value, or None when disabled."""
    if max_age <= 0:
        return None
    value = f"max-age={int(max_age)}"
    if include_subdomains:
        value += "; includeSubDomains"
    return value


def make_security_headers_middleware(
    *, docs_paths: frozenset[str], hsts: str | None
):
    """Build an ``@app.middleware("http")`` function adding the headers above."""

    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        for name, value in BASE_HEADERS.items():
            if name not in headers:
                headers[name] = value
        if request.url.path not in docs_paths and "content-security-policy" not in headers:
            headers["Content-Security-Policy"] = API_CSP
        if hsts and "strict-transport-security" not in headers:
            headers["Strict-Transport-Security"] = hsts
        return response

    return security_headers_middleware
