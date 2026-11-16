"""Phase 6: the production startup gate against the REAL test cluster.

The suite connects as the restricted NOBYPASSRLS ``app_user`` (enforced by the
session safety gate), so the gate must find no problems and the app must boot
with ENVIRONMENT=production semantics. Read-only: no rows are written.
"""


def test_app_user_passes_runtime_role_gate(fastapi_app):
    from app.core.db import engine, runtime_role_issues

    with engine.connect() as conn:
        assert runtime_role_issues(conn) == []


def test_production_lifespan_boots_as_app_user(fastapi_app, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with TestClient(fastapi_app, raise_server_exceptions=False) as c:
        assert c.get("/health").status_code == 200
