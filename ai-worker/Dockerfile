# Campus AI - AI Worker for Hugging Face Spaces (Docker SDK) / any container host
# FastAPI service: proof scoring, AI mentor (RAG), resume/ATS, face attendance.
# Pinned (Phase 6) to the digest verified in local builds since Phase 4.
#
# Phase 7 hardening: two stages. The compiler toolchain (build-essential +
# cmake, needed only to compile insightface's Cython extensions) stays in the
# builder stage; the runtime image gets the finished venv and runtime libs only.
# The venv lives at the SAME absolute path in both stages, so its scripts and
# interpreter links stay valid.

# ---------------------------------------------------------------- builder
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    UV_CACHE_DIR=/home/user/.cache/uv \
    UV_LINK_MODE=copy
WORKDIR /home/user/app

COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- runtime
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# Runtime libs only:
#  - libglib2.0-0 + libgomp1: opencv-headless / onnxruntime
#  - curl: health checks / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run the container as a non-root user (UID 1000). HOME stays
# writable: model downloads (InsightFace, fastembed ONNX) are cached there,
# and the embedded Qdrant fallback (QDRANT_URL empty) writes under the app dir.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/app/.venv/bin:/home/user/.local/bin:$PATH \
    INSIGHTFACE_HOME=/home/user/.insightface \
    XDG_CACHE_HOME=/home/user/.cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --from=builder --chown=user /home/user/app/.venv /home/user/app/.venv
# App source (.env is excluded via .dockerignore - secrets come from the host)
COPY --chown=user . .

# Hugging Face Spaces expect the app on port 7860
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
