"""Phase 7.9: worker /metrics in production needs a dedicated METRICS_TOKEN.

Fresh subprocess per scenario (the production gates run at import), synthetic
secrets only. With a valid token /metrics is exempt from X-Worker-Token and
checks its own bearer token; without one it does not exist (404).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
WORKER_TOKEN = "w" * 40
METRICS = "m" * 20 + "-worker-metrics-" + "z" * 10  # synthetic

_PROBE = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    out = {}
    for name, headers in json.loads(sys.argv[1]).items():
        r = c.get("/metrics", headers=headers)
        out[name] = {"status": r.status_code, "body": r.text[:20000]}
    # the worker token check still protects everything else
    out["score_no_token"] = c.post("/score/proof", json={}).status_code
    print("PROBE_RESULT " + json.dumps(out), flush=True)
    """
)

REQUESTS = {
    "none": {},
    "worker_token_only": {"X-Worker-Token": WORKER_TOKEN},
    "wrong": {"Authorization": "Bearer " + "x" * 46},
    "right": {"Authorization": "Bearer " + METRICS},
}


def _run(extra: dict) -> tuple[dict, str]:
    env = {**os.environ, "ENVIRONMENT": "production", "AI_WORKER_TOKEN": WORKER_TOKEN,
           "METRICS_TOKEN": "", "SENTRY_DSN": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
           **extra}
    proc = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(REQUESTS)], cwd=WORKER, env=env,
                          capture_output=True, text=True, timeout=180)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_RESULT ")), None)
    assert line, f"probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    return json.loads(line[len("PROBE_RESULT "):]), proc.stdout + proc.stderr


def test_production_without_token_has_no_metrics():
    # Unchanged Phase 7 behaviour (test_worker_security): /metrics is not a
    # route, so the worker-token check answers first (401), and with the
    # worker token the path simply does not exist (404).
    out, _ = _run({})
    assert out["worker_token_only"]["status"] == 404
    assert {out[k]["status"] for k in ("none", "wrong", "right")} == {401}
    assert all("http_requests_total" not in out[k]["body"] for k in REQUESTS)
    assert out["score_no_token"] == 401


def test_production_with_token():
    out, logs = _run({"METRICS_TOKEN": METRICS})
    assert out["none"]["status"] == 401
    assert out["worker_token_only"]["status"] == 401  # the worker token is not enough
    assert out["wrong"]["status"] == 401
    assert out["right"]["status"] == 200 and "http_requests_total" in out["right"]["body"]
    assert out["score_no_token"] == 401
    assert METRICS not in logs


def test_token_equal_to_worker_token_or_short_fails_closed():
    for bad in (WORKER_TOKEN, "tiny-mtok-9q"):
        out, logs = _run({"METRICS_TOKEN": bad})
        assert out["worker_token_only"]["status"] == 404  # not registered
        assert out["right"]["status"] == 401  # token check, never the metrics
        assert all("http_requests_total" not in out[k]["body"] for k in REQUESTS)
        assert "/metrics stays disabled" in logs and bad not in logs


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASSED {name}")
