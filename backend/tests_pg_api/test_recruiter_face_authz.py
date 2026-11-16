"""Phase 5A - recruiter & face authorization across institutes.

``recruiters``, ``recruiter_users``, ``recruiter_invites`` and
``face_enrollments`` have NO tenant_id and NO RLS, so the database cannot help:
isolation depends entirely on the routes. Each test states the secure
expectation; a failure is a real cross-tenant gap in the current code.
AI/face worker calls are mocked (``face_mock``); nothing leaves the process.
"""

# Same marker values as conftest.py (synthetic-data tags used for cleanup).

MARK = "zz-api-iso"
EMAIL_DOMAIN = "zz-api-iso.example.com"


# --- Recruiters ------------------------------------------------------------
def test_recruiter_company_list_hides_other_tenant(client, auth, world):
    r = client.get("/v1/recruiters?limit=100", headers=auth(world.a.tpo))
    assert r.status_code == 200, r.text
    ids = {c["id"] for c in r.json()}
    assert world.a.company in ids, "control: own company must be listed"
    assert world.b.company not in ids, (
        "TPO of institute A can list institute B's recruiter companies"
    )


def test_recruiter_invite_list_hides_other_tenant(client, auth, world):
    r = client.get("/v1/recruiters/invites?limit=100", headers=auth(world.a.tpo))
    assert r.status_code == 200, r.text
    emails = {i["email"] for i in r.json()}
    assert world.a.invite_email in emails, "control: own invite must be listed"
    assert world.b.invite_email not in emails, (
        "TPO of institute A can read institute B's recruiter invites (emails)"
    )


def test_revoke_other_tenant_invite_is_refused(client, auth, world, sql):
    r = client.post(f"/v1/recruiters/invites/{world.b.invite}/revoke",
                    headers=auth(world.a.tpo))
    # Read as B (recruiter_invites is under RLS since Phase 5B).
    status = sql("SELECT status::text AS s FROM recruiter_invites WHERE id = :i",
                 {"i": world.b.invite}, tenant=world.b.tenant)[0]["s"]
    assert r.status_code == 404 and status == "pending", (
        f"TPO of A revoked B's invite: http={r.status_code}, invite status={status}"
    )


def test_link_own_drive_to_other_tenant_company_is_refused(client, auth, world, sql):
    r = client.patch(f"/v1/recruiters/drives/{world.a.drive}/recruiter",
                     json={"recruiter_id": world.b.company}, headers=auth(world.a.tpo))
    linked = sql("SELECT recruiter_id FROM drives WHERE id = :d", {"d": world.a.drive},
                 tenant=world.a.tenant)[0]["recruiter_id"]
    assert r.status_code == 404 and linked is None, (
        f"A's drive linked to B's company: http={r.status_code}, recruiter_id={linked}"
    )


def test_link_other_tenant_drive_is_404(client, auth, world, sql):
    """RLS-only guard: B's drive is invisible to A's TPO."""
    r = client.patch(f"/v1/recruiters/drives/{world.b.drive}/recruiter",
                     json={"recruiter_id": world.a.company}, headers=auth(world.a.tpo))
    assert r.status_code == 404, r.text
    linked = sql("SELECT recruiter_id FROM drives WHERE id = :d", {"d": world.b.drive},
                 tenant=world.b.tenant)[0]["recruiter_id"]
    assert linked is None


