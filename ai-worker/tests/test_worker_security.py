"""Phase 7: AI worker token check, production gates, docs/metrics exposure.

Each case runs main.py in a fresh subprocess (its gates run at import time)
with synthetic tokens only. Works under pytest, or standalone in the
production image (which ships no pytest):

    python tests/test_worker_security.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
GOOD = "w" * 40  # synthetic shared secret (>= 32 chars), test-only

_PROBE = """
import json
from fastapi.testclient import TestClient
import main
paths = ["/", "/health", "/docs", "/redoc", "/openapi.json", "/metrics"]
out = {"open": sorted(main._OPEN_PATHS)}
with TestClient(main.app) as c:
    for p in paths:
        out[p] = c.get(p).status_code
    # A protected route: wrong / missing / right token. 422 = auth passed,
    # body validation failed (no LLM call is made).
    url = "/score/proof"
    out["no_token"] = c.post(url, json={}).status_code
    out["bad_token"] = c.post(url, json={}, headers={"X-Worker-Token": "nope"}).status_code
    out["prefix_token"] = c.post(url, json={}, headers={"X-Worker-Token": TOKEN[:-1]}).status_code
    out["good_token"] = c.post(url, json={}, headers={"X-Worker-Token": TOKEN}).status_code
    fh = c.get("/face/health", headers={"X-Worker-Token": TOKEN})
    out["face_health"] = fh.status_code
    out["face_enroll"] = c.post("/face/enroll", json={}, headers={"X-Worker-Token": TOKEN}).status_code
print("RESULT " + json.dumps(out))
"""


def _run(environment: str, token: str, **extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in {"AI_WORKER_TOKEN", "ENVIRONMENT", "SENTRY_DSN",
                        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"}}
    env.update({"ENVIRONMENT": environment, "AI_WORKER_TOKEN": token,
                "PYTHONDONTWRITEBYTECODE": "1", "GROQ_API_KEY": "", "GOOGLE_API_KEY": ""})
    env.update(extra)
    code = f"TOKEN = {token!r}\n" + textwrap.dedent(_PROBE)
    return subprocess.run([sys.executable, "-c", code], cwd=WORKER, env=env,
                          capture_output=True, text=True, timeout=300)


def _result(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, r.stderr[-2000:]
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")][0]
    return json.loads(line[len("RESULT "):])


def test_production_refuses_empty_or_short_token():
    for tok in ("", "short-token"):
        r = _run("production", tok)
        assert r.returncode != 0
        assert "Refusing to start in production" in r.stderr
        if tok:
            assert tok not in r.stderr  # never echo the secret


def test_unknown_environment_is_rejected():
    r = _run("prod", GOOD)
    assert r.returncode != 0
    assert "ENVIRONMENT must be one of" in r.stderr


def test_production_hides_docs_and_metrics_and_checks_token():
    out = _result(_run("production", GOOD))
    assert out["open"] == ["/", "/health"]
    assert out["/health"] == 200 and out["/"] == 200
    for p in ("/docs", "/redoc", "/openapi.json", "/metrics"):
        # Not open and not registered: the token gate answers first (401).
        assert out[p] in (401, 404), (p, out[p])
    assert out["no_token"] == 401
    assert out["bad_token"] == 401
    assert out["prefix_token"] == 401
    assert out["good_token"] == 422  # past auth; empty body rejected


def test_development_keeps_docs_and_metrics():
    out = _result(_run("development", GOOD))
    assert out["/docs"] == 200 and out["/openapi.json"] == 200 and out["/metrics"] == 200
    assert out["no_token"] == 401 and out["good_token"] == 422


def test_face_kill_switch_returns_503_without_loading_models():
    out = _result(_run("production", GOOD, FACE_ENABLED="false"))
    assert out["face_health"] == 503
    assert out["face_enroll"] == 503  # refused before body validation / model load
    assert out["good_token"] == 422  # other routes unaffected


def test_face_enabled_by_default():
    out = _result(_run("development", GOOD))
    assert out["face_health"] != 503
    assert out["face_enroll"] == 422  # reaches validation; no model loaded for a bad body


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASSED {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAILED {t.__name__}: {exc!r}"[:600])
    print(f"== {len(tests) - failed} passed, {failed} failed ==")
    sys.exit(1 if failed else 0)
