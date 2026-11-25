"""Write a SYNTHETIC env file for the compose policy check (Phase 10.2 / 10.4).

    python deploy/make_synthetic_env.py <out.env>
    docker compose -f deploy/compose.prod.yml --env-file <out.env> --profile migrate \
        config --format json | python deploy/check_compose_prod.py

Every key in prod.env.example gets its documented default, and the required
keys get obviously fake values (``*.invalid`` hosts, repeated letters). Nothing
here is a real secret, host or decision; the output is safe to print.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MANIFEST = Path(__file__).with_name("prod.env.example")

SYNTHETIC = {
    "BACKEND_IMAGE": "registry.invalid/backend@sha256:" + "a" * 64,
    "AI_WORKER_IMAGE": "registry.invalid/ai-worker@sha256:" + "b" * 64,
    "FRONTEND_IMAGE": "registry.invalid/frontend@sha256:" + "c" * 64,
    "APP_DATABASE_URL": "postgresql+psycopg://app_user:SYNTHETIC@db.invalid:5432/app",
    "MIGRATION_DATABASE_URL": "postgresql+psycopg://owner:SYNTHETIC@db.invalid:5432/app",
    "SECRET_KEY": "S" * 48,
    "AI_WORKER_TOKEN": "W" * 48,
    "REDIS_URL": "rediss://:SYNTHETIC@redis.invalid:6379/0",
    "QDRANT_URL": "https://qdrant.invalid",
    "CORS_ORIGINS": "https://frontend.invalid",
    "TRUSTED_PROXY_HOPS": "1",
    "AI_PLATFORM_DAILY_REQUEST_MAX": "500",
    "AI_PLATFORM_DAILY_TOKEN_MAX": "400000",
}


def main(out: str) -> None:
    lines = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            key, default = m.groups()
            lines.append(f"{key}={SYNTHETIC.get(key, default.strip())}")
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
