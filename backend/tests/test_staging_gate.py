"""Phase 7B: private-staging access gate (STAGING_GATE_TOKEN)."""

import json

from tests.test_config_hardening import _run_prod

GATE = "g" * 48  # synthetic, test-only

_PROBE = """
import json
from fastapi.testclient import TestClient
import main
G = {"X-Staging-Gate": GATE}
O = {"Origin": "https://app.example.com"}
out = {}
with TestClient(main.app) as c:
    out["health"] = c.get("/health").status_code
    out["version_nogate"] = c.get("/version").status_code
    out["root_nogate"] = c.get("/").status_code
    out["login_nogate"] = c.post("/v1/auth/login", data={"username": "a@b.co", "password": "x"}, headers=O).status_code
    out["signup_nogate"] = c.post("/v1/tenants/signup", json={}, headers=O).status_code
    out["wrong_gate"] = c.get("/version", headers={"X-Staging-Gate": GATE[:-1] + "x"}).status_code
    r = c.get("/version", headers={"X-Staging-Gate": ""})
    out["empty_gate"] = r.status_code
    out["gate_403_has_headers"] = r.headers.get("x-frame-options") == "DENY"
    out["gate_403_body"] = r.json()
    out["version_gate"] = c.get("/version", headers=G).status_code
    out["signup_gate"] = c.post("/v1/tenants/signup", json={}, headers={**G, **O}).status_code
    out["csrf_still_applies"] = c.post("/v1/auth/logout", headers={**G, "Origin": "https://evil.example"}).status_code
print("RESULT " + json.dumps(out))
"""


def _probe(**env):
    r = _run_prod(f"GATE = {GATE!r}\n" + _PROBE, **env)
    assert r.returncode == 0, r.stderr[-2000:]
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")][0]
    return json.loads(line[len("RESULT "):]), r


def test_gate_blocks_everything_but_health_without_header():
    out, _ = _probe(STAGING_GATE_TOKEN=GATE)
    assert out["health"] == 200
    for k in ("version_nogate", "root_nogate", "login_nogate", "signup_nogate",
              "wrong_gate", "empty_gate"):
        assert out[k] == 403, (k, out[k])
    assert out["gate_403_has_headers"] is True
    assert out["gate_403_body"] == {"detail": "Forbidden"}


def test_gate_passes_with_header_and_csrf_still_enforced():
    out, _ = _probe(STAGING_GATE_TOKEN=GATE)
    assert out["version_gate"] == 200
    assert out["signup_gate"] == 422  # reached the route (empty body)
    assert out["csrf_still_applies"] == 403


def test_gate_value_never_logged():
    _, r = _probe(STAGING_GATE_TOKEN=GATE)
    assert GATE not in r.stderr


def test_production_refuses_short_gate_token():
    r = _run_prod("import main", STAGING_GATE_TOKEN="short-gate")
    assert r.returncode != 0
    assert "STAGING_GATE_TOKEN" in r.stderr and "short-gate" not in r.stderr


def test_gate_off_by_default(client):
    assert client.get("/version").status_code == 200