def test_cross_linked_company_recruiter_cannot_see_or_act(client, auth, world, sql):
    """Impact check for the drive-link gap. Phase 5B made the cross-institute
    link itself impossible at the database level (composite FK), which this
    now asserts first; B's recruiter account must still not see A's
    candidates or record a decision on them."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        sql("UPDATE drives SET recruiter_id = :r WHERE id = :d",
            {"r": world.b.company, "d": world.a.drive}, tenant=world.a.tenant)
    h = auth(world.b.recruiter_user)
    drives = client.get("/v1/recruiters/me/drives", headers=h)
    cands = client.get("/v1/recruiters/me/candidates", headers=h)
    assert drives.status_code == 200 and cands.status_code == 200
    assert world.a.drive not in {d["id"] for d in drives.json()}
    assert world.a.application not in {c["application_id"] for c in cands.json()}
    dec = client.patch(f"/v1/recruiters/me/candidates/{world.a.application}/decision",
                       json={"decision": "interested"}, headers=h)
    assert dec.status_code in (403, 404), dec.text
    row = sql("SELECT recruiter_decision::text AS d FROM applications WHERE id = :i",
              {"i": world.a.application}, tenant=world.a.tenant)
    assert row == [{"d": "pending"}]


def test_same_tenant_other_company_recruiter_cannot_act(client, auth, world, sql):
    """App-layer guard: a recruiter of company X cannot decide on a candidate
    on company Y's drive, even within the same institute."""
    other = sql("INSERT INTO recruiters (tenant_id, company_name, status, "
                "created_by_id) VALUES (:t, :n, 'active', :u) RETURNING id",
                {"t": world.a.tenant, "n": f"{MARK}-{world.run}-a other co",
                 "u": world.a.tpo}, tenant=world.a.tenant)[0]["id"]
    sql("UPDATE drives SET recruiter_id = :r WHERE id = :d",
        {"r": other, "d": world.a.drive}, tenant=world.a.tenant)
    dec = client.patch(f"/v1/recruiters/me/candidates/{world.a.application}/decision",
                       json={"decision": "interested"}, headers=auth(world.a.recruiter_user))
    assert dec.status_code == 403, dec.text


def test_recruiter_invite_acceptance_assigns_inviting_tenant(client, auth, world, sql):
    """The accept-invite flow should create the recruiter account in the
    inviting TPO's institute (users.tenant_id is NOT NULL)."""
    email = f"newrec-{world.run}@{EMAIL_DOMAIN}"
    inv = client.post("/v1/recruiters/invite",
                      json={"company_name": f"{MARK}-{world.run}-a invited co",
                            "email": email},
                      headers=auth(world.a.tpo))
    assert inv.status_code == 201, inv.text
    acc = client.post("/v1/recruiters/accept-invite",
                      json={"token": inv.json()["token"], "full_name": "Synthetic Rec",
                            "password": "synthetic-pass-123"})
    rows = sql("SELECT tenant_id FROM users WHERE email = :e", {"e": email})
    assert acc.status_code == 200 and rows == [{"tenant_id": world.a.tenant}], (
        f"accept-invite: http={acc.status_code}, created user rows={rows}"
    )


# --- Face ------------------------------------------------------------------
def test_face_delete_other_tenant_student_is_refused(client, auth, world, sql, face_mock):
    r = client.delete(f"/v1/face/enroll/{world.b.student}", headers=auth(world.a.teacher))
    remaining = sql("SELECT count(*) AS n FROM face_enrollments WHERE student_id = :s",
                    {"s": world.b.student})[0]["n"]
    assert (r.status_code == 404 and face_mock.delete == [] and remaining == 1), (
        f"A's teacher removed B's face enrollment: http={r.status_code}, "
        f"worker delete called for={face_mock.delete}, B record remaining={remaining}"
    )


def test_face_enroll_other_tenant_student_is_404(
    client, auth, world, sql, face_mock, tiny_png_b64
):
    r = client.post("/v1/face/enroll",
                    json={"student_id": world.b.student, "image_base64": tiny_png_b64},
                    headers=auth(world.a.teacher))
    assert r.status_code == 404, r.text
    assert face_mock.enroll == []
    rec = sql("SELECT enrolled_by_id FROM face_enrollments WHERE student_id = :s",
              {"s": world.b.student})
    assert rec == [{"enrolled_by_id": world.b.teacher}]
    ok = client.post("/v1/face/enroll",
                     json={"student_id": world.a.student2, "image_base64": tiny_png_b64},
                     headers=auth(world.a.teacher))
    assert ok.status_code == 200, ok.text
    assert face_mock.enroll == [world.a.student2]


def test_face_enrollment_listing_is_tenant_scoped(client, auth, world, face_mock):
    cross = client.get(f"/v1/face/enrollments?section_id={world.b.section}",
                       headers=auth(world.a.teacher))
    assert cross.status_code == 404, cross.text
    r = client.get("/v1/face/enrollments?limit=100", headers=auth(world.a.teacher))
    assert r.status_code == 200, r.text
    ids = {s["student_id"] for s in r.json()}
    assert world.a.student in ids
    assert not ids & {world.b.student, world.b.student2}


