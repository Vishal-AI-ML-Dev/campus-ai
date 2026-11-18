"""API versioning: feature routes live under /v1 AND legacy unversioned paths.

The unversioned copies are deprecated aliases kept for backward compatibility,
so both surfaces must resolve to the same handlers. Ops endpoints (/health,
/metrics, /version) stay unversioned.
"""

import app.models as models


def test_version_endpoint(client):
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_versions"] == ["v1"]
    assert body["current"] == "/v1"
    assert "version" in body


def test_versioned_register_and_login(client, make_tenant, make_user, token_header):
    """The canonical /v1 surface works end-to-end (register + approve + login + me)."""
    acme = make_tenant(slug="acme")
    make_user("v1admin@test.dev", role=models.UserRole.admin, tenant=acme)
    reg = client.post(
        "/v1/auth/register",
        json={
            "email": "v1user@test.dev",
            "full_name": "V1 User",
            "password": "secret123",
            "tenant_slug": "acme",
        },
    )
    assert reg.status_code == 201, reg.text

    # Pending until an admin of the institute approves it.
    approve = client.post(
        f"/v1/admin/users/{reg.json()['id']}/approve",
        headers=token_header("v1admin@test.dev"),
    )
    assert approve.status_code == 200, approve.text

    login = client.post(
        "/v1/auth/login",
        data={"username": "v1user@test.dev", "password": "secret123"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "v1user@test.dev"


def test_legacy_unversioned_still_works(client, make_user, token_header):
    """Legacy unversioned paths remain functional (backward compatible)."""
    make_user("legacy@test.dev")
    resp = client.get("/auth/me", headers=token_header("legacy@test.dev"))
    assert resp.status_code == 200
    assert resp.json()["email"] == "legacy@test.dev"


def test_v1_and_legacy_both_in_openapi(client):
    """Both surfaces expose the same route in the OpenAPI schema."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/auth/login" in paths
    assert "/auth/login" in paths
    # The legacy copy is flagged deprecated so Swagger nudges clients to /v1.
    assert paths["/auth/login"]["post"].get("deprecated") is True
    assert paths["/v1/auth/login"]["post"].get("deprecated") in (False, None)
