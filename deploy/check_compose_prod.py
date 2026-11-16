"""Static policy check for deploy/compose.prod.yml (Phase 7).

Feed it the RESOLVED config produced with a SYNTHETIC env file (never a real
one - `config` output contains every value):

    docker compose -f deploy/compose.prod.yml --env-file <synthetic.env> \
        --profile migrate config --format json | python deploy/check_compose_prod.py

Exit 0 when every rule passes. Prints rule names only, never values.
"""

from __future__ import annotations

import json
import sys

cfg = json.load(sys.stdin)
svcs = cfg["services"]
failures: list[str] = []


def rule(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {name}")
    if not ok:
        failures.append(name)


RUNTIME = ("backend", "worker", "ai-worker", "frontend")
rule("all expected services present", set(RUNTIME) | {"migrate"} <= set(svcs))
rule("no data services (db/redis/qdrant) defined", not ({"db", "redis", "qdrant"} & set(svcs)))

for name, s in svcs.items():
    env = s.get("environment") or {}
    rule(f"{name}: no build section (prebuilt images only)", "build" not in s)
    rule(f"{name}: image pinned by digest", "@sha256:" in (s.get("image") or ""))
    rule(f"{name}: read-only root filesystem", s.get("read_only") is True)
    rule(f"{name}: drops all capabilities", s.get("cap_drop") == ["ALL"])
    rule(f"{name}: no-new-privileges", "no-new-privileges:true" in (s.get("security_opt") or []))
    rule(f"{name}: not privileged", not s.get("privileged"))
    if name != "frontend":
        rule(f"{name}: ENVIRONMENT=production", env.get("ENVIRONMENT") == "production")
    for k, v in env.items():
        low = str(v).lower()
        rule(f"{name}: {k} is not a pasted comment", not low.lstrip().startswith("#"))
        rule(f"{name}: {k} has no dev default credential",
             not any(bad in low for bad in (
                 "postgres:postgres", "local-dev-secret", "local-dev-worker-token",
                 "change-me", "changeme")))
    for p in s.get("ports") or []:
        rule(f"{name}: published port bound to a specific address",
             bool(p.get("host_ip")) and p.get("host_ip") not in ("0.0.0.0", "::"))

mig = svcs.get("migrate", {})
aiw = svcs.get("ai-worker", {})
owner_holders = sorted(n for n, s in svcs.items()
                       if "MIGRATION_DATABASE_URL" in (s.get("environment") or {}))
rule("only the migrate job holds the owner-role URL", owner_holders == ["migrate"])
rule("migrate job is opt-in (profile) and never restarts",
     mig.get("profiles") == ["migrate"] and mig.get("restart") == "no")
rule("migrate job gets no JWT secret", "SECRET_KEY" not in (mig.get("environment") or {}))
rule("ai-worker is not published", not aiw.get("ports"))
rule("worker is not published", not svcs.get("worker", {}).get("ports"))
rule("ai-worker requires a Qdrant server (no embedded fallback)",
     bool((aiw.get("environment") or {}).get("QDRANT_URL")))
rule("frontend has no server-side secrets", not any(
    k in (svcs.get("frontend", {}).get("environment") or {})
    for k in ("SECRET_KEY", "AI_WORKER_TOKEN", "DATABASE_URL", "SUPABASE_SERVICE_KEY")))

print(f"== {'OK' if not failures else 'FAILED'}: {len(failures)} failed ==")
sys.exit(1 if failures else 0)
