"""Phase 10.5 - monitoring additions: client error intake, readiness queue/AI status,
and content-free alerts for every D5 alert type (isolated, no network)."""

from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.observability import alerts
from app.routers.client_errors import normalise_route
from app.services import ai_governance

SENTINELS = ("Priya", "9876543210", "my answer", "secret-resume")


def test_client_errors_off_by_default(client):
    resp = client.post("/client-errors", json={"kind": "error", "name": "TypeError", "route": "/x"})
    assert resp.status_code == 404


def test_client_error_is_logged_content_free(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "CLIENT_ERROR_REPORTING_ENABLED", True)
    with caplog.at_level(logging.WARNING, logger="campus.client_error"):
        resp = client.post("/v1/client-errors", json={
            "kind": "render", "name": "TypeError",
            "route": "/dashboard/mentor/42?q=Priya 9876543210", "release": "1.0.0", "digest": "abc123"})
    assert resp.status_code == 204
    record = [r for r in caplog.records if r.name == "campus.client_error"][-1]
    fields = record.extra_fields
    assert fields["route"] == "/dashboard/mentor/:id" and fields["error_name"] == "TypeError"
    assert not any(s in json.dumps(fields) for s in SENTINELS)


def test_client_error_rejects_messages_and_unknown_fields(client, monkeypatch):
    monkeypatch.setattr(settings, "CLIENT_ERROR_REPORTING_ENABLED", True)
    bad = client.post("/client-errors", json={"kind": "error", "message": "Priya's answer"})
    assert bad.status_code == 422
    assert client.post("/client-errors", json={"kind": "boom"}).status_code == 422


def test_normalise_route():
    assert normalise_route("/a/12/b?x=1#y") == "/a/:id/b"
    assert normalise_route("/u/someone@example.com") == "/u/:x"


def test_readiness_reports_queue_and_ai_status(client, monkeypatch):
    body = client.get("/health/ready").json()
    assert body["checks"]["queue"] == {"status": "in_process", "required": False}
    assert body["checks"]["ai"]["status"] == "ok"
    monkeypatch.setattr(settings, "AI_KILL_SWITCH", True)
    resp = client.get("/health/ready")
    assert resp.status_code == 200  # AI never fails readiness (D4)
    assert resp.json()["checks"]["ai"] == {"status": "degraded", "required": False, "reasons": ["kill_switch"]}


def test_every_d5_alert_type_is_content_free(monkeypatch, caplog):
    """D5 alert types emitted in isolation (log sink only; no webhook)."""
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "")
    monkeypatch.setattr(settings, "AI_GOVERNANCE_ENABLED", True)
    ai_governance._reset()
    with caplog.at_level(logging.WARNING, logger="campus.alert"):
        alerts.emit_alert("readiness_failed", "Readiness check failed: database unreachable.",
                          severity="critical", components=["database"])
        alerts.emit_alert("backup_failed", "Daily backup failed.", severity="critical", stage="pg_dump")
        ai_governance.AIRefused  # noqa: B018 - module import check
        monkeypatch.setattr(ai_governance.ai_alerts, "alert",
                            lambda name, message, **f: alerts.emit_alert(name, message, **{
                                k: v for k, v in f.items() if k != "key"}))
        ai_governance.mark_provider_quota_exhausted("mentor")
        try:
            monkeypatch.setattr(settings, "AI_PLATFORM_DAILY_REQUEST_MAX", "1")
            ai_governance._refuse("mentor", "platform_requests")
        finally:
            ai_governance._reset()
    names = [getattr(r, "extra_fields", {}).get("alert_name") or r.getMessage() for r in caplog.records]
    text = " ".join(str(n) for n in names) + " ".join(json.dumps(getattr(r, "extra_fields", {}), default=str)
                                                    for r in caplog.records)
    for expected in ("readiness_failed", "backup_failed", "ai_quota_exhausted", "ai_platform_quota_reached"):
        assert expected in text, expected
    assert not any(s in text for s in SENTINELS)


def test_rate_limiter_falls_back_to_memory_when_redis_is_configured():
    """Phase 10.9 Redis drill: login must not 500 when Redis is down; limits stay on.

    Checked in a subprocess so this process keeps its own limiter object."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "REDIS_URL": "redis://127.0.0.1:1/0", "SECRET_KEY": "x" * 40,
           "DATABASE_URL": "sqlite://"}
    code = ("import app.core.rate_limit as rl; "
            "print(rl.limiter._in_memory_fallback_enabled, rl.limiter.enabled)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         timeout=120, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.stdout.strip().splitlines()[-1] == "True True", out.stderr[-500:]


# --- Review #4: browser errors reach Sentry (mocked; no network) --------------


def _sentry_mock(monkeypatch):
    import app.routers.client_errors as ce

    events = []
    ce._reset_sentry_throttle()
    monkeypatch.setattr(settings, "CLIENT_ERROR_REPORTING_ENABLED", True)
    monkeypatch.setattr(ce, "_sentry_active", lambda: True)
    monkeypatch.setattr(ce, "_sentry_capture", events.append)
    return ce, events


def _report(client, name="TypeError", route="/dashboard/mentor/42?q=Priya 9876543210"):
    return client.post("/client-errors", json={"kind": "error", "name": name, "route": route,
                                               "release": "1.0.0", "digest": "abc123"})


def test_client_error_reaches_sentry_content_free(client, monkeypatch):
    from app.observability.observability import scrub_event

    _ce, events = _sentry_mock(monkeypatch)
    assert _report(client).status_code == 204
    assert len(events) == 1
    sent = scrub_event(dict(events[0]))  # the shared privacy filter still applies
    assert sent["level"] == "error"
    assert sent["logentry"]["message"] == "Frontend error reported by the browser"
    assert sent["tags"]["client_route"] == "/dashboard/mentor/:id"
    assert sent["tags"]["client_error_name"] == "TypeError"
    assert sent["fingerprint"] == ["frontend-error", "error", "TypeError", "/dashboard/mentor/:id"]
    text = json.dumps(sent)
    assert not any(s in text for s in SENTINELS)


def test_client_error_sentry_is_throttled_per_signature_and_capped(client, monkeypatch):
    ce, events = _sentry_mock(monkeypatch)
    for _ in range(5):
        _report(client)
    assert len(events) == 1  # same signature: one event per window
    for i in range(30):
        _report(client, name=f"Err{i}")
    assert len(events) == ce.SENTRY_EVENTS_PER_WINDOW  # global cap per window


def test_client_error_no_sentry_when_not_configured_and_counter_kept(client, monkeypatch):
    import app.routers.client_errors as ce

    events = []
    ce._reset_sentry_throttle()
    monkeypatch.setattr(settings, "CLIENT_ERROR_REPORTING_ENABLED", True)
    monkeypatch.setattr(ce, "_sentry_capture", events.append)  # _sentry_active: real (inactive)
    before = ce._ERRORS.labels("error")._value.get() if ce._ERRORS else 0
    assert _report(client).status_code == 204
    assert events == []
    if ce._ERRORS:
        assert ce._ERRORS.labels("error")._value.get() == before + 1  # Prometheus counter kept


def test_client_error_sentry_failure_never_fails_the_request(client, monkeypatch):
    import app.routers.client_errors as ce

    ce._reset_sentry_throttle()
    monkeypatch.setattr(settings, "CLIENT_ERROR_REPORTING_ENABLED", True)
    monkeypatch.setattr(ce, "_sentry_active", lambda: True)

    def _boom(_event):
        raise RuntimeError("sentry down")

    monkeypatch.setattr(ce, "_sentry_capture", _boom)
    assert _report(client).status_code == 204
