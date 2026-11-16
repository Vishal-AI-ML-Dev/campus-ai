"""Analytics dashboards coverage: section/at-risk, placement, institute.

The analytics surface is read-only aggregation on top of existing data (no new
tables). These tests seed a small, deterministic dataset directly via the ORM
session, then hit the real endpoints through the API client and assert both the
numbers AND the role gates:

  * GET /analytics/section/{id}        -> class aggregate + risk-band counts
  * GET /analytics/section/{id}/at-risk -> per-student list (most-at-risk first)
  * GET /analytics/me                   -> a student's own self-assessment
  * GET /placement/analytics/overview   -> funnel + placement rate + companies
  * GET /institute/dashboard            -> whole-institute admin KPI snapshot

The risk model is transparent + weighted (attendance .40, academics .35,
submissions .25) with bands high>=60, medium>=35; the fixtures are chosen so one
student lands clearly "high" and another clearly "low".
"""

from datetime import date, datetime, timedelta, timezone

import app.models as models


# --- seeding helpers --------------------------------------------------------
def _seed_section(session, tenant):
    """Create a department + section (the public API cannot create these)."""
    dept = models.Department(tenant_id=tenant.id, name="CSE", code="CSE")
    session.add(dept)
    session.commit()
    session.refresh(dept)
    section = models.Section(
        tenant_id=tenant.id, name="A", year=3, department_id=dept.id
    )
    session.add(section)
    session.commit()
    session.refresh(section)
    return dept, section


def _attach(session, section, *students):
    for stu in students:
        stu.section_id = section.id
    session.commit()


def _mark(session, tenant, student, section, n_present, n_absent):
    """Seed attendance: n_present present days then n_absent absent days."""
    recs = []
    day = 0
    for _ in range(n_present):
        recs.append(
            models.AttendanceRecord(
                tenant_id=tenant.id,
                student_id=student.id,
                section_id=section.id,
                date=date(2026, 6, 1) + timedelta(days=day),
                status=models.AttendanceStatus.present,
            )
        )
        day += 1
    for _ in range(n_absent):
        recs.append(
            models.AttendanceRecord(
                tenant_id=tenant.id,
                student_id=student.id,
                section_id=section.id,
                date=date(2026, 6, 1) + timedelta(days=day),
                status=models.AttendanceStatus.absent,
            )
        )
        day += 1
    session.add_all(recs)
    session.commit()


def _result(session, tenant, subject, student, grade_point):
    session.add(
        models.Result(
            student_id=student.id,
            tenant_id=tenant.id,
            subject_id=subject.id,
            marks_obtained=grade_point * 10,
            max_marks=100.0,
            grade_point=grade_point,
        )
    )
    session.commit()


def _subject(session, tenant, dept, code="CS101"):
    subject = models.Subject(
        tenant_id=tenant.id,
        name="DBMS",
        code=code,
        credits=4,
        semester=5,
        department_id=dept.id,
    )
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return subject


