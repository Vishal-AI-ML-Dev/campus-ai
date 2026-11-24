# Campus AI code runner (Phase 8.5b)

A standalone service that compiles and runs coding-interview submissions inside **gVisor (`runsc`) containers** and
judges them against a private, digest-verified test pack. It is **not connected to the backend** yet (that is 8.6,
behind `AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED`, default off) and it has **not executed any student code**.

Status (2026-11-24): code and synthetic test suites complete. The real escape/conformance suite has **not run**:
it needs a verified Linux host with the pinned Docker + gVisor (CI job `sandbox`, pending a push approval, or the
8.5c VM). **G2 is not passed.** Spec: `docs/mock-interview/specs/phase-8-advanced-ai-intelligence.md` §4, §11.5, §18.

## Trust boundaries

```
backend (8.6, not built) --HTTPS + bearer--> TRUSTED LAYER (this service, runner user, no Campus AI secrets)
                                               auth -> strict schema -> bounded queue -> executor
                                               test pack (read-only, digest-verified), checkers, reduction
                                               ------------------- docker CLI (arg arrays only) ------------
                                             UNTRUSTED: fresh gVisor containers, no network, no secrets,
                                               non-root, read-only root, tmpfs /workspace, hard limits
```

- Only `runner/sandbox.py` starts processes, always with argument arrays (no shell). Request data travels only as
  bytes on a process's stdin (source via `dd`, test input to the program); never on a command line.
- Hidden inputs and expected outputs stay in the trusted layer. A program only ever sees one test's stdin at a time.
- The Docker socket is never mounted into a container; the runner is the only Docker client.

## Execution flow

1. Verify token (constant time), body size (<= 96 KB), strict schema (unknown fields -> 422), problem/version in the
   installed pack, and for graded runs the exact hidden test-set digest (-> 409 on mismatch).
2. Compile container (compile limits, uid 65533): write source, compile with the fixed command, make `src/` and
   `build/` read-only, export them as an opaque tar kept in memory, remove the container.
3. Run container (run limits; its own init/keep-alive processes run as uid 65533): restore the tar (owned by 65533,
   read-only to 65534); for each test run the program as uid 65534, stream stdin, capture <= 64 KB per stream,
   enforce per-test wall (2.5 x CPU) and the 30 s submission budget. Uid 65534 cannot signal the container's own
   processes, so neither the cleanup nor a hostile program can stop the container from inside.
4. Any limit -> `docker kill` of the whole container (cgroup = process-tree kill), fresh container for the next test.
   Normal exit -> kill every run-uid process (`kill -9 -1`) and wipe run-uid files; if that cannot be proven, discard
   the container.
5. Judge with the problem checker (here, never in the sandbox); reduce hidden results; remove all containers.

## API (internal, v1)

`POST /v1/executions` (bearer: one of `RUNNER_TOKENS`)

```json
{"execution_id": "uuid-v4", "kind": "visible|custom|graded", "problem_key": "pair-with-target-sum",
 "problem_version": 1, "test_set": "visible|hidden", "language": "python313|cpp17|java21|c11",
 "source": "<= 64 KB", "custom_input": "<= 16 KB, custom only", "hidden_digest": "64 hex, graded only"}
```

`hidden_digest` is an addition to spec §4.6: it binds a graded run to the exact hidden test set named by the backend's
public bundle. There are no fields for limits, flags, images, runtimes, mounts, paths or expected outputs.

Response 200: `execution_id`, `runner_version`, `pack_digest`, `compile {ok, message_trunc (<= 8 KB, paths
scrubbed), time_ms}`, `verdict`, then exactly one of `visible` (visible/custom: per test `verdict`, `stdout_trunc`
and `stderr_trunc` <= 4 KB, `time_ms`, `memory_kb: null`), `hidden_summary` (graded: `groups {basic|edge|performance:
[passed, total]}`, `first_failure {group, class}`, `max_time_ms`, `max_memory_kb: null`) or `sandbox_error {class}`.
Timings are rounded to 10 ms. Per-test memory is not measured in v1 (`null`).

Errors (fixed bodies `{"error": "<code>"}`, never echoing input): 401 `unauthorized`, 413 `too_large`,
422 `invalid_request`, 409 `pack_mismatch` / `replay_conflict`, 503 `not_accepting` / `degraded` / `queue_full`
(+ `Retry-After`), 500 `internal`.

`GET /healthz` -> `{"status": "ok" | "degraded"}` (no auth, no details). `GET /metrics` -> bearer `METRICS_TOKEN`
(separate from runner tokens); 404 when unset. No docs/OpenAPI endpoints.

Replays: same `execution_id` + same request within 10 minutes -> cached result, no second execution; same id with a
different request -> 409. Only a SHA-256 fingerprint is kept; cached responses live only in memory.

## Verdicts and the error model (spec §3.3, U4)

