"""Phase 7: invite tokens are stored only as SHA-256 digests.

The raw token is returned once in the create response; the DB row keeps
``token_hash`` only. Accepting with the raw token works; presenting the stored
digest itself (what a DB leak would reveal) does not.
"""

import hashlib

from sqlalchemy import inspect

import app.core.db as db
from app.models import RecruiterInvite, TenantInvite, UserRole


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_invite_tables_have_no_plaintext_token_column():
    insp = inspect(db.engine)
    for table in ("tenant_invites", "recruiter_invites"):
        cols = {c["name"] for c in insp.get_columns(table)}
        assert "token_hash" in cols
        assert "token" not in cols


def test_tenant_invite_stores_digest_and_accepts_raw_token(
    client, session, make_user, token_header
):
    make_user("admin@acme.dev", role=UserRole.admin)
    r = client.post(
        "/v1/tenants/invites",
        json={"email": "new.teacher@acme.dev", "role": "teacher"},
        headers=token_header("admin@acme.dev"),
    )
    assert r.status_code == 201, r.text
    raw = r.json()["token"]
    assert r.json()["accept_path"].endswith(raw)

    row = session.query(TenantInvite).one()
    assert row.token_hash == _sha(raw)
    assert row.token_hash != raw

    # The digest (what a DB/backup leak exposes) is NOT a usable credential.
    leaked = client.post(
        "/v1/tenants/accept-invite",
        json={"token": row.token_hash, "full_name": "Mallory", "password": "secret123"},
    )
    assert leaked.status_code == 400

    ok = client.post(
        "/v1/tenants/accept-invite",
        json={"token": raw, "full_name": "New Teacher", "password": "secret123"},
    )
    assert ok.status_code == 200, ok.text
    # Single use.
    again = client.post(
        "/v1/tenants/accept-invite",
        json={"token": raw, "full_name": "Again", "password": "secret123"},
    )
    assert again.status_code == 400


def test_invite_listing_never_exposes_token_or_digest(client, make_user, token_header):
    make_user("admin@acme.dev", role=UserRole.admin)
    h = token_header("admin@acme.dev")
    raw = client.post(
        "/v1/tenants/invites", json={"email": "s@acme.dev", "role": "student"}, headers=h
    ).json()["token"]
    listing = client.get("/v1/tenants/invites", headers=h)
    assert listing.status_code == 200
    assert raw not in listing.text and _sha(raw) not in listing.text
    assert "token" not in listing.json()[0]


def test_recruiter_invite_stores_digest_and_accepts_raw_token(
    client, session, make_user, token_header
):
    make_user("tpo@acme.dev", role=UserRole.tpo)
    r = client.post(
        "/v1/recruiters/invite",
        json={"company_name": "Synthetic Co", "email": "hr@synthetic.dev"},
        headers=token_header("tpo@acme.dev"),
    )
    assert r.status_code == 201, r.text
    raw = r.json()["token"]

    row = session.query(RecruiterInvite).one()
    assert row.token_hash == _sha(raw) and row.token_hash != raw

    leaked = client.post(
        "/v1/recruiters/accept-invite",
        json={"token": row.token_hash, "full_name": "Mallory", "password": "secret123"},
    )
    assert leaked.status_code == 400

    ok = client.post(
        "/v1/recruiters/accept-invite",
        json={"token": raw, "full_name": "Synthetic HR", "password": "secret123"},
    )
    assert ok.status_code == 200, ok.text
