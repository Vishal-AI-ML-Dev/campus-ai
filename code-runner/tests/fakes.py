"""A scripted stand-in for ``DockerCLI``. It never runs anything: "programs" are Python behaviours chosen by the
submitted source text, so the executor's orchestration (limits, kills, recreation, cleanup, faults, reduction) can be
tested on any machine. The real sandbox is exercised only by the gVisor escape suite."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from runner.sandbox import ContainerState, ExecResult, SandboxFault, assert_hardened

from synthetic import SENTINEL_STDERR


@dataclass
class FakeContainer:
    argv: list[str]
    running: bool = True
    oom: bool = False
    files: dict[str, bytes] = field(default_factory=dict)
    build: bytes | None = None


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def program(source: str, stdin: bytes) -> dict:
    """Behaviour of a fake student program, selected by a keyword in its source."""
    first = stdin.split()[0] if stdin.split() else b""
    if "ECHO_FIRST" in source:
        return {"stdout": first + b"\n"}
    if "LEAK_INPUT" in source:  # tries to exfiltrate hidden data through stdout and stderr
        return {"stdout": stdin, "stderr": stdin + SENTINEL_STDERR.encode(), "exit": 1}
    if "WRONG" in source:
        return {"stdout": b"nope\n"}
    if "CRASH" in source:
        return {"stderr": b"Traceback: boom at /workspace/src/main.py\n", "exit": 1}
    if "LOOP" in source:
        return {"timeout": True}
    if "FLOOD" in source:
        return {"overflow": True}
    if "OOM" in source:
        return {"oom": True}
    if "BAD_ALLOC" in source:
        return {"stderr": b"terminate called after throwing an instance of 'std::bad_alloc'\n", "exit": 134}
    if "EXIT126" in source:
        return {"exit": 126}
    if "XCPU" in source:
        return {"exit": 152}
    if "SLOW_TAIL" in source:  # fine on visible input, loops when the input is long (the performance test)
        return {"timeout": True} if len(stdin) > 500 else {"stdout": first + b"\n"}
    return {"stdout": b"\n"}


class FakeDocker:
    def __init__(self, clock: FakeClock | None = None, *, compile_error: bytes | None = None,
                 fail_create_after: int | None = None, fail_remove: bool = False, cleanup_fails: bool = False,
                 compile_timeout: bool = False, infra_126: bool = False, test_cost_s: float = 0.1):
        self.clock = clock or FakeClock()
        self.containers: dict[str, FakeContainer] = {}
        self.created: list[str] = []
        self.argvs: dict[str, list[str]] = {}
        self.removed: list[str] = []
        self.killed: list[str] = []
        self.exec_log: list[tuple[str, int, tuple[str, ...]]] = []
        self.compile_error = compile_error
        self.fail_create_after = fail_create_after
        self.fail_remove = fail_remove
        self.cleanup_fails = cleanup_fails
        self.compile_timeout = compile_timeout
        self.infra_126 = infra_126
        self.test_cost_s = test_cost_s
        self._ids = itertools.count(1)
        self.source = ""

    # --- DockerCLI interface ---------------------------------------------------------------
    def create(self, argv: list[str]) -> str:
        assert_hardened(argv)  # the real wrapper does this too
        if self.fail_create_after is not None and len(self.created) >= self.fail_create_after:
            raise SandboxFault("container_start_failed")
        cid = f"c{next(self._ids):04d}"
        self.containers[cid] = FakeContainer(argv=list(argv))
        self.argvs[cid] = list(argv)
        self.created.append(cid)
        return cid

    def kill(self, cid: str) -> None:
        self.killed.append(cid)
        if cid in self.containers:
            self.containers[cid].running = False

    def remove(self, cid: str) -> bool:
        if self.fail_remove:
            return False
        self.removed.append(cid)
        self.containers.pop(cid, None)
        return True

    def state(self, cid: str) -> ContainerState:
        c = self.containers.get(cid)
        if c is None:
            return ContainerState(False, False, False)
        return ContainerState(True, c.running, c.oom)

    def list_managed(self) -> list[tuple[str, int]]:
        return [(cid, 0) for cid in self.containers]

    def exec(self, cid, user, argv, stdin, timeout_s, cap, workdir="/workspace", env=None) -> ExecResult:
        self.exec_log.append((cid, user, tuple(argv)))
        c = self.containers.get(cid)
        if c is None or not c.running:
            return ExecResult(exit_code=125, stdout=b"", stderr=b"", wall_ms=1)
        cmd = argv[0]
        if cmd == "dd":
            self.source = stdin.decode()
            return ExecResult(0, b"", b"", 1)
        if cmd in ("mkdir", "chmod", "true"):
            return ExecResult(0, b"", b"", 1)
        if cmd == "tar":
            if "-cf" in argv:
                return ExecResult(0, b"TAR:" + self.source.encode(), b"", 1)
            c.build = stdin
            return ExecResult(0, b"", b"", 1)
        if cmd == "sh":  # the fixed cleanup script
            return ExecResult(1 if self.cleanup_fails else 0, b"", b"", 1)
        if cmd in ("gcc", "g++", "javac") or (cmd == "python3" and "-c" in argv):
            if self.compile_timeout:
                self.clock.now += timeout_s
                self.kill(cid)
                return ExecResult(None, b"", b"", timeout_s * 1000, timed_out=True, killed=True)
            if self.compile_error is not None:
                return ExecResult(1, b"", self.compile_error, 5)
            return ExecResult(0, b"", b"", 5)
        # the student's program
        assert c.build is not None and c.build.startswith(b"TAR:"), "program run without a restored build"
        behaviour = program(c.build[4:].decode(), stdin)
        if self.infra_126:
            c.running = False
            return ExecResult(126, b"", b"", 1)
        if behaviour.get("timeout"):
            self.clock.now += timeout_s
            self.kill(cid)
            return ExecResult(None, b"", b"", timeout_s * 1000, timed_out=True, killed=True)
        if behaviour.get("overflow"):
            self.clock.now += self.test_cost_s
            self.kill(cid)
            return ExecResult(None, b"y" * cap, b"", self.test_cost_s * 1000, stdout_overflow=True, killed=True)
        if behaviour.get("oom"):
            self.clock.now += self.test_cost_s
            c.running, c.oom = False, True
            return ExecResult(137, b"", b"", self.test_cost_s * 1000)
        self.clock.now += self.test_cost_s
        return ExecResult(behaviour.get("exit", 0), behaviour.get("stdout", b""), behaviour.get("stderr", b""),
                          self.test_cost_s * 1000 + 3.7)
