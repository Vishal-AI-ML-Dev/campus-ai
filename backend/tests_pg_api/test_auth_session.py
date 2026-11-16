"""Phase 5C - authentication & session behaviour through the REAL /v1 routes.

Runs against the isolated Postgres test cluster as app_user (see conftest).
Every test gets a fresh TestClient whose httpx cookie jar is PATH-AWARE
(stdlib http.cookiejar): a cookie scoped to Path=/auth is only sent to
/auth/... URLs, exactly as a browser would. The frontend calls the /v1 paths
(frontend/src/lib/api.ts: API_PREFIX = "/v1"), so those are what we test.

Secrets hygiene: users get random per-run passwords; assertion messages only
ever contain status codes, cookie NAMES/paths/flags and booleans - never a
cookie value, token, password or connection string.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

MARK = "zz-api-iso"
EMAIL_DOMAIN = "zz-api-iso.example.com"
LOGIN, REFRESH, LOGOUT, ME = (
    "/v1/auth/login", "/v1/auth/refresh", "/v1/auth/logout", "/v1/auth/me")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _detail(r) -> str:
    """Status + server 'detail' only (never the body of a token response)."""
    try:
        d = r.json().get("detail")
    except Exception:  # noqa: BLE001
        d = None
    return f"http={r.status_code} detail={d!r}"


def _cookie_headers(r) -> list[tuple[str, dict]]:
    """All Set-Cookie headers as (name, {attr: value}) WITHOUT cookie values."""
    out = []
    for header in r.headers.get_list("set-cookie"):
        parts = [p.strip() for p in header.split(";")]
        name, _, value = parts[0].partition("=")
        attrs = {"_has_value": bool(value.strip('"'))}
        for p in parts[1:]:
            k, _, v = p.partition("=")
            attrs[k.lower()] = v or True
        out.append((name, attrs))
    return out


def _is_deletion(attrs: dict) -> bool:
    return str(attrs.get("max-age")) == "0" or not attrs["_has_value"]


def _set_cookies(r) -> dict[str, dict]:
    """Cookies the response SETS (deletions excluded): {name: attrs}."""
    return {n: a for n, a in _cookie_headers(r) if not _is_deletion(a)}


def _deleted_cookies(r) -> set[tuple[str, str]]:
    """(name, path) pairs the response EXPIRES."""
    return {(n, a.get("path")) for n, a in _cookie_headers(r) if _is_deletion(a)}


def _jar(client) -> dict[str, list[str]]:
    """{cookie name: [paths]} currently held by the client's cookie jar."""
    out: dict[str, list[str]] = {}
    for c in client.cookies.jar:
        out.setdefault(c.name, []).append(c.path)
    return out


def _jar_value(client, name):
    for c in client.cookies.jar:
        if c.name == name:
            return c.value
    return None


def _path_match(request_path: str, cookie_path: str) -> bool:
    """RFC 6265 section 5.1.4 path-match."""
    if request_path == cookie_path:
        return True
    if request_path.startswith(cookie_path):
        return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"
    return False


def _login(client, u):
    return client.post(LOGIN, data={"username": u.email, "password": u.password})


