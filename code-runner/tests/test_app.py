"""HTTP surface: authentication, size limits, strict schema without echo, replay/idempotency, back-pressure,
health, protected metrics, content-free logs, and the reaper."""

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from fakes import FakeClock, FakeDocker
from runner.app import create_app
from runner.config import load_config
from runner.executor import Executor
from runner.observability import Metrics, configure_logging
from runner.sandbox import SelfCheck
from runner.schemas import MAX_BODY_BYTES
from runner.service import Runner

import synthetic

TOKEN = "r" * 40
NEXT_TOKEN = "s" * 40
METRICS = "m" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class CountingExecutor(Executor):
    calls = 0

    def run(self, req, request_id):
        CountingExecutor.calls += 1
        return super().run(req, request_id)


def build(pack, *, accepting=True, self_check_ok=True, queue_max=20, max_concurrency=2, metrics_token=METRICS,
          docker=None):
    cfg = load_config({
        "RUNNER_TOKENS": f"{TOKEN},{NEXT_TOKEN}", "RUNNER_PACK_DIR": "/srv/pack",
        "METRICS_TOKEN": metrics_token or "", "RUNNER_ACCEPTING": "true" if accepting else "false",
        "RUNNER_QUEUE_MAX": str(queue_max), "RUNNER_MAX_CONCURRENCY": str(max_concurrency),
    })
    clock = FakeClock()
    docker = docker or FakeDocker(clock)
    CountingExecutor.calls = 0
    executor = CountingExecutor(docker, pack, clock=clock)
    metrics = Metrics()
    runner = Runner(executor, docker, metrics, accepting=cfg.accepting, max_concurrency=cfg.max_concurrency,
                    queue_max=cfg.queue_max, self_check=lambda: SelfCheck(ok=self_check_ok))
    runner.run_self_check()
    runner.reap(startup=True)
    app = create_app(cfg, runner, metrics, background=False)
    return TestClient(app), runner, metrics, docker


def body(**kw):
    base = {"execution_id": str(uuid.uuid4()), "kind": "visible", "problem_key": synthetic.KEY, "problem_version": 1,
            "test_set": "visible", "language": "python313", "source": "ECHO_FIRST"}
    base.update(kw)
    return base


def test_happy_path_and_token_rotation(pack):
    client, *_ = build(pack)
    for token in (TOKEN, NEXT_TOKEN):
        r = client.post("/v1/executions", json=body(), headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["verdict"] == "accepted"
        assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong" + "x" * 40}, {"Authorization": TOKEN},
                                     {"Authorization": f"Basic {TOKEN}"}, {"Authorization": f"Bearer {METRICS}"}])
def test_bad_or_missing_token_gets_a_generic_401(pack, headers):
    client, *_ = build(pack)
    r = client.post("/v1/executions", json=body(source=synthetic.SENTINEL_INPUT), headers=headers)
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    assert TOKEN not in r.text and synthetic.SENTINEL_INPUT not in r.text
    assert CountingExecutor.calls == 0


def test_oversized_body_is_413_before_parsing(pack):
    client, *_ = build(pack)
    big = json.dumps(body(source="a" * (MAX_BODY_BYTES + 10)))
    r = client.post("/v1/executions", content=big, headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == {"error": "too_large"}
    assert CountingExecutor.calls == 0


def test_oversized_streamed_body_without_length_is_413(pack):
    client, *_ = build(pack)

    def chunks():
        for _ in range(20):
            yield b"x" * 8192

    r = client.post("/v1/executions", content=chunks(), headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("override", [
    {"memory_mb": 4096}, {"docker_args": ["--privileged"]}, {"unknown": 1}, {"language": "ruby"},
    {"kind": "graded"}, {"problem_version": "1"}, {"source": ""},
])
def test_invalid_requests_get_422_without_echo(pack, override):
    client, *_ = build(pack)
    payload = body(source=f"print('{synthetic.SENTINEL_INPUT}')")
    payload.update(override)
    r = client.post("/v1/executions", json=payload, headers=AUTH)
    assert r.status_code == 422 and r.json() == {"error": "invalid_request"}
    assert synthetic.SENTINEL_INPUT not in r.text
    assert CountingExecutor.calls == 0


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"{", b'"string"', b"\xff\xfe"])
def test_malformed_json_gets_422(pack, raw):
    client, *_ = build(pack)
    r = client.post("/v1/executions", content=raw, headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 422 and r.json() == {"error": "invalid_request"}


def test_41st_test_cannot_be_requested(pack):
    """The request has no way to add tests; any test list field is an unknown field."""
    client, *_ = build(pack)
    r = client.post("/v1/executions", json=body(tests=[{"input": "x"}] * 41), headers=AUTH)
    assert r.status_code == 422


def test_replay_is_idempotent_and_conflicting_reuse_is_409(pack):
    client, *_ = build(pack)
    b = body()
    first = client.post("/v1/executions", json=b, headers=AUTH)
    second = client.post("/v1/executions", json=b, headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert CountingExecutor.calls == 1  # the replay did not execute again
    other = client.post("/v1/executions", json=dict(b, source="WRONG"), headers=AUTH)
    assert other.status_code == 409 and other.json() == {"error": "replay_conflict"}
    assert CountingExecutor.calls == 1


def test_pack_mismatch_is_409(pack):
    client, *_ = build(pack)
    r = client.post("/v1/executions", json=body(kind="graded", test_set="hidden", hidden_digest="0" * 64), headers=AUTH)
    assert r.status_code == 409 and r.json() == {"error": "pack_mismatch"}
    r = client.post("/v1/executions", json=body(problem_key="unknown-problem"), headers=AUTH)
    assert r.status_code == 409


@pytest.mark.parametrize("kw,reason", [({"accepting": False}, "not_accepting"), ({"self_check_ok": False}, "degraded"),
                                       ({"queue_max": 0, "max_concurrency": 1}, None)])
def test_back_pressure_and_fail_closed(pack, kw, reason):
    client, runner, *_ = build(pack, **kw)
    if reason is None:  # queue full: one execution is running and nothing may wait
        runner._busy = 1
        reason = "queue_full"
    r = client.post("/v1/executions", json=body(), headers=AUTH)
    assert r.status_code == 503 and r.json() == {"error": reason} and r.headers["retry-after"] == "5"
    assert CountingExecutor.calls == 0


def test_healthz_reveals_nothing(pack):
    client, runner, *_ = build(pack)
    assert client.get("/healthz").json() == {"status": "ok"}
    runner.self_check_ok = False
    assert client.get("/healthz").json() == {"status": "degraded"}


def test_metrics_are_protected_and_content_free(pack_dir, pack):
    client, *_ = build(pack)
    client.post("/v1/executions", json=body(kind="graded", test_set="hidden", source="LEAK_INPUT",
                                            hidden_digest=synthetic.hidden_digest(pack_dir)), headers=AUTH)
    client.post("/v1/executions", json=body(), headers={"Authorization": "Bearer nope"})
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers=AUTH).status_code == 401  # the runner token is not the metrics token
    r = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS}"})
    assert r.status_code == 200
    assert 'runner_executions_total{language="python313",kind="graded",verdict="runtime_error"} 1' in r.text
    assert 'runner_rejections_total{reason="unauthorized"} 1' in r.text
    for s in (*synthetic.SENTINELS, TOKEN, METRICS, "LEAK_INPUT", synthetic.KEY):
        assert s not in r.text


