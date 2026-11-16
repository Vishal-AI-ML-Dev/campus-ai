"""Auth cookies + refresh-token rotation (Batch 7).

Covers: login sets httpOnly cookies, /auth/refresh rotates the token, reusing a
rotated token revokes the whole family, logout clears + revokes, the access
cookie authenticates without a Bearer header, and garbage tokens 401.
"""


def _login(client, email, password="secret123"):
    resp = client.post(
        "/auth/login", data={"username": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp


def test_login_sets_auth_cookies(client, make_user):
    make_user("cookie@test.dev")
    resp = _login(client, "cookie@test.dev")
    assert "access_token" in resp.cookies
    assert "refresh_token" in resp.cookies
    # Body still carries the access token (backward compatible).
    assert resp.json()["access_token"]


def test_access_cookie_authenticates_without_bearer(client, make_user):
    make_user("cookieauth@test.dev")
    _login(client, "cookieauth@test.dev")
    # No Authorization header - the jar's access cookie should suffice.
    resp = client.get("/auth/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "cookieauth@test.dev"


def test_refresh_rotates_token(client, make_user):
    make_user("rot@test.dev")
    login = _login(client, "rot@test.dev")
    old_refresh = login.cookies["refresh_token"]

    resp = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    new_refresh = resp.cookies["refresh_token"]
    assert new_refresh and new_refresh != old_refresh


def test_reused_refresh_token_kills_family(client, make_user):
    make_user("reuse@test.dev")
    login = _login(client, "reuse@test.dev")
    old_refresh = login.cookies["refresh_token"]

    first = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200, first.text
    new_refresh = first.cookies["refresh_token"]

    # Reusing the already-rotated token must fail...
    reused = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401, reused.text

    # ...and the whole family is revoked, so the newest token is dead too.
    after = client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401, after.text


def test_logout_revokes_refresh_token(client, make_user):
    make_user("out@test.dev")
    login = _login(client, "out@test.dev")
    refresh = login.cookies["refresh_token"]

    # Cookie-only logout uses the supported versioned path: since Phase 5D the
    # refresh cookie is scoped to /v1/auth, so the deprecated unversioned
    # /auth/logout alias no longer receives it (deliberate, documented change).
    assert client.post("/v1/auth/logout").status_code == 204

    # The revoked token can no longer be refreshed.
    resp = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401, resp.text


def test_invalid_refresh_token_is_401(client):
    resp = client.post("/auth/refresh", json={"refresh_token": "garbage"})
    assert resp.status_code == 401, resp.text
