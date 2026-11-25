"""Browser error reports (Phase 10.5, frontend error tracking on the free tier).

``POST /client-errors`` - the frontend's global error listeners report an
uncaught error or unhandled promise rejection here. Behind
``CLIENT_ERROR_REPORTING_ENABLED`` (default OFF): while off the route is 404.

Privacy by construction: only an error KIND, an error CLASS NAME (identifier
characters only), a normalised ROUTE (numbers and ids replaced by ``:id``,
no query string or fragment), the build RELEASE label and a Next.js DIGEST are
accepted. Error MESSAGES and stack traces are never accepted - they can contain
student data. Unknown fields are rejected (422). The report becomes one
structured ``campus.client_error`` log line (which Sentry/log alerting can pick
up) and a Prometheus counter. No authentication is required (errors happen
before login); the route is rate-limited per client address.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.rate_limit import limiter

logger = logging.getLogger("campus.client_error")
router = APIRouter(tags=["observability"])

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,59}$")
_ROUTE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_ID_LIKE = re.compile(r"^(\d+|[0-9a-fA-F\-]{16,})$")

try:  # optional dependency guard, as in ai_metrics
    from prometheus_client import Counter

    _ERRORS = Counter("frontend_errors_total", "Browser errors reported by the frontend.", ["kind"])
except Exception:  # pragma: no cover
    _ERRORS = None

# --- Sentry (Phase 10 review #4) ---------------------------------------------
# The WARNING log line above never becomes a Sentry event (the SDK's logging
# integration only sends ERROR logs), so a report is ALSO sent as one explicit
# Sentry event - only when Sentry is configured. The event carries a fixed
# message and the already-sanitised fields as tags; it still passes the shared
# ``scrub_event`` filter. Storm control (per process): one event per
# (kind, error class, route) per AI_ALERT_THROTTLE_SECONDS, and at most
# SENTRY_EVENTS_PER_WINDOW events per window in total.
SENTRY_EVENTS_PER_WINDOW = 20
_sentry_lock = threading.Lock()
_sentry_last: dict[tuple[str, str, str], float] = {}
_sentry_window: list[float] = []
_clock: Callable[[], float] = time.monotonic


def _sentry_active() -> bool:
    try:
        import sentry_sdk

        return sentry_sdk.get_client().is_active()
    except Exception:  # noqa: BLE001
        return False


def _sentry_capture(event: dict[str, Any]) -> None:
    import sentry_sdk

    sentry_sdk.capture_event(event)


def build_sentry_event(kind: str, name: str, route: str, release: str, digest: str) -> dict[str, Any]:
    """A content-free Sentry event: fixed message + sanitised tags only."""
    tags = {"source": "frontend", "client_error_kind": kind, "client_error_name": name,
            "client_route": route}
    if release:
        tags["client_release"] = release
    if digest:
        tags["client_digest"] = digest
    return {
        "level": "error",
        "logger": "campus.client_error",
        "logentry": {"message": "Frontend error reported by the browser"},
        "tags": tags,
        "fingerprint": ["frontend-error", kind, name, route],
    }


def _allow_sentry_event(key: tuple[str, str, str]) -> bool:
    window = max(1, settings.AI_ALERT_THROTTLE_SECONDS)
    now = _clock()
    with _sentry_lock:
        last = _sentry_last.get(key)
        if last is not None and now - last < window:
            return False
        _sentry_window[:] = [t for t in _sentry_window if now - t < window]
        if len(_sentry_window) >= SENTRY_EVENTS_PER_WINDOW:
            return False
        _sentry_last[key] = now
        _sentry_window.append(now)
        return True


def _report_to_sentry(kind: str, name: str, route: str, release: str, digest: str) -> None:
    """Never raises: error reporting must not fail the request."""
    try:
        if not _sentry_active() or not _allow_sentry_event((kind, name, route)):
            return
        _sentry_capture(build_sentry_event(kind, name, route, release, digest))
    except Exception as exc:  # noqa: BLE001
        logger.warning("client error not sent to Sentry (%s)", type(exc).__name__)


def _reset_sentry_throttle() -> None:
    """Test hook."""
    with _sentry_lock:
        _sentry_last.clear()
        _sentry_window.clear()


def normalise_route(raw: str) -> str:
    """Path only, ids replaced, odd segments dropped: '/dashboard/x/42?q=1' -> '/dashboard/x/:id'."""
    path = raw.split("?", 1)[0].split("#", 1)[0]
    parts = []
    for seg in path.split("/"):
        if not seg:
            continue
        if _ID_LIKE.match(seg):
            parts.append(":id")
        elif _ROUTE_SEGMENT.match(seg):
            parts.append(seg.lower())
        else:
            parts.append(":x")
        if len(parts) >= 8:
            break
    return "/" + "/".join(parts)


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^(error|unhandledrejection|render)$")
    name: str = Field(default="Error", max_length=60)
    route: str = Field(default="/", max_length=300)
    release: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9._\-]*$")
    digest: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9]*$")

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        return value if _NAME.match(value) else "Error"


@router.post("/client-errors", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
@limiter.limit("30/minute")
def report_client_error(request: Request, response: Response, payload: ClientErrorReport) -> Response:
    if not settings.CLIENT_ERROR_REPORTING_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    route = normalise_route(payload.route)
    logger.warning(
        "client error",
        extra={"extra_fields": {"client_error": True, "kind": payload.kind,
                                "error_name": payload.name, "route": route,
                                "release": payload.release, "digest": payload.digest}},
    )
    if _ERRORS is not None:
        try:
            _ERRORS.labels(payload.kind).inc()
        except Exception:  # noqa: BLE001
            pass
    _report_to_sentry(payload.kind, payload.name, route, payload.release, payload.digest)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
