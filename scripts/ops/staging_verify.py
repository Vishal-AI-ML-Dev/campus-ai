"""Read-only deployment verification for Phase 10.7 (gates G2, G4, G10 - partial).

    python scripts/ops/staging_verify.py --api <backend URL> [--expect-commit <sha>]
        [--expect-version 0.35.0] [--gate-header-file <file>] [--frontend-origin <origin>]

Running it against the hosted staging needs SEPARATE OWNER AUTHORIZATION
(Phase 10.7). It never logs in, never writes data and never sends a valid
credential except the optional staging-gate header (read from a file, never
printed). Checks (PASS/FAIL per line; exit 1 on any FAIL):

  * /health 200; /health/ready 200 with database ok (with the gate header);
  * /version: version and build commit equal the expected values (G10);
  * production surface: /docs, /redoc, /openapi.json 404; /metrics 404 or 401;
  * without the gate header every other path is 403 (staging gate on);
  * security headers present; HSTS absent unless --expect-hsts;
  * CORS: a foreign origin gets no Access-Control-Allow-Origin; the frontend
    origin does (when given);
  * CSRF: a POST from a foreign Origin is rejected 403 before any route runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

FOREIGN = "https://foreign.invalid"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api", required=True)
    p.add_argument("--expect-commit", default="")
    p.add_argument("--expect-version", default="")
    p.add_argument("--gate-header-file", type=Path)
    p.add_argument("--frontend-origin", default="")
    p.add_argument("--expect-hsts", action="store_true")
    a = p.parse_args()

    gate = {}
    if a.gate_header_file:
        gate = {"X-Staging-Gate": a.gate_header_file.read_text(encoding="utf-8").strip()}
    with httpx.Client(base_url=a.api.rstrip("/"), timeout=90.0, follow_redirects=False) as c:
        r = c.get("/health")
        check("liveness /health 200", r.status_code == 200, str(r.status_code))
        headers = {k.lower(): v for k, v in r.headers.items()}
        for h in ("x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy"):
            check(f"security header {h}", h in headers)
        check("HSTS as expected", ("strict-transport-security" in headers) == a.expect_hsts)

        r = c.get("/health/ready", headers=gate)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        db_ok = (body.get("checks") or {}).get("database", {}).get("status") == "ok"
        check("readiness 200 + database ok", r.status_code == 200 and db_ok, str(r.status_code))

        r = c.get("/version", headers=gate)
        v = r.json() if r.status_code == 200 else {}
        if a.expect_version:
            check("version matches", v.get("version") == a.expect_version, str(v.get("version")))
        if a.expect_commit:
            got, want = str(v.get("build", "")).lower(), a.expect_commit.lower()
            same = len(got) >= 7 and (want.startswith(got) or got.startswith(want))
            check("build commit matches (G10)", same, got or "missing")

        for path in ("/docs", "/redoc", "/openapi.json"):
            r = c.get(path, headers=gate)
            check(f"{path} not served (production)", r.status_code == 404, str(r.status_code))
        r = c.get("/metrics", headers=gate)
        check("/metrics not public", r.status_code in (401, 404), str(r.status_code))

        if gate:
            r = c.get("/version")
            check("staging gate: 403 without header", r.status_code == 403, str(r.status_code))

        r = c.options("/v1/auth/me", headers={**gate, "Origin": FOREIGN,
                                              "Access-Control-Request-Method": "GET"})
        check("CORS: foreign origin not allowed", "access-control-allow-origin" not in
              {k.lower() for k in r.headers})
        if a.frontend_origin:
            r = c.options("/v1/auth/me", headers={**gate, "Origin": a.frontend_origin,
                                                  "Access-Control-Request-Method": "GET"})
            check("CORS: frontend origin allowed",
                  r.headers.get("access-control-allow-origin") == a.frontend_origin)

        r = c.post("/v1/auth/logout", headers={**gate, "Origin": FOREIGN})
        check("CSRF: foreign-origin POST rejected", r.status_code == 403, str(r.status_code))

    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail and not ok else ""))
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"== {'OK' if not failed else 'FAILED'}: {failed} failed of {len(results)} ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
