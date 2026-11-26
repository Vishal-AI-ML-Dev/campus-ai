"""Docker + gVisor sandbox: hardened container arguments, the Docker CLI wrapper and the fail-closed self-check.

TRUSTED LAYER. Only this module talks to the Docker daemon. Every Docker invocation is an argument array (never a
shell string); request data only ever travels as bytes on a process's stdin, never on a command line.

Hardening (spec §4.3, P8-S2), applied to every container and verified by ``assert_hardened`` before creation:
``--runtime=runsc`` (gVisor, systrap platform, its own network disabled), never ``--privileged``,
``--cap-drop ALL``, ``--security-opt no-new-privileges``, a fixed non-root uid, ``--read-only`` root filesystem,
``--tmpfs /workspace`` (nosuid, nodev, size-capped), ``--network none``, ``--ipc none``, ``--memory`` with equal
``--memory-swap`` (no swap), ``--pids-limit``, ``--ulimit nofile/fsize/core/cpu``, ``--init``, ``--log-driver
none``, ``--pull never`` and an image referenced by ``@sha256`` digest. No volumes, bind mounts, devices, published
ports, extra capabilities or host namespaces, and no Docker socket inside any container.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import pins
from .languages import WORKSPACE
from .limits import FSIZE_BYTES, WORKSPACE_TMPFS_MB

MANAGED_LABEL = "campus-ai-runner"
CREATED_LABEL = "campus-ai-runner.created"
_DOCKER_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}

# Flags that must never appear in a container's arguments.
_FORBIDDEN_PREFIXES = (
    "--privileged", "-v", "--volume", "--mount", "--device", "--cap-add", "-p", "--publish", "-P",
    "--publish-all", "--pid", "--userns", "--uts", "--cgroupns", "--security-opt=seccomp=unconfined",
    "--security-opt=apparmor=unconfined", "--volumes-from", "--add-host", "--dns", "--env-file", "-e", "--env",
    "--gpus", "--group-add", "--sysctl", "--oom-kill-disable",
)


# The only options a sandbox container may be created with (exact values, or prefixes ending in "=").
_ALLOWED_OPTIONS = (
    "--detach", "--name", "--label", "--runtime=runsc", "--pull=never", "--network=none", "--ipc=none",
    "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=", "--read-only", "--tmpfs=", "--cpus=1",
    "--memory=", "--memory-swap=", "--pids-limit=", "--ulimit=nofile=", "--ulimit=fsize=", "--ulimit=core=0:0",
    "--ulimit=cpu=", "--init", "--log-driver=none", "--hostname=sandbox", "--workdir=/workspace",
)


class SandboxFault(Exception):
    """A runner-side failure (never the student's fault). ``error_class`` is a sanitized enum value."""

    def __init__(self, error_class: str):
        super().__init__(error_class)
        self.error_class = error_class


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    memory_mb: int
    pids: int
    nofile: int
    cpu_rlimit_s: int
    user: int
    name: str
    created_epoch: int


def container_argv(spec: ContainerSpec) -> list[str]:
    """The full ``docker run`` argument array for one sandbox container (main process: ``sleep infinity``)."""
    return [
        "run", "--detach",
        "--name", spec.name,
        "--label", f"{MANAGED_LABEL}=1",
        "--label", f"{CREATED_LABEL}={spec.created_epoch}",
        "--runtime=runsc",
        "--pull=never",
        "--network=none",
        "--ipc=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--user={spec.user}:{spec.user}",
        "--read-only",
        f"--tmpfs={WORKSPACE}:rw,exec,nosuid,nodev,size={WORKSPACE_TMPFS_MB}m,mode=1777",
        "--cpus=1",
        f"--memory={spec.memory_mb}m",
        f"--memory-swap={spec.memory_mb}m",
        f"--pids-limit={spec.pids}",
        f"--ulimit=nofile={spec.nofile}:{spec.nofile}",
        f"--ulimit=fsize={FSIZE_BYTES}:{FSIZE_BYTES}",
        "--ulimit=core=0:0",
        f"--ulimit=cpu={spec.cpu_rlimit_s}:{spec.cpu_rlimit_s}",
        "--init",
        "--log-driver=none",
        "--hostname=sandbox",
        f"--workdir={WORKSPACE}",
        spec.image,
        "sleep", "infinity",
    ]


def assert_hardened(argv: Sequence[str]) -> None:
    """Refuse any container argument list that lacks a mandatory control or contains a forbidden one."""
    required = {
        "--runtime=runsc", "--pull=never", "--network=none", "--ipc=none", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--read-only", "--init", "--log-driver=none", "--cpus=1",
        "--ulimit=core=0:0",
    }
    missing = required - set(argv)
    if missing:
        raise SandboxFault("unsafe_configuration")
    # Allow-list: every option must be one this module emits (catches concatenated short flags such as "-v/x:/y").
    if not argv or argv[0] != "run" or list(argv[-2:]) != ["sleep", "infinity"] or argv[-3] not in pins.ALL_IMAGES:
        raise SandboxFault("unsafe_configuration")
    for a in argv:
        if a.startswith("-") and not any(a == x or (x.endswith("=") and a.startswith(x)) for x in _ALLOWED_OPTIONS):
            raise SandboxFault("unsafe_configuration")
        if a in _FORBIDDEN_PREFIXES or any(a.startswith(p + "=") for p in _FORBIDDEN_PREFIXES if p.startswith("--")):
            raise SandboxFault("unsafe_configuration")
        if a.startswith("--runtime") and a != "--runtime=runsc":
            raise SandboxFault("unsafe_configuration")
        if a.startswith("--network") and a != "--network=none":
            raise SandboxFault("unsafe_configuration")
    users = [a for a in argv if a.startswith("--user=")]
    if len(users) != 1 or users[0].split("=", 1)[1].split(":")[0] in ("0", "root"):
        raise SandboxFault("unsafe_configuration")
    mem = [a.split("=", 1)[1] for a in argv if a.startswith("--memory=")]
    swap = [a.split("=", 1)[1] for a in argv if a.startswith("--memory-swap=")]
    if len(mem) != 1 or mem != swap:
        raise SandboxFault("unsafe_configuration")
    if not any(a.startswith("--pids-limit=") for a in argv) or not any(a.startswith("--tmpfs=") for a in argv):
        raise SandboxFault("unsafe_configuration")
    tmpfs = [a for a in argv if a.startswith("--tmpfs=")]
    if len(tmpfs) != 1 or not tmpfs[0].startswith(f"--tmpfs={WORKSPACE}:") or "nosuid" not in tmpfs[0]:
        raise SandboxFault("unsafe_configuration")
    images = [a for a in argv if a in pins.ALL_IMAGES]
    if len(images) != 1 or "@sha256:" not in images[0]:
        raise SandboxFault("unsafe_configuration")


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    wall_ms: float
    timed_out: bool = False
    stdout_overflow: bool = False
    stderr_overflow: bool = False
    killed: bool = False  # the container was killed to stop this process tree


@dataclass
class ContainerState:
    exists: bool
    running: bool
    oom_killed: bool


class DockerCLI:
    """Thin wrapper over the ``docker`` CLI. Arguments are arrays; request bytes only travel on stdin."""

    def __init__(self, binary: str = "/usr/bin/docker"):
        if not (binary.startswith("/") or os.path.isabs(binary)):
            raise ValueError("docker binary path must be absolute")
        self.binary = binary

    # ------------------------------------------------------------------ plain commands
    def _run(self, args: list[str], timeout: float = 30, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.binary, *args], input=input_bytes, capture_output=True, timeout=timeout, env=_DOCKER_ENV,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            # Docker missing, not executable or hung: report failure; callers fail closed.
            return subprocess.CompletedProcess([self.binary, *args], 127, b"", b"")

    def server_version(self) -> str:
        p = self._run(["version", "--format", "{{.Server.Version}}"])
        return p.stdout.decode("ascii", "replace").strip() if p.returncode == 0 else ""

    def runtimes(self) -> dict:
        p = self._run(["info", "--format", "{{json .Runtimes}}"])
        if p.returncode != 0:
            return {}
        try:
            return json.loads(p.stdout or b"{}")
        except json.JSONDecodeError:
            return {}

    def image_present(self, ref: str) -> bool:
        p = self._run(["image", "inspect", "--format", "{{json .RepoDigests}}", ref])
        if p.returncode != 0:
            return False
        try:
            digests = json.loads(p.stdout or b"[]")
        except json.JSONDecodeError:
            return False
        want = ref.split("@", 1)[1]
        return any(d.endswith("@" + want) for d in digests or [])

    def create(self, argv: list[str]) -> str:
        assert_hardened(argv)
        p = self._run(argv, timeout=60)
        cid = p.stdout.decode("ascii", "replace").strip()
        if p.returncode != 0 or not cid:
            raise SandboxFault("container_start_failed")
        return cid

    def kill(self, cid: str) -> None:
        self._run(["kill", "--signal=KILL", cid], timeout=30)

    def remove(self, cid: str) -> bool:
        p = self._run(["rm", "--force", "--volumes", cid], timeout=60)
        return p.returncode == 0 or b"No such container" in p.stderr

    def state(self, cid: str) -> ContainerState:
        p = self._run(["inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}}", cid])
        if p.returncode != 0:
            return ContainerState(False, False, False)
        running, oom = (p.stdout.decode("ascii", "replace").split() + ["false", "false"])[:2]
        return ContainerState(True, running == "true", oom == "true")

    def list_managed(self) -> list[tuple[str, int]]:
        p = self._run([
            "ps", "--all", "--no-trunc", "--filter", f"label={MANAGED_LABEL}=1",
            "--format", '{{.ID}} {{.Label "' + CREATED_LABEL + '"}}',
        ])
        if p.returncode != 0:
            raise SandboxFault("docker_unavailable")
        out = []
        for line in p.stdout.decode("ascii", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                out.append((parts[0], int(parts[1])))
            elif parts:
                out.append((parts[0], 0))  # unlabelled timestamp: treat as old
        return out

    # ------------------------------------------------------------------ exec with caps and a wall clock
    def exec(
        self,
        cid: str,
        user: int,
        argv: Sequence[str],
        stdin: bytes,
        timeout_s: float,
        cap: int,
        workdir: str = WORKSPACE,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        args = [self.binary, "exec", "--interactive", f"--user={user}:{user}", f"--workdir={workdir}"]
        for k, v in (env or {}).items():
            args.append(f"--env={k}={v}")
        args += [cid, *argv]
        start = time.monotonic()
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_DOCKER_ENV
        )
        out = _CappedReader(proc.stdout, cap)
        err = _CappedReader(proc.stderr, cap)
        writer = threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True)
        for t in (out, err, writer):
            t.start()
        timed_out = killed = False
        while proc.poll() is None:
            if time.monotonic() - start > timeout_s:
                timed_out = True
            if timed_out or out.overflow.is_set() or err.overflow.is_set():
                # Stopping the docker client would not stop the process inside the sandbox: kill the whole
                # container cgroup instead (process-tree kill, spec §4.4).
                self.kill(cid)
                killed = True
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                break
            time.sleep(0.005)
        wall_ms = (time.monotonic() - start) * 1000
        out.join(5)
        err.join(5)
        writer.join(1)
        return ExecResult(
            exit_code=proc.returncode,
            stdout=out.data(),
            stderr=err.data(),
            wall_ms=wall_ms,
            timed_out=timed_out,
            stdout_overflow=out.overflow.is_set(),
            stderr_overflow=err.overflow.is_set(),
            killed=killed,
        )


def _feed(pipe, data: bytes) -> None:
    try:
        view = memoryview(data)
        for i in range(0, len(view), 65536):
            pipe.write(view[i : i + 65536])
        pipe.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


class _CappedReader(threading.Thread):
    """Reads a stream, keeping at most ``cap`` bytes; sets ``overflow`` as soon as more arrive."""

    def __init__(self, stream, cap: int):
        super().__init__(daemon=True)
        self.stream, self.cap = stream, cap
        self.buf = bytearray()
        self.overflow = threading.Event()

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read1(65536) if hasattr(self.stream, "read1") else self.stream.read(65536)
                if not chunk:
                    break
                room = self.cap - len(self.buf)
                if len(chunk) > room:
                    self.buf += chunk[: max(room, 0)]
                    self.overflow.set()
                    # keep draining so the docker client never blocks, but store nothing more
                else:
                    self.buf += chunk
        except (OSError, ValueError):
            pass

    def data(self) -> bytes:
        return bytes(self.buf)


# ------------------------------------------------------------------------------------------------ self-check


@dataclass
class SelfCheck:
    ok: bool
    failures: list[str] = field(default_factory=list)


def _sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def self_check(docker: DockerCLI, runsc_path: str = pins.RUNSC_PATH) -> SelfCheck:
    """Fail closed: every pin and mandatory runtime setting must hold, or the runner does not accept work.

    Failure names are fixed strings (no host details, paths or versions are echoed outside the runner log).
    """
    failures: list[str] = []
    if docker.server_version() != pins.DOCKER_ENGINE_VERSION:
        failures.append("docker_version_not_approved")
    runtimes = docker.runtimes()
    runsc = runtimes.get(pins.RUNSC_RUNTIME_NAME)
    if not isinstance(runsc, dict):
        failures.append("runsc_runtime_missing")
    else:
        if runsc.get("path") != runsc_path:
            failures.append("runsc_path_not_approved")
        args = tuple(runsc.get("runtimeArgs") or ())
        if any(a.startswith("--platform") for a in args if a != "--platform=systrap") or "--platform=systrap" not in args:
            failures.append("runsc_platform_not_systrap")
        if any(req not in args for req in pins.RUNSC_REQUIRED_ARGS):
            failures.append("runsc_args_not_approved")
    if not shutil.which(runsc_path) and not os.path.exists(runsc_path):
        failures.append("runsc_binary_missing")
    else:
        if _sha256_file(runsc_path) != pins.RUNSC_SHA256:
            failures.append("runsc_binary_not_approved")
        try:
            v = subprocess.run([runsc_path, "--version"], capture_output=True, timeout=10, env=_DOCKER_ENV)
            if pins.GVISOR_RELEASE.encode() not in v.stdout:
                failures.append("runsc_version_not_approved")
        except (OSError, subprocess.SubprocessError):
            failures.append("runsc_version_not_approved")
    for image in pins.ALL_IMAGES:
        if "@sha256:" not in image or not docker.image_present(image):
            failures.append("image_not_pinned_or_missing")
            break
    return SelfCheck(ok=not failures, failures=sorted(set(failures)))
