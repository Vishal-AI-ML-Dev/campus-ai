"""Escape / abuse / conformance suite (spec §11.5, P8-S6) -- REAL sandbox, synthetic hostile programs only.

Runs ONLY when ``RUNNER_GVISOR_E2E=1`` AND the host passes ``sandbox.self_check`` (pinned Docker Engine, pinned gVisor
``runsc`` with the systrap platform, digest-pinned images). If the variable is set but the self-check fails, the suite
FAILS instead of skipping: hostile fixtures never run on plain Docker or on a host that is not the verified sandbox.
Every container is still created through ``assert_hardened``, so ``--runtime=runsc`` is mandatory regardless.

Each fixture prints ``BLOCKED <check>`` when an attack fails and ``ESCAPED <check>`` when it succeeds. A single
``ESCAPED`` line, an unexpected verdict, a leftover container or a sentinel in any output fails the suite.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

from runner.executor import Executor
from runner.limits import FSIZE_BYTES
from runner.pack import load_pack
from runner.sandbox import MANAGED_LABEL, DockerCLI, self_check
from runner.schemas import ExecutionRequest

import synthetic

pytestmark = pytest.mark.gvisor
ENABLED = os.environ.get("RUNNER_GVISOR_E2E") == "1"
FIXTURES = Path(__file__).parent / "fixtures"
LANG_OF = {".c": "c11", ".cpp": "cpp17", ".java": "java21", ".py": "python313"}

if not ENABLED:
    pytest.skip("escape suite needs the verified gVisor host (RUNNER_GVISOR_E2E=1); it never runs elsewhere",
                allow_module_level=True)


def _docker() -> DockerCLI:
    return DockerCLI(os.environ.get("RUNNER_DOCKER_BIN", "/usr/bin/docker"))


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    docker = _docker()
    check = self_check(docker)
    if not check.ok:
        pytest.fail(f"refusing to run hostile fixtures: self-check failed {check.failures}")
    root = tmp_path_factory.mktemp("pack")
    synthetic.build_pack(root, extra={"synthetic-census": {
        "visible": [("spawn\n", "left-orphans\n"), ("census\n", "BLOCKED leftovers\n")],
        "basic": [("x\n", "x\n")], "edge": [("x\n", "x\n")], "performance": [("x\n", "x\n")],
    }})
    for p in sorted(root.rglob("*"), reverse=True):  # install read-only, as on the VM
        p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if p.is_dir() else 0))
    root.chmod(0o555)
    pack = load_pack(root, require_read_only=True)
    return Executor(docker, pack), docker, root


def run(executor, source: str, language: str, kind="custom", problem="synthetic-echo", stdin="x\n", **kw):
    req = ExecutionRequest.model_validate(dict(
        execution_id=str(uuid.uuid4()), kind=kind, problem_key=problem, problem_version=1,
        test_set="hidden" if kind == "graded" else "visible", language=language, source=source,
        custom_input=stdin if kind == "custom" else None, **kw,
    ))
    return executor.run(req, "escape-suite")


def fixture(name: str) -> tuple[str, str]:
    path = FIXTURES / name
    return path.read_text(), LANG_OF[path.suffix]


def no_leftovers(docker: DockerCLI) -> None:
    assert docker.list_managed() == [], "an execution container was left behind"


def no_escape(resp) -> str:
    text = json.dumps(resp.wire())
    assert "ESCAPED" not in text, text[:2000]
    return text


@pytest.mark.parametrize("name,verdicts", [
    ("infinite_loop.c", {"time_limit"}),
    ("busy_threads.java", {"time_limit"}),
    ("fork_bomb.c", {"time_limit", "runtime_error"}),
    ("fork_bomb.py", {"time_limit", "runtime_error"}),
    ("memory_bomb.c", {"memory_limit", "runtime_error"}),
    ("memory_bomb.java", {"memory_limit"}),
    ("stdout_flood.py", {"output_limit"}),
    ("stderr_flood.c", {"output_limit"}),
    ("sleep_past_wall.py", {"time_limit"}),
])
def test_resource_exhaustion_is_contained(sandbox, name, verdicts):
    executor, docker, _ = sandbox
    source, lang = fixture(name)
    started = time.monotonic()
    resp, log = run(executor, source, lang)
    assert resp.verdict in verdicts, resp.verdict
    assert log.outcome == "completed"  # a candidate failure, never a runner fault
    assert time.monotonic() - started < 40  # bounded by the 30 s budget plus container setup
    no_escape(resp)
    no_leftovers(docker)


@pytest.mark.parametrize("name", ["disk_fill.c", "filesystem_probe.py", "secret_probe.py", "network_probe.py",
                                  "privilege_probe.c"])
def test_host_filesystem_network_and_privileges_are_unreachable(sandbox, name):
    executor, docker, _ = sandbox
    source, lang = fixture(name)
    resp, _ = run(executor, source, lang)
    text = no_escape(resp)
    assert resp.verdict == "accepted", text[:2000]
    assert "BLOCKED" in resp.visible[0].stdout_trunc
    if name == "disk_fill.c":
        # Proof that the 16 MiB RLIMIT_FSIZE stopped the writes: the kernel raised SIGXFSZ (the fixture records it
        # instead of dying) and no more than 16 MiB was written.
        out = resp.visible[0].stdout_trunc
        assert "sigxfsz=1" in out, out
        assert int(out.split()[2]) <= FSIZE_BYTES, out
    no_leftovers(docker)


def test_orphans_and_zombies_are_gone_before_the_next_test(sandbox):
    executor, docker, _ = sandbox
    spawn = textwrap.dedent("""
        import os, sys
        if sys.stdin.read().strip() == "spawn":
            for _ in range(4):
                if os.fork() == 0:
                    os._exit(0)
            if os.fork() == 0:
                os.setsid()
                if os.fork() == 0:
                    import time
                    while True:
                        time.sleep(1)
                os._exit(0)
            print("left-orphans")
        else:
            # Candidate leftovers are processes of the RUN uid (65534) other than this census. The container's own
            # init (pid 1) and its builder-owned keep-alive (`sleep infinity`, uid 65533) are runner infrastructure.
            me, run_uid, others = os.getpid(), os.getuid(), []
            for pid in os.listdir("/proc"):
                if not pid.isdigit() or int(pid) == me:
                    continue
                try:
                    with open(f"/proc/{pid}/status") as f:
                        status = dict(line.rstrip("\\n").split(":\\t", 1) for line in f if ":\\t" in line)
                except OSError:
                    continue  # exited while we looked
                uids = status.get("Uid", "").split()
                if uids and int(uids[0]) == run_uid:
                    others.append(f"pid={pid} uid={uids[0]} name={status.get('Name', '?')}")
            print("ESCAPED leftovers " + "; ".join(others) if others else "BLOCKED leftovers")
    """)
    resp, _ = run(executor, spawn, "python313", kind="visible", problem="synthetic-census")
    no_escape(resp)
    assert [v.verdict for v in resp.visible] == ["accepted", "accepted"]
    no_leftovers(docker)


def test_nothing_persists_between_executions(sandbox):
    executor, docker, _ = sandbox
    write, _ = fixture("marker_write.py")
    check, _ = fixture("marker_check.py")
    run(executor, write, "python313")
    resp, _ = run(executor, check, "python313")
    no_escape(resp)
    assert "BLOCKED marker" in resp.visible[0].stdout_trunc
    no_leftovers(docker)


def test_hidden_data_sentinels_never_leave_the_runner(sandbox):
    executor, docker, root = sandbox
    leak = "import sys\ndata = sys.stdin.read()\nprint(data)\nsys.stderr.write(data)\nraise SystemExit(3)\n"
    resp, log = run(executor, leak, "python313", kind="graded", hidden_digest=synthetic.hidden_digest(root))
    text = json.dumps(resp.wire()) + json.dumps(log.__dict__)
    for s in synthetic.SENTINELS:
        assert s not in text
    assert resp.verdict == "runtime_error"
    no_leftovers(docker)


def test_reference_like_program_passes_every_group(sandbox):
    executor, _, root = sandbox
    resp, _ = run(executor, "print(input().split()[0])\n", "python313", kind="graded",
                  hidden_digest=synthetic.hidden_digest(root))
    assert resp.verdict == "accepted"
    assert resp.hidden_summary.groups == {"basic": (2, 2), "edge": (1, 1), "performance": (1, 1)}


@pytest.mark.parametrize("lang,source", [
    ("c11", '#include <stdio.h>\nint main(void){char w[64];scanf("%63s",w);puts(w);return 0;}\n'),
    ("cpp17", '#include <iostream>\n#include <string>\nint main(){std::string w;std::cin>>w;std::cout<<w<<"\\n";}\n'),
    ("java21", 'public class Main{public static void main(String[] a){System.out.println(new java.util.Scanner(System.in).next());}}\n'),
])
def test_every_language_compiles_and_runs_in_the_sandbox(sandbox, lang, source):
    executor, docker, _ = sandbox
    resp, _ = run(executor, source, lang, kind="visible")
    assert resp.verdict == "accepted", resp.wire()
    no_leftovers(docker)


def test_runner_killed_mid_execution_is_cleaned_up_by_the_reaper(sandbox, tmp_path):
    """Start an execution in a separate process, SIGKILL that process mid-run, then the startup sweep must remove
    every container it left behind."""
    _, docker, root = sandbox
    source, _ = fixture("infinite_loop.c")
    script = tmp_path / "victim.py"
    script.write_text(textwrap.dedent(f"""
        import sys, uuid
        sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
        from runner.executor import Executor
        from runner.pack import load_pack
        from runner.sandbox import DockerCLI
        from runner.schemas import ExecutionRequest
        ex = Executor(DockerCLI({docker.binary!r}), load_pack(__import__('pathlib').Path({str(root)!r})))
        req = ExecutionRequest.model_validate(dict(execution_id=str(uuid.uuid4()), kind="custom",
            problem_key="synthetic-echo", problem_version=1, test_set="visible", language="c11",
            source={source!r}, custom_input="x\\n"))
        ex.run(req, "victim")
    """))
    def running_run_containers() -> list[str]:
        # The first managed container is the COMPILE one (cr-c-*); the run phase has a running cr-r-* container.
        p = subprocess.run([docker.binary, "ps", "--no-trunc", "--filter", f"label={MANAGED_LABEL}=1",
                            "--filter", "status=running", "--format", "{{.ID}} {{.Names}}"],
                           capture_output=True, text=True, timeout=30, check=False)
        return [line.split()[0] for line in p.stdout.splitlines() if line.split()[1:2] and
                line.split()[1].startswith("cr-r-")]

    victim = subprocess.Popen([sys.executable, str(script)])
    deadline = time.monotonic() + 60
    run_cids: list[str] = []
    while time.monotonic() < deadline and victim.poll() is None and not run_cids:
        run_cids = running_run_containers()
        if not run_cids:
            time.sleep(0.05)
    assert run_cids, f"the victim never reached the run phase (victim exit code {victim.poll()})"
    victim.kill()  # mid-execution: its run-phase container is running right now
    victim.wait(30)
    assert victim.returncode == -signal.SIGKILL, victim.returncode  # killed, did not finish and clean up
    managed = [cid for cid, _ in docker.list_managed()]
    assert set(run_cids) <= set(managed), "the abandoned run container must still exist for the reaper"
    from runner.observability import Metrics
    from runner.sandbox import SelfCheck
    from runner.service import Runner

    runner = Runner(Executor(docker, load_pack(root)), docker, Metrics(), accepting=True, max_concurrency=1,
                    queue_max=0, self_check=lambda: SelfCheck(ok=True))
    assert runner.reap(startup=True) >= 1
    assert runner.reaper_ok
    no_leftovers(docker)
    ps = subprocess.run([docker.binary, "ps", "-a", "--filter", f"label={MANAGED_LABEL}=1", "-q"],
                        capture_output=True, text=True)
    assert ps.stdout.strip() == ""
