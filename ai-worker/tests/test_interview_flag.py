"""Phase 1: the mock-interview engine is mounted only when its flag is on.

main.py reads the flag at import time, so each case imports a fresh copy of
the app in a subprocess with a controlled environment.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[1]

_PROBE = """
import json
from fastapi.testclient import TestClient
from config import Settings
from main import app
c = TestClient(app)
print(json.dumps({
    "default": Settings.model_fields["MOCK_INTERVIEW_ENABLED"].default,
    "ping": c.get("/interview/ping", headers={"X-Worker-Token": "t" * 40}).status_code,
    "ping_no_token": c.get("/interview/ping").status_code,
    "health": c.get("/health").status_code,
}))
"""


def _probe(flag: str) -> dict:
    # Set the flag explicitly: an env var beats a developer's local .env, so
    # the result never depends on what that file contains. The code default
    # is checked separately via Settings.model_fields.
    env = dict(os.environ)
    env.update(ENVIRONMENT="test", AI_WORKER_TOKEN="t" * 40, SENTRY_DSN="")
    env.update(LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")
    env["MOCK_INTERVIEW_ENABLED"] = flag
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=WORKER_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_default_off_routes_absent():
    r = _probe("false")
    assert r["default"] is False
    assert r["ping"] == 404
    assert r["health"] == 200


def test_enabled_routes_present_and_token_protected():
    r = _probe("true")
    assert r["ping"] == 200
    assert r["ping_no_token"] == 401
    assert r["health"] == 200
