"""Lightweight Redis read-cache with a graceful in-process fallback.

Why this exists
---------------
Hot read paths recompute the same query on every request: role dashboards,
placement/analytics rollups, `/auth/me` lookups, section attendance summaries.
A small cache in front of them cuts DB load and tail latency at very low cost.

Design: OPTIONAL by default
---------------------------
With no `REDIS_URL` configured (local dev, tests, single-dyno) every helper
degrades to a no-op: `get_json` always misses and `set_json`/`invalidate` do
nothing. So adding a cache read to a hot endpoint is safe everywhere and only
starts *doing* something once Redis is wired up. All Redis errors are caught
and logged - a cache outage must never take down a request.

Usage
-----
    from cache import cached_json, invalidate

    @cached_json(lambda tenant_id: f"dash:tpo:{tenant_id}", ttl_seconds=30)
    def tpo_dashboard(tenant_id: int) -> dict:
        ...  # expensive aggregation

    # on write:
    invalidate(f"dash:tpo:{tenant_id}")
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from typing import Any

from app.core.config import settings

logger = logging.getLogger("campus_ai.cache")

try:  # redis is an optional dependency; the app runs fine without it.
    import redis as _redis
except Exception:  # pragma: no cover - import guard
    _redis = None  # type: ignore[assignment]


def _make_client():
    """Build a Redis client, or None when caching is disabled/unavailable."""
    if not settings.REDIS_URL or _redis is None:
        return None
    try:
        client = _redis.Redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_timeout=1.0
        )
        client.ping()
        logger.info("Cache backend: Redis.")
        return client
    except Exception as exc:  # noqa: BLE001 - degrade to no-op, never fatal
        logger.warning("Redis cache unavailable, using no-op cache: %s", exc)
        return None


_client = _make_client()


def enabled() -> bool:
    """True when a live Redis cache is backing the helpers."""
    return _client is not None


def ping() -> bool:
    """Best-effort Redis liveness check for readiness probes.

    Returns False when caching is simply disabled (no client). Raises when a
    client exists but the server is unreachable, so health checks can surface
    the failure (see health.py).
    """
    if _client is None:
        return False
    _client.ping()
    return True


def get_json(key: str) -> Any | None:
    """Return the cached JSON value for `key`, or None on miss/disabled/error."""
    if _client is None:
        return None
    try:
        raw = _client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache get failed (%s): %s", key, exc)
        return None


def set_json(key: str, value: Any, ttl_seconds: int = 60) -> None:
    """Store a JSON-serializable `value` under `key` with a TTL (no-op if off)."""
    if _client is None:
        return
    try:
        _client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache set failed (%s): %s", key, exc)


def invalidate(*keys: str) -> None:
    """Delete one or more cache keys (call this after the underlying write)."""
    if _client is None or not keys:
        return
    try:
        _client.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache invalidate failed: %s", exc)


def cached_json(
    key_fn: Callable[..., str], ttl_seconds: int = 60
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Cache a function's JSON-serializable return under ``key_fn(*args, **kwargs)``.

    On a hit the cached value is returned without calling the function; on a
    miss the function runs and its result is stored. With caching disabled the
    wrapper simply calls through, so behaviour is identical minus the speedup.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = key_fn(*args, **kwargs)
            hit = get_json(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            set_json(key, result, ttl_seconds)
            return result

        return wrapper

    return decorator
