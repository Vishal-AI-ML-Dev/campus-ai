"""Runner configuration from the environment. Invalid configuration refuses to start (fail closed).

``RUNNER_TOKENS``        comma-separated bearer tokens (current[,next]) for rotation; each >= 32 characters; 1-3.
``METRICS_TOKEN``        bearer token for ``/metrics`` (>= 32 characters, different from every runner token);
                         empty -> ``/metrics`` answers 404.
``RUNNER_ACCEPTING``     ``true`` to accept work; anything else (including unset) -> 503 for new work.
``RUNNER_MAX_CONCURRENCY`` executions at once (default 2, 1-8).
``RUNNER_QUEUE_MAX``     waiting executions (default 20, 0-200); beyond -> 503 with Retry-After.
``RUNNER_PACK_DIR``      the installed, read-only test pack directory (required).
``RUNNER_DOCKER_BIN``    absolute path of the docker CLI (default /usr/bin/docker).

Nothing else is read from the environment. Token values are never logged or echoed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MIN_TOKEN_LEN = 32


class ConfigError(Exception):
    """Fixed message only; never contains a configured value."""


@dataclass(frozen=True)
class RunnerConfig:
    tokens: tuple[str, ...] = field(repr=False)
    metrics_token: str | None = field(repr=False)
    accepting: bool
    max_concurrency: int
    queue_max: int
    pack_dir: Path
    docker_bin: str


def _int(env: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or not lo <= int(raw) <= hi:
        raise ConfigError(f"{name} must be an integer in {lo}-{hi}")
    return int(raw)


def load_config(env: dict | None = None) -> RunnerConfig:
    env = dict(os.environ if env is None else env)
    tokens = tuple(t.strip() for t in env.get("RUNNER_TOKENS", "").split(",") if t.strip())
    if not 1 <= len(tokens) <= 3:
        raise ConfigError("RUNNER_TOKENS must list 1-3 tokens")
    if any(len(t) < MIN_TOKEN_LEN or not t.isascii() or not t.isprintable() for t in tokens):
        raise ConfigError("every runner token must be at least 32 printable ASCII characters")
    metrics = env.get("METRICS_TOKEN", "").strip() or None
    if metrics is not None and (len(metrics) < MIN_TOKEN_LEN or metrics in tokens):
        raise ConfigError("METRICS_TOKEN must be at least 32 characters and differ from the runner tokens")
    pack = env.get("RUNNER_PACK_DIR", "").strip()
    if not pack:
        raise ConfigError("RUNNER_PACK_DIR is required")
    docker_bin = env.get("RUNNER_DOCKER_BIN", "/usr/bin/docker").strip()
    if not (docker_bin.startswith("/") or os.path.isabs(docker_bin)):
        raise ConfigError("RUNNER_DOCKER_BIN must be an absolute path")
    return RunnerConfig(
        tokens=tokens,
        metrics_token=metrics,
        accepting=env.get("RUNNER_ACCEPTING", "").strip().lower() == "true",
        max_concurrency=_int(env, "RUNNER_MAX_CONCURRENCY", 2, 1, 8),
        queue_max=_int(env, "RUNNER_QUEUE_MAX", 20, 0, 200),
        pack_dir=Path(pack),
        docker_bin=docker_bin,
    )
