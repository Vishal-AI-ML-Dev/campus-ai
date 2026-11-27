"""Executor orchestration with a scripted Docker: verdicts, limits, process-tree kills, U4, faults, cleanup and
hidden-result reduction. Nothing is executed (see fakes.py)."""

import json
import uuid

import pytest

from fakes import FakeClock, FakeDocker
from runner.executor import Executor
from runner.languages import BUILDER_UID, COMPILE_TMP_DIR, RUN_DIR, RUN_UID
from runner.pack import PackMismatch
from runner.schemas import ExecutionRequest

import synthetic


def make(pack, **docker_kw):
    clock = FakeClock()
    docker = FakeDocker(clock, **docker_kw)
    return Executor(docker, pack, clock=clock), docker


def request(source, kind="visible", language="python313", **kw):
    base = dict(execution_id=str(uuid.uuid4()), kind=kind, problem_key=synthetic.KEY, problem_version=1,
                test_set="hidden" if kind == "graded" else "visible", language=language, source=source)
    base.update(kw)
    return ExecutionRequest.model_validate(base)


def graded(pack_dir, source, **kw):
    return request(source, kind="graded", hidden_digest=synthetic.hidden_digest(pack_dir), **kw)


def wire(resp):
    return json.dumps(resp.wire())


def assert_all_removed(docker):
    assert set(docker.created) == set(docker.removed), "every container must be removed"
    assert docker.containers == {}


# --- visible runs ---------------------------------------------------------------------------------------------


def test_visible_accepted(pack):
    ex, docker = make(pack)
    resp, log = ex.run(request("ECHO_FIRST"), "rid-1")
    assert resp.verdict == "accepted"
    assert [v.verdict for v in resp.visible] == ["accepted", "accepted"]
    assert resp.visible[0].stdout_trunc == "alpha\n"
    assert resp.hidden_summary is None
    assert all(v.time_ms % 10 == 0 for v in resp.visible)  # rounded timings
    assert log.outcome == "completed" and log.verdict == "accepted" and log.tests_run == 2
    assert_all_removed(docker)


def test_compile_and_run_use_separate_users_and_containers(pack):
    ex, docker = make(pack)
    ex.run(request("ECHO_FIRST", language="cpp17"), "r")
    compile_cid = docker.created[0]
    run_cid = docker.created[1]
    assert {u for c, u, _ in docker.exec_log if c == compile_cid} == {BUILDER_UID}
    program_runs = [a for c, u, a in docker.exec_log if c == run_cid and a and a[0] == "/workspace/build/main"]
    assert program_runs and all(u == RUN_UID for c, u, a in docker.exec_log if c == run_cid and a[0] == "/workspace/build/main")
    # compile limits for the compile container, the run profile for the run container (spec §4.4)
    assert "--memory=512m" in docker.argvs[compile_cid] and "--user=65533:65533" in docker.argvs[compile_cid]
    assert "--memory=256m" in docker.argvs[run_cid] and "--user=65533:65533" in docker.argvs[run_cid]
    assert "--ulimit=cpu=1:1" in docker.argvs[run_cid]  # 1000 ms x C++ multiplier 1


def test_wrong_answer_and_runtime_error(pack):
    ex, _ = make(pack)
    assert ex.run(request("WRONG"), "r")[0].verdict == "wrong_answer"
    resp, _ = ex.run(request("CRASH"), "r")
    assert resp.verdict == "runtime_error"
    assert "/workspace/" not in resp.visible[0].stderr_trunc  # internal paths scrubbed


def test_compile_error_message_is_capped_and_scrubbed(pack):
    ex, docker = make(pack, compile_error=b"/workspace/src/main.cpp:1: error: " + b"x" * 20000)
    resp, log = ex.run(request("anything", language="cpp17"), "r")
    assert resp.verdict == "compile_error" and resp.compile.ok is False
    assert len(resp.compile.message_trunc) <= 8 * 1024
    assert "/workspace/" not in resp.compile.message_trunc
    assert resp.visible is None and resp.hidden_summary is None
    assert log.verdict == "compile_error" and log.outcome == "completed"
    assert_all_removed(docker)


def test_compile_timeout_is_a_compile_error(pack):
    ex, docker = make(pack, compile_timeout=True)
    resp, _ = ex.run(request("x", language="java21"), "r")
    assert resp.verdict == "compile_error" and "time limit" in resp.compile.message_trunc
    assert_all_removed(docker)


def test_custom_run_returns_output_without_judging(pack):
    ex, _ = make(pack)
    resp, _ = ex.run(request("ECHO_FIRST", kind="custom", custom_input="hello world\n"), "r")
    assert resp.verdict == "accepted" and resp.visible[0].id == "custom" and resp.visible[0].stdout_trunc == "hello\n"


# --- limits and process-tree kills -----------------------------------------------------------------------------


@pytest.mark.parametrize("source,verdict", [("LOOP", "time_limit"), ("FLOOD", "output_limit"), ("OOM", "memory_limit"),
                                            ("BAD_ALLOC", "memory_limit"), ("XCPU", "time_limit")])