def test_metrics_disabled_without_token(pack):
    client, *_ = build(pack, metrics_token=None)
    assert client.get("/metrics", headers={"Authorization": f"Bearer {METRICS}"}).status_code == 404


def test_docs_and_openapi_are_not_served(pack):
    client, *_ = build(pack)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_logs_are_one_content_free_line_per_execution(pack_dir, pack, capsys):
    configure_logging()
    try:
        client, *_ = build(pack)
        client.post("/v1/executions", json=body(kind="graded", test_set="hidden", source="LEAK_INPUT",
                                                hidden_digest=synthetic.hidden_digest(pack_dir)),
                    headers={**AUTH, "X-Request-ID": "req-123"})
        client.post("/v1/executions", json=body(source=f"ECHO_FIRST {synthetic.SENTINEL_REFERENCE}"),
                    headers={**AUTH, "X-Request-ID": f"bad id {synthetic.SENTINEL_NAME}"})
        out = capsys.readouterr().out
    finally:
        logging.getLogger("campus_ai_runner").handlers.clear()
    lines = [json.loads(line) for line in out.splitlines() if '"event": "execution"' in line]
    assert len(lines) == 2
    assert lines[0]["request_id"] == "req-123" and lines[1]["request_id"] == "none"
    assert set(lines[0]) == {"event", "execution_id", "request_id", "language", "kind", "verdict", "outcome",
                             "limit_hit", "error_class", "compile_ms", "run_ms", "tests_run", "pack_digest"}
    for s in (*synthetic.SENTINELS, TOKEN, NEXT_TOKEN, METRICS, "LEAK_INPUT", "ECHO_FIRST"):
        assert s not in out


# --- reaper -----------------------------------------------------------------------------------------------------


def _argv():
    from runner import pins
    from runner.sandbox import ContainerSpec, container_argv

    return container_argv(ContainerSpec(image=pins.IMAGE_GCC, memory_mb=256, pids=64, nofile=64, cpu_rlimit_s=1,
                                        user=65534, name="cr-r-x", created_epoch=0))


def test_reaper_removes_stale_containers_and_startup_removes_all(pack):
    clock = FakeClock()
    docker = FakeDocker(clock)
    for _ in range(3):
        docker.create(_argv())
    metrics = Metrics()
    runner = Runner(Executor(docker, pack, clock=clock), docker, metrics, accepting=True, max_concurrency=2,
                    queue_max=2, self_check=lambda: SelfCheck(ok=True), wall_clock=lambda: 10_000)
    assert runner.reap(startup=True) == 3
    assert docker.containers == {} and runner.reaper_ok


def test_reaper_failure_turns_the_runner_degraded(pack):
    clock = FakeClock()
    docker = FakeDocker(clock, fail_remove=True)
    docker.create(_argv())
    client, runner, metrics, _ = build(pack, docker=docker)
    assert not runner.reaper_ok and runner.healthy is False
    r = client.post("/v1/executions", json=body(), headers=AUTH)
    assert r.status_code == 503 and r.json() == {"error": "degraded"}
    assert "runner_degraded 1" in metrics.render()
