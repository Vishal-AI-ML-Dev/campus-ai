"""Phase 7.9: production /metrics requires a dedicated METRICS_TOKEN (P7-D7).

Each scenario imports the REAL main.py in a fresh subprocess (the production
gates run at import time) with synthetic secrets and a throwaway SQLite file.
The lifespan (production DB-role check) is not entered: no DB connection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SECRET = "s" * 40
WORKER_TOKEN = "w" * 40
METRICS = "m" * 20 + "-metrics-token-" + "z" * 10  # synthetic, 45 chars

_PROBE = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    c.get("/health")  # something to count
    out = {}
    for name, headers in json.loads(sys.argv[1]).items():
        r = c.get("/metrics", headers=headers)
        out[name] = {"status": r.status_code, "body": r.text[:20000],
                     "www": r.headers.get("www-authenticate"), "cache": r.headers.get("cache-control")}
    print("PROBE_RESULT " + json.dumps(out), flush=True)
    """
)


def _run(env_extra: dict, requests: dict) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory(prefix="metrics_probe_") as tmp:
        return _run_in(os.path.join(tmp, "probe.db"), env_extra, requests)


def _run_in(db: str, env_extra: dict, requests: dict) -> tuple[dict, str]:
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db}",
        "SECRET_KEY": SECRET,
        "AI_WORKER_TOKEN": WORKER_TOKEN,
        "ENVIRONMENT": "production",
        "METRICS_TOKEN": "",
        "STAGING_GATE_TOKEN": "",
        "REDIS_URL": "",
        # Phase 10.3 (OQ-2): AI governance is mandatory in production (synthetic caps).
        "AI_GOVERNANCE_ENABLED": "true",
        "AI_PLATFORM_DAILY_REQUEST_MAX": "500",
        "AI_PLATFORM_DAILY_TOKEN_MAX": "400000",
        **env_extra,
    }
    proc = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(requests)], cwd=BACKEND,
                          env=env, capture_output=True, text=True, timeout=180)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_RESULT ")), None)
    assert line, f"probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    return json.loads(line[len("PROBE_RESULT "):]), proc.stdout + proc.stderr


REQUESTS = {
    "none": {},
    "wrong": {"Authorization": "Bearer " + "x" * 45},
    "prefix": {"Authorization": "Bearer " + METRICS[:-1]},
    "basic": {"Authorization": "Basic " + METRICS},
    "right": {"Authorization": "Bearer " + METRICS},
}


def test_production_without_token_is_404():
    out, _ = _run({}, REQUESTS)
    assert {v["status"] for v in out.values()} == {404}


def test_production_with_valid_token_requires_it():
    out, logs = _run({"METRICS_TOKEN": METRICS}, REQUESTS)
    for name in ("none", "wrong", "prefix", "basic"):
        assert out[name]["status"] == 401 and out[name]["www"] == "Bearer", name
        assert METRICS not in out[name]["body"]
    assert out["right"]["status"] == 200 and out["right"]["cache"] == "no-store"
    assert "http_requests_total" in out["right"]["body"]
    assert METRICS not in logs and METRICS not in out["right"]["body"]


def test_metrics_carry_no_tenant_user_or_content_labels():
    out, _ = _run({"METRICS_TOKEN": METRICS}, {"right": REQUESTS["right"]})
    body = out["right"]["body"]
    for forbidden in ("tenant", "user_id", "student", "session_id", "email", "request_id", "invocation"):
        assert f'{forbidden}="' not in body


def test_unusable_tokens_fail_closed_without_logging_the_value():
    for bad in ("short-token", METRICS + " ", SECRET, WORKER_TOKEN):
        out, logs = _run({"METRICS_TOKEN": bad}, {"right": {"Authorization": f"Bearer {bad}"}})
        assert out["right"]["status"] == 404, bad
        assert "/metrics stays disabled" in logs
        for value in (bad.strip(), SECRET, WORKER_TOKEN):
            assert value not in logs  # reasons are logged, values never


def test_development_is_unchanged_open_endpoint():
    out, _ = _run({"ENVIRONMENT": "development", "METRICS_TOKEN": METRICS}, {"none": {}})
    assert out["none"]["status"] == 200