def test_limits_kill_the_container_and_later_tests_still_run(pack, source, verdict):
    ex, docker = make(pack)
    lang = "cpp17" if source == "BAD_ALLOC" else "python313"
    resp, log = ex.run(request(source, language=lang), "r")
    assert resp.verdict == verdict
    assert [v.verdict for v in resp.visible] == [verdict, verdict]
    if source in ("LOOP", "FLOOD"):
        # the whole container (cgroup) was killed for each test and a fresh one was started for the next
        assert len(docker.killed) >= 2
        assert len(docker.created) == 3  # compile + one run container per test
    assert log.limit_hit == verdict.removesuffix("_limit")
    assert_all_removed(docker)


def test_output_is_capped_at_64kb(pack):
    ex, _ = make(pack)
    resp, _ = ex.run(request("FLOOD"), "r")
    assert len(resp.visible[0].stdout_trunc) <= 4 * 1024  # display cap; capture cap is 64 KB


def test_u4_budget_exhaustion_marks_remaining_tests_time_limit(pack_dir, pack):
    ex, docker = make(pack)
    resp, log = ex.run(graded(pack_dir, "LOOP"), "r")
    # Each looping test burns its wall limit (2.5 x 3 s for Python = 7.5 s); after 30 s the rest are not run.
    groups = resp.hidden_summary.groups
    assert groups == {"basic": (0, 2), "edge": (0, 1), "performance": (0, 1)}
    assert resp.verdict == "time_limit"
    assert resp.hidden_summary.first_failure.group == "basic"
    assert log.tests_run <= 4
    assert log.outcome == "completed"  # a candidate timeout, not a runner fault (U4)
    assert_all_removed(docker)


def test_student_exit_code_126_is_not_mistaken_for_an_infrastructure_fault(pack):
    ex, _ = make(pack)
    resp, log = ex.run(request("EXIT126"), "r")
    assert resp.verdict == "runtime_error" and log.outcome == "completed"


def test_cleanup_failure_discards_the_container(pack):
    ex, docker = make(pack, cleanup_fails=True)
    resp, _ = ex.run(request("ECHO_FIRST"), "r")
    assert resp.verdict == "accepted"
    assert len(docker.created) == 3 and len(docker.killed) == 2  # never reuse a container not proven clean
    assert_all_removed(docker)


# --- runner faults (U4: incomplete, never a candidate verdict) --------------------------------------------------


def test_container_start_failure_is_a_sandbox_error(pack):
    ex, docker = make(pack, fail_create_after=1)
    resp, log = ex.run(request("ECHO_FIRST"), "r")
    assert resp.verdict == "sandbox_error"
    assert resp.sandbox_error.error_class == "container_start_failed"
    assert resp.visible is None and resp.hidden_summary is None and resp.compile is None
    assert log.outcome == "sandbox_error" and log.error_class == "container_start_failed"
    assert_all_removed(docker)


def test_dead_container_during_a_test_is_a_sandbox_error(pack):
    ex, docker = make(pack, infra_126=True)
    resp, log = ex.run(request("ECHO_FIRST"), "r")
    assert resp.verdict == "sandbox_error" and resp.sandbox_error.error_class == "exec_failed"
    assert_all_removed(docker)


def test_unremovable_containers_are_handed_to_the_reaper(pack):
    left = []
    clock = FakeClock()
    docker = FakeDocker(clock, fail_remove=True)
    ex = Executor(docker, pack, clock=clock, on_container_left=left.append)
    ex.run(request("ECHO_FIRST"), "r")
    assert left == docker.created


# --- hidden results ---------------------------------------------------------------------------------------------


def test_graded_accepted_reduces_to_group_counts(pack_dir, pack):
    ex, _ = make(pack)
    resp, _ = ex.run(graded(pack_dir, "ECHO_FIRST"), "r")
    hs = resp.hidden_summary
    assert resp.verdict == "accepted"
    assert hs.groups == {"basic": (2, 2), "edge": (1, 1), "performance": (1, 1)}
    assert hs.first_failure is None and hs.max_time_ms % 10 == 0
    assert resp.visible is None


def test_first_failing_group_and_class(pack_dir, pack):
    ex, _ = make(pack)
    resp, _ = ex.run(graded(pack_dir, "SLOW_TAIL"), "r")
    hs = resp.hidden_summary
    assert hs.groups == {"basic": (2, 2), "edge": (1, 1), "performance": (0, 1)}
    assert hs.first_failure.group == "performance" and hs.first_failure.failure_class == "time_limit"
    assert resp.wire()["hidden_summary"]["first_failure"] == {"group": "performance", "class": "time_limit"}


