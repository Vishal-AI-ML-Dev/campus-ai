"""Phase 5B - recruiter data owned by one institute (Option A), via the real API.

Complements the original gap tests in test_recruiter_face_authz.py (kept
unchanged) with same-tenant success controls, the public accept-invite flow's
tenant resolution, and database-level guarantees (RLS + composite FKs).
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import pytest
from sqlalchemy.exc import IntegrityError

MARK = "zz-api-iso"
EMAIL_DOMAIN = "zz-api-iso.example.com"


def _invite(client, auth, tpo, run, label):
    email = f"{label}-{run}@{EMAIL_DOMAIN}"
    r = client.post("/v1/recruiters/invite",
                    json={"company_name": f"{MARK}-{run} {label} co", "email": email},
                    headers=auth(tpo))
    assert r.status_code == 201, r.text
    return email, r.json()


def _h(raw: str) -> str:
    """Independent oracle for the stored digest (Phase 7)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _accept(client, token, **extra):
    body = {"token": token, "full_name": "Synthetic Rec",
            "password": "synthetic-pass-123", **extra.pop("body", {})}
    return client.post("/v1/recruiters/accept-invite", json=body, **extra)


# --- Database guarantees ----------------------------------------------------
def test_recruiter_tables_forced_rls_with_expected_policies(sql):
    rows = sql("SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS f, "
               "array_agg(p.polname::text ORDER BY p.polname) AS pols "
               "FROM pg_class c LEFT JOIN pg_policy p ON p.polrelid = c.oid "
               "WHERE c.relname IN ('recruiters','recruiter_invites','recruiter_users') "
               "GROUP BY 1, 2, 3 ORDER BY 1")
    assert rows == [
        {"relname": "recruiter_invites", "rls": True, "f": True,
         "pols": ["invite_token_lookup", "tenant_isolation"]},
        {"relname": "recruiter_users", "rls": True, "f": True, "pols": ["tenant_isolation"]},
        {"relname": "recruiters", "rls": True, "f": True, "pols": ["tenant_isolation"]},
    ]


def test_db_rejects_cross_institute_recruiter_links(sql, world):
    """Composite FKs: a tenant-A row cannot reference B's company or B's user."""
    with pytest.raises(IntegrityError):  # A invite -> B company
        sql("INSERT INTO recruiter_invites (tenant_id, recruiter_id, email, token_hash, "
            "status, expires_at) VALUES (:t, :r, 'x@zz-api-iso.example.com', :tok, "
            "'pending', now() + interval '1 day')",
            {"t": world.a.tenant, "r": world.b.company, "tok": _h(secrets.token_urlsafe(24))},
            tenant=world.a.tenant)
    with pytest.raises(IntegrityError):  # A company <- B user account
        sql("INSERT INTO recruiter_users (tenant_id, recruiter_id, user_id, is_primary) "
            "VALUES (:t, :r, :u, false)",
            {"t": world.a.tenant, "r": world.a.company, "u": world.b.tpo},
            tenant=world.a.tenant)


def test_invite_token_policy_is_narrow(aux_engine, world, sql):
    """With ONLY the token GUC (no tenant): exactly that pending invite is
    visible - no other invite, no company, no recruiter link."""
    from sqlalchemy import text

    with aux_engine.begin() as c:
        c.execute(text("SELECT set_config('app.invite_token_hash', :t, true)"),
                  {"t": _h(world.b.invite_token)})
        invites = c.execute(text("SELECT id, tenant_id FROM recruiter_invites")).all()
        companies = c.execute(text("SELECT count(*) FROM recruiters")).scalar()
        links = c.execute(text("SELECT count(*) FROM recruiter_users")).scalar()
    assert [tuple(r) for r in invites] == [(world.b.invite, world.b.tenant)]
    assert companies == 0 and links == 0
    # A non-pending invite is not revealed by its token.
    sql("UPDATE recruiter_invites SET status = 'revoked' WHERE id = :i",
        {"i": world.b.invite}, tenant=world.b.tenant)
    with aux_engine.begin() as c:
        c.execute(text("SELECT set_config('app.invite_token_hash', :t, true)"),
                  {"t": _h(world.b.invite_token)})
        assert c.execute(text("SELECT count(*) FROM recruiter_invites")).scalar() == 0


