"""CSRF origin verification (Phase 6, policy v2) - app/core/csrf.py.

Cookie-based, state-changing requests must carry browser provenance from an
allowed origin. An Authorization header never relaxes the check (non-Bearer
schemes fall back to the access cookie; refresh/logout read only body/cookie).
Requests with no Origin/Referer and no auth cookie are served WITHOUT any
Set-Cookie, so non-browser clients authenticate explicitly with Bearer.

The shared ``client`` fixture sends ``Origin: http://testserver`` (the API's
own origin); ``raw`` below sends no Origin at all, like a script.
All data is synthetic (test.dev / example.com).
"""

import pytest
from fastapi.testclient import TestClient

from app.core.csrf import ALLOW, ALLOW_NO_COOKIES, csrf_decision, normalize_origin
from app.models import Announcement, RefreshToken, Tenant, UserRole

ALLOWED = "http://localhost:3000"
FOREIGN = "https://evil.example.com"
PW = "secret123"


@pytest.fixture
def raw():
    """Non-browser client: no default Origin header."""
    from main import app

    with TestClient(app) as c:
        yield c


def _login(c, email, **headers):
    return c.post(
        "/v1/auth/login",
        data={"username": email, "password": PW},
        headers=headers,
    )


def _active_tokens(session) -> int:
    session.expire_all()
    return session.query(RefreshToken).filter(RefreshToken.revoked_at.is_(None)).count()