def test_hidden_data_never_leaves_the_runner(pack_dir, pack):
    """A program that echoes its stdin to stdout and stderr: no hidden input, expected output, stderr, test name or
    reference text may appear anywhere in the response or the log record."""
    ex, _ = make(pack)
    resp, log = ex.run(graded(pack_dir, "LEAK_INPUT"), "rid-leak")
    text = wire(resp) + json.dumps(log.__dict__)
    for s in synthetic.SENTINELS:
        assert s not in text, s
    hs = resp.wire()["hidden_summary"]
    assert set(hs) == {"groups", "first_failure", "max_time_ms", "max_memory_kb"}
    assert set(hs["first_failure"]) == {"group", "class"}


def test_graded_request_must_name_the_installed_digest(pack_dir, pack):
    ex, docker = make(pack)
    with pytest.raises(PackMismatch):
        ex.run(request("ECHO_FIRST", kind="graded", hidden_digest="0" * 64), "r")
    assert docker.created == []  # refused before any container exists


@pytest.mark.parametrize("override", [{"problem_key": "unknown-problem"}, {"problem_version": 2}])
def test_unknown_problem_or_version_is_a_pack_mismatch(pack, override):
    ex, docker = make(pack)
    with pytest.raises(PackMismatch):
        ex.run(request("ECHO_FIRST", **override), "r")
    assert docker.created == []


def test_u4_tests_left_when_the_30s_budget_is_spent_count_as_time_limit(tmp_path):
    """Ten hidden tests of a looping Python program: each burns 7.5 s of wall clock, so only four can start inside the
    30 s submission budget; the other six are reported as time_limit without being run (U4)."""
    from runner.pack import load_pack

    root = tmp_path / "pack"
    synthetic.build_pack(root, extra={"synthetic-many": {
        "visible": [("a\n", "a\n")],
        "basic": [(f"b{i}\n", f"b{i}\n") for i in range(8)],
        "edge": [("e\n", "e\n")],
        "performance": [("p\n", "p\n")],
    }})
    pack = load_pack(root, require_read_only=False)
    ex, docker = make(pack)
    digest = synthetic.hidden_digest(root, "synthetic-many")
    resp, log = ex.run(request("LOOP", kind="graded", problem_key="synthetic-many", hidden_digest=digest), "r")
    assert log.tests_run == 4
    assert resp.hidden_summary.groups == {"basic": (0, 8), "edge": (0, 1), "performance": (0, 1)}
    assert resp.verdict == "time_limit" and log.outcome == "completed"
    assert len(docker.created) == 1 + 4  # compile + one container per started test; none for tests not run
    assert_all_removed(docker)


def test_programs_and_cleanup_run_as_the_run_uid_but_never_own_the_container(pack):
    """The container's init/keep-alive runs as the builder uid, so the run uid's `kill -9 -1` cleanup (or a hostile
    program) cannot stop the container; programs and cleanup themselves always run as the run uid."""
    ex, docker = make(pack)
    ex.run(request("ECHO_FIRST"), "r")
    for cid in docker.created:
        assert f"--user={BUILDER_UID}:{BUILDER_UID}" in docker.argvs[cid]
    run_cid = docker.created[1]
    for _cid, user, argv in docker.exec_log:
        if _cid == run_cid and argv[0] in ("python3", "sh"):
            assert user == RUN_UID, argv
    restores = [u for c, u, a in docker.exec_log if c == run_cid and a[:1] == ("tar",)]
    assert restores == [BUILDER_UID]


@pytest.mark.parametrize("language", ["c11", "cpp17", "java21", "python313"])
def test_compile_temporaries_go_to_a_builder_dir_in_the_workspace_not_tmp(pack, language):
    """/tmp is a read-only mount, so the compiler's TMPDIR is a builder-owned dir inside the /workspace tmpfs; the
    build tar copies only src/ and build/, and programs keep TMPDIR inside their run dir."""
    ex, docker = make(pack)
    calls = []
    real_exec = docker.exec

    def recording_exec(cid, user, argv, stdin, timeout_s, cap, workdir="/workspace", env=None):
        calls.append((cid, user, tuple(argv), env))
        return real_exec(cid, user, argv, stdin, timeout_s, cap, workdir, env)

    docker.exec = recording_exec
    ex.run(request("ECHO_FIRST", language=language), "r")
    compile_cid, run_cid = docker.created[0], docker.created[1]
    mkdirs = [a for c, u, a, e in calls if c == compile_cid and a[:1] == ("mkdir",)]
    assert mkdirs and COMPILE_TMP_DIR in mkdirs[0]
    from runner.languages import LANGUAGES
    compiles = [(u, e) for c, u, a, e in calls if c == compile_cid and a == LANGUAGES[language].compile_cmd]
    assert compiles == [(BUILDER_UID, {"TMPDIR": COMPILE_TMP_DIR})]
    assert COMPILE_TMP_DIR.startswith("/workspace/")
    tars = [a for c, u, a, e in calls if c == compile_cid and a[:1] == ("tar",)]
    assert tars and tars[0][-2:] == ("src", "build")  # the compiler scratch dir is never copied to the run container
    runs = [e for c, u, a, e in calls if c == run_cid and u == RUN_UID and a and a[0] not in ("mkdir", "sh")]
    assert runs and all(e is not None and e["TMPDIR"] == RUN_DIR for e in runs)
