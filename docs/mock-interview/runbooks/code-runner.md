# Code runner (gVisor sandbox) — security and operations runbook (Phase 8.5b)

Spec: `../specs/phase-8-advanced-ai-intelligence.md` §4, §5, §11.5, §18. Service: `code-runner/README.md`.

**Status (2026-11-24): nothing here has been executed.** No runner VM exists (G3 not passed), no student code has
run (G4 not passed) and the escape suite has not run on a gVisor host (G2 not passed). Every step below needs an
explicit, approved change. Provisioning and spending need the owner's separate G3 approval.

## 1. Host baseline (8.5c, after G3)

- Dedicated Ubuntu 24.04 VM running nothing else; no Campus AI secret on it except the runner token list and the
  metrics token. SSH key-only, no root login, named operators only, SSH from operator IPs only.
- Firewall: inbound 443 from the backend's egress IPs only (or a private network/tunnel); everything else denied.
  Outbound denied except the scheduled patch window. Docker bridges get a drop rule (`DOCKER-USER`).
- Unattended security updates; disk alert at 80 %; memory alert.
- Install exactly the pinned packages (verify every checksum against `code-runner/runner/pins.py`; the CI job
  `sandbox` in `.github/workflows/ci.yml` is the reference script):
  1. Docker apt key: fingerprint `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`.
  2. `docker-ce` / `docker-ce-cli` `5:29.9.3-1~ubuntu.24.04~noble`, `containerd.io` `2.4.2-3~ubuntu.24.04~noble`
     (download, `sha256sum --check`, then install the local `.deb` files; hold the versions).
  3. gVisor `release-20261102.0`: download `gvisor.tar.bz2`, check SHA-512, extract `runsc` and
     `containerd-shim-runsc-v1`, check their SHA-256, install to `/usr/local/bin` (0755, root-owned).
  4. `/etc/docker/daemon.json`: `"runtimes": {"runsc": {"path": "/usr/local/bin/runsc", "runtimeArgs":
     ["--platform=systrap", "--network=none"]}}`; restart Docker.
  5. `docker pull` each image in `pins.ALL_IMAGES` by digest (the runner uses `--pull=never`).
- The runner runs as a dedicated system user in the `docker` group (Docker access is root-equivalent: this is why the
  VM holds nothing else). Rootless Docker with `runsc` is to be evaluated in 8.5c and adopted only if cgroup limits
  still hold.

## 2. Installing a test pack (audited)

1. In the private repository: `python tools/verify.py --archive`; note the pack digest and the archive SHA-256.
2. Copy `dist/campus-ai-testpack-<digest>.tar` and its `.sha256` to the VM over SSH; `sha256sum --check`.
3. Extract into a new directory owned by root, then `chmod -R a-w` (the runner refuses a writable pack:
   `pack_not_read_only`). Point `RUNNER_PACK_DIR` at it; restart the runner. The runner re-verifies every digest at
   startup (`pack_invalid` on any mismatch).
4. Append to the VM admin log: who, when, pack digest, archive SHA-256, reason. Mirror it in the change record.
5. Old packs are deleted after the switch; a pack is never edited in place.

## 3. Starting, pausing and the kill switches

- Start: `RUNNER_ACCEPTING=true` and a passing self-check. At startup the runner removes every leftover runner
  container, runs the self-check and only then accepts work.
- Pause new work: `RUNNER_ACCEPTING=false` (503 `not_accepting`). Backend-side (8.6): set
  `AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED=false` or empty `CODE_RUNNER_URL` (review-only mode). Last resort: stop
  the VM. Rollback never deletes student data.
- `GET /healthz` = `degraded` means the self-check failed (pins/runtime/images) or the reaper could not remove
  containers. The runner refuses work until fixed. Logs show the fixed reason code (e.g. `runsc_runtime_missing`,
  `docker_version_not_approved`, `image_not_pinned_or_missing`, `remove_failed`).

## 4. Token rotation

1. Generate a new token (>= 32 random characters) on an operator machine; never paste it into tickets or chat.
2. Runner: `RUNNER_TOKENS=<current>,<next>`; restart. 3. Backend: deploy with `CODE_RUNNER_TOKEN=<next>`.
4. Verify traffic (no 401s in `runner_rejections_total{reason="unauthorized"}`). 5. Runner: `RUNNER_TOKENS=<next>`;
restart. 6. Admin-log entry. On suspected compromise: drop the old token immediately (in-flight requests fail as
`error` and are retried by the backend). The metrics token rotates the same way.

## 5. Suspected sandbox compromise

1. Stop the VM (or firewall it off) immediately; set `AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED=false`.
2. Rotate runner and metrics tokens. Treat the installed test pack as leaked (private repository README, "If the pack
   leaks").
3. Preserve the disk image for investigation; rebuild a fresh VM from the baseline; re-run the escape suite (100 %
   required) before re-enabling. Check for a newer gVisor/Docker release and update the pins through review.

## 6. Patching

Monthly window and emergency patches: update the pins in `runner/pins.py` and the CI job in one reviewed change
(verify every checksum at the publisher), run the CI `sandbox` job, then re-run the escape suite on the VM before
accepting work again.

## 7. What the runner records (privacy)

One JSON line per execution (ids, language, kind, verdict, outcome, limit hit, error class, timings, tests run, pack
digest) and fixed-vocabulary events. Never source, input, output, stderr, hidden data or tokens. No Sentry or other
third-party telemetry. Source and outputs exist only in container tmpfs and runner memory for one execution; replay
results are kept in memory for 10 minutes.