# --- TPO management: same-tenant success ------------------------------------
def test_each_tpo_lists_only_own_companies_and_invites(client, auth, world):
    for me, other in ((world.a, world.b), (world.b, world.a)):
        cos = {c["id"] for c in client.get("/v1/recruiters?limit=200",
                                           headers=auth(me.tpo)).json()}
        invs = {i["email"] for i in client.get("/v1/recruiters/invites?limit=200",
                                               headers=auth(me.tpo)).json()}
        assert me.company in cos and other.company not in cos
        assert me.invite_email in invs and other.invite_email not in invs


def test_invite_creates_company_and_invite_in_callers_institute(client, auth, world, sql):
    _, body = _invite(client, auth, world.a.tpo, world.run, "own")
    rid, iid = body["recruiter"]["id"], body["invite"]["id"]
    assert sql("SELECT tenant_id FROM recruiters WHERE id = :i", {"i": rid},
               tenant=world.a.tenant) == [{"tenant_id": world.a.tenant}]
    assert sql("SELECT tenant_id FROM recruiter_invites WHERE id = :i", {"i": iid},
               tenant=world.a.tenant) == [{"tenant_id": world.a.tenant}]
    assert sql("SELECT count(*) AS n FROM recruiters WHERE id = :i", {"i": rid},
               tenant=world.b.tenant) == [{"n": 0}]


def test_revoke_own_invite_succeeds(client, auth, world, sql):
    r = client.post(f"/v1/recruiters/invites/{world.a.invite}/revoke",
                    headers=auth(world.a.tpo))
    assert r.status_code == 200, r.text
    assert sql("SELECT status::text AS s FROM recruiter_invites WHERE id = :i",
               {"i": world.a.invite}, tenant=world.a.tenant) == [{"s": "revoked"}]


def test_link_and_unlink_own_drive_to_own_company(client, auth, world, sql):
    url = f"/v1/recruiters/drives/{world.a.drive}/recruiter"
    r = client.patch(url, json={"recruiter_id": world.a.company}, headers=auth(world.a.tpo))
    assert r.status_code == 200, r.text
    q = "SELECT recruiter_id FROM drives WHERE id = :d"
    assert sql(q, {"d": world.a.drive}, tenant=world.a.tenant) == [
        {"recruiter_id": world.a.company}]
    # Same-tenant recruiter now sees the shortlisted candidate (access control).
    cands = client.get("/v1/recruiters/me/candidates", headers=auth(world.a.recruiter_user))
    assert cands.status_code == 200
    assert world.a.application in {c["application_id"] for c in cands.json()}
    r = client.patch(url, json={"recruiter_id": None}, headers=auth(world.a.tpo))
    assert r.status_code == 200, r.text
    assert sql(q, {"d": world.a.drive}, tenant=world.a.tenant) == [{"recruiter_id": None}]