`accepted`, `wrong_answer`, `compile_error`, `runtime_error`, `time_limit`, `memory_limit`, `output_limit` are the
candidate's results. Tests not started because the 30 s budget ran out count as `time_limit` (U4). `sandbox_error`
(classes `container_start_failed`, `exec_failed`, `restore_failed`, `unsafe_configuration`, `docker_unavailable`,
`internal`) means the runner failed: the whole execution is incomplete and must never be scored or chosen as best.
A program's own exit codes 125-127 are classified as `runtime_error` unless the container is proven unhealthy, so a
student cannot fake an infrastructure fault. `memory_limit` = cgroup OOM, or the runtime's own out-of-memory error
(Java `OutOfMemoryError`, Python `MemoryError`, C++ `std::bad_alloc`).

## Limits (P8-S3 ceilings; calibration may only tighten)

| | C11 / C++17 | Java 21 | Python 3.13 |
|---|---|---|---|
| CPU per test | time_ms x 1 | time_ms x 2 | time_ms x 3 (all <= 6 s; RLIMIT_CPU, whole seconds) |
| Wall per test | 2.5 x CPU | 2.5 x CPU | 2.5 x CPU |
| Memory (standard / large) | 256 / 512 MB | 512 / 768 MB (`-Xmx` = profile - 64 MB) | 256 / 512 MB |
| Compile wall / memory | 10 s / 512 MB | 15 s / 768 MB (`javac -proc:none`) | 5 s / 256 MB (syntax check) |
| pids / nofile | 64 / 64 | 128 / 256 | 64 / 64 |

All: 30 s per submission, 64 KB per output stream, <= 40 tests, fsize 16 MB, core 0, tmpfs 64 MB, 1 CPU, no swap, no
network. `limits.test_limits` rejects any calibration factor above 1.

## Container hardening (enforced by `sandbox.assert_hardened` before every `docker run`)

`--runtime=runsc` (gVisor; daemon runtimeArgs `--platform=systrap --network=none`), `--network=none`, `--ipc=none`,
`--cap-drop=ALL`, `--security-opt=no-new-privileges`, non-root `--user`, `--read-only`, `--tmpfs /workspace`
(nosuid, nodev, 64 MB), `--memory` = `--memory-swap`, `--pids-limit`, `--ulimit` nofile/fsize/core/cpu, `--cpus=1`,
`--init`, `--log-driver=none`, `--pull=never`, image by `@sha256` digest. Refused: `--privileged`, volumes, mounts,
devices, `--cap-add`, host namespaces, published ports, extra env, unconfined seccomp/AppArmor, any other runtime or
network. There is no fallback to `runc` and no Judge0.

## Pins (`runner/pins.py`, verified 2026-11-24)

gVisor `release-20261102.0` (archive SHA-512 from Google's release bucket; `runsc` SHA-256 checked at startup);
Docker Engine `29.9.3` / containerd.io `2.4.2-3` (Ubuntu 24.04 packages, SHA-256 from Docker's apt index);
images `gcc:14.4.0`, `python:3.13.17-slim-bookworm`, `eclipse-temurin:21.0.12.1_1-jdk-noble`, each by index digest
(Docker Hub tag API, confirmed against the registry). The self-check refuses to serve on any mismatch.

## Configuration

`RUNNER_TOKENS` (1-3 tokens, each >= 32 chars; current + next for rotation), `METRICS_TOKEN` (>= 32 chars, different;
empty = no `/metrics`), `RUNNER_ACCEPTING` (`true` to accept; default off), `RUNNER_MAX_CONCURRENCY` (2),
`RUNNER_QUEUE_MAX` (20), `RUNNER_PACK_DIR` (installed read-only pack), `RUNNER_DOCKER_BIN` (`/usr/bin/docker`).
Invalid configuration refuses to start. No token is ever logged. Production entry point: `runner.app:build_from_env`
(uvicorn `--factory`, no access log); operations: `docs/mock-interview/runbooks/code-runner.md`.

## Privacy and observability (P8-S5)

One JSON log line per execution with only: execution id, request id, language, kind, verdict, outcome, limit hit,
error class, compile/run ms, tests run, pack digest. Never source, input, output, stderr, hidden data or tokens
(values are re-validated against enums before logging). Metrics: `runner_executions_total{language,kind,verdict}`,
`runner_rejections_total{reason}`, `runner_sandbox_errors_total{class}`, `runner_busy`, `runner_queued`,
`runner_orphans`, `runner_accepting`, `runner_degraded`. No tenant labels, no Sentry or other third-party telemetry.

## Tests

```
uv sync && uv run pytest -m "not gvisor"      # anywhere: schema, auth, config, hardening, self-check, executor
                                               # (scripted Docker), API, privacy sentinels, metrics, reaper, guards
RUNNER_GVISOR_E2E=1 uv run pytest tests/escape  # ONLY on the verified gVisor host (CI job `sandbox`, 8.5c VM)
```

The escape suite (`tests/escape/`) contains synthetic hostile programs (loops, threads, fork and memory bombs, output
floods, disk filling, host file/proc/socket probes, network/DNS/raw socket/metadata probes, ptrace/mount/unshare/
setuid, orphans and zombies, persistence markers, hidden-data sentinels, runner kill mid-execution). It refuses to
run unless the self-check passes; it must never be run on plain Docker or a developer machine.