def _assignment(session, tenant, section, teacher=None):
    a = models.Assignment(
        tenant_id=tenant.id,
        section_id=section.id,
        title="HW1",
        due_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
        max_marks=100.0,
        created_by_id=teacher.id if teacher else None,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _submit(session, tenant, assignment, student):
    session.add(
        models.Submission(
            tenant_id=tenant.id,
            assignment_id=assignment.id,
            student_id=student.id,
            status=models.SubmissionStatus.submitted,
        )
    )
    session.commit()


# --- section analytics + at-risk --------------------------------------------
def test_section_analytics_and_at_risk(
    client, session, make_user, make_tenant, token_header
):
    t = make_tenant(slug="anlx", name="Analytics U")
    make_user("anlx.teach@test.dev", role=models.UserRole.teacher, tenant=t)
    strong = make_user(
        "anlx.strong@test.dev", role=models.UserRole.student, tenant=t
    )
    weak = make_user(
        "anlx.weak@test.dev", role=models.UserRole.student, tenant=t
    )

    dept, section = _seed_section(session, t)
    _attach(session, section, strong, weak)
    teacher_row = session.query(models.User).filter_by(
        email="anlx.teach@test.dev"
    ).first()

    subject = _subject(session, t, dept)
    _mark(session, t, strong, section, 10, 0)  # 100% attendance
    _mark(session, t, weak, section, 2, 8)     # 20% attendance
    _result(session, t, subject, strong, 9.0)  # CGPA 9.0
    _result(session, t, subject, weak, 4.0)    # CGPA 4.0 (< 6 flag)
    a = _assignment(session, t, section, teacher_row)
    _submit(session, t, a, strong)             # strong submits, weak doesn't

    hdr = token_header("anlx.teach@test.dev")
    resp = client.get(f"/analytics/section/{section.id}", headers=hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["student_count"] == 2
    assert data["total_assignments"] == 1
    assert data["risk_high"] == 1
    assert data["risk_low"] == 1
    assert data["risk_medium"] == 0
    assert data["at_risk_count"] == 1

    at_risk = client.get(
        f"/analytics/section/{section.id}/at-risk", headers=hdr
    )
    assert at_risk.status_code == 200, at_risk.text
    rows = at_risk.json()
    assert len(rows) == 2
    # Most-at-risk first -> the weak student leads with a high band.
    assert rows[0]["student_id"] == weak.id
    assert rows[0]["band"] == "high"
    assert any("attendance" in r.lower() for r in rows[0]["reasons"])

    # ?band=high filter narrows to just the weak student.
    high_only = client.get(
        f"/analytics/section/{section.id}/at-risk?band=high", headers=hdr
    )
    assert high_only.status_code == 200
    assert {r["student_id"] for r in high_only.json()} == {weak.id}


def test_my_risk_self_assessment(
    client, session, make_user, make_tenant, token_header
):
    t = make_tenant(slug="myrisk", name="MyRisk U")
    student = make_user(
        "myrisk.stu@test.dev", role=models.UserRole.student, tenant=t
    )
    _, section = _seed_section(session, t)
    _attach(session, section, student)
    _mark(session, t, student, section, 3, 7)  # 30% attendance

    resp = client.get(
        "/analytics/me", headers=token_header("myrisk.stu@test.dev")
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["student_id"] == student.id
    assert data["attendance_pct"] == 30.0
    assert data["band"] in {"high", "medium", "low"}


def test_section_analytics_is_staff_only(
    client, session, make_user, make_tenant, token_header
):
    t = make_tenant(slug="anrbac", name="Analytics RBAC")
    make_user("anrbac.stu@test.dev", role=models.UserRole.student, tenant=t)
    _, section = _seed_section(session, t)
    resp = client.get(
        f"/analytics/section/{section.id}",
        headers=token_header("anrbac.stu@test.dev"),
    )
    assert resp.status_code == 403, resp.text


# --- placement analytics ----------------------------------------------------
def test_placement_overview(
    client, session, make_user, make_tenant, token_header
):
    t = make_tenant(slug="plc", name="Placement U")
    make_user("plc.tpo@test.dev", role=models.UserRole.tpo, tenant=t)
    s1 = make_user("plc.s1@test.dev", role=models.UserRole.student, tenant=t)
    s2 = make_user("plc.s2@test.dev", role=models.UserRole.student, tenant=t)
    s3 = make_user("plc.s3@test.dev", role=models.UserRole.student, tenant=t)

    d1 = models.Drive(
        tenant_id=t.id, company_name="Acme", role_title="Backend",
        package_lpa=12.0,
    )
    d2 = models.Drive(
        tenant_id=t.id, company_name="Globex", role_title="Data",
        package_lpa=20.0,
    )
    session.add_all([d1, d2])
    session.commit()
    session.refresh(d1)
    session.refresh(d2)

    session.add_all(
        [
            models.Application(
                tenant_id=t.id, drive_id=d1.id, student_id=s1.id,
                status=models.ApplicationStatus.selected,
            ),
            models.Application(
                tenant_id=t.id, drive_id=d1.id, student_id=s2.id,
                status=models.ApplicationStatus.rejected,
            ),
            models.Application(
                tenant_id=t.id, drive_id=d1.id, student_id=s3.id,
                status=models.ApplicationStatus.applied,
            ),
            models.Application(
                tenant_id=t.id, drive_id=d2.id, student_id=s1.id,
                status=models.ApplicationStatus.shortlisted,
            ),
        ]
    )
    session.commit()

    resp = client.get(
        "/placement/analytics/overview",
        headers=token_header("plc.tpo@test.dev"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_drives"] == 2
    assert data["total_applications"] == 4
    assert data["unique_applicants"] == 3
    assert data["placed_students"] == 1
    assert data["funnel"] == {
        "applied": 1, "shortlisted": 1, "selected": 1, "rejected": 1,
    }
    assert data["highest_package"] == 20.0
    assert data["highest_package_company"] == "Globex"
    assert data["placement_rate"] == 33.3  # 1 placed / 3 active students
    assert len(data["drives"]) == 2
    assert len(data["companies"]) == 2


def test_placement_overview_is_staff_only(
    client, make_user, make_tenant, token_header
):
    t = make_tenant(slug="plcr", name="Placement RBAC")
    make_user("plcr.stu@test.dev", role=models.UserRole.student, tenant=t)
    resp = client.get(
        "/placement/analytics/overview",
        headers=token_header("plcr.stu@test.dev"),
    )
    assert resp.status_code == 403, resp.text


# --- institute dashboard ----------------------------------------------------
def test_institute_dashboard(
    client, session, make_user, make_tenant, token_header
):
    t = make_tenant(slug="inst", name="Institute U")
    make_user("inst.admin@test.dev", role=models.UserRole.admin, tenant=t)
    make_user("inst.teach@test.dev", role=models.UserRole.teacher, tenant=t)
    s1 = make_user("inst.s1@test.dev", role=models.UserRole.student, tenant=t)
    s2 = make_user("inst.s2@test.dev", role=models.UserRole.student, tenant=t)

    _, section = _seed_section(session, t)
    _attach(session, section, s1, s2)

    d = models.Drive(
        tenant_id=t.id, company_name="Acme", role_title="Backend",
        package_lpa=15.0,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    session.add(
        models.Application(
            tenant_id=t.id, drive_id=d.id, student_id=s1.id,
            status=models.ApplicationStatus.selected,
        )
    )
    session.commit()

    resp = client.get(
        "/institute/dashboard", headers=token_header("inst.admin@test.dev")
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "generated_at" in data
    assert data["users"]["students"] == 2
    assert data["users"]["teachers"] == 1
    assert data["users"]["admins"] == 1
    assert data["structure"]["departments"] == 1
    assert data["structure"]["sections"] == 1
    assert data["placement"]["total_drives"] == 1
    assert data["placement"]["placed_students"] == 1
    assert data["risk"]["assessed_students"] == 2


def test_institute_dashboard_is_admin_only(
    client, make_user, make_tenant, token_header
):
    t = make_tenant(slug="instr", name="Institute RBAC")
    make_user("instr.tpo@test.dev", role=models.UserRole.tpo, tenant=t)
    resp = client.get(
        "/institute/dashboard", headers=token_header("instr.tpo@test.dev")
    )
    assert resp.status_code == 403, resp.text
