"""Container hardening is mandatory and the runner fails closed when the host does not match the pins."""

import shutil
import subprocess

import pytest

from runner import pins
from runner.languages import LANGUAGES, RUN_UID
from runner.sandbox import (
    ContainerSpec,
    DockerCLI,
    SandboxFault,
    assert_hardened,
    container_argv,
    self_check,
)


def spec(**kw):
    base = dict(image=pins.IMAGE_PYTHON, memory_mb=256, pids=64, nofile=64, cpu_rlimit_s=3, user=RUN_UID,
                name="cr-r-test", created_epoch=1)
    base.update(kw)
    return ContainerSpec(**base)


def test_container_arguments_carry_every_control():
    argv = container_argv(spec())
    assert_hardened(argv)
    for flag in ("--runtime=runsc", "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--read-only", "--ipc=none", "--init", "--pull=never", "--log-driver=none", "--cpus=1",
                 "--memory=256m", "--memory-swap=256m", "--pids-limit=64", "--ulimit=nofile=64:64",
                 "--ulimit=core=0:0", "--ulimit=cpu=3:3", f"--user={RUN_UID}:{RUN_UID}"):
        assert flag in argv, flag
    assert any(a.startswith("--tmpfs=/workspace:") and "nosuid" in a and "nodev" in a and "size=64m" in a for a in argv)
    assert pins.IMAGE_PYTHON in argv and "@sha256:" in pins.IMAGE_PYTHON
    # no host paths, sockets or devices anywhere
    joined = " ".join(argv)
    for bad in ("docker.sock", "/var/run", "--privileged", "--volume", "--mount", "--device", "-v "):
        assert bad not in joined


@pytest.mark.parametrize("flag", ["--runtime=runsc", "--network=none", "--cap-drop=ALL",
                                  "--security-opt=no-new-privileges", "--read-only", "--init", "--pull=never",
                                  "--ipc=none", "--log-driver=none"])
def test_missing_mandatory_control_is_refused(flag):
    argv = [a for a in container_argv(spec()) if a != flag]
    with pytest.raises(SandboxFault, match="unsafe_configuration"):
        assert_hardened(argv)


@pytest.mark.parametrize("extra", [
    ["--privileged"], ["-v", "/:/host"], ["--volume=/var/run/docker.sock:/var/run/docker.sock"],
    ["--mount=type=bind,src=/,dst=/host"], ["--device=/dev/kvm"], ["--cap-add=SYS_ADMIN"], ["--network=host"],
    ["--runtime=runc"], ["--pid=host"], ["--userns=host"], ["-e", "TOKEN=x"], ["--env=TOKEN=x"],
    ["--security-opt=seccomp=unconfined"], ["-p", "8080:8080"], ["--sysctl=net.ipv4.ip_forward=1"],
    ["-v/:/host"], ["-e", "X=1"], ["-eX=1"], ["--security-opt=apparmor=unconfined"], ["--tmpfs=/host:rw"],
    ["--mount", "type=bind,src=/,dst=/h"], ["--gpus=all"], ["--entrypoint=/bin/sh"], ["--rm"], ["-it"],
])
def test_forbidden_controls_are_refused(extra):
    argv = container_argv(spec())
    argv[2:2] = extra
    with pytest.raises(SandboxFault, match="unsafe_configuration"):
        assert_hardened(argv)


def test_root_user_unpinned_image_and_swap_are_refused():
    for bad in (spec(user=0), spec(image="python:3.13-slim")):
        with pytest.raises(SandboxFault):
            assert_hardened(container_argv(bad))
    argv = [a if not a.startswith("--memory-swap=") else "--memory-swap=1024m" for a in container_argv(spec())]
    with pytest.raises(SandboxFault):
        assert_hardened(argv)


def test_every_language_uses_a_pinned_image_and_fixed_commands():
    for lang in LANGUAGES.values():
        assert lang.image in pins.ALL_IMAGES and "@sha256:" in lang.image
        assert all(isinstance(a, str) for a in lang.compile_cmd)
    assert "-proc:none" in LANGUAGES["java21"].compile_cmd  # no annotation processors at compile time


def test_pins_are_well_formed():
    import re

    assert re.fullmatch(r"release-\d{8}\.\d+", pins.GVISOR_RELEASE)
    assert re.fullmatch(r"[0-9a-f]{64}", pins.RUNSC_SHA256)
    assert re.fullmatch(r"[0-9a-f]{128}", pins.GVISOR_ARCHIVE_SHA512)
    for image in pins.ALL_IMAGES:
        assert re.fullmatch(r"[a-z-]+@sha256:[0-9a-f]{64}", image)
    assert set(pins.RUNSC_REQUIRED_ARGS) == {"--platform=systrap", "--network=none"}


