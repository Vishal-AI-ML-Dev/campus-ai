"""Pinned versions, checksums and image digests (P8-S2). Changing any value here is a reviewed code change.

Every value below was verified on 2026-11-24 against the publisher's own distribution point; none is guessed:

- gVisor ``release-20261102.0`` (x86_64): ``gvisor.tar.bz2`` from
  https://storage.googleapis.com/gvisor/releases/release/20261102.0/x86_64/ matched its published ``.sha512``; the
  SHA-256 values are of the ``runsc`` and ``containerd-shim-runsc-v1`` binaries inside that verified archive, and
  ``runsc`` embeds the version string ``release-20261102.0``.
- Docker Engine 29.9.3 / containerd.io 2.4.2-3 for Ubuntu 24.04 (noble, amd64): versions and package SHA-256 from
  https://download.docker.com/linux/ubuntu/dists/noble/stable/binary-amd64/Packages (published 2026-11-03 and
  2026-10-20).
- Images: index digests read from Docker Hub's tag API and confirmed against the registry's
  ``Docker-Content-Digest`` header (registry-1.docker.io), then pulled by digest.

The runner refuses to serve when the host does not match these pins (``sandbox.self_check``).
"""

GVISOR_RELEASE = "release-20261102.0"
GVISOR_ARCHIVE_URL = "https://storage.googleapis.com/gvisor/releases/release/20261102.0/x86_64/gvisor.tar.bz2"
GVISOR_ARCHIVE_SHA512 = (
    "2e6671bb2d033b871c5811ab57e16ac570988b0e0054c525f911cfaf191bab3c"
    "a4efb08e0a98daa4b9e7d248f9e5776ef2ef2f8a6b82d829646a42924c98bb05"
)
RUNSC_SHA256 = "c5a5a7ba1209b605d08874fdd4429035f95afcb2d482bc95ed3fb13d31a25098"
CONTAINERD_SHIM_RUNSC_SHA256 = "6187d6d34f4996e859353fbb4c56d5ed41fc35b5afa44bff766f4e4b424da5bb"
RUNSC_PATH = "/usr/local/bin/runsc"
RUNSC_RUNTIME_NAME = "runsc"
# The Docker runtime entry must pass exactly these arguments to runsc (daemon.json "runtimeArgs").
RUNSC_REQUIRED_ARGS = ("--platform=systrap", "--network=none")

DOCKER_ENGINE_VERSION = "29.9.3"
DOCKER_CE_DEB = "5:29.9.3-1~ubuntu.24.04~noble"
DOCKER_CE_DEB_SHA256 = "c546181f0bc5f1d19085d764a3219aa86069468dbee4743fae420ee8a31809a4"
DOCKER_CE_CLI_DEB_SHA256 = "24f78a893cece8d345c4e90b5163907c047b996b747265f873743e8b0b0546d4"
CONTAINERD_DEB = "2.4.2-3~ubuntu.24.04~noble"
CONTAINERD_DEB_SHA256 = "f811ab57d1f4bb0cce19df7d526da8c711d8ebea0447cc83da1999188e664122"

# Official images pinned by index digest. Tags are informational only; the runner always uses the digest.
IMAGE_GCC = "gcc@sha256:a6f425a380ee03e682df3b436fc9077c544fb0fea0a9cab943c3b8320db10765"  # gcc:14.4.0
IMAGE_PYTHON = (
    "python@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530"  # python:3.13.17-slim-bookworm
)
IMAGE_JAVA = (
    "eclipse-temurin@sha256:57424b1d8eca9c4cf8a4fe226207027911f08d6758628891bf59fcfb53777029"
)  # eclipse-temurin:21.0.12.1_1-jdk-noble

ALL_IMAGES = (IMAGE_GCC, IMAGE_PYTHON, IMAGE_JAVA)
