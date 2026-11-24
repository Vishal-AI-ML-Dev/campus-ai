"""HTTP surface of the runner (internal API v1, spec §4.6).

POST /v1/executions   bearer RUNNER_TOKENS; body <= 96 KB; strict schema; 200 result | 401 | 413 | 422 | 409 | 503
GET  /healthz         no auth; {"status": "ok" | "degraded"} only
GET  /metrics         bearer METRICS_TOKEN (separate from runner tokens); 404 when METRICS_TOKEN is empty

Error bodies are fixed: {"error": "<code>"}. They never echo request content (FastAPI's default 422 would reflect
the submitted source, so validation errors are handled here). No docs/OpenAPI endpoints, no CORS, no access log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

from .auth import bearer_ok
from .config import RunnerConfig
from .observability import Metrics, log_event, safe_request_id
from .schemas import MAX_BODY_BYTES, ExecutionRequest
from .service import Rejection, Runner

SELF_CHECK_INTERVAL_S = 300
REAPER_INTERVAL_S = 30


def _error(status: int, code: str, retry_after: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse({"error": code}, status_code=status, headers=headers)


async def _read_body(request: Request) -> bytes | None:
    """Read at most MAX_BODY_BYTES; None when the body is larger (checked before and while streaming)."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY_BYTES:
            return None
    return bytes(buf)


def create_app(config: RunnerConfig, runner: Runner, metrics: Metrics, background: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        if background:
            runner.reap(startup=True)
            await asyncio.to_thread(runner.run_self_check)
            log_event("runner_started", "accepting" if runner.serving else "not_accepting")
            tasks = [asyncio.create_task(_every(REAPER_INTERVAL_S, runner.reap)),
                     asyncio.create_task(_every(SELF_CHECK_INTERVAL_S, runner.run_self_check))]
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            runner.shutdown()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(Exception)
    async def _internal(_request: Request, _exc: Exception) -> JSONResponse:
        log_event("internal_error")
        return _error(500, "internal")

    @app.post("/v1/executions")
    async def executions(request: Request) -> JSONResponse:
        if not bearer_ok(request.headers.get("authorization"), config.tokens):
            metrics.record_rejection("unauthorized")
            return _error(401, "unauthorized")
        raw = await _read_body(request)
        if raw is None:
            metrics.record_rejection("too_large")
            return _error(413, "too_large")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            req = ExecutionRequest.model_validate(data)
        except (ValueError, ValidationError, UnicodeDecodeError):
            metrics.record_rejection("invalid_request")
            return _error(422, "invalid_request")
        try:
            body = await runner.submit(req, safe_request_id(request.headers.get("x-request-id")))
        except Rejection as r:
            if r.status != 500:
                metrics.record_rejection(r.reason)
            return _error(r.status, r.reason, r.retry_after)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok" if runner.healthy else "degraded"})

    @app.get("/metrics")
    async def metrics_endpoint(request: Request):
        if config.metrics_token is None:
            return _error(404, "not_found")
        if not bearer_ok(request.headers.get("authorization"), (config.metrics_token,)):
            return _error(401, "unauthorized")
        return PlainTextResponse(metrics.render(), headers={"Cache-Control": "no-store"})

    return app


async def _every(seconds: int, fn) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await asyncio.to_thread(fn)
        except Exception:  # noqa: BLE001 -- a failing sweep must not kill the loop; it is logged by name only
            log_event("background_task_failed")


def build_from_env() -> FastAPI:  # pragma: no cover - production entry point (Linux VM)
    from .config import load_config
    from .executor import Executor
    from .observability import configure_logging
    from .pack import load_pack
    from .sandbox import DockerCLI, self_check

    configure_logging()
    config = load_config()
    pack = load_pack(config.pack_dir)
    docker = DockerCLI(config.docker_bin)
    metrics = Metrics()
    executor = Executor(docker, pack, on_container_left=lambda _cid: log_event("container_left_for_reaper"))
    runner = Runner(
        executor, docker, metrics, accepting=config.accepting, max_concurrency=config.max_concurrency,
        queue_max=config.queue_max, self_check=lambda: self_check(docker),
    )
    return create_app(config, runner, metrics)
