"""Users-table tenant isolation (Batch 4).

The ``users`` table intentionally has NO Postgres RLS - authentication must
read a user by email *before* any tenant is known (login is a chicken-and-egg:
you learn the tenant from the user row). That leaves user-by-id / roster
lookups relying on an APPLICATION-LAYER guard (``security.get_scoped_user`` +
explicit ``User.tenant_id`` filters).

This suite proves that guard: one institute can neither LIST nor MUTATE another
institute's accounts. It runs on the normal SQLite suite because the guard is
application-layer (not RLS), so no Postgres is required.
"""

import app.models as models


def test_people_students_list_is_tenant_scoped(
    client, make_user, make_tenant, token_header
):
    """GET /people/students returns only the caller's institute's students."""
    iit = make_tenant(slug="iit-people", name="IIT People")
    nit = make_tenant(slug="nit-people", name="NIT People")

    make_user("iitp.admin@test.dev", role=models.UserRole.admin, tenant=iit)
    make_user(
        "iitp.stu@test.dev",
        role=models.UserRole.student,
        tenant=iit,
        full_name="IIT Student",
    )
    make_user(
        "nitp.stu@test.dev",
        role=models.UserRole.student,
        tenant=nit,
        full_name="NIT Student",
    )

    resp = client.get(
        "/people/students", headers=token_header("iitp.admin@test.dev")
    )
    assert resp.status_code == 200, resp.text
    names = {s["full_name"] for s in resp.json()}
    assert "IIT Student" in names
    assert "NIT Student" not in names  # the leak this batch closes


def test_admin_cannot_modify_cross_tenant_user(
    client, make_user, make_tenant, token_header
):
    """An admin cannot change the role/status of a user in another institute."""
    iit = make_tenant(slug="iit-admin", name="IIT Admin")
    nit = make_tenant(slug="nit-admin", name="NIT Admin")

    make_user("iita.admin@test.dev", role=models.UserRole.admin, tenant=iit)
    nit_stu = make_user(
        "nita.stu@test.dev", role=models.UserRole.student, tenant=nit
    )

    hdr = token_header("iita.admin@test.dev")

    # Role change on another tenant's user -> 404 (invisible outside its tenant).
    r_role = client.patch(
        f"/admin/users/{nit_stu.id}/role",
        json={"role": models.UserRole.teacher.value},
        headers=hdr,
    )
    assert r_role.status_code == 404, r_role.text

    # Status change (disable account) is likewise blocked cross-tenant.
    r_status = client.patch(
        f"/admin/users/{nit_stu.id}/status",
        json={"is_active": False},
        headers=hdr,
    )
    assert r_status.status_code == 404, r_status.text

    # And the victim's role/status are untouched.
    assert nit_stu.role == models.UserRole.student
    assert nit_stu.is_active is True


def test_admin_can_still_manage_own_tenant_user(
    client, make_user, make_tenant, token_header
):
    """Guard must not break the legitimate same-tenant path."""
    iit = make_tenant(slug="iit-ok", name="IIT OK")

    make_user("iitok.admin@test.dev", role=models.UserRole.admin, tenant=iit)
    stu = make_user(
        "iitok.stu@test.dev", role=models.UserRole.student, tenant=iit
    )

    r = client.patch(
        f"/admin/users/{stu.id}/role",
        json={"role": models.UserRole.teacher.value},
        headers=token_header("iitok.admin@test.dev"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == models.UserRole.teacher.value
