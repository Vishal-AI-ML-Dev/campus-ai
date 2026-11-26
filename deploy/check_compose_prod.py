"""Static policy check for deploy/compose.prod.yml (Phase 7; extended in Phase 10.2).

Feed it the RESOLVED config produced with a SYNTHETIC env file (never a real
one - `config` output contains every value):

    docker compose -f deploy/compose.prod.yml --env-file <synthetic.env> \
        --profile migrate config --format json | python deploy/check_compose_prod.py

Product feature flags must stay OFF unless explicitly approved for this
deployment, one ``--approve KEY`` per flag (e.g. ``--approve
AI_MOCK_INTERVIEW_ENABLED``). Exit 0 when every rule passes. Prints rule names
and key names only, never values.
"""

from __future__ import annotations

import argparse
import json
import sys

RUNTIME = ("backend", "worker", "ai-worker", "frontend")

# Phase 10.2 (gate G1): every key a backend/worker container must receive.
BACKEND_KEYS = (
    "ENVIRONMENT", "DATABASE_URL", "REDIS_URL", "SECRET_KEY", "AI_WORKER_URL", "AI_WORKER_TOKEN",
    "SENTRY_DSN", "ALERT_WEBHOOK_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
    "SUPABASE_STORAGE_BUCKET", "METRICS_TOKEN", "STAGING_GATE_TOKEN",
    "AI_GOVERNANCE_ENABLED", "AI_PLATFORM_DAILY_REQUEST_MAX", "AI_PLATFORM_DAILY_TOKEN_MAX",
    "AI_TENANT_DAILY_REQUEST_MAX", "AI_USER_DAILY_REQUEST_MAX", "AI_MAX_CONCURRENT_CALLS",
    "AI_CONCURRENCY_WAIT_SECONDS", "AI_QUOTA_COOLDOWN_SECONDS", "AI_IDEMPOTENCY_TTL_SECONDS",
    "AI_KILL_SWITCH", "AI_DISABLED_FEATURES",
    "AI_CIRCUIT_ENABLED", "AI_CIRCUIT_FAILURE_THRESHOLD", "AI_CIRCUIT_ERROR_RATE",
    "AI_CIRCUIT_MIN_CALLS", "AI_CIRCUIT_WINDOW_SECONDS", "AI_CIRCUIT_OPEN_SECONDS",
    "AI_ALERT_THROTTLE_SECONDS",
    "AI_MOCK_INTERVIEW_TENANT_IDS", "AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS",
    "AI_MOCK_INTERVIEW_CODING_TENANT_IDS", "AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX",
    "AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX", "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT",
    "AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_MIN_CALLS", "AI_PRICE_TABLE",
    "PLACEMENT_MANAGEMENT_TENANT_IDS", "BUILD_COMMIT",
)
# Product feature flags: must be "false" unless approved with --approve.
BACKEND_FLAGS = (
    "AI_MOCK_INTERVIEW_ENABLED", "AI_MOCK_INTERVIEW_VOICE_ENABLED", "AI_MOCK_INTERVIEW_VIDEO_ENABLED",
    "AI_MOCK_INTERVIEW_CODING_ENABLED", "AI_MOCK_INTERVIEW_CODING_AI_ENABLED",
    "AI_MOCK_INTERVIEW_COMPANY_ENABLED", "AI_MOCK_INTERVIEW_OPS_ENABLED",
    "PLACEMENT_MANAGEMENT_ENABLED", "CLIENT_ERROR_REPORTING_ENABLED",
)
# Never true in production, approval or not.
NEVER_TRUE = ("AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS",)
AI_WORKER_KEYS = (
    "ENVIRONMENT", "PORT", "AI_WORKER_TOKEN", "QDRANT_URL", "QDRANT_API_KEY", "LLM_PROVIDER",
    "GROQ_API_KEY", "GOOGLE_API_KEY", "GROQ_MODEL", "SENTRY_DSN", "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY", "METRICS_TOKEN", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES",
)
# FACE_ENABLED (biometric face recognition): disabled for the pilot (release
# decision B-8); true fails unless separately approved with --approve FACE_ENABLED.
AI_WORKER_FLAGS = ("MOCK_INTERVIEW_ENABLED", "MOCK_INTERVIEW_CODING_ENABLED", "MOCK_INTERVIEW_COMPANY_ENABLED",
                   "FACE_ENABLED")
