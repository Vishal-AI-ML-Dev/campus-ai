"""Durable background-job queue (Redis + RQ) with an in-process fallback.

Why this exists
---------------
AI proof-scoring (skills/projects) used to run via FastAPI ``BackgroundTasks``,
which execute INSIDE the web-server process. That means:
  * a redeploy/crash mid-job silently loses the work (``ai_score`` never set),
  * there is no retry if the AI worker is cold-starting or briefly 5xx-ing,
  * heavy bursts compete with request handling on the same process.

When ``REDIS_URL`` is set we instead enqueue jobs onto a Redis-backed RQ queue
that a SEPARATE worker process drains (see ``worker.py``): durable across
restarts, retried with backoff, and isolated from request handling.

Graceful degradation
--------------------
With no ``REDIS_URL`` (local dev, the test-suite, or the simplest single-process
deploy) we transparently fall back to FastAPI's in-process ``BackgroundTasks`` -
behaviour is exactly as before and NOTHING new is required to run the app. The
queue is an opt-in reliability upgrade, not a hard dependency.

Note: this module is deliberately named ``jobs`` (not ``queue``) so it never
shadows Python's stdlib ``queue`` module that RQ/redis themselves import.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger("campus_ai.jobs")

# Single named queue for all AI-scoring work.
QUEUE_NAME = "campus-ai"
# Hard ceiling for one job (project scoring loops over every member). Generous
# because the worker's FIRST call can hit a cold-starting AI service.
JOB_TIMEOUT = 600
# Keep finished/failed job metadata around briefly for debugging.
RESULT_TTL = 3600
FAILURE_TTL = 86400

_redis_conn: Any = None
_queue: Any = None
_init_done = False


def _init() -> None:
    """Lazily build the Redis connection + RQ queue exactly once.

    Any problem (no REDIS_URL, missing deps, unreachable Redis) leaves both
    globals as ``None`` so callers transparently fall back to in-process work.
    """
    global _redis_conn, _queue, _init_done
    if _init_done:
        return
    _init_done = True
    if not settings.REDIS_URL:
        logger.info(
            "REDIS_URL not set - background jobs run in-process "
            "(FastAPI BackgroundTasks fallback)."
        )
        return
    try:
        from redis import Redis
        from rq import Queue

        _redis_conn = Redis.from_url(settings.REDIS_URL)
        _queue = Queue(QUEUE_NAME, connection=_redis_conn)
        logger.info("Durable job queue ready (Redis, queue=%r).", QUEUE_NAME)
    except Exception as exc:  # noqa: BLE001 - fall back to in-process on any error
        logger.warning(
            "Could not initialise Redis job queue (%s) - "
            "falling back to in-process BackgroundTasks.",
            exc,
        )
        _redis_conn = None
        _queue = None


def get_redis() -> Any:
    """Return the shared Redis connection (or None if the queue is disabled)."""
    _init()
    return _redis_conn


def get_queue() -> Any:
    """Return the RQ queue (or None when in in-process fallback mode)."""
    _init()
    return _queue


def _reset() -> None:
    """Reset the lazy singletons. Test-only hook (never used at runtime)."""
    global _redis_conn, _queue, _init_done
    _redis_conn = None
    _queue = None
    _init_done = False


def _retry() -> Any:
    """Build an RQ Retry policy, or None if RQ isn't importable.

    Kept defensive so the routing logic (and its unit tests) don't hard-depend
    on RQ being installed when the in-process fallback is the active path.
    """
    try:
        from rq import Retry

        return Retry(max=3, interval=[10, 30, 60])
    except Exception:  # noqa: BLE001
        return None


def enqueue(
    background_tasks: Any,
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    """Run ``func(*args, **kwargs)`` as a background job.

    Uses the durable Redis/RQ queue when configured (processed + retried by the
    separate ``worker.py`` process); otherwise falls back to FastAPI's
    in-process ``BackgroundTasks`` so the app still works with zero extra infra.

    ``func`` MUST be importable by module path (e.g. ``ai_client.score_skill``)
    because RQ serialises it by reference for the worker to import and call.
    """
    q = get_queue()
    if q is None:
        background_tasks.add_task(func, *args, **kwargs)
        return
    try:
        options: dict[str, Any] = {
            "job_timeout": JOB_TIMEOUT,
            "result_ttl": RESULT_TTL,
            "failure_ttl": FAILURE_TTL,
        }
        retry = _retry()
        if retry is not None:
            options["retry"] = retry
        q.enqueue(func, args=args, kwargs=kwargs, **options)
    except Exception as exc:  # noqa: BLE001 - never lose a job over a broker hiccup
        logger.warning(
            "Enqueue to Redis failed (%s) - running %s in-process instead.",
            exc,
            getattr(func, "__name__", func),
        )
        background_tasks.add_task(func, *args, **kwargs)
