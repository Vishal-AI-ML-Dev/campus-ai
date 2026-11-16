"""Phase 7: security headers, docs/OpenAPI and /metrics exposure by environment.

Development/test behaviour uses the normal client; production behaviour runs
in a subprocess (main.py evaluates its gates at import time), reusing the
Phase 6 helper.
"""

import json

from app.core.security_headers import API_CSP, hsts_value
from tests.test_config_hardening import _run_prod


def _assert_base_headers(h):
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store"
    assert "camera=()" in h["permissions-policy"]


# --- development / test ---------------------------------------------------------
def test_api_responses_carry_strict_headers(client):
    r = client.get("/health")
    _assert_base_headers(r.headers)
    assert r.headers["content-security-policy"] == API_CSP
    assert "strict-transport-security" not in r.headers  # HSTS is opt-in


def test_error_and_csrf_responses_carry_headers(client):
    assert client.get("/v1/does-not-exist").headers["x-frame-options"] == "DENY"
    blocked = client.post("/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert blocked.status_code == 403
    _assert_base_headers(blocked.headers)
    assert blocked.headers["content-security-policy"] == API_CSP


def test_docs_and_metrics_available_outside_production(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    # Swagger UI needs its CDN assets + inline script: no strict CSP here.
    assert "content-security-policy" not in docs.headers
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/db-check").status_code == 200


def test_hsts_value():
    assert hsts_value(0, True) is None
    assert hsts_value(300, False) == "max-age=300"
    assert hsts_value(31536000, True) == "max-age=31536000; includeSubDomains"


# --- production (subprocess) ------------------------------------------------------
_PROBE = """
import json
from fastapi.testclient import TestClient
import main
paths = ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/metrics",
         "/db-check", "/health", "/health/ready", "/version"]
out = {}
with TestClient(main.app) as c:
    for p in paths:
        r = c.get(p)
        out[p] = {"status": r.status_code,
                  "csp": r.headers.get("content-security-policy"),
                  "hsts": r.headers.get("strict-transport-security"),
                  "xfo": r.headers.get("x-frame-options")}
print("RESULT " + json.dumps(out))
"""


def _prod_probe(**env):
    r = _run_prod(_PROBE, **env)
    assert r.returncode == 0, r.stderr[-2000:]
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")][0]
    return json.loads(line[len("RESULT "):])


def test_production_hides_docs_openapi_and_metrics():
    out = _prod_probe()
    for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/metrics",
              "/db-check"):
        assert out[p]["status"] == 404, (p, out[p])
        assert out[p]["csp"] == API_CSP  # even the 404 is strict
    assert out["/health"]["status"] == 200
    assert out["/version"]["status"] == 200
    for p in out:
        assert out[p]["xfo"] == "DENY"
        assert out[p]["hsts"] is None  # not configured -> not sent


def test_production_hsts_only_when_configured():
    out = _prod_probe(HSTS_MAX_AGE="300")
    assert out["/health"]["hsts"] == "max-age=300"
    out = _prod_probe(HSTS_MAX_AGE="31536000", HSTS_INCLUDE_SUBDOMAINS="true")
    assert out["/health"]["hsts"] == "max-age=31536000; includeSubDomains"