ALL_FLAGS = BACKEND_FLAGS + AI_WORKER_FLAGS


def _pos_int(value: object) -> bool:
    try:
        return int(str(value).strip()) > 0
    except ValueError:
        return False


def _float_in(value: object, lo: float, hi: float) -> bool:
    try:
        return lo <= float(str(value).strip()) <= hi
    except ValueError:
        return False


def check(cfg: dict, approved: frozenset[str] = frozenset()) -> list[tuple[str, bool]]:
    """All (rule name, passed) pairs for a resolved compose config."""
    results: list[tuple[str, bool]] = []

    def rule(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))

    svcs = cfg.get("services") or {}
    rule("all expected services present", set(RUNTIME) | {"migrate"} <= set(svcs))
    rule("no data services (db/redis/qdrant) defined", not ({"db", "redis", "qdrant"} & set(svcs)))
    rule("every --approve names a known feature flag", approved <= set(ALL_FLAGS))

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

    # --- Phase 10.2: configuration parity, flags OFF, mandatory AI governance.
    for name in ("backend", "worker"):
        env = (svcs.get(name) or {}).get("environment") or {}
        for key in BACKEND_KEYS + BACKEND_FLAGS + NEVER_TRUE:
            rule(f"{name}: key {key} present", key in env)
        for flag in BACKEND_FLAGS:
            value = str(env.get(flag, "false")).strip().lower()
            rule(f"{name}: flag {flag} off unless approved", value == "false" or flag in approved)
        for key in NEVER_TRUE:
            rule(f"{name}: {key} is false", str(env.get(key, "false")).strip().lower() == "false")
        rule(f"{name}: AI_GOVERNANCE_ENABLED=true",
             str(env.get("AI_GOVERNANCE_ENABLED", "")).strip().lower() == "true")
        for key in ("AI_PLATFORM_DAILY_REQUEST_MAX", "AI_PLATFORM_DAILY_TOKEN_MAX"):
            rule(f"{name}: {key} is a positive integer", _pos_int(env.get(key, "")))
        rule(f"{name}: AI_PRICE_TABLE empty (D4, never invent prices)", not env.get("AI_PRICE_TABLE"))

    aiw_env = (svcs.get("ai-worker") or {}).get("environment") or {}
    for key in AI_WORKER_KEYS + AI_WORKER_FLAGS:
        rule(f"ai-worker: key {key} present", key in aiw_env)
    for flag in AI_WORKER_FLAGS:
        value = str(aiw_env.get(flag, "false")).strip().lower()
        rule(f"ai-worker: flag {flag} off unless approved", value == "false" or flag in approved)
    rule("ai-worker: LLM_TIMEOUT_SECONDS within 1-45", _float_in(aiw_env.get("LLM_TIMEOUT_SECONDS", ""), 1, 45))
    rule("ai-worker: LLM_MAX_RETRIES is 0 or 1", str(aiw_env.get("LLM_MAX_RETRIES", "")).strip() in ("0", "1"))
    rule("ai-worker: LLM_PROVIDER is groq or gemini",
         str(aiw_env.get("LLM_PROVIDER", "")).strip().lower() in ("groq", "gemini"))

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
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--approve", action="append", default=[], metavar="FLAG",
                        help="a product feature flag approved ON for this deployment")
    args = parser.parse_args(argv)
    cfg = json.load(sys.stdin)
    results = check(cfg, frozenset(args.approve))
    failures = [name for name, ok in results if not ok]
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}: {name}")
    print(f"== {'OK' if not failures else 'FAILED'}: {len(failures)} failed ==")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