def _tokens(sql, user_id):
    return sql("SELECT id, family_id, tenant_id, revoked_at, replaced_by_id "
               "FROM refresh_tokens WHERE user_id = :u ORDER BY id", {"u": user_id})


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Login/refresh are rate-limited per client IP (in-memory store here);
    reset so tests are independent. Harness-only - limits are unchanged."""
    from app.core.rate_limit import limiter

    for attr in ("_storage", "storage"):
        store = getattr(limiter, attr, None)
        if store is not None and hasattr(store, "reset"):
            try:
                store.reset()
            except Exception:  # noqa: BLE001
                pass
    yield


@pytest.fixture
def browser(fastapi_app):
    """A fresh path-aware cookie client per test (like a new browser profile)."""
    from fastapi.testclient import TestClient

    with TestClient(
        fastapi_app,
        raise_server_exceptions=False,
        headers={"Origin": "http://testserver"},  # browser provenance (CSRF)
    ) as c:
        yield c


@pytest.fixture
def people(sql):
    """Two synthetic institutes with real bcrypt-hashed synthetic logins."""
    from app.core.security import hash_password

    run = uuid.uuid4().hex[:10]
    tenants = []
    out = {}
    try:
        for key in ("a", "b"):
            tid = sql("INSERT INTO tenants (name, slug, is_active) VALUES (:n, :s, true) "
                      "RETURNING id", {"n": f"Auth {key} {run}",
                                       "s": f"{MARK}-auth-{run}-{key}"})[0]["id"]
            tenants.append(tid)
            out[f"tenant_{key}"] = tid
        pw_hash_cache = {}

        def mk(label, tid, active=True):
            pw = secrets.token_urlsafe(18)
            pw_hash_cache[label] = hash_password(pw)
            email = f"{label}-{run}@{EMAIL_DOMAIN}"
            uid = sql("INSERT INTO users (email, full_name, hashed_password, role, "
                      "is_active, tenant_id) VALUES (:e, 'Synthetic', :h, 'student', "
                      ":a, :t) RETURNING id",
                      {"e": email, "h": pw_hash_cache[label], "a": active, "t": tid})[0]["id"]
            return SimpleNamespace(id=uid, email=email, password=pw, tenant=tid)

        out["a"] = mk("auth-a", out["tenant_a"])
        out["b"] = mk("auth-b", out["tenant_b"])
        out["inactive"] = mk("auth-inactive", out["tenant_a"], active=False)
        yield SimpleNamespace(**out)
    finally:
        for tid in tenants:  # cascades users -> refresh_tokens
            sql("DELETE FROM tenants WHERE id = :t", {"t": tid})


# --------------------------------------------------------------------------
# 1. Login
# --------------------------------------------------------------------------
def test_login_active_user_sets_httponly_cookies_and_cookie_auth_works(browser, people, sql):
    r = _login(browser, people.a)
    assert r.status_code == 200, _detail(r)
    assert bool(r.json().get("access_token")), "no access_token in body"
    sc = _set_cookies(r)
    assert set(sc) >= {"access_token", "refresh_token"}, sorted(sc)
    for name in ("access_token", "refresh_token"):
        assert sc[name]["_has_value"] and sc[name].get("httponly") is True, name
        assert str(sc[name].get("samesite", "")).lower() == "lax", name
    assert sc["access_token"].get("path") == "/"
    # Cookie-only (no Authorization header) authenticated request on /v1.
    me = browser.get(ME)
    assert me.status_code == 200, _detail(me)
    assert me.json()["id"] == people.a.id
    rows = _tokens(sql, people.a.id)
    assert len(rows) == 1 and rows[0]["tenant_id"] == people.a.tenant
    assert rows[0]["revoked_at"] is None


def test_login_rejects_wrong_password_and_unknown_email_identically(browser, people, sql):
    bad_pw = browser.post(LOGIN, data={"username": people.a.email, "password": "wrong-" + secrets.token_hex(4)})
    unknown = browser.post(LOGIN, data={"username": f"nobody-{uuid.uuid4().hex[:6]}@{EMAIL_DOMAIN}",
                                        "password": "whatever-123"})
    assert bad_pw.status_code == 401 and unknown.status_code == 401
    assert bad_pw.json().get("detail") == unknown.json().get("detail")  # no enumeration
    assert _set_cookies(bad_pw) == {} and _set_cookies(unknown) == {}
    assert _tokens(sql, people.a.id) == []


def test_login_rejects_inactive_user(browser, people, sql):
    """A deactivated account must not be able to log in or obtain a session."""
    r = _login(browser, people.inactive)
    issued = _tokens(sql, people.inactive.id)
    cookies = sorted(_set_cookies(r))
    assert r.status_code in (401, 403) and not issued and not cookies, (
        f"inactive login: {_detail(r)}; refresh rows issued={len(issued)}; "
        f"cookies set={cookies}"
    )


def test_deactivated_users_existing_session_is_rejected(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    sql("UPDATE users SET is_active = false WHERE id = :u", {"u": people.a.id})
    me = browser.get(ME)
    assert me.status_code == 401, _detail(me)
    raw = _jar_value(browser, "refresh_token")
    ref = browser.post("/v1/auth/refresh", json={"refresh_token": raw})
    assert ref.status_code == 401, _detail(ref)


def test_expired_or_forged_access_token_rejected(browser, people):
    from jose import jwt

    from app.core.config import settings

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = jwt.encode({"sub": str(people.a.id), "exp": past}, settings.SECRET_KEY,
                         algorithm=settings.ALGORITHM)
    forged = jwt.encode({"sub": str(people.a.id),
                         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                        "not-the-server-key-" + secrets.token_hex(8), algorithm="HS256")
    for tok in (expired, forged):
        r = browser.get(ME, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, _detail(r)
    browser.cookies.set("access_token", expired)
    assert browser.get(ME).status_code == 401


# --------------------------------------------------------------------------
# 2. Cookie-only refresh through the /v1 paths the frontend uses
# --------------------------------------------------------------------------
def test_refresh_cookie_path_covers_frontend_auth_urls(browser, people):
    """The refresh cookie's Path must path-match the URLs the frontend posts
    to (/v1/auth/refresh and /v1/auth/logout), or a browser never sends it."""
    r = _login(browser, people.a)
    assert r.status_code == 200, _detail(r)
    path = _set_cookies(r)["refresh_token"].get("path")
    misses = [u for u in (REFRESH, LOGOUT) if not _path_match(u, str(path))]
    assert not misses, f"refresh cookie Path={path!r} is not sent to {misses}"


def test_cookie_only_refresh_via_v1(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    jar_before = _jar(browser)
    r = browser.post(REFRESH)  # no body: cookie-only, like the frontend
    assert r.status_code == 200, (
        f"cookie-only {REFRESH}: {_detail(r)}; jar holds {jar_before}"
    )
    assert _set_cookies(r).keys() >= {"access_token", "refresh_token"}
    rows = _tokens(sql, people.a.id)
    assert len(rows) == 2 and rows[0]["revoked_at"] is not None


def test_legacy_unversioned_cookie_refresh_is_unsupported(browser, people, sql):
    """Deliberate Phase 5D compatibility change (replaces the Phase 5C control
    that showed cookie refresh working on /auth): cookie auth is supported on
    /v1/auth only. A cookie-only POST to the deprecated /auth/refresh alias
    does not receive the refresh cookie -> 401, and nothing is rotated; the
    same session still refreshes on the supported /v1 path."""
    assert _login(browser, people.a).status_code == 200
    r = browser.post("/auth/refresh")
    assert r.status_code == 401, _detail(r)
    rows = _tokens(sql, people.a.id)
    assert len(rows) == 1 and rows[0]["revoked_at"] is None
    ok = browser.post(REFRESH)
    assert ok.status_code == 200, _detail(ok)
    me = browser.get(ME)  # rotated access cookie authenticates /v1
    assert me.status_code == 200 and me.json()["id"] == people.a.id


# --------------------------------------------------------------------------
# 3. Logout
# --------------------------------------------------------------------------
def test_logout_via_v1_clears_both_cookies(browser, people):
    assert _login(browser, people.a).status_code == 200
    r = browser.post(LOGOUT)
    assert r.status_code == 204, _detail(r)
    assert _set_cookies(r) == {}, "logout must not set any cookie"
    # Phase 5D: refresh cookie lives at /v1/auth; the legacy /auth one is
    # expired too (browser cleanup for cookies issued before the change).
    expected = {("access_token", "/"), ("refresh_token", "/v1/auth"),
                ("refresh_token", "/auth")}
    assert _deleted_cookies(r) == expected, sorted(_deleted_cookies(r))
    assert _jar(browser) == {}, f"jar after logout: {_jar(browser)}"
    assert browser.get(ME).status_code == 401


def test_logout_via_v1_revokes_refresh_family_server_side(browser, people, sql):
    """Cookie-only logout (as the frontend does it) must revoke the session's
    refresh tokens in the database, not just clear the browser cookie."""
    assert _login(browser, people.a).status_code == 200
    raw = _jar_value(browser, "refresh_token")
    jar_before = _jar(browser)
    assert browser.post(LOGOUT).status_code == 204
    n_active = sum(r["revoked_at"] is None for r in _tokens(sql, people.a.id))
    replay = browser.post(REFRESH, json={"refresh_token": raw})
    assert n_active == 0 and replay.status_code == 401, (
        f"after cookie-only {LOGOUT}: {n_active} refresh row(s) still active; "
        f"replaying the old refresh token -> {_detail(replay)}; jar held {jar_before}"
    )


def test_logout_with_explicit_token_revokes_family_control(browser, people, sql):
    """Control for non-cookie API clients: body token -> family revoked."""
    assert _login(browser, people.a).status_code == 200
    raw = _jar_value(browser, "refresh_token")
    assert browser.post(LOGOUT, json={"refresh_token": raw}).status_code == 204
    assert all(r["revoked_at"] is not None for r in _tokens(sql, people.a.id))
    assert browser.post(REFRESH, json={"refresh_token": raw}).status_code == 401


# --------------------------------------------------------------------------
# 4. Rotation and reuse
# --------------------------------------------------------------------------
def test_refresh_rotates_single_use_within_family(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    raw1 = _jar_value(browser, "refresh_token")
    r = browser.post(REFRESH, json={"refresh_token": raw1})
    assert r.status_code == 200, _detail(r)
    raw2 = _jar_value(browser, "refresh_token")
    assert raw2 is not None and (raw2 != raw1), "refresh token was not rotated"
    rows = _tokens(sql, people.a.id)
    assert len(rows) == 2
    old, new = rows
    assert old["revoked_at"] is not None and old["replaced_by_id"] == new["id"]
    assert new["revoked_at"] is None and new["family_id"] == old["family_id"]
    assert new["tenant_id"] == people.a.tenant
    me = browser.get(ME, headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["id"] == people.a.id


def test_reuse_of_rotated_token_is_rejected_and_revokes_family(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    raw1 = _jar_value(browser, "refresh_token")
    assert browser.post(REFRESH, json={"refresh_token": raw1}).status_code == 200
    raw2 = _jar_value(browser, "refresh_token")
    reuse = browser.post(REFRESH, json={"refresh_token": raw1})
    assert reuse.status_code == 401, _detail(reuse)
    after = browser.post(REFRESH, json={"refresh_token": raw2})
    assert after.status_code == 401, f"successor after reuse: {_detail(after)}"
    assert all(r["revoked_at"] is not None for r in _tokens(sql, people.a.id))


def test_refresh_rejects_missing_unknown_and_expired_tokens(browser, people, sql):
    assert browser.post(REFRESH).status_code == 401  # nothing at all
    assert browser.post(REFRESH, json={"refresh_token": secrets.token_urlsafe(48)}).status_code == 401
    raw = secrets.token_urlsafe(48)
    sql("INSERT INTO refresh_tokens (user_id, tenant_id, token_hash, family_id, expires_at) "
        "VALUES (:u, :t, :h, :f, :x)",
        {"u": people.a.id, "t": people.a.tenant,
         "h": hashlib.sha256(raw.encode()).hexdigest(), "f": secrets.token_hex(16),
         "x": datetime.now(timezone.utc) - timedelta(minutes=1)})
    r = browser.post(REFRESH, json={"refresh_token": raw})
    assert r.status_code == 401, _detail(r)


# --------------------------------------------------------------------------
# 5. Same-tenant controls
# --------------------------------------------------------------------------
def test_sessions_carry_each_users_own_tenant(fastapi_app, people, sql):
    """Users of two institutes log in via the real route; cookie-authenticated
    requests (and refreshed sessions) stamp each user's OWN tenant for RLS."""
    from fastapi.testclient import TestClient

    for u in (people.a, people.b):
        with TestClient(
            fastapi_app,
            raise_server_exceptions=False,
            headers={"Origin": "http://testserver"},
        ) as c:
            assert _login(c, u).status_code == 200
            probe = c.get("/__pgapi_probe__/guc")  # cookie-only
            assert probe.status_code == 200 and probe.json()["tid"] == u.tenant
            raw = _jar_value(c, "refresh_token")
            r = c.post(REFRESH, json={"refresh_token": raw})
            assert r.status_code == 200, _detail(r)
            probe2 = c.get("/__pgapi_probe__/guc")  # rotated access cookie
            assert probe2.json()["tid"] == u.tenant and probe2.json()["user_tenant"] == u.tenant
            assert {row["tenant_id"] for row in _tokens(sql, u.id)} == {u.tenant}


