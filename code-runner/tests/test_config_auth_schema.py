"""Configuration fails closed; tokens are compared in constant time; the request schema is strict."""

import json
import uuid

import pytest
from pydantic import ValidationError

from runner.auth import bearer_ok
from runner.config import ConfigError, load_config
from runner.schemas import MAX_SOURCE_BYTES, ExecutionRequest

T1 = "t" * 40
T2 = "u" * 40
M = "m" * 40


def env(**kw):
    base = {"RUNNER_TOKENS": T1, "RUNNER_PACK_DIR": "/srv/pack"}
    base.update(kw)
    return base


# --- configuration ---------------------------------------------------------------------------------------------


def test_defaults_fail_closed():
    cfg = load_config(env())
    assert cfg.accepting is False  # must be switched on explicitly
    assert cfg.metrics_token is None
    assert (cfg.max_concurrency, cfg.queue_max) == (2, 20)
    assert T1 not in repr(cfg)


@pytest.mark.parametrize("bad", [
    {"RUNNER_TOKENS": ""},
    {"RUNNER_TOKENS": "short"},
    {"RUNNER_TOKENS": ",".join(["x" * 40] * 4)},
    {"METRICS_TOKEN": "short"},
    {"METRICS_TOKEN": T1},  # must differ from the runner tokens
    {"RUNNER_PACK_DIR": ""},
    {"RUNNER_DOCKER_BIN": "docker"},
    {"RUNNER_MAX_CONCURRENCY": "0"},
    {"RUNNER_QUEUE_MAX": "1000"},
])
def test_invalid_configuration_refuses_to_start(bad):
    with pytest.raises(ConfigError) as info:
        load_config(env(**bad))
    for value in bad.values():
        if len(value) >= 8:
            assert value not in str(info.value)  # never echo a configured value


def test_accepting_only_when_explicitly_true():
    assert load_config(env(RUNNER_ACCEPTING="true")).accepting is True
    assert load_config(env(RUNNER_ACCEPTING="1")).accepting is False


# --- authentication ---------------------------------------------------------------------------------------------


def test_bearer_ok_and_rotation():
    tokens = (T1, T2)
    assert bearer_ok(f"Bearer {T1}", tokens)
    assert bearer_ok(f"Bearer {T2}", tokens)  # current + next during rotation
    assert bearer_ok(f"bearer {T1}", tokens)
    for header in (None, "", T1, f"Basic {T1}", f"Bearer {T1[:-1]}", f"Bearer {T1}x", "Bearer ", f"Bearer {M}"):
        assert not bearer_ok(header, tokens)


def test_auth_uses_constant_time_comparison():
    import inspect

    import runner.auth as auth

    src = inspect.getsource(auth.bearer_ok)
    assert "compare_digest" in src and "==" not in src.split('"""')[-1]


# --- request schema ---------------------------------------------------------------------------------------------


def req(**kw):
    base = {
        "execution_id": str(uuid.uuid4()), "kind": "visible", "problem_key": "synthetic-echo", "problem_version": 1,
        "test_set": "visible", "language": "python313", "source": "print(1)",
    }
    base.update(kw)
    return base


def test_valid_requests():
    ExecutionRequest.model_validate(req())
    ExecutionRequest.model_validate(req(kind="custom", custom_input="1 2\n"))
    ExecutionRequest.model_validate(req(kind="graded", test_set="hidden", hidden_digest="a" * 64))


@pytest.mark.parametrize("field,value", [
    ("memory_mb", 4096), ("cpu_ms", 99999), ("time_limit_ms", 1), ("image", "ubuntu"), ("runtime", "runc"),
    ("docker_args", ["--privileged"]), ("compiler_flags", "-O3"), ("mounts", ["/:/host"]), ("network", "host"),
    ("privileged", True), ("expected_output", "1"), ("tests", [{"input": "1"}]), ("pack_path", "/srv/pack"),
    ("env", {"A": "1"}), ("user", "root"), ("reference_solution", "x"),
])
def test_caller_cannot_control_execution_settings(field, value):
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(req(**{field: value}))


@pytest.mark.parametrize("override", [
    {"execution_id": "not-a-uuid"},
    {"execution_id": str(uuid.uuid1())},
    {"problem_key": "../etc/passwd"},
    {"problem_key": "Bad Key"},
    {"problem_version": "1"},  # strict types
    {"problem_version": 0},
    {"language": "ruby"},
    {"kind": "admin"},
    {"source": ""},
    {"source": "a" * (MAX_SOURCE_BYTES + 1)},
    {"source": "print(1)\x00"},
    {"kind": "custom"},  # needs custom_input
    {"kind": "custom", "custom_input": "x" * (16 * 1024 + 1)},
    {"kind": "graded", "test_set": "hidden"},  # needs hidden_digest
    {"kind": "graded", "test_set": "visible", "hidden_digest": "a" * 64},
    {"kind": "visible", "test_set": "hidden"},
    {"kind": "visible", "hidden_digest": "a" * 64},
    {"kind": "graded", "test_set": "hidden", "hidden_digest": "A" * 64},
])
def test_invalid_requests_are_rejected(override):
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(req(**override))


def test_source_limit_counts_bytes_not_characters():
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(req(source="é" * (MAX_SOURCE_BYTES // 2 + 1)))


def test_schema_has_no_execution_setting_fields():
    fields = set(ExecutionRequest.model_fields)
    assert fields == {"execution_id", "kind", "problem_key", "problem_version", "test_set", "language", "source",
                      "custom_input", "hidden_digest"}
    assert json.dumps(sorted(fields))  # documented contract
