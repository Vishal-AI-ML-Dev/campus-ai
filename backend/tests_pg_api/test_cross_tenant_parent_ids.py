"""Phase 5A - cross-tenant parent-ID operations through the real API.

Raw-SQL testing showed Postgres ACCEPTS a tenant-A row whose FK points at a
tenant-B parent (FKs are not tenant-aware). So the API itself must refuse such
IDs. Each case: a caller from institute A passes institute B's parent id ->
must be 404 (B's existence not confirmed) and NO row referencing B's parent may
be created; plus an own-tenant control with the same payload shape that must
succeed (so a 404 is not just a broken request).

Several of these routes do the parent lookup with plain ``db.get()`` and NO
app-layer tenant comparison, i.e. they rely solely on RLS - which the SQLite
suite cannot exercise. Those are marked "RLS-only" below.
"""

from datetime import date


def test_assignment_create_rejects_other_tenant_section(client, auth, world, count_both):
    """RLS-only guard (assignments.create_assignment)."""
    body = {"title": "zz", "due_date": "2030-01-01T00:00:00Z"}
    before = count_both("assignments", "section_id = :s", {"s": world.b.section})
    r = client.post("/v1/assignments", json={**body, "section_id": world.b.section},
                    headers=auth(world.a.teacher))
    assert r.status_code == 404, r.text
    assert count_both("assignments", "section_id = :s", {"s": world.b.section}) == before
    ok = client.post("/v1/assignments", json={**body, "section_id": world.a.section},
                     headers=auth(world.a.teacher))
    assert ok.status_code == 201, ok.text


def test_section_create_rejects_other_tenant_department(client, auth, world, count_both):
    """App-layer + RLS guard (admin.create_section)."""
    before = count_both("sections", "department_id = :d", {"d": world.b.dept})
    r = client.post(f"/v1/admin/departments/{world.b.dept}/sections",
                    json={"name": "ZX"}, headers=auth(world.a.admin))
    assert r.status_code == 404, r.text
    assert count_both("sections", "department_id = :d", {"d": world.b.dept}) == before
    ok = client.post(f"/v1/admin/departments/{world.a.dept}/sections",
                     json={"name": "ZX"}, headers=auth(world.a.admin))
    assert ok.status_code == 201, ok.text


def test_list_sections_of_other_tenant_department_is_404(client, auth, world):
    r = client.get(f"/v1/admin/departments/{world.b.dept}/sections",
                   headers=auth(world.a.teacher))
    assert r.status_code == 404, r.text


def test_user_section_assign_rejects_other_tenant_section(client, auth, world, sql):
    """RLS-only guard (admin.update_user_section). ``users`` has no RLS, so a
    successful write here would persist a cross-tenant users.section_id."""
    r = client.patch(f"/v1/admin/users/{world.a.student2}/section",
                     json={"section_id": world.b.section}, headers=auth(world.a.admin))
    assert r.status_code == 404, r.text
    row = sql("SELECT section_id FROM users WHERE id = :u", {"u": world.a.student2})
    assert row == [{"section_id": None}]
    ok = client.patch(f"/v1/admin/users/{world.a.student2}/section",
                      json={"section_id": world.a.section}, headers=auth(world.a.admin))
    assert ok.status_code == 200, ok.text


def test_attendance_mark_rejects_other_tenant_section(client, auth, world, count_both):
    """RLS-only guard (attendance.mark_attendance) on section_id."""
    payload = {"date": str(date(2030, 1, 2)),
               "records": [{"student_id": world.a.student, "status": "present"}]}
    before = count_both("attendance_records", "section_id = :s", {"s": world.b.section})
    r = client.post("/v1/attendance/mark", json={**payload, "section_id": world.b.section},
                    headers=auth(world.a.teacher))
    assert r.status_code == 404, r.text
    assert count_both("attendance_records", "section_id = :s",
                      {"s": world.b.section}) == before
    ok = client.post("/v1/attendance/mark", json={**payload, "section_id": world.a.section},
                     headers=auth(world.a.teacher))
    assert ok.status_code == 200, ok.text


def test_attendance_mark_rejects_other_tenant_student(client, auth, world, count_both):
    """student_id is a users FK and ``users`` has no RLS: the route does not
    validate the student's tenant or section membership. Expected secure
    behaviour: reject (4xx) and create no attendance row for B's student."""
    payload = {"section_id": world.a.section, "date": str(date(2030, 1, 3)),
               "records": [{"student_id": world.b.student, "status": "absent"}]}
    r = client.post("/v1/attendance/mark", json=payload, headers=auth(world.a.teacher))
    created = count_both("attendance_records", "student_id = :s", {"s": world.b.student})
    assert r.status_code in (400, 403, 404, 422) and created == 0, (
        f"tenant-A teacher marked attendance for tenant-B student: "
        f"status={r.status_code}, rows referencing B's student={created}"
    )


