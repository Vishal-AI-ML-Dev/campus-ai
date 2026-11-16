# Campus AI backend - FastAPI (uv, Python 3.13)
# Pinned (Phase 6) to the digest verified in local builds since Phase 4.
# Phase 7 hardening: the runtime never invokes uv (no sync, no network at
# start), app code is root-owned and read-only for the runtime user, and the
# venv is first on PATH so `python worker.py` uses the real dependencies.
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# uv (fast Python package manager) from the official image - BUILD time only.
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /bin/

WORKDIR /app

# curl kept for container health checks / debugging
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching (no project, no dev deps)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application source and finish the install
COPY . .
RUN uv sync --frozen --no-dev

# Non-root runtime user. /app stays owned by root: the API and the job worker
# only READ their code and venv (nothing is written under /app at runtime).
RUN useradd --create-home --uid 10001 appuser
USER appuser

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_SYNC=1 \
    UV_OFFLINE=1 \
    PORT=8000
EXPOSE 8000

# PaaS hosts (Koyeb/Render) inject $PORT; default to 8000 locally.
# exec: uvicorn becomes PID 1's child-free replacement and receives SIGTERM.
# Job worker: override the command with `python worker.py` (venv python).
# Migrations: run `alembic upgrade head` as a ONE-OFF container with the owner
# role URL in MIGRATION_DATABASE_URL (never in the API/worker environment).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