# --- pure policy ------------------------------------------------------------
@pytest.mark.parametrize(
    "raw_value,expected",
    [
        ("http://localhost:3000", "http://localhost:3000"),
        ("HTTP://LocalHost:3000/", "http://localhost:3000"),
        ("https://app.example.com:443", "https://app.example.com"),
        ("http://app.example.com:80/path?q=1", "http://app.example.com"),
        ("null", None),
        ("javascript:alert(1)", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_origin(raw_value, expected):
    assert normalize_origin(raw_value) == expected


def test_policy_matrix():
    allowed = {ALLOWED}

    def d(method, h, cookie=False):
        return csrf_decision(method, h, cookie, allowed)[0]

    assert d("GET", {"origin": FOREIGN}, cookie=True) == ALLOW
    assert d("OPTIONS", {"origin": FOREIGN}) == ALLOW
    assert d("POST", {"origin": ALLOWED}, cookie=True) == ALLOW
    assert d("POST", {"origin": FOREIGN}) == "reject"
    assert d("DELETE", {"origin": "null"}, cookie=True) == "reject"
    assert d("POST", {"origin": "http://localhost:3000.evil.example.com"}) == "reject"
    assert d("POST", {"origin": "https://localhost:3000"}) == "reject"
    assert d("PATCH", {"referer": ALLOWED + "/dashboard"}, cookie=True) == ALLOW
    assert d("PATCH", {"referer": FOREIGN + "/x"}) == "reject"
    assert d("PUT", {"sec-fetch-site": "cross-site"}) == "reject"
    # Origin wins over a matching Referer.
    assert d("POST", {"origin": FOREIGN, "referer": ALLOWED + "/"}) == "reject"
    # No provenance: cookie-based write rejected; cookie-less served cookie-free.
    assert d("POST", {}, cookie=True) == "reject"
    assert d("POST", {}) == ALLOW_NO_COOKIES
    # Authorization never exempts (any scheme, with or without cookies).
    for auth in ("Bearer x", "Basic eDp5"):
        assert d("POST", {"authorization": auth, "origin": FOREIGN}) == "reject"
        assert d("POST", {"authorization": auth}, cookie=True) == "reject"
        assert d("POST", {"authorization": auth}) == ALLOW_NO_COOKIES
    # The API's own origin (Swagger UI at /docs) is accepted.
    assert csrf_decision(
        "POST", {"origin": "http://testserver"}, True, allowed, "http://testserver"
    )[0] == ALLOW


# --- login and other cookie-setting routes -----------------------------------
def test_browser_login_from_allowed_origin_sets_cookies(raw, make_user):
    make_user("csrf-ok@test.dev")
    resp = _login(raw, "csrf-ok@test.dev", origin=ALLOWED)
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.cookies and "refresh_token" in resp.cookies


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": FOREIGN},
        {"origin": "null"},
        {"referer": FOREIGN + "/phish"},
        {"sec-fetch-site": "cross-site"},
        {"origin": FOREIGN, "authorization": "Bearer anything"},
    ],
)
def test_forged_login_is_blocked_and_sets_no_cookie(raw, make_user, headers):
    make_user("csrf-login@test.dev")
    resp = _login(raw, "csrf-login@test.dev", **headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["type"] == "csrf_error"
    assert FOREIGN not in resp.text  # offending header is not echoed
    assert "set-cookie" not in resp.headers
    assert not raw.cookies


def test_legacy_unversioned_mount_is_also_protected(raw, make_user):
    make_user("csrf-legacy@test.dev")
    resp = raw.post(
        "/auth/login",
        data={"username": "csrf-legacy@test.dev", "password": PW},
        headers={"origin": FOREIGN},
    )
    assert resp.status_code == 403


def test_script_login_gets_body_token_but_no_cookie_session(raw, make_user):
    """No browser provenance: token in body, NO Set-Cookie (login CSRF guard)."""
    make_user("csrf-script@test.dev")
    resp = _login(raw, "csrf-script@test.dev")
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    assert token
    assert "set-cookie" not in resp.headers
    assert not raw.cookies
    # Explicit Bearer authentication works.
    me = raw.get("/v1/auth/me", headers={"authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_script_tenant_signup_sets_no_cookie(raw, session):
    resp = raw.post(
        "/v1/tenants/signup",
        json={
            "institution_name": "Script Institute",
            "institution_slug": "csrf-script-inst",
            "admin_full_name": "Script Admin",
            "admin_email": "script-admin@test.dev",
            "admin_password": "synthetic-pass-1",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["access_token"]
    assert "set-cookie" not in resp.headers


def test_forged_tenant_signup_is_blocked(raw, session):
    resp = raw.post(
        "/v1/tenants/signup",
        json={
            "institution_name": "Forged Institute",
            "institution_slug": "csrf-forged",
            "admin_full_name": "Forged Admin",
            "admin_email": "forged@test.dev",
            "admin_password": "synthetic-pass-1",
        },
        headers={"origin": FOREIGN},
    )
    assert resp.status_code == 403
    assert session.query(Tenant).filter_by(slug="csrf-forged").count() == 0


# --- cookie-authenticated state changes -------------------------------------
def test_forged_logout_does_not_revoke_session(client, make_user, session):
    make_user("csrf-logout@test.dev")
    assert _login(client, "csrf-logout@test.dev").status_code == 200

    forged = client.post("/v1/auth/logout", headers={"origin": FOREIGN})
    assert forged.status_code == 403
    assert _active_tokens(session) == 1
    assert client.get("/v1/auth/me").status_code == 200

    ok = client.post("/v1/auth/logout", headers={"origin": ALLOWED})
    assert ok.status_code == 204
    assert _active_tokens(session) == 0


def test_cookie_write_without_origin_is_blocked(client, raw, make_user, session):
    make_user("csrf-noorigin@test.dev")
    assert _login(client, "csrf-noorigin@test.dev").status_code == 200
    raw.cookies.update(client.cookies)  # same cookies, no Origin header
    resp = raw.post("/v1/auth/logout")
    assert resp.status_code == 403
    assert _active_tokens(session) == 1


@pytest.mark.parametrize("auth", ["Basic eDp5", "Bearer not-a-real-token"])
def test_authorization_header_does_not_bypass_for_cookie_requests(
    client, make_user, session, auth
):
    """Previously exempt: a non-Bearer header still authenticates via cookie,
    and logout/refresh never read Authorization at all."""
    make_user("csrf-authhdr@test.dev")
    assert _login(client, "csrf-authhdr@test.dev").status_code == 200
    for path in ("/v1/auth/logout", "/v1/auth/refresh"):
        resp = client.post(path, headers={"origin": FOREIGN, "authorization": auth})
        assert resp.status_code == 403, path
    assert _active_tokens(session) == 1


def test_valid_bearer_plus_cookie_without_origin_is_blocked(
    client, raw, make_user, session
):
    make_user("csrf-mixed@test.dev", role=UserRole.admin)
    token = _login(client, "csrf-mixed@test.dev").json()["access_token"]
    raw.cookies.update(client.cookies)
    resp = raw.post(
        "/v1/announcements",
        json={"title": "t", "body": "b"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert session.query(Announcement).count() == 0


def test_bearer_only_client_can_write(raw, make_user, session):
    """Legitimate non-browser client: Bearer, no cookies, no Origin."""
    make_user("csrf-bearer@test.dev", role=UserRole.admin)
    token = _login(raw, "csrf-bearer@test.dev").json()["access_token"]
    assert not raw.cookies
    resp = raw.post(
        "/v1/announcements",
        json={"title": "Synthetic", "body": "From a script"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


def test_bearer_with_foreign_origin_is_blocked(raw, make_user):
    make_user("csrf-bearer-foreign@test.dev", role=UserRole.admin)
    token = _login(raw, "csrf-bearer-foreign@test.dev").json()["access_token"]
    resp = raw.post(
        "/v1/announcements",
        json={"title": "t", "body": "b"},
        headers={"authorization": f"Bearer {token}", "origin": FOREIGN},
    )
    assert resp.status_code == 403


def test_forged_refresh_is_blocked(client, make_user):
    make_user("csrf-refresh@test.dev")
    assert _login(client, "csrf-refresh@test.dev").status_code == 200
    old = client.cookies.get("refresh_token")
    resp = client.post("/v1/auth/refresh", headers={"origin": "null"})
    assert resp.status_code == 403
    assert client.cookies.get("refresh_token") == old


def test_script_refresh_with_body_token_rotates_without_cookies(
    client, raw, make_user
):
    make_user("csrf-bodyrefresh@test.dev")
    assert _login(client, "csrf-bodyrefresh@test.dev").status_code == 200
    refresh = client.cookies.get("refresh_token")
    resp = raw.post("/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]
    assert "set-cookie" not in resp.headers


# --- safe methods and preflight untouched ------------------------------------
def test_cookie_get_from_foreign_origin_not_blocked(client, make_user):
    make_user("csrf-get@test.dev")
    assert _login(client, "csrf-get@test.dev").status_code == 200
    resp = client.get("/v1/auth/me", headers={"origin": FOREIGN})
    assert resp.status_code == 200
    # CORS still withholds the response from the foreign page.
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_still_answered_for_allowed_origin(raw):
    resp = raw.options(
        "/v1/auth/login",
        headers={"origin": ALLOWED, "access-control-request-method": "POST"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED
