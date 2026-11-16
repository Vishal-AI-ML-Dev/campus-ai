"""Readiness probe tests - /health (liveness) and /health/ready (readiness)."""


def test_liveness_is_dependency_free(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_readiness_ok_when_database_reachable(client):
    # The throwaway SQLite test DB is reachable, so readiness is 200.
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "ok"
    # Redis is not configured in tests -> reported disabled, never fatal.
    assert body["checks"]["redis"]["status"] == "disabled"


def test_readiness_503_when_database_down(client, monkeypatch):
    import app.observability.health as health

    # Force the DB check to report "down" WITHOUT touching engine.connect - the
    # conftest teardown (drop_all) reuses that same engine, so patching connect
    # directly would break teardown. Patching the check keeps the engine intact.
    monkeypatch.setattr(
        health,
        "_check_database",
        lambda: {"status": "down", "required": True, "error": "db unreachable"},
    )
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "down"


def test_readiness_does_not_leak_connection_details(client, monkeypatch):
    """Phase 6: the public probe reports the error class only, never the
    driver message (which can name the DB host, port and role)."""
    import app.observability.health as health

    secret_msg = (
        'connection to server at "db.internal.example" (10.1.2.3), port 5432 '
        'failed: password authentication failed for user "app_owner"'
    )

    class _Boom:
        def connect(self):
            raise OSError(secret_msg)

    # Patch only health's reference; conftest teardown uses app.core.db.engine.
    monkeypatch.setattr(health, "engine", _Boom())
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == {
        "status": "down",
        "required": True,
        "error": "OSError",
    }
    for fragment in ("db.internal.example", "10.1.2.3", "app_owner", "5432"):
        assert fragment not in resp.text
