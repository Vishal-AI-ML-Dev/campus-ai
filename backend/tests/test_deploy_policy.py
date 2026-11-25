"""Phase 10.2 (gate G1): production templates cover every setting; policy check rules.

Static and offline: reads deploy/compose.prod.yml, deploy/prod.env.example and
deploy/check_compose_prod.py from the repository. No docker, no real env file.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from app.core.config import Settings

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
_spec = importlib.util.spec_from_file_location("check_compose_prod", DEPLOY / "check_compose_prod.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

# Settings that are intentionally NOT passed by the compose template.
_NOT_DEPLOYED = {
    "PROJECT_NAME", "API_V1_PREFIX", "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS", "MIGRATION_DATABASE_URL",  # migrate job only
    "COOKIE_SECURE",  # code default (production: Secure) is the policy
    # set on the backend service itself (edge policy), not in the shared anchor
    "CORS_ORIGINS", "TRUSTED_PROXY_HOPS", "COOKIE_SAMESITE", "COOKIE_DOMAIN",
    "HSTS_MAX_AGE", "HSTS_INCLUDE_SUBDOMAINS",
}


def _compose_text() -> str:
    return (DEPLOY / "compose.prod.yml").read_text(encoding="utf-8")


def _manifest_keys() -> set[str]:
    text = (DEPLOY / "prod.env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.M))


def test_every_backend_setting_is_in_the_compose_template():
    compose = _compose_text()
    missing = [
        name for name in Settings.model_fields
        if name not in _NOT_DEPLOYED and not re.search(rf"^\s+{name}:", compose, flags=re.M)
    ]
    assert missing == []


def test_policy_lists_every_deployed_backend_setting():
    listed = set(policy.BACKEND_KEYS) | set(policy.BACKEND_FLAGS) | set(policy.NEVER_TRUE)
    missing = [n for n in Settings.model_fields
               if n not in _NOT_DEPLOYED and n not in listed and n != "DATABASE_URL"]
    assert missing == []


def test_manifest_names_every_template_variable():
    lines = [ln for ln in _compose_text().splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(lines)
    variables = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", code))
    assert sorted(variables - _manifest_keys()) == []


def test_manifest_flags_default_off_and_governance_on():
    text = (DEPLOY / "prod.env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.M))
    for flag in policy.ALL_FLAGS:
        assert values.get(flag, "false") == "false", flag
    assert values["AI_GOVERNANCE_ENABLED"] == "true"
    assert values["AI_PRICE_TABLE"] == ""
    # No secret values in the tracked manifest.
    for key in ("SECRET_KEY", "AI_WORKER_TOKEN", "METRICS_TOKEN", "STAGING_GATE_TOKEN",
                "SUPABASE_SERVICE_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "APP_DATABASE_URL",
                "MIGRATION_DATABASE_URL", "LANGFUSE_SECRET_KEY", "QDRANT_API_KEY"):
        assert values.get(key, "") == "", key


def _good_cfg() -> dict:
    hardening = {"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
    backend_env = {k: "" for k in policy.BACKEND_KEYS}
    backend_env.update({f: "false" for f in policy.BACKEND_FLAGS + policy.NEVER_TRUE})
    backend_env.update({"ENVIRONMENT": "production", "AI_GOVERNANCE_ENABLED": "true",
                        "AI_PLATFORM_DAILY_REQUEST_MAX": "500", "AI_PLATFORM_DAILY_TOKEN_MAX": "400000"})
    aiw_env = {k: "" for k in policy.AI_WORKER_KEYS}
    aiw_env.update({f: "false" for f in policy.AI_WORKER_FLAGS})
    aiw_env.update({"ENVIRONMENT": "production", "QDRANT_URL": "https://q.invalid",
                    "LLM_PROVIDER": "groq", "LLM_TIMEOUT_SECONDS": "30", "LLM_MAX_RETRIES": "0"})
    image = "r.invalid/x@sha256:" + "a" * 64
    return {"services": {
        "migrate": {**hardening, "image": image, "profiles": ["migrate"], "restart": "no",
                    "environment": {"ENVIRONMENT": "production", "MIGRATION_DATABASE_URL": "x"}},
        "backend": {**hardening, "image": image, "environment": dict(backend_env),
                    "ports": [{"host_ip": "127.0.0.1"}]},
        "worker": {**hardening, "image": image, "environment": dict(backend_env)},
        "ai-worker": {**hardening, "image": image, "environment": aiw_env},
        "frontend": {**hardening, "image": image, "environment": {}, "ports": [{"host_ip": "127.0.0.1"}]},
    }}


def _failed(cfg, approved=frozenset()):
    return [name for name, ok in policy.check(cfg, approved) if not ok]


def test_good_config_passes():
    assert _failed(_good_cfg()) == []


def test_each_missing_backend_key_fails():
    for key in policy.BACKEND_KEYS:
        cfg = _good_cfg()
        del cfg["services"]["backend"]["environment"][key]
        assert any(key in f for f in _failed(cfg)), key


def test_enabled_flag_needs_approval():
    cfg = _good_cfg()
    cfg["services"]["backend"]["environment"]["AI_MOCK_INTERVIEW_ENABLED"] = "true"
    assert _failed(cfg) == ["backend: flag AI_MOCK_INTERVIEW_ENABLED off unless approved"]
    assert _failed(cfg, frozenset({"AI_MOCK_INTERVIEW_ENABLED"})) == []
    assert _failed(_good_cfg(), frozenset({"NOT_A_FLAG"})) == ["every --approve names a known feature flag"]


def test_face_recognition_is_off_for_the_pilot():
    """Release decision B-8: FACE_ENABLED must be present and false unless separately approved."""
    assert "FACE_ENABLED" in policy.AI_WORKER_FLAGS
    assert re.search(r"^\s+FACE_ENABLED: \$\{FACE_ENABLED:-false\}$", _compose_text(), flags=re.M)
    text = (DEPLOY / "prod.env.example").read_text(encoding="utf-8")
    assert re.search(r"^FACE_ENABLED=false$", text, flags=re.M)

    cfg = _good_cfg()
    cfg["services"]["ai-worker"]["environment"]["FACE_ENABLED"] = "true"
    assert _failed(cfg) == ["ai-worker: flag FACE_ENABLED off unless approved"]
    assert _failed(cfg, frozenset({"FACE_ENABLED"})) == []

    cfg = _good_cfg()
    del cfg["services"]["ai-worker"]["environment"]["FACE_ENABLED"]
    assert _failed(cfg) == ["ai-worker: key FACE_ENABLED present"]


def test_governance_and_worker_budget_are_mandatory():
    cfg = _good_cfg()
    env = cfg["services"]["worker"]["environment"]
    env["AI_GOVERNANCE_ENABLED"] = "false"
    env["AI_PLATFORM_DAILY_TOKEN_MAX"] = "0"
    env["AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS"] = "true"
    env["AI_PRICE_TABLE"] = '{"x": 1}'
    aiw = cfg["services"]["ai-worker"]["environment"]
    aiw["LLM_TIMEOUT_SECONDS"] = "120"
    aiw["LLM_MAX_RETRIES"] = "6"
    failed = _failed(cfg)
    for expected in ("worker: AI_GOVERNANCE_ENABLED=true",
                     "worker: AI_PLATFORM_DAILY_TOKEN_MAX is a positive integer",
                     "worker: AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS is false",
                     "worker: AI_PRICE_TABLE empty (D4, never invent prices)",
                     "ai-worker: LLM_TIMEOUT_SECONDS within 1-45",
                     "ai-worker: LLM_MAX_RETRIES is 0 or 1"):
        assert expected in failed