# --- self-check (fail closed) -------------------------------------------------------------------------------------


class HostDocker:
    def __init__(self, version=pins.DOCKER_ENGINE_VERSION, runtimes=None, images=True):
        self.version = version
        self._runtimes = runtimes if runtimes is not None else {
            "runsc": {"path": "/usr/local/bin/runsc", "runtimeArgs": ["--platform=systrap", "--network=none"]}
        }
        self.images = images

    def server_version(self):
        return self.version

    def runtimes(self):
        return self._runtimes

    def image_present(self, ref):
        return self.images


@pytest.mark.parametrize("docker,failure", [
    (HostDocker(runtimes={}), "runsc_runtime_missing"),
    (HostDocker(version="28.0.0"), "docker_version_not_approved"),
    (HostDocker(runtimes={"runsc": {"path": "/usr/local/bin/runsc", "runtimeArgs": ["--network=none"]}}),
     "runsc_platform_not_systrap"),
    (HostDocker(runtimes={"runsc": {"path": "/usr/local/bin/runsc",
                                    "runtimeArgs": ["--platform=ptrace", "--network=none"]}}),
     "runsc_platform_not_systrap"),
    (HostDocker(runtimes={"runsc": {"path": "/tmp/runsc", "runtimeArgs": ["--platform=systrap", "--network=none"]}}),
     "runsc_path_not_approved"),
    (HostDocker(runtimes={"runsc": {"path": "/usr/local/bin/runsc", "runtimeArgs": ["--platform=systrap"]}}),
     "runsc_args_not_approved"),
    (HostDocker(images=False), "image_not_pinned_or_missing"),
])
def test_self_check_fails_closed(docker, failure, tmp_path):
    result = self_check(docker, runsc_path=str(tmp_path / "no-runsc"))
    assert not result.ok
    assert failure in result.failures
    assert "runsc_binary_missing" in result.failures


def test_self_check_rejects_an_unapproved_runsc_binary(tmp_path):
    fake = tmp_path / "runsc"
    fake.write_bytes(b"#!/bin/sh\necho runsc version release-20261102.0\n")
    result = self_check(HostDocker(runtimes={"runsc": {"path": str(fake),
                                                       "runtimeArgs": ["--platform=systrap", "--network=none"]}}),
                        runsc_path=str(fake))
    assert not result.ok and "runsc_binary_not_approved" in result.failures


def _local_docker() -> str | None:
    path = shutil.which("docker")
    if not path:
        return None
    try:
        ok = subprocess.run([path, "version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None
    return path if ok else None


@pytest.mark.skipif(_local_docker() is None, reason="no local Docker daemon")
def test_real_local_docker_without_gvisor_is_refused():
    """Against this machine's real Docker daemon (no gVisor): the self-check must refuse to serve.

    Only read-only Docker queries run here (version, info, image inspect). No container is created.
    """
    docker = DockerCLI(_local_docker())
    result = self_check(docker)
    assert not result.ok
    assert "runsc_runtime_missing" in result.failures or "runsc_binary_missing" in result.failures


@pytest.mark.skipif(_local_docker() is None, reason="no local Docker daemon")
def test_real_docker_cli_refuses_unsafe_arguments_before_calling_docker():
    docker = DockerCLI(_local_docker())
    argv = [a for a in container_argv(spec()) if a != "--runtime=runsc"]
    with pytest.raises(SandboxFault, match="unsafe_configuration"):
        docker.create(argv)


def test_missing_docker_binary_fails_closed(tmp_path):
    docker = DockerCLI(str(tmp_path / "no-docker"))
    result = self_check(docker, runsc_path=str(tmp_path / "no-runsc"))
    assert not result.ok
    assert {"docker_version_not_approved", "runsc_runtime_missing", "runsc_binary_missing"} <= set(result.failures)
    with pytest.raises(SandboxFault, match="docker_unavailable"):
        docker.list_managed()


def test_argument_structure_is_fixed():
    argv = container_argv(spec())
    for bad in (["create", *argv[1:]], [*argv[:-2], "sh", "-c"], [*argv[:-3], "ubuntu", "sleep", "infinity"]):
        with pytest.raises(SandboxFault):
            assert_hardened(bad)
