"""Phase 7.1: Sentry never carries interview content or credentials (backend).

Runs ``packages/campus_obs/sentry_privacy_probe.py`` in a subprocess with this
service's venv and module layout. The probe uses the REAL ``init_sentry`` with
a fake DSN and an in-memory transport (nothing leaves the machine), raises an
error full of sentinel values (resume, JD, answer, bearer token, cookie,
worker token, query string, e-mail) and returns the serialized envelopes -
the exact bytes Sentry would upload. The AI worker has the same test.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
LAYOUT = "backend"
PROBE = SERVICE_DIR.parent / "packages" / "campus_obs" / "sentry_privacy_probe.py"
sys.path.insert(0, str(PROBE.parent))
from sentry_privacy_probe import REQUEST_ID, SENTINELS  # noqa: E402

_CACHE: dict = {}


def _probe() -> dict:
    if "v" in _CACHE:
        return _CACHE["v"]
    env = {**os.environ, "ENVIRONMENT": "test", "PYTHONPATH": str(SERVICE_DIR)}
    proc = subprocess.run(
        [sys.executable, str(PROBE), LAYOUT],
        cwd=SERVICE_DIR, env=env, capture_output=True, text=True, timeout=180,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_RESULT ")), None)
    assert line, f"probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    out = json.loads(line[len("PROBE_RESULT "):])
    out["raw"] = [base64.b64decode(e) for e in out["envelopes"]]
    out["items"] = [
        obj for raw in out["raw"] for ln in raw.splitlines()
        if (obj := _json(ln)) is not None and ("event_id" in obj or "exception" in obj or "spans" in obj)
    ]
    _CACHE["v"] = out
    return out


def _json(line: bytes):
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _errors(out):
    return [i for i in out["items"] if i.get("type") != "transaction" and "exception" in i]


def test_privacy_options_are_set_on_the_real_init():
    out = _probe()
    assert out["enabled"] is True and out["status"] == 500
    assert out["options"]["send_default_pii"] is False
    assert out["options"]["include_local_variables"] is False
    assert out["options"]["max_request_body_size"] == "never"
    assert out["hooks"] == {"before_send": "scrub_event",
                            "before_send_transaction": "scrub_event",
                            "before_breadcrumb": "scrub_breadcrumb"}


def test_no_sentinel_reaches_the_wire_payload():
    out = _probe()
    assert out["raw"], "probe captured no envelopes"
    wire = b"\n".join(out["raw"]).decode("utf-8", "replace")
    for name, value in SENTINELS.items():
        assert value not in wire, f"{name} sentinel leaked into the Sentry payload"
    # Partial tokens too (e.g. a truncated or re-encoded value).
    for frag in ("RESUME-SENTINEL", "JD-SENTINEL", "ANSWER-SENTINEL", "AUTH-SENTINEL",
                 "COOKIE-SENTINEL", "WORKERTOKEN-SENTINEL", "QUERY-SENTINEL", "sentinel@"):
        assert frag not in wire, f"{frag} leaked"


def test_useful_diagnostics_are_kept():
    out = _probe()
    errors = _errors(out)
    assert errors, "no error event captured"
    unhandled = [e for e in errors if any(v.get("type") == "ValueError"
                                          for v in e["exception"]["values"])]
    assert unhandled, "the unhandled ValueError was not reported"
    ev = unhandled[0]
    exc = ev["exception"]["values"][-1]
    assert exc["type"] == "ValueError" and exc["value"] == "[redacted]"
    frames = exc["stacktrace"]["frames"]
    assert any(f.get("function") == "answer" for f in frames)
    assert all("vars" not in f for f in frames)
    req = ev["request"]
    assert req["method"] == "POST"
    assert req["url"].endswith("/v1/mock-interview/sessions/42/answer")
    assert "data" not in req and "cookies" not in req and "query_string" not in req
    assert {k.lower() for k in req.get("headers", {})} <= {
        "content-type", "content-length", "accept", "user-agent", "x-request-id"}
    assert ev["tags"]["request_id"] == REQUEST_ID
    assert ev["environment"] == "test" and ev["release"] == "probe@1"
    assert "user" not in ev and "extra" not in ev
    assert "candidate" not in ev.get("contexts", {})


def test_log_error_event_keeps_template_not_values():
    out = _probe()
    logged = [e for e in out["items"] if (e.get("logentry") or {}).get("message")]
    assert logged, "expected the logger.error event"
    entry = logged[0]["logentry"]
    assert entry["message"] == "evaluation failed for %s"
    assert "params" not in entry and "formatted" not in entry


def test_transaction_payload_is_scrubbed():
    out = _probe()
    txs = [i for i in out["items"] if i.get("type") == "transaction"]
    assert txs, "expected a transaction (sample rate forced to 1.0 in the probe)"
    assert txs[0]["tags"]["request_id"] == REQUEST_ID


def test_scrubber_on_synthetic_payloads():
    out = _probe()
    tx, crumb = out["synthetic_tx"], out["crumb"]
    text = json.dumps(tx) + json.dumps(crumb)
    for value in SENTINELS.values():
        assert value not in text
    assert tx["request"] == {"url": "http://w/x"}
    assert tx["spans"][0]["data"] == {"http.method": "POST"}
    assert tx["spans"][0]["description"] == "POST http://worker/interview/turn"
    assert tx["spans"][1]["data"] == {}
    assert tx["contexts"] == {"trace": {"trace_id": "t", "span_id": "s"}}
    kept = tx["breadcrumbs"]["values"][0]
    assert kept == {"category": "httplib",
                    "data": {"method": "POST", "status_code": 200, "url": "http://w/p"}}
    assert crumb == {"category": "log", "level": "info"}
    assert out["scrub_failure_drops_event"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASSED {name}")
