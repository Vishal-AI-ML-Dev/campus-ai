"""Self-serve institution onboarding: tenant + first admin."""

from app.models import Tenant, User, UserRole


def _payload(**overrides):
    data = {
        "institution_name": "Acme Institute",
        "institution_slug": "acme-new",
        "admin_full_name": "Acme Admin",
        "admin_email": "admin@acme.dev",
        "admin_password": "secret123",
    }
    data.update(overrides)
    return data


def test_tenant_signup_creates_tenant_first_admin_and_token(client, session):
    resp = client.post("/tenants/signup", json=_payload())

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    tenant = session.query(Tenant).filter_by(slug="acme-new").one()
    admin = session.query(User).filter_by(email="admin@acme.dev").one()
    assert tenant.name == "Acme Institute"
    assert admin.tenant_id == tenant.id
    assert admin.role == UserRole.admin

    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "admin@acme.dev"
    assert me.json()["role"] == "admin"


def test_tenant_signup_duplicate_slug_conflicts(client, make_tenant):
    make_tenant(slug="taken")

    resp = client.post(
        "/tenants/signup",
        json=_payload(institution_slug="taken", admin_email="new@acme.dev"),
    )

    assert resp.status_code == 409
    assert "slug" in resp.json()["detail"]


def test_tenant_signup_duplicate_admin_email_conflicts(client, make_user):
    make_user("admin@acme.dev", role=UserRole.admin)

    resp = client.post(
        "/tenants/signup",
        json=_payload(institution_slug="unique-slug"),
    )

    assert resp.status_code == 409
    assert "email" in resp.json()["detail"]


def test_tenant_signup_normalizes_slug_and_email(client, session):
    resp = client.post(
        "/tenants/signup",
        json=_payload(
            institution_slug="mixed-case",
            admin_email="ADMIN2@acme.dev",
        ),
    )

    assert resp.status_code == 201, resp.text
    assert session.query(Tenant).filter_by(slug="mixed-case").one()
    assert session.query(User).filter_by(email="admin2@acme.dev").one()