# --------------------------------------------------------------------------
# Phase 5D regression tests: cookie attributes, paths, rotation, revocation
# --------------------------------------------------------------------------
def test_login_cookie_attributes(browser, people):
    from app.core.config import settings

    r = _login(browser, people.a)
    assert r.status_code == 200, _detail(r)
    sc = _set_cookies(r)
    want = {
        "access_token": ("/", settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        "refresh_token": ("/v1/auth", settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400),
    }
    assert set(sc) == set(want), sorted(sc)
    for name, (path, max_age) in want.items():
        a = sc[name]
        assert a.get("path") == path, (name, a.get("path"))
        assert str(a.get("max-age")) == str(max_age), (name, a.get("max-age"))
        assert a.get("httponly") is True, name
        assert str(a.get("samesite")).lower() == settings.cookie_samesite, name
        assert bool(a.get("secure")) is settings.cookie_secure, name
        assert "domain" not in a, name  # host-only (COOKIE_DOMAIN unset)
    # Legacy /auth-path refresh cookie is expired with matching flags.
    legacy = [a for n, a in _cookie_headers(r)
              if n == "refresh_token" and a.get("path") == "/auth"]
    assert len(legacy) == 1 and _is_deletion(legacy[0])
    assert legacy[0].get("httponly") is True
    assert str(legacy[0].get("samesite")).lower() == settings.cookie_samesite


def test_refresh_cookie_set_and_delete_paths_match(browser, people):
    """Every response sets the refresh cookie at the path it is later deleted
    at, and that path covers both frontend URLs."""
    login = _login(browser, people.a)
    refresh = browser.post(REFRESH)
    assert login.status_code == 200 and refresh.status_code == 200, _detail(refresh)
    set_paths = {_set_cookies(x)["refresh_token"].get("path") for x in (login, refresh)}
    assert set_paths == {"/v1/auth"}
    assert all(_path_match(u, "/v1/auth") for u in (REFRESH, LOGOUT))
    # Not sent on ordinary (non-auth) API calls...
    for url in ("/v1/admin/departments", "/v1/announcements", "/v1/authx", "/auth/refresh"):
        assert not _path_match(url, "/v1/auth"), url
    # ...but, like the old /auth scope, it IS sent to every /v1/auth/* route
    # (incl. /v1/auth/me): one cookie path must cover both refresh and logout.
    assert _path_match(ME, "/v1/auth")
    logout = browser.post(LOGOUT)
    assert logout.status_code == 204
    assert ("refresh_token", "/v1/auth") in _deleted_cookies(logout)
    assert ("access_token", "/") in _deleted_cookies(logout)


def test_cookie_only_rotation_and_cookie_replay_rejected(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    raw1 = _jar_value(browser, "refresh_token")
    domain = next(c.domain for c in browser.cookies.jar if c.name == "refresh_token")
    r = browser.post(REFRESH)  # cookie-only
    assert r.status_code == 200, _detail(r)
    raw2 = _jar_value(browser, "refresh_token")
    assert raw2 is not None and (raw2 != raw1), "cookie-only refresh did not rotate"
    old, new = _tokens(sql, people.a.id)
    assert old["revoked_at"] is not None and old["replaced_by_id"] == new["id"]
    # Replay the rotated token AS A COOKIE (e.g. restored from a stolen copy).
    browser.cookies.clear()
    browser.cookies.set("refresh_token", raw1, domain=domain, path="/v1/auth")
    replay = browser.post(REFRESH)
    assert replay.status_code == 401, _detail(replay)
    # Reuse detection revoked the family: the legitimate successor is dead too.
    browser.cookies.clear()
    browser.cookies.set("refresh_token", raw2, domain=domain, path="/v1/auth")
    assert browser.post(REFRESH).status_code == 401
    assert all(t["revoked_at"] is not None for t in _tokens(sql, people.a.id))


def test_cookie_only_logout_revokes_entire_rotated_family(browser, people, sql):
    assert _login(browser, people.a).status_code == 200
    raws = [_jar_value(browser, "refresh_token")]
    for _ in range(2):
        r = browser.post(REFRESH)
        assert r.status_code == 200, _detail(r)
        raws.append(_jar_value(browser, "refresh_token"))
    assert len(set(raws)) == 3
    assert browser.post(LOGOUT).status_code == 204  # cookie-only
    rows = _tokens(sql, people.a.id)
    assert len(rows) == 3 and all(t["revoked_at"] is not None for t in rows)
    for raw in raws:
        assert browser.post(REFRESH, json={"refresh_token": raw}).status_code == 401
    assert browser.get(ME).status_code == 401  # access cookie cleared too


def test_legacy_path_cookie_is_expired_in_browser_but_not_revoked(browser, people, sql):
    """Documents the limit of the cleanup: a browser holding a pre-5D refresh
    cookie at Path=/auth never sends it to /v1, so the server cannot revoke
    its token. The next /v1 auth response only EXPIRES it in the browser; the
    server-side row stays active until it expires or is revoked separately."""
    assert _login(browser, people.a).status_code == 200
    domain = next(c.domain for c in browser.cookies.jar if c.name == "refresh_token")
    legacy_raw = secrets.token_urlsafe(48)
    sql("INSERT INTO refresh_tokens (user_id, tenant_id, token_hash, family_id, expires_at) "
        "VALUES (:u, :t, :h, :f, now() + interval '1 day')",
        {"u": people.a.id, "t": people.a.tenant,
         "h": hashlib.sha256(legacy_raw.encode()).hexdigest(), "f": secrets.token_hex(16)})
    browser.cookies.set("refresh_token", legacy_raw, domain=domain, path="/auth")
    assert "/auth" in _jar(browser)["refresh_token"]
    r = browser.post(REFRESH)  # supported path; legacy cookie is not sent here
    assert r.status_code == 200, _detail(r)
    assert ("refresh_token", "/auth") in _deleted_cookies(r)
    assert _jar(browser)["refresh_token"] == ["/v1/auth"], _jar(browser)
    legacy_row = sql("SELECT revoked_at FROM refresh_tokens WHERE token_hash = :h",
                     {"h": hashlib.sha256(legacy_raw.encode()).hexdigest()})
    assert legacy_row == [{"revoked_at": None}], "browser cleanup must not be mistaken for revocation"
