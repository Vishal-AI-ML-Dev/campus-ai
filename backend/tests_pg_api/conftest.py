"""Phase 5A - real-Postgres API tenant-isolation suite (fixtures + safety gate).

Why a separate directory
------------------------
The main suite (``tests/``) runs on SQLite via the repo-root ``conftest.py``,
where Postgres Row-Level Security does not exist. These tests drive the REAL
FastAPI app (TestClient) against a real Postgres connected as the restricted
NOBYPASSRLS ``app_user``, so they prove what the API actually does under RLS:
which tenant each request stamps, whether it leaks across pooled connections,
and whether cross-tenant parent IDs are rejected.

They are NOT collected by a plain ``pytest`` run (pyproject ``testpaths`` is
``tests``), and every test is skipped unless ``RLS_TEST_DATABASE_URL`` is set.

How to run (inside the backend test image, on the isolated test network)
-----------------------------------------------------------------------
    RLS_TEST_DATABASE_URL=postgresql+psycopg://app_user:...@<test-db>/campus_rls_test \
    EXPECT_SYSID=<pg system identifier of the disposable test cluster> \
    python -m pytest tests_pg_api --confcutdir=tests_pg_api -p no:cacheprovider -v

``--confcutdir`` is REQUIRED: it stops pytest loading the repo-root
``conftest.py``, which would otherwise force ``DATABASE_URL`` to SQLite.

Safety (fails closed)
---------------------
* Before any write, ``_pg_safety`` checks - on BOTH a dedicated connection and
  the app's own engine - that the target is database ``EXPECT_DATABASE``
  (default ``campus_rls_test``) as role ``EXPECT_ROLE`` (default ``app_user``),
  NOT superuser, NOT BYPASSRLS, the cluster system identifier equals
  ``EXPECT_SYSID`` (required), and that the database holds no tenants other
  than this suite's synthetic ones. Any mismatch aborts the whole session.
* All data is synthetic and tagged with the ``zz-api-iso`` marker. Each test's
  data is deleted afterwards (tenants -> ON DELETE CASCADE; recruiter companies
  explicitly, they have no tenant_id) and the session verifies the non-RLS
  tables are back to their starting row counts.
* External AI/face calls are mocked; the single outbound chokepoint
  ``ai_client._worker_request`` is replaced with a function that FAILS the test
  if anything reaches it unmocked.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL", "")
ENABLED = RLS_URL.startswith("postgresql")
EXPECT_DATABASE = os.environ.get("EXPECT_DATABASE", "campus_rls_test")
EXPECT_ROLE = os.environ.get("EXPECT_ROLE", "app_user")
EXPECT_SYSID = os.environ.get("EXPECT_SYSID", "")
MARK = "zz-api-iso"
EMAIL_DOMAIN = "zz-api-iso.example.com"

if ENABLED:
    # Must happen BEFORE the app's config/db modules are imported (fixtures
    # below import them lazily). No real secrets: a throwaway JWT key, a dummy
    # worker token, an unroutable worker URL, and no Redis/Sentry/webhooks.
    os.environ["DATABASE_URL"] = RLS_URL
    os.environ["MIGRATION_DATABASE_URL"] = ""
    os.environ["SECRET_KEY"] = "pg-api-iso-" + secrets.token_hex(24)
    os.environ["AI_WORKER_URL"] = "http://127.0.0.1:9"
    os.environ["AI_WORKER_TOKEN"] = "pg-api-iso-dummy-token"
    os.environ["REDIS_URL"] = ""
    os.environ["SENTRY_DSN"] = ""
    os.environ["ALERT_WEBHOOK_URL"] = ""
    os.environ["SUPABASE_URL"] = ""
    os.environ["SUPABASE_SERVICE_KEY"] = ""
    os.environ["ENVIRONMENT"] = "test"

# Non-RLS tables whose row counts must return to baseline after the session.
# (recruiters / recruiter_invites / recruiter_users are under RLS since Phase
# 5B: app_user cannot count them without a tenant, and they are removed by the
# tenant ON DELETE CASCADE. The runner's external check covers every table.)
NON_RLS_TABLES = (
    "tenants",
    "users",
    "face_enrollments",
    "tenant_invites",
)


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        return
    skip = pytest.mark.skip(
        reason="RLS_TEST_DATABASE_URL not set to a Postgres app_user URL"
    )
    for item in items:
        item.add_marker(skip)


# --------------------------------------------------------------------------
# Safety gate + session-wide cleanup
# --------------------------------------------------------------------------
def _identity(conn):
    from sqlalchemy import text

    return conn.execute(
        text(
            "SELECT current_database(), current_user, r.rolsuper, r.rolbypassrls, "
            "(SELECT system_identifier FROM pg_control_system())::text "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
    ).one()


def _check_identity(where: str, ident) -> list[str]:
    db, user, superuser, bypass, sysid = ident
    problems = []
    if db != EXPECT_DATABASE:
        problems.append(f"{where}: database is not {EXPECT_DATABASE!r}")
    if user != EXPECT_ROLE:
        problems.append(f"{where}: role is not {EXPECT_ROLE!r}")
    if superuser is not False:
        problems.append(f"{where}: role is SUPERUSER")
    if bypass is not False:
        problems.append(f"{where}: role has BYPASSRLS")
    if not EXPECT_SYSID:
        problems.append(f"{where}: EXPECT_SYSID not provided (required)")
    elif sysid != EXPECT_SYSID:
        problems.append(f"{where}: cluster system identifier mismatch")
    return problems


def _delete_marked(conn) -> None:
    """Delete every synthetic row this suite can have created.

    Tenants cascade to users and every tenant-scoped table, including the
    recruiter tables since Phase 5B (FK ON DELETE CASCADE; referential actions
    are not subject to RLS)."""
    from sqlalchemy import text

    conn.execute(text("DELETE FROM tenants WHERE slug LIKE :m"), {"m": f"{MARK}%"})
    # Belt and braces for a user that might have been created with an
    # unexpected tenant (e.g. by the recruiter accept-invite flow).
    conn.execute(
        text("DELETE FROM users WHERE email LIKE :m"), {"m": f"%@{EMAIL_DOMAIN}"}
    )


def _non_rls_counts(conn) -> dict[str, int]:
    from sqlalchemy import text

    return {
        t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
        for t in NON_RLS_TABLES
    }


@pytest.fixture(scope="session")
def aux_engine():
    """Dedicated app_user engine for seeding/inspection. NullPool: it never
    shares connections with the app's pool under test."""
    if not ENABLED:
        pytest.skip("RLS_TEST_DATABASE_URL not set")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    eng = create_engine(RLS_URL, poolclass=NullPool, future=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def _pg_safety(request, aux_engine):
    """Abort the whole session unless the target is provably the disposable
    RLS test cluster as the restricted app role. Then clean up at the end."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    import app.core.db as appdb

    problems: list[str] = []
    with aux_engine.connect() as conn:
        problems += _check_identity("aux connection", _identity(conn))

    # The app under test must point at exactly the same target.
    app_url, want = appdb.engine.url, make_url(RLS_URL)
    if appdb.engine.dialect.name != "postgresql":
        problems.append("app engine is not PostgreSQL (root conftest loaded?)")
    elif (app_url.host, app_url.port, app_url.database, app_url.username) != (
        want.host,
        want.port,
        want.database,
        want.username,
    ):
        problems.append("app engine URL differs from RLS_TEST_DATABASE_URL")
    else:
        with appdb.engine.connect() as conn:
            problems += _check_identity("app engine", _identity(conn))

    if not problems:
        with aux_engine.begin() as conn:
            foreign = conn.execute(
                text("SELECT count(*) FROM tenants WHERE slug NOT LIKE :m"),
                {"m": f"{MARK}%"},
            ).scalar()
            if foreign:
                problems.append(
                    f"database holds {foreign} non-synthetic tenant(s); refusing "
                    "to write to what may not be a disposable test database"
                )
            else:
                _delete_marked(conn)  # leftovers from an aborted earlier run

    if problems:
        pytest.exit("SAFETY PREFLIGHT FAILED: " + "; ".join(problems), returncode=90)

    with aux_engine.connect() as conn:
        baseline = _non_rls_counts(conn)
    print(f"\n[pg-api] preflight OK; non-RLS baseline counts = {baseline}")

    yield

    with aux_engine.begin() as conn:
        _delete_marked(conn)
    with aux_engine.connect() as conn:
        after = _non_rls_counts(conn)
        marked = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM tenants WHERE slug LIKE :m) + "
                "(SELECT count(*) FROM users WHERE email LIKE :e)"
            ),
            {"m": f"{MARK}%", "e": f"%@{EMAIL_DOMAIN}"},
        ).scalar()
    print(f"[pg-api] cleanup: non-RLS counts after = {after}; marked left = {marked}")
    assert after == baseline and marked == 0, (
        f"CLEANUP INCOMPLETE: baseline={baseline} after={after} marked={marked}"
    )


# --------------------------------------------------------------------------
# App, auth, probes, AI mocks
# --------------------------------------------------------------------------
_probe_lock = threading.Lock()
_probe_installed = False


def _install_probes(app) -> None:
    """Register test-only read-only routes that report the tenant GUC as seen
    INSIDE a real request's DB session (in-process only; app code untouched)."""
    global _probe_installed
    with _probe_lock:
        if _probe_installed:
            return
        from fastapi import APIRouter, Depends
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        from app.core.db import get_db
        from app.core.security import get_current_user

        probe = APIRouter(prefix="/__pgapi_probe__")
        q = text(
            "SELECT current_setting('app.current_tenant_id', true), "
            "app.current_tenant_id(), pg_backend_pid(), "
            "(SELECT coalesce(array_agg(DISTINCT tenant_id), '{}') FROM departments)"
        )

        def _snap(db: Session) -> dict:
            raw, tid, pid, visible = db.execute(q).one()
            return {"raw": raw, "tid": tid, "pid": pid, "visible": sorted(visible)}

        @probe.get("/guc")
        def guc(user=Depends(get_current_user), db: Session = Depends(get_db)):
            return {"user_tenant": user.tenant_id, **_snap(db)}

        @probe.get("/guc-after-commit")
        def guc_after_commit(
            user=Depends(get_current_user), db: Session = Depends(get_db)
        ):
            db.commit()  # ends the txn; the connection goes back to the pool
            return {"user_tenant": user.tenant_id, **_snap(db)}

        @probe.get("/guc-public")
        def guc_public(db: Session = Depends(get_db)):
            return _snap(db)

        app.include_router(probe)
        _probe_installed = True


@pytest.fixture(scope="session")
def fastapi_app(_pg_safety):
    from main import app

    _install_probes(app)
    return app


@pytest.fixture(scope="session")
def client(fastapi_app):
    from fastapi.testclient import TestClient

    # raise_server_exceptions=False: a 500 is reported as a status code (what
    # a real caller sees) instead of aborting the test with a traceback.
    # Browser-like client (cookie jar) -> send the API's own origin (CSRF).
    return TestClient(
        fastapi_app,
        raise_server_exceptions=False,
        headers={"Origin": "http://testserver"},
    )


@pytest.fixture(scope="session")
def auth(_pg_safety):
    """auth(user_id) -> Authorization header with a JWT minted by the app's own
    create_access_token (bypasses /auth/login rate limits; the request is still
    authenticated by the real get_current_user dependency)."""
    from app.core.security import create_access_token

    return lambda user_id: {
        "Authorization": f"Bearer {create_access_token(str(user_id))}"
    }


class WorkerCalls(list):
    pass


@pytest.fixture(autouse=True)
def no_ai_network(monkeypatch):
    """Fail loudly if any code path reaches the real AI worker."""
    if not ENABLED:
        yield None
        return
    import ai_client

    calls = WorkerCalls()

    def _blocked(method, path, *a, **kw):
        calls.append((method, path))
        raise AssertionError(f"unmocked AI-worker call: {method} {path}")

    monkeypatch.setattr(ai_client, "_worker_request", _blocked)
    yield calls


@pytest.fixture
def face_mock(monkeypatch):
    """Mock the three face functions the backend calls; record arguments."""
    import ai_client

    # enroll/delete record student ids (as before); *_tenants record the
    # tenant_id each call carried; match records the tenant_id per call.
    rec = SimpleNamespace(enroll=[], delete=[], match=[], enroll_tenants=[],
                          delete_tenants=[], match_result=None)

    # Keyword-only tenant_id, mirroring ai_client: a caller that forgets it
    # fails with TypeError instead of silently matching across tenants.
    def enroll_face(student_id, image_base64, *, tenant_id):
        rec.enroll.append(student_id)
        rec.enroll_tenants.append(tenant_id)
        return {"student_id": student_id, "enrolled": True, "det_score": 0.97}

    def delete_face_enrollment(student_id, *, tenant_id):
        rec.delete.append(student_id)
        rec.delete_tenants.append(tenant_id)

    def match_faces(image_base64, *, tenant_id, score_threshold=None):
        rec.match.append(tenant_id)
        return rec.match_result or {
            "detected_faces": 0, "matched": [], "unmatched_faces": 0, "threshold": 0.5
        }

    monkeypatch.setattr(ai_client, "enroll_face", enroll_face)
    monkeypatch.setattr(ai_client, "delete_face_enrollment", delete_face_enrollment)
    monkeypatch.setattr(ai_client, "match_faces", match_faces)
    return rec


@pytest.fixture(scope="session")
def tiny_png_b64() -> str:
    """A synthetic 1x1 grey PNG (no face, no personal data)."""
    import base64
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x80"))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


# --------------------------------------------------------------------------
# SQL helpers (app_user, explicit tenant GUC, committed)
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def sql(aux_engine):
    """sql(stmt, params=None, tenant=None) -> list of row mappings.

    Runs in its own committed transaction on the aux engine; when ``tenant`` is
    given the RLS GUC is set transaction-locally first."""
    from sqlalchemy import text

    def run(stmt, params=None, tenant=None):
        with aux_engine.begin() as conn:
            if tenant is not None:
                conn.execute(
                    text("SELECT set_config('app.current_tenant_id', :t, true)"),
                    {"t": str(tenant)},
                )
            res = conn.execute(text(stmt), params or {})
            return [dict(r) for r in res.mappings()] if res.returns_rows else []

    return run


@pytest.fixture
def world(sql):
    """Two fully-populated synthetic institutes, A and B, deleted afterwards."""
    run = uuid.uuid4().hex[:10]
    created_tenants: list[int] = []
    out = {}
    future = datetime.now(timezone.utc) + timedelta(days=7)
    try:
        for key in ("a", "b"):
            t = SimpleNamespace(key=key)
            slug = f"{MARK}-{run}-{key}"
            t.tenant = sql(
                "INSERT INTO tenants (name, slug, is_active) VALUES (:n, :s, true) "
                "RETURNING id",
                {"n": f"Synthetic Institute {key.upper()} {run}", "s": slug},
            )[0]["id"]
            created_tenants.append(t.tenant)

            def user(role, label, _t=t, _key=key):
                return sql(
                    "INSERT INTO users (email, full_name, hashed_password, role, "
                    "is_active, tenant_id) VALUES (:e, :f, 'not-a-real-hash', "
                    "CAST(:r AS user_role), true, :t) RETURNING id",
                    {
                        "e": f"{label}-{_key}-{run}@{EMAIL_DOMAIN}",
                        "f": f"Synthetic {label} {_key.upper()}",
                        "r": role,
                        "t": _t.tenant,
                    },
                )[0]["id"]

            t.admin = user("admin", "admin")
            t.teacher = user("teacher", "teacher")
            t.tpo = user("tpo", "tpo")
            t.student = user("student", "student")
            t.student2 = user("student", "student2")
            t.recruiter_user = user("recruiter", "recruiter")

            tid = t.tenant
            t.dept = sql(
                "INSERT INTO departments (tenant_id, name, code) VALUES (:t, :n, :c) "
                "RETURNING id",
                {"t": tid, "n": f"Dept {key}", "c": f"D{key.upper()}"},
                tenant=tid,
            )[0]["id"]
            t.section = sql(
                "INSERT INTO sections (tenant_id, name, department_id) "
                "VALUES (:t, :n, :d) RETURNING id",
                {"t": tid, "n": f"S{key.upper()}", "d": t.dept},
                tenant=tid,
            )[0]["id"]
            sql(
                "UPDATE users SET section_id = :s WHERE id = :u",
                {"s": t.section, "u": t.student},
            )
            t.assignment = sql(
                "INSERT INTO assignments (tenant_id, section_id, title, due_date, "
                "max_marks) VALUES (:t, :s, 'Synthetic assignment', :d, 100) "
                "RETURNING id",
                {"t": tid, "s": t.section, "d": future},
                tenant=tid,
            )[0]["id"]
            t.doubt = sql(
                "INSERT INTO doubts (tenant_id, section_id, title, body, status, "
                "asked_by_id) VALUES (:t, :s, 'Synthetic doubt', 'body', "
                "'open', :u) RETURNING id",
                {"t": tid, "s": t.section, "u": t.student},
                tenant=tid,
            )[0]["id"]
            t.drive = sql(
                "INSERT INTO drives (tenant_id, company_name, role_title, min_cgpa, "
                "min_attendance, min_verified_projects, is_open, created_by_id) "
                "VALUES (:t, 'Synthetic Co', 'Engineer', 0, 0, 0, true, :u) "
                "RETURNING id",
                {"t": tid, "u": t.tpo},
                tenant=tid,
            )[0]["id"]
            t.application = sql(
                "INSERT INTO applications (tenant_id, drive_id, student_id, status, "
                "contact_revealed, recruiter_decision) VALUES (:t, :d, :s, "
                "'shortlisted', false, 'pending') RETURNING id",
                {"t": tid, "d": t.drive, "s": t.student2},
                tenant=tid,
            )[0]["id"]
            t.company_name = f"{MARK}-{run}-{key} Synthetic Recruiting Co"
            # Recruiter data is owned by the institute (Phase 5B: RLS).
            t.company = sql(
                "INSERT INTO recruiters (tenant_id, company_name, status, "
                "created_by_id) VALUES (:t, :n, 'active', :u) RETURNING id",
                {"t": tid, "n": t.company_name, "u": t.tpo},
                tenant=tid,
            )[0]["id"]
            sql(
                "INSERT INTO recruiter_users (tenant_id, recruiter_id, user_id, "
                "is_primary) VALUES (:t, :r, :u, true)",
                {"t": tid, "r": t.company, "u": t.recruiter_user},
                tenant=tid,
            )
            t.invite_email = f"invitee-{key}-{run}@{EMAIL_DOMAIN}"
            t.invite_token = secrets.token_urlsafe(32)
            t.invite = sql(
                "INSERT INTO recruiter_invites (tenant_id, recruiter_id, email, "
                "token_hash, status, expires_at, invited_by_id) VALUES (:t, :r, :e, "
                ":tok, 'pending', :x, :u) RETURNING id",
                {
                    "t": tid,
                    "r": t.company,
                    "e": t.invite_email,
                    # Phase 7: only the SHA-256 digest is stored.
                    "tok": hashlib.sha256(t.invite_token.encode()).hexdigest(),
                    "x": future,
                    "u": t.tpo,
                },
                tenant=tid,
            )[0]["id"]
            # Synthetic enrollment RECORD only (det_score); no image/embedding.
            sql(
                "INSERT INTO face_enrollments (student_id, enrolled_by_id, det_score) "
                "VALUES (:s, :u, 0.99)",
                {"s": t.student, "u": t.teacher},
            )
            out[key] = t
        out["run"] = run
        yield SimpleNamespace(**out)
    finally:
        # Tenant cascade removes users, recruiter companies/invites/links and
        # every tenant-scoped row created by the API during the test.
        for tid in created_tenants:
            sql("DELETE FROM tenants WHERE id = :t", {"t": tid})


@pytest.fixture
def count_both(sql, world):
    """count_both(table, where, params) -> rows matching under A's GUC + B's GUC
    (every row of an RLS table is visible under exactly one of them)."""

    def run(table, where, params):
        stmt = f'SELECT count(*) AS n FROM "{table}" WHERE {where}'
        return sum(
            sql(stmt, params, tenant=t.tenant)[0]["n"] for t in (world.a, world.b)
        )

    return run


# --------------------------------------------------------------------------
# API-built synthetic institutes (shared by the flow test modules)
# --------------------------------------------------------------------------
from flowkit import EMAIL_DOMAIN as _FK_EMAIL_DOMAIN, MARK as _FK_MARK  # noqa: E402
from flowkit import Actor, _d  # noqa: E402,F401

@pytest.fixture
def fresh_rate_limits():
    """signup/login/accept are per-IP rate-limited (in-memory here). Reset per
    test so fixtures that create many sessions stay independent."""
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
def campus(fastapi_app, sql, fresh_rate_limits):
    from fastapi.testclient import TestClient

    run = uuid.uuid4().hex[:10]
    clients: list = []
    slugs: list[str] = []

    def new_client():
        c = TestClient(
            fastapi_app,
            raise_server_exceptions=False,
            headers={"Origin": "http://testserver"},
        )
        clients.append(c)
        return c

    def login(email, password, role_label):
        c = new_client()
        r = c.post("/v1/auth/login", data={"username": email, "password": password})
        assert r.status_code == 200, f"login {role_label}: {_d(r)}"
        me = c.get("/v1/auth/me")
        assert me.status_code == 200, f"/me {role_label}: {_d(me)}"
        return Actor(client=c, id=me.json()["id"], email=email, password=password,
                     role=me.json()["role"])

    def invite_and_accept(admin, role, label):
        email = f"{label}-{run}@{_FK_EMAIL_DOMAIN}"
        inv = admin.post("/v1/tenants/invites", json={"email": email, "role": role})
        assert inv.status_code == 201, f"invite {label}: {_d(inv)}"
        pw = secrets.token_urlsafe(16)
        acc = new_client().post("/v1/tenants/accept-invite", json={
            "token": inv.json()["token"], "full_name": f"Synthetic {label}", "password": pw})
        assert acc.status_code == 200, f"accept {label}: {_d(acc)}"
        return login(email, pw, label)

    def institute(key):
        slug = f"{_FK_MARK}-{run}-{key}"
        slugs.append(slug)
        admin_email, admin_pw = f"admin-{key}-{run}@{_FK_EMAIL_DOMAIN}", secrets.token_urlsafe(16)
        su = new_client().post("/v1/tenants/signup", json={
            "institution_name": f"Synthetic Institute {key.upper()} {run}",
            "institution_slug": slug, "admin_full_name": f"Admin {key}",
            "admin_email": admin_email, "admin_password": admin_pw})
        assert su.status_code == 201, f"signup {key}: {_d(su)}"
        t = SimpleNamespace(key=key, slug=slug)
        t.tenant = sql("SELECT id FROM tenants WHERE slug = :s", {"s": slug})[0]["id"]
        t.admin = login(admin_email, admin_pw, f"admin-{key}")
        dept = t.admin.post("/v1/admin/departments", json={"name": f"Dept {key}", "code": f"D{key.upper()}"})
        assert dept.status_code == 201, f"dept {key}: {_d(dept)}"
        t.dept = dept.json()["id"]
        sec = t.admin.post(f"/v1/admin/departments/{t.dept}/sections", json={"name": "S1"})
        assert sec.status_code == 201, f"section {key}: {_d(sec)}"
        t.section = sec.json()["id"]
        t.teacher = invite_and_accept(t.admin, "teacher", f"teacher-{key}")
        t.tpo = invite_and_accept(t.admin, "tpo", f"tpo-{key}")
        t.student = invite_and_accept(t.admin, "student", f"student-{key}")
        # Second student via direct admin user creation.
        s2_email, s2_pw = f"student2-{key}-{run}@{_FK_EMAIL_DOMAIN}", secrets.token_urlsafe(16)
        cu = t.admin.post("/v1/admin/users", json={
            "email": s2_email, "full_name": f"Synthetic student2 {key}",
            "password": s2_pw, "role": "student"})
        assert cu.status_code == 201, f"admin create user {key}: {_d(cu)}"
        t.student2 = login(s2_email, s2_pw, f"student2-{key}")
        for s in (t.student, t.student2):
            r = t.admin.patch(f"/v1/admin/users/{s.id}/section", json={"section_id": t.section})
            assert r.status_code == 200, f"assign section {key}: {_d(r)}"
        return t

    try:
        a = institute("a")
        b = institute("b")
        yield SimpleNamespace(a=a, b=b, run=run, new_client=new_client, login=login)
    finally:
        for c in clients:
            c.close()
        for slug in slugs:  # tenant cascade removes users, invites and all rows
            sql("DELETE FROM tenants WHERE slug = :s", {"s": slug})


@pytest.fixture
def count_rows(sql):
    def run(tenant, table, where, params):
        return sql(f'SELECT count(*) AS n FROM "{table}" WHERE {where}', params,
                   tenant=tenant)[0]["n"]
    return run