def test_drive_apply_rejects_other_tenant_drive(client, auth, world, count_both):
    before = count_both("applications", "drive_id = :d", {"d": world.b.drive})
    r = client.post(f"/v1/drives/{world.b.drive}/apply", headers=auth(world.a.student))
    assert r.status_code == 404, r.text
    assert count_both("applications", "drive_id = :d", {"d": world.b.drive}) == before
    ok = client.post(f"/v1/drives/{world.a.drive}/apply", headers=auth(world.a.student))
    assert ok.status_code == 201, ok.text


def test_doubt_answer_rejects_other_tenant_doubt(client, auth, world, count_both):
    before = count_both("doubt_answers", "doubt_id = :d", {"d": world.b.doubt})
    r = client.post(f"/v1/doubts/{world.b.doubt}/answers", json={"body": "zz"},
                    headers=auth(world.a.student))
    assert r.status_code == 404, r.text
    assert count_both("doubt_answers", "doubt_id = :d", {"d": world.b.doubt}) == before
    ok = client.post(f"/v1/doubts/{world.a.doubt}/answers", json={"body": "zz"},
                     headers=auth(world.a.student))
    assert ok.status_code == 201, ok.text


def test_submission_rejects_other_tenant_assignment(client, auth, world, count_both):
    before = count_both("submissions", "assignment_id = :a", {"a": world.b.assignment})
    r = client.post(f"/v1/assignments/{world.b.assignment}/submit",
                    json={"content": "zz"}, headers=auth(world.a.student))
    assert r.status_code == 404, r.text
    assert count_both("submissions", "assignment_id = :a",
                      {"a": world.b.assignment}) == before
    ok = client.post(f"/v1/assignments/{world.a.assignment}/submit",
                     json={"content": "zz"}, headers=auth(world.a.student))
    assert ok.status_code == 200, ok.text


def test_contact_reveal_rejects_other_tenant_application(client, auth, world, sql):
    """RLS-only guard (recruiters.set_contact_visibility)."""
    r = client.patch(f"/v1/recruiters/applications/{world.b.application}/contact",
                     json={"revealed": True}, headers=auth(world.a.tpo))
    assert r.status_code == 404, r.text
    row = sql("SELECT contact_revealed FROM applications WHERE id = :i",
              {"i": world.b.application}, tenant=world.b.tenant)
    assert row == [{"contact_revealed": False}]
    ok = client.patch(f"/v1/recruiters/applications/{world.a.application}/contact",
                      json={"revealed": True}, headers=auth(world.a.tpo))
    assert ok.status_code == 200, ok.text


# --- Phase 5B regression tests (attendance student scoping) -----------------
def test_attendance_mark_mixed_batch_rejected_atomically(client, auth, world, count_both):
    """One foreign id in an otherwise valid batch rejects the WHOLE batch:
    no row is written for the own-tenant student either."""
    payload = {"section_id": world.a.section, "date": str(date(2030, 1, 4)),
               "records": [{"student_id": world.a.student, "status": "present"},
                           {"student_id": world.b.student, "status": "present"}]}
    r = client.post("/v1/attendance/mark", json=payload, headers=auth(world.a.teacher))
    assert r.status_code == 400, r.text
    assert count_both("attendance_records", "date = :d", {"d": date(2030, 1, 4)}) == 0


def test_attendance_mark_rejects_non_student_and_unknown_ids(client, auth, world, count_both):
    for sid in (world.a.teacher, 2_000_000_000):
        payload = {"section_id": world.a.section, "date": str(date(2030, 1, 5)),
                   "records": [{"student_id": sid, "status": "present"}]}
        r = client.post("/v1/attendance/mark", json=payload, headers=auth(world.a.teacher))
        assert r.status_code == 400, (sid, r.text)
    assert count_both("attendance_records", "date = :d", {"d": date(2030, 1, 5)}) == 0


def test_attendance_mark_own_students_succeeds_and_is_tenant_stamped(
    client, auth, world, sql
):
    """Own-tenant control for the student-scoping fix (two own students)."""
    payload = {"section_id": world.a.section, "date": str(date(2030, 1, 6)),
               "records": [{"student_id": world.a.student, "status": "present"},
                           {"student_id": world.a.student2, "status": "late"}]}
    r = client.post("/v1/attendance/mark", json=payload, headers=auth(world.a.teacher))
    assert r.status_code == 200, r.text
    rows = sql("SELECT student_id, tenant_id FROM attendance_records WHERE date = :d "
               "ORDER BY student_id", {"d": date(2030, 1, 6)}, tenant=world.a.tenant)
    assert rows == sorted([{"student_id": world.a.student, "tenant_id": world.a.tenant},
                           {"student_id": world.a.student2, "tenant_id": world.a.tenant}],
                          key=lambda x: x["student_id"])
