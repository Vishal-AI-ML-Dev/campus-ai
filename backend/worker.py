"""RQ worker entry-point: drains the durable AI-scoring job queue.

Run this as a SEPARATE long-lived process from the API server, e.g.

    python worker.py
    # or, equivalently, the RQ CLI:
    #   rq worker campus-ai --url "$REDIS_URL" --with-scheduler

It shares the backend codebase and environment, so the job functions
(``ai_client.score_skill`` / ``ai_client.score_project_members``) open their own
DB session and call the AI worker exactly as before - just off the request path.

Requires ``REDIS_URL`` (plus the usual ``DATABASE_URL`` / ``AI_WORKER_URL`` env).
"""

from __future__ import annotations

import logging

import jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("campus_ai.worker")


def main() -> int:
    conn = jobs.get_redis()
    if conn is None:
        logger.error(
            "REDIS_URL is not set - the queue worker needs Redis to run. "
            "Set REDIS_URL (same value as the backend) and retry."
        )
        return 1

    from rq import Worker

    logger.info("Starting RQ worker on queue %r ...", jobs.QUEUE_NAME)
    worker = Worker([jobs.QUEUE_NAME], connection=conn)
    # with_scheduler=True runs RQ's built-in scheduler so the retry backoff
    # intervals (10s / 30s / 60s) are actually honoured.
    worker.work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
