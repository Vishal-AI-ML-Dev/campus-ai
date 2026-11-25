"""Phase 6 configuration hardening: fail-closed secrets, environment names,
production-only origins, and the runtime DB-role gate.

Production behaviour is exercised in a SUBPROCESS (fresh interpreter, SQLite
URL, synthetic secrets) because main.py evaluates its gates at import time.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, insecure_secret_issues
from app.core.db import runtime_role_issues

BACKEND = Path(__file__).resolve().parents[1]
STRONG_KEY = "k" * 64  # synthetic, only ever used inside these tests


def _settings(**overrides) -> Settings:
    base = {"DATABASE_URL": "sqlite://", "SECRET_KEY": STRONG_KEY}
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- ENVIRONMENT -------------------------------------------------------------
@pytest.mark.parametrize("value", ["prod", "staging", "dev", "", "production2"])
def test_unknown_environment_is_rejected(value):
    with pytest.raises(ValidationError):
        _settings(ENVIRONMENT=value)


@pytest.mark.parametrize(
    "value,expected",
    [("Production", "production"), (" TEST ", "test"), ("development", "development")],
)
def test_environment_is_normalised(value, expected):
    assert _settings(ENVIRONMENT=value).ENVIRONMENT == expected


def test_uppercase_production_still_gets_secure_cookies():
    s = _settings(ENVIRONMENT="PRODUCTION")
    assert s.cookie_secure is True
    assert s.cookie_samesite == "none"


# --- SECRET_KEY ----------------------------------------------------------------
def test_secret_key_has_no_default(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DATABASE_URL="sqlite://")


@pytest.mark.parametrize(
    "key", ["dev-secret-change-me", "changeme", "short-but-not-a-placeholder"]
)
def test_weak_secret_keys_are_flagged(key):
    issues = insecure_secret_issues(_settings(SECRET_KEY=key, AI_WORKER_TOKEN="t"))
    assert any("SECRET_KEY" in i for i in issues)


def test_strong_secrets_pass():
    assert insecure_secret_issues(_settings(AI_WORKER_TOKEN="worker-token")) == []


# --- runtime DB role gate ------------------------------------------------------
class _FakeConn:
    def __init__(self, dialect, row):
        self.dialect = type("D", (), {"name": dialect})()
        self._row = row

    def execute(self, _stmt):
        row = self._row
        return type("R", (), {"one": lambda _self: row})()


@pytest.mark.parametrize(
    "row,count",
    [((False, False), 0), ((True, False), 1), ((False, True), 1), ((True, True), 2)],
)
def test_runtime_role_issues(row, count):
    assert len(runtime_role_issues(_FakeConn("postgresql", row))) == count


def test_runtime_role_check_skips_non_postgres():
    assert runtime_role_issues(_FakeConn("sqlite", (True, True))) == []


# --- production boot (subprocess) ----------------------------------------------
def _run_prod(code: str, **env_overrides) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"SECRET_KEY", "AI_WORKER_TOKEN", "CORS_ORIGINS", "ENVIRONMENT"}
    }
    env.update(
        {
            "DATABASE_URL": "sqlite://",
            "ENVIRONMENT": "production",
            "SECRET_KEY": STRONG_KEY,
            "AI_WORKER_TOKEN": "synthetic-worker-token",
            "CORS_ORIGINS": "https://app.example.com",
            "REDIS_URL": "",
            "SENTRY_DSN": "",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Phase 10.3 (OQ-2): AI governance is mandatory in production (synthetic caps).
            "AI_GOVERNANCE_ENABLED": "true",
            "AI_PLATFORM_DAILY_REQUEST_MAX": "500",
            "AI_PLATFORM_DAILY_TOKEN_MAX": "400000",
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_production_refuses_weak_secret_key():
    r = _run_prod("import main", SECRET_KEY="too-short")
    assert r.returncode != 0
    assert "Refusing to start in production" in r.stderr
    assert "too-short" not in r.stderr  # the key itself is never echoed


def test_production_refuses_missing_secret_key():
    env = {"SECRET_KEY": ""}
    r = _run_prod("import main", **env)
    assert r.returncode != 0


def test_production_does_not_trust_localhost_origins():
    r = _run_prod(
        """
        from fastapi.testclient import TestClient
        import main
        with TestClient(main.app) as c:
            # Token-less logout touches no tables (in-memory SQLite has none).
            local = c.post("/v1/auth/logout", headers={"Origin": "http://localhost:3000"})
            prod = c.post("/v1/auth/logout", headers={"Origin": "https://app.example.com"})
            pre = c.options("/v1/auth/login", headers={
                "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
        print("RESULT", local.status_code, prod.status_code,
              pre.headers.get("access-control-allow-origin"))
        """
    )
    assert r.returncode == 0, r.stderr[-2000:]
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")][0]
    local, prod, acao = line.split()[1:]
    assert local == "403"  # CSRF: localhost is not trusted in production
    assert prod == "204"  # the configured origin passes CSRF
    assert acao == "None"  # and CORS no longer allows localhost