def test_face_routes_refuse_student_role(client, auth, world, face_mock):
    r = client.delete(f"/v1/face/enroll/{world.a.student2}", headers=auth(world.a.student))
    assert r.status_code == 403 and face_mock.delete == []


def test_photo_match_does_not_reveal_other_tenant_students(
    client, auth, world, face_mock, tiny_png_b64
):
    """The (mocked) worker matches against ONE global embedding store keyed by
    student id; it reports a tenant-A and a tenant-B student. A's response must
    not disclose B's student id (suggestions or matched_outside_section)."""
    face_mock.match_result = {
        "detected_faces": 2, "unmatched_faces": 0, "threshold": 0.5,
        "matched": [{"student_id": world.a.student, "score": 0.91},
                    {"student_id": world.b.student, "score": 0.88}],
    }
    cross = client.post("/v1/attendance/photo",
                        json={"section_id": world.b.section, "image_base64": tiny_png_b64},
                        headers=auth(world.a.teacher))
    assert cross.status_code == 404, cross.text
    r = client.post("/v1/attendance/photo",
                    json={"section_id": world.a.section, "image_base64": tiny_png_b64},
                    headers=auth(world.a.teacher))
    assert r.status_code == 200, r.text
    body = r.json()
    assert world.a.student in {s["student_id"] for s in body["suggestions"]}
    leaked = {o["student_id"] for o in body["matched_outside_section"]} | {
        s["student_id"] for s in body["suggestions"]
    }
    assert world.b.student not in leaked, (
        f"tenant-B student id disclosed to tenant-A teacher: {body['matched_outside_section']}"
    )


# --- Phase 5B regression tests (face) ---------------------------------------
def test_face_delete_own_tenant_student_succeeds(client, auth, world, sql, face_mock):
    """Own-tenant control for the delete fix: allowed, worker told the tenant."""
    r = client.delete(f"/v1/face/enroll/{world.a.student}", headers=auth(world.a.teacher))
    assert r.status_code == 200, r.text
    assert face_mock.delete == [world.a.student]
    assert face_mock.delete_tenants == [world.a.tenant]
    left = sql("SELECT count(*) AS n FROM face_enrollments WHERE student_id = :s",
               {"s": world.a.student})[0]["n"]
    assert left == 0


def test_face_delete_non_student_or_unknown_id_is_404(client, auth, world, face_mock):
    """Ownership check is role-aware and existence-blind: a same-tenant
    teacher id and a non-existent id are both 404 with no worker call."""
    for target in (world.a.teacher, 2_000_000_000):
        r = client.delete(f"/v1/face/enroll/{target}", headers=auth(world.a.teacher))
        assert r.status_code == 404, (target, r.text)
    assert face_mock.delete == []


def test_face_enroll_sends_callers_tenant_to_worker(
    client, auth, world, face_mock, tiny_png_b64
):
    r = client.post("/v1/face/enroll",
                    json={"student_id": world.b.student2, "image_base64": tiny_png_b64},
                    headers=auth(world.b.teacher))
    assert r.status_code == 200, r.text
    assert face_mock.enroll == [world.b.student2]
    assert face_mock.enroll_tenants == [world.b.tenant]


def test_photo_match_sends_tenant_and_keeps_own_outsiders(
    client, auth, world, face_mock, tiny_png_b64
):
    """Worker is asked for the caller's tenant only; an own-tenant student from
    ANOTHER section is still reported as an outsider (feature preserved), while
    unknown / foreign ids are dropped."""
    face_mock.match_result = {
        "detected_faces": 3, "unmatched_faces": 0, "threshold": 0.5,
        "matched": [{"student_id": world.a.student, "score": 0.9},
                    {"student_id": world.a.student2, "score": 0.8},   # A, no section
                    {"student_id": 2_000_000_000, "score": 0.7}],     # unknown id
    }
    r = client.post("/v1/attendance/photo",
                    json={"section_id": world.a.section, "image_base64": tiny_png_b64},
                    headers=auth(world.a.teacher))
    assert r.status_code == 200, r.text
    body = r.json()
    assert face_mock.match == [world.a.tenant]
    present = {s["student_id"] for s in body["suggestions"] if s["matched"]}
    assert present == {world.a.student}
    assert {o["student_id"] for o in body["matched_outside_section"]} == {world.a.student2}
