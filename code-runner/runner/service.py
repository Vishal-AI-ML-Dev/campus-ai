"""Admission control, bounded queue, idempotent replays, self-check state and the orphan reaper.

- Bounded concurrency: at most ``max_concurrency`` executions run; at most ``queue_max`` wait; beyond that the
  request is refused with 503 + Retry-After (never queued without bound).
- Idempotency: ``execution_id`` is the key. A replay with the same request fingerprint within 10 minutes returns the
  cached result without executing again (an in-flight duplicate waits for the first). The same id with a different
  request is a 409. Only a SHA-256 fingerprint of the request is kept, never the source; cached responses live in
  process memory only and expire after 10 minutes.
- Fail closed: the runner accepts work only while ``RUNNER_ACCEPTING`` is true, the last self-check passed and the
  reaper is healthy.
- Reaper: removes every runner-managed container older than 120 s (no execution can legitimately live that long);
  at startup it removes all of them (recovery after a runner crash). If removal fails the runner turns ``degraded``
  and refuses new work until a sweep succeeds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .executor import ExecutionLog, Executor
from .observability import Metrics, log_event, log_execution
from .pack import PackMismatch
from .sandbox import DockerCLI, SandboxFault, SelfCheck
from .schemas import ExecutionRequest

REPLAY_TTL_S = 600
REPLAY_MAX_ENTRIES = 1000
ORPHAN_AGE_S = 120
RETRY_AFTER_S = 5


@dataclass
class Rejection(Exception):
    status: int
    reason: str
    retry_after: int | None = None


def fingerprint(req: ExecutionRequest) -> str:
    return hashlib.sha256(json.dumps(req.model_dump(), sort_keys=True).encode("utf-8")).hexdigest()


class Runner:
    def __init__(
        self,
        executor: Executor,
        docker: DockerCLI,
        metrics: Metrics,
        *,
        accepting: bool,
        max_concurrency: int,
        queue_max: int,
        self_check: Callable[[], SelfCheck],
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.executor = executor
        self.docker = docker
        self.metrics = metrics
        self.accepting = accepting
        self.max_concurrency = max_concurrency
        self.queue_max = queue_max
        self._self_check = self_check
        self.clock = clock
        self.wall_clock = wall_clock
        self.self_check_ok = False
        self.reaper_ok = False
        self._sem: asyncio.Semaphore | None = None
        self._pool = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="exec")
        self._cache: OrderedDict[str, tuple[str, float, dict]] = OrderedDict()
        self._inflight: dict[str, tuple[str, asyncio.Future]] = {}
        self._waiting = 0
        self._busy = 0
        self._sync_gauges()

    # ------------------------------------------------------------------ state
    @property
    def serving(self) -> bool:
        return self.accepting and self.self_check_ok and self.reaper_ok

    @property
    def healthy(self) -> bool:
        return self.self_check_ok and self.reaper_ok

    def _sync_gauges(self) -> None:
        self.metrics.busy, self.metrics.queued = self._busy, self._waiting
        self.metrics.accepting = int(self.serving)
        self.metrics.degraded = int(not self.healthy)

    def run_self_check(self) -> SelfCheck:
        result = self._self_check()
        self.self_check_ok = result.ok
        for failure in result.failures:
            log_event("self_check_failed", failure)
        if result.ok:
            log_event("self_check_passed")
        self._sync_gauges()
        return result

    def reap(self, startup: bool = False) -> int:
        """Remove stale runner containers. Returns how many stale containers were found."""
        try:
            managed = self.docker.list_managed()
        except SandboxFault:
            self.reaper_ok = False
            log_event("reaper_failed", "docker_unavailable")
            self._sync_gauges()
            return 0
        now = int(self.wall_clock())
        stale = [cid for cid, created in managed if startup or now - created > ORPHAN_AGE_S]
        failed = [cid for cid in stale if not self.docker.remove(cid)]
        self.metrics.orphans = len(stale)
        self.reaper_ok = not failed
        if stale:
            log_event("reaper_removed", "startup" if startup else "stale", len(stale) - len(failed))
        if failed:
            log_event("reaper_failed", "remove_failed", len(failed))
        self._sync_gauges()
        return len(stale)

    # ------------------------------------------------------------------ requests
    def _cached(self, execution_id: str, fp: str) -> dict | None:
        now = self.clock()
        for key in [k for k, (_, exp, _) in self._cache.items() if exp <= now]:
            del self._cache[key]
        hit = self._cache.get(execution_id)
        if hit is None:
            return None
        if hit[0] != fp:
            raise Rejection(409, "replay_conflict")
        return hit[2]

    def _remember(self, execution_id: str, fp: str, body: dict) -> None:
        self._cache[execution_id] = (fp, self.clock() + REPLAY_TTL_S, body)
        self._cache.move_to_end(execution_id)
        while len(self._cache) > REPLAY_MAX_ENTRIES:
            self._cache.popitem(last=False)

    async def submit(self, req: ExecutionRequest, request_id: str) -> dict:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrency)
        fp = fingerprint(req)
        cached = self._cached(req.execution_id, fp)
        if cached is not None:
            return cached
        inflight = self._inflight.get(req.execution_id)
        if inflight is not None:
            if inflight[0] != fp:
                raise Rejection(409, "replay_conflict")
            return await asyncio.shield(inflight[1])
        if not self.healthy:
            raise Rejection(503, "degraded", RETRY_AFTER_S)
        if not self.accepting:
            raise Rejection(503, "not_accepting", RETRY_AFTER_S)
        if self._busy + self._waiting >= self.max_concurrency + self.queue_max:
            raise Rejection(503, "queue_full", RETRY_AFTER_S)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        future.add_done_callback(lambda f: f.cancelled() or f.exception())  # never "exception never retrieved"
        self._inflight[req.execution_id] = (fp, future)
        try:
            body = await self._execute(req, request_id, loop)
        except PackMismatch:
            rejection = Rejection(409, "pack_mismatch")
            future.set_exception(rejection)
            raise rejection from None
        except BaseException:
            if not future.done():
                future.set_exception(Rejection(500, "internal"))
            raise
        else:
            self._remember(req.execution_id, fp, body)
            future.set_result(body)
            return body
        finally:
            self._inflight.pop(req.execution_id, None)

    async def _execute(self, req: ExecutionRequest, request_id: str, loop: asyncio.AbstractEventLoop) -> dict:
        assert self._sem is not None
        self._waiting += 1
        self._sync_gauges()
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1
        self._busy += 1
        self._sync_gauges()
        try:
            response, log = await loop.run_in_executor(self._pool, self.executor.run, req, request_id)
        finally:
            self._busy -= 1
            self._sem.release()
            self._sync_gauges()
        self._record(log)
        return response.wire()

    def _record(self, log: ExecutionLog) -> None:
        log_execution(log)
        self.metrics.record_execution(log)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