# --- Public accept-invite: tenant comes from the invite only ----------------
def test_accept_invite_uses_invite_tenant_and_ignores_client_hints(client, auth, world, sql):
    """B's invite accepted while the request carries A-pointing hints (body
    tenant_id, X-Tenant-ID header, even A's admin JWT): account lands in B."""
    email, inv = _invite(client, auth, world.b.tpo, world.run, "hint")
    hints = {"X-Tenant-ID": str(world.a.tenant), **auth(world.a.admin)}
    r = _accept(client, inv["token"], body={"tenant_id": world.a.tenant}, headers=hints)
    assert r.status_code == 200, r.text
    assert sql("SELECT tenant_id FROM users WHERE email = :e", {"e": email}) == [
        {"tenant_id": world.b.tenant}]
    me = client.get("/v1/recruiters/me",
                    headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["recruiter"]["id"] == inv["recruiter"]["id"]
    assert me.json()["user"]["email"] == email


def test_accept_invite_rejects_unknown_used_revoked_and_expired(client, auth, world, sql):
    assert _accept(client, "zz-unknown-" + secrets.token_urlsafe(16)).status_code == 400
    # used
    _, inv = _invite(client, auth, world.a.tpo, world.run, "used")
    assert _accept(client, inv["token"]).status_code == 200
    assert _accept(client, inv["token"]).status_code == 400
    # revoked (by its own TPO)
    _, inv2 = _invite(client, auth, world.a.tpo, world.run, "revoked")
    client.post(f"/v1/recruiters/invites/{inv2['invite']['id']}/revoke",
                headers=auth(world.a.tpo))
    assert _accept(client, inv2["token"]).status_code == 400
    # expired: still 'pending' but past expiry -> 400 and marked expired
    tok = secrets.token_urlsafe(24)
    iid = sql("INSERT INTO recruiter_invites (tenant_id, recruiter_id, email, token_hash, "
              "status, expires_at) VALUES (:t, :r, :e, :tok, 'pending', :x) RETURNING id",
              {"t": world.a.tenant, "r": world.a.company,
               "e": f"expired-{world.run}@{EMAIL_DOMAIN}", "tok": _h(tok),
               "x": datetime.now(timezone.utc) - timedelta(days=1)},
              tenant=world.a.tenant)[0]["id"]
    r = _accept(client, tok)
    assert r.status_code == 400 and "expired" in r.text
    assert sql("SELECT status::text AS s FROM recruiter_invites WHERE id = :i",
               {"i": iid}, tenant=world.a.tenant) == [{"s": "expired"}]


def test_accept_invite_leaves_no_token_or_tenant_on_pooled_connections(client, auth, world):
    import app.core.db as appdb

    _, inv = _invite(client, auth, world.a.tpo, world.run, "pool")
    assert _accept(client, inv["token"]).status_code == 200
    conns = [appdb.engine.raw_connection()
             for _ in range(max(appdb.engine.pool.checkedin(), 1))]
    try:
        for c in conns:
            cur = c.cursor()
            cur.execute("SELECT current_setting('app.invite_token_hash', true), "
                        "app.current_tenant_id()")
            tok, tid = cur.fetchone()
            assert tok in (None, "") and tid is None, (tok, tid)
    finally:
        for c in conns:
            c.close()


def test_same_email_at_two_institutes_needs_different_logins(client, auth, world, sql):
    """Documents the accepted consequence of globally unique login emails:
    both institutes may invite the same address, but only the first
    acceptance creates a login; the second is refused (409)."""
    email = f"shared-{world.run}@{EMAIL_DOMAIN}"
    invs = []
    for t in (world.a, world.b):
        r = client.post("/v1/recruiters/invite",
                        json={"company_name": f"{MARK}-{world.run} shared co",
                              "email": email}, headers=auth(t.tpo))
        assert r.status_code == 201, r.text
        invs.append(r.json()["token"])
    assert _accept(client, invs[0]).status_code == 200
    assert _accept(client, invs[1]).status_code == 409
    assert sql("SELECT tenant_id FROM users WHERE email = :e", {"e": email}) == [
        {"tenant_id": world.a.tenant}]


# --- Phase 7: invite tokens are stored only as SHA-256 digests --------------
def test_db_refuses_plaintext_invite_tokens(sql, world):
    """CHECK (token_hash ~ '^[0-9a-f]{64}$') on both invite tables: a raw
    token can never be written again, by old code or by hand."""
    raw = secrets.token_urlsafe(32)
    with pytest.raises(IntegrityError):
        sql("INSERT INTO recruiter_invites (tenant_id, recruiter_id, email, token_hash, "
            "status, expires_at) VALUES (:t, :r, :e, :tok, 'pending', now() + interval '1 day')",
            {"t": world.a.tenant, "r": world.a.company,
             "e": f"plain-{world.run}@{EMAIL_DOMAIN}", "tok": raw},
            tenant=world.a.tenant)
    with pytest.raises(IntegrityError):
        sql("INSERT INTO tenant_invites (tenant_id, email, role, token_hash, status, "
            "expires_at) VALUES (:t, :e, 'student', :tok, 'pending', now() + interval '1 day')",
            {"t": world.a.tenant, "e": f"plain-t-{world.run}@{EMAIL_DOMAIN}", "tok": raw},
            tenant=world.a.tenant)


def test_api_invite_rows_hold_only_the_digest(client, auth, world, sql):
    _, inv = _invite(client, auth, world.a.tpo, world.run, "digest")
    row = sql("SELECT token_hash FROM recruiter_invites WHERE id = :i",
              {"i": inv["invite"]["id"]}, tenant=world.a.tenant)[0]
    assert row["token_hash"] == _h(inv["token"]) != inv["token"]
    # The stored digest itself is not a usable credential.
    assert _accept(client, row["token_hash"]).status_code == 400
    assert _accept(client, inv["token"]).status_code == 200
