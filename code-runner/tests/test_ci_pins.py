"""The CI sandbox job installs exactly the versions the runner's self-check approves (no drift between them)."""

from pathlib import Path

from runner import pins

CI = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_ci_installs_the_pinned_docker_and_gvisor():
    for value in (
        pins.DOCKER_CE_DEB, pins.DOCKER_CE_DEB_SHA256, pins.DOCKER_CE_CLI_DEB_SHA256, pins.CONTAINERD_DEB,
        pins.CONTAINERD_DEB_SHA256, pins.GVISOR_ARCHIVE_URL, pins.GVISOR_ARCHIVE_SHA512, pins.RUNSC_SHA256,
        pins.CONTAINERD_SHIM_RUNSC_SHA256,
    ):
        assert f'"{value}"' in CI, value
    assert '"--platform=systrap", "--network=none"' in CI
    assert pins.RUNSC_PATH in CI


def test_ci_sandbox_job_uses_no_secrets_and_runs_the_escape_suite():
    job = CI.split("  sandbox:\n", 1)[1]
    assert "secrets." not in job
    assert 'RUNNER_GVISOR_E2E: "1"' in job and "tests/escape" in job
    assert "persist-credentials: false" in job
