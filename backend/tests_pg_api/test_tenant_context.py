"""Phase 5A - does every API request stamp the RIGHT tenant, and does that
tenant ever leak onto the next request / pooled connection?

All requests go through the real app (TestClient) and its real engine/pool,
connected as app_user. The ``/__pgapi_probe__`` routes are test-only, read-only
and report what Postgres sees INSIDE the request's own DB session:
``raw`` = current_setting('app.current_tenant_id'), ``tid`` =
app.current_tenant_id() (what RLS policies use), ``pid`` = backend PID (i.e.
which pooled connection served the request), and ``visible`` = DISTINCT
tenant_id of ``departments`` read with NO WHERE clause (so only RLS filters it).
"""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from sqlalchemy import event, text

PROBE = "/__pgapi_probe__"


def _probe(client, path, headers=None):
    r = client.get(PROBE + path, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def test_request_sets_callers_tenant(client, auth, world):
    """Proves: inside an authenticated request, the GUC equals the caller's
    tenant and RLS shows that tenant's departments only - for both tenants."""
    for t in (world.a, world.b):
        snap = _probe(client, "/guc", auth(t.teacher))
        assert snap["user_tenant"] == t.tenant
        assert snap["tid"] == t.tenant
        assert snap["visible"] == [t.tenant]


def test_tenant_reapplied_after_commit_within_request(client, auth, world):
    """Proves: after db.commit() the session's NEXT transaction (possibly on
    another pooled connection) is re-stamped by the after_begin listener."""
    for t in (world.a, world.b):
        snap = _probe(client, "/guc-after-commit", auth(t.admin))
        assert snap["tid"] == t.tenant
        assert snap["visible"] == [t.tenant]


def test_no_leak_across_sequential_requests_on_reused_connection(
    client, auth, world
):
    """Proves: alternating A / unauthenticated / B requests each see only their
    own tenant (or none), and that at least one pooled backend connection was
    actually REUSED across tenants - so the no-leak result is not vacuous."""
    pid_tenants: dict[int, set] = {}
    sequence = [world.a, None, world.b, None] * 6
    for t in sequence:
        if t is None:
            snap = _probe(client, "/guc-public")
            assert snap["tid"] is None, f"tenant leaked to public request: {snap}"
            assert snap["visible"] == [], f"rows visible with no tenant: {snap}"
            pid_tenants.setdefault(snap["pid"], set()).add("none")
        else:
            snap = _probe(client, "/guc", auth(t.student))
            assert snap["tid"] == t.tenant
            assert snap["visible"] == [t.tenant]
            pid_tenants.setdefault(snap["pid"], set()).add(t.tenant)
    reused = [p for p, ts in pid_tenants.items() if len(ts) >= 2]
    assert reused, f"no connection reuse observed; test inconclusive: {pid_tenants}"


def test_idle_pool_connections_carry_no_tenant(client, auth, world):
    """Proves: after tenant-scoped requests, every idle connection in the app's
    pool has NO tenant GUC (SET LOCAL semantics; nothing session-level)."""
    import app.core.db as appdb

    for t in (world.a, world.b, world.a):
        _probe(client, "/guc-after-commit", auth(t.teacher))
        client.get("/v1/admin/departments", headers=auth(t.admin))

    idle = max(appdb.engine.pool.checkedin(), 1)
    conns = [appdb.engine.raw_connection() for _ in range(idle)]
    try:
        for c in conns:
            cur = c.cursor()
            cur.execute(
                "SELECT current_setting('app.current_tenant_id', true), "
                "app.current_tenant_id()"
            )
            raw, tid = cur.fetchone()
            assert raw in (None, "") and tid is None, (raw, tid)
            cur.close()
    finally:
        for c in conns:
            c.close()


def test_fresh_session_without_tenant_is_default_deny(client, auth, world):
    """Proves: a new app Session (as a worker/background job would get) does
    NOT inherit the previous request's tenant via the pool or session.info."""
    import app.core.db as appdb

    _probe(client, "/guc", auth(world.a.admin))
    s = appdb.SessionLocal()
    try:
        n = s.execute(text("SELECT count(*) FROM departments")).scalar()
        tid = s.execute(text("SELECT app.current_tenant_id()")).scalar()
    finally:
        s.close()
    assert tid is None and n == 0


def test_every_set_config_is_local_and_matches_caller(client, auth, world):
    """Proves, across several REAL endpoints: every tenant GUC write the app
    issues is transaction-local (is_local = true) and carries the caller's own
    tenant id - never another tenant's, never session-wide."""
    import app.core.db as appdb

    seen: list[tuple[str, object]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "app.current_tenant_id" in statement and "set_config" in statement:
            seen.append((statement, parameters))

    event.listen(appdb.engine, "before_cursor_execute", capture)
    try:
        for t in (world.a, world.b):
            seen.clear()
            for method, path, who in [
                ("get", "/v1/admin/departments", t.admin),
                ("get", "/v1/announcements", t.student),
                ("get", f"/v1/admin/departments/{t.dept}/sections", t.teacher),
                ("get", "/v1/face/enrollments", t.teacher),
                ("get", "/v1/recruiters/offers", t.tpo),
            ]:
                r = client.request(method, path, headers=auth(who))
                assert r.status_code == 200, (path, r.status_code, r.text)
            assert seen, "no tenant GUC was set at all"
            for statement, params in seen:
                assert "true)" in statement.replace(" ", ""), statement
                assert params == {"tid": str(t.tenant)}, (statement, params)
    finally:
        event.remove(appdb.engine, "before_cursor_execute", capture)


def test_client_supplied_tenant_is_ignored(client, auth, world, sql):
    """Proves: a tenant id in the body or a header cannot redirect a write -
    the row lands in the CALLER's tenant (derived from the JWT's user)."""
    r = client.post(
        "/v1/announcements",
        json={"title": "zz synthetic", "body": "b", "tenant_id": world.b.tenant},
        headers={**auth(world.a.admin), "X-Tenant-ID": str(world.b.tenant)},
    )
    assert r.status_code == 201, r.text
    ann_id = r.json()["id"]
    in_a = sql("SELECT tenant_id FROM announcements WHERE id = :i", {"i": ann_id},
               tenant=world.a.tenant)
    in_b = sql("SELECT tenant_id FROM announcements WHERE id = :i", {"i": ann_id},
               tenant=world.b.tenant)
    assert in_a == [{"tenant_id": world.a.tenant}] and in_b == []


def test_concurrent_mixed_tenant_requests_never_cross(fastapi_app, auth, world):
    """Proves: 160 concurrent requests from A and B (8 threads, real threadpool
    + shared pool) each see exactly their own tenant - via the GUC probe and
    via a real RLS-only endpoint (section lookup without app-layer filter)."""
    from fastapi.testclient import TestClient

    local = threading.local()

    def call(i):
        t = world.a if i % 2 == 0 else world.b
        other = world.b if t is world.a else world.a
        c = getattr(local, "c", None)
        if c is None:
            c = local.c = TestClient(fastapi_app, raise_server_exceptions=False)
        snap = c.get(PROBE + "/guc", headers=auth(t.teacher)).json()
        own = c.get(f"/v1/face/enrollments?section_id={t.section}",
                    headers=auth(t.teacher)).status_code
        cross = c.get(f"/v1/face/enrollments?section_id={other.section}",
                      headers=auth(t.teacher)).status_code
        return t.tenant, snap, own, cross

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(call, range(160)))

    bad = [
        (tenant, snap, own, cross)
        for tenant, snap, own, cross in results
        if snap.get("tid") != tenant
        or snap.get("visible") != [tenant]
        or own != 200
        or cross != 404
    ]
    assert not bad, f"{len(bad)} of {len(results)} requests crossed: {bad[:3]}"
