"""Prometheus metrics: request counts, latency histogram, in-flight gauge.

Why this exists
---------------
Structured logs (observability.py) describe *individual* requests; metrics
describe the system in *aggregate* - request rate, error rate, and latency
percentiles over time. A Prometheus scraper (Grafana Cloud free tier, Render
metrics, self-hosted, ...) reads the ``/metrics`` endpoint and turns these
counters into dashboards + alerts.

Design: OPTIONAL by default
---------------------------
``prometheus_client`` is imported behind a guard. If the package isn't
installed (e.g. a stripped test env) every helper degrades to a no-op and
``/metrics`` reports that metrics are disabled - the app runs exactly as
before. Install the dependency (it's declared in pyproject) to turn real
metrics on; no code change needed.

Labels are kept LOW-cardinality on purpose:
  * ``path`` is the matched ROUTE TEMPLATE (e.g. "/drives/{drive_id}"), never
    the raw URL, so a million distinct ids don't explode into a million series.
  * Requests that matched no route (404s to random URLs) collapse to
    "__unmatched__" so a scanner can't grow memory by hitting random paths.
  * The ``/metrics`` scrape itself is skipped so it never counts itself.

Location:
    E:\\campus-ai\\backend\\metrics.py   (near-identical copy in ai-worker/)
"""

from __future__ import annotations

import time

from starlette.routing import Match

try:  # prometheus_client is declared in pyproject; guard so tests still import.
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _ENABLED = True
except Exception:  # pragma: no cover - import guard
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

if _ENABLED:
    REQUESTS = Counter(
        "http_requests_total",
        "Total HTTP requests processed.",
        ["method", "path", "status"],
    )
    LATENCY = Histogram(
        "http_request_duration_seconds",
        "HTTP request latency in seconds.",
        ["method", "path"],
    )
    IN_PROGRESS = Gauge(
        "http_requests_in_progress",
        "HTTP requests currently being processed.",
    )


def enabled() -> bool:
    """True when prometheus_client is installed and metrics are recording."""
    return _ENABLED


def _route_template(request) -> str:
    """Low-cardinality path label: the matched route template or a placeholder.

    Prefer the Route stashed on the scope after routing; fall back to matching
    the request against the app's routes for Starlette versions that don't set
    it. Unmatched (404) requests collapse to "__unmatched__".
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    for r in request.app.routes:
        try:
            if r.matches(request.scope)[0] == Match.FULL:
                return getattr(r, "path", None) or "__unmatched__"
        except Exception:  # noqa: BLE001 - never let metrics break a request
            continue
    return "__unmatched__"


async def metrics_middleware(request, call_next):
    """ASGI middleware: record count + latency + in-flight for every request.

    Register with: ``app.middleware("http")(metrics_middleware)``.
    No-op passthrough when prometheus_client isn't installed. The ``/metrics``
    scrape endpoint is skipped so it never inflates its own counters.
    """
    if not _ENABLED or request.url.path == "/metrics":
        return await call_next(request)

    method = request.method
    IN_PROGRESS.inc()
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - start
        path = _route_template(request)
        REQUESTS.labels(method, path, str(status)).inc()
        LATENCY.labels(method, path).observe(elapsed)
        IN_PROGRESS.dec()


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint.

    When metrics are disabled (package missing) we still return a valid,
    scrapeable body so a scraper gets a 200 rather than a hard error.
    """
    if not _ENABLED:
        return (
            b"# metrics disabled: prometheus_client not installed\n",
            CONTENT_TYPE_LATEST,
        )
    return (generate_latest(), CONTENT_TYPE_LATEST)
