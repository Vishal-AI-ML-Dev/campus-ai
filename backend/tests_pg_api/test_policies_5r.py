"""Phase 5R - approved policies: file-download authorization, calendar and
recruiters, student-only skill/ECA claims. (Leads: test_remaining_features.)

File storage is MOCKED (storage.* replaced; fake URLs, never a real Supabase
call). Signed URLs are only compared, never printed. Real storage: NOT TESTED.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from flowkit import EMAIL_DOMAIN, MARK, _d

ALIASES = ("/v1", "")
FAKE_DL = "https://storage.invalid/d"


@pytest.fixture
def mock_storage(monkeypatch):
    """MOCKED storage: records which paths reached the signer."""
    import storage

    calls: list[str] = []
    monkeypatch.setattr(storage, "storage_enabled", lambda: True)
    monkeypatch.setattr(storage, "create_signed_upload_url", lambda path: (
        {"path": path, "upload_url": "https://storage.invalid/u", "token": "mock"}))
    monkeypatch.setattr(storage, "create_signed_download_url", lambda path, exp=None: (
        calls.append(path) or FAKE_DL))
    return calls


@pytest.fixture
def mock_ai(monkeypatch):
    import ai_client

    class _Resp:
        def json(self):
            return {"score": 77}

    monkeypatch.setattr(ai_client, "_worker_request", lambda *a, **k: _Resp())


def _recruiter(campus, t, label):
    email, pw = f"rec-{label}-{campus.run}@{EMAIL_DOMAIN}", secrets.token_urlsafe(14)
    inv = t.tpo.post("/v1/recruiters/invite", json={
        "company_name": f"{MARK}-{campus.run} {label} Co", "email": email})
    assert inv.status_code == 201, _d(inv)
    acc = campus.new_client().post("/v1/recruiters/accept-invite", json={
        "token": inv.json()["token"], "full_name": "Rec", "password": pw})
    assert acc.status_code == 200, _d(acc)
    return campus.login(email, pw, f"recruiter-{label}")


def _upload(actor, kind, name="file.pdf"):
    r = actor.post("/v1/files/sign-upload", json={"filename": name, "kind": kind})
    assert r.status_code == 200, _d(r)
    return r.json()["path"]


def _dl(actor, path, prefix="/v1", **extra):
    r = actor.post(f"{prefix}/files/sign-download", json={"path": path, **extra})
    ok = r.status_code == 200 and r.json().get("download_url") == FAKE_DL
    return 200 if ok else r.status_code


# ==========================================================================
# Files
# ==========================================================================
def test_files_private_skill_evidence(campus, mock_storage, mock_ai):
    a, b = campus.a, campus.b
    path = _upload(a.student, "proof")
    sk = a.student.post("/v1/skills", json={"name": "Docker", "evidence_url": path})
    assert sk.status_code == 201, _d(sk)
    rec = _recruiter(campus, a, "files")
    expect = {"owner": (a.student, 200), "teacher": (a.teacher, 200), "tpo": (a.tpo, 200),
              "admin (no skill read rule)": (a.admin, 403), "another student": (a.student2, 403),
              "recruiter": (rec, 403), "other institute teacher": (b.teacher, 403),
              "other institute admin": (b.admin, 403)}
    for prefix in ALIASES:
        got = {k: _dl(who, path, prefix) for k, (who, _) in expect.items()}
        assert got == {k: code for k, (_, code) in expect.items()}, (prefix, got)
    assert mock_storage == [path] * 6  # only the 3 authorized readers x 2 aliases


def test_files_cannot_hijack_another_students_file(campus, mock_storage, mock_ai):
    """Student 2 references student 1's stored path in their OWN record; that
    must not grant student 2 access (record owner != uploader in the path)."""
    a = campus.a
    path = _upload(a.student, "proof")
    assert a.student.post("/v1/skills", json={"name": "Owner skill", "evidence_url": path}).status_code == 201
    assert a.student2.post("/v1/skills", json={"name": "Hijack", "evidence_url": path}).status_code == 201
    assert _dl(a.student2, path) == 403
    assert _dl(a.student2, path, owner_id=a.student.id, tenant_id=a.tenant) == 403  # client hints ignored
    assert _dl(a.student, path) == 200


def test_files_shared_study_material(campus, mock_storage):
    a, b = campus.a, campus.b
    path = _upload(a.teacher, "material", "notes.pdf")
    m = a.teacher.post("/v1/materials", json={"section_id": a.section, "title": "Notes", "link": path})
    assert m.status_code == 201, _d(m)
    sec2 = a.admin.post(f"/v1/admin/departments/{a.dept}/sections", json={"name": "S2"}).json()["id"]
    assert a.admin.patch(f"/v1/admin/users/{a.student2.id}/section",
                         json={"section_id": sec2}).status_code == 200
    rec = _recruiter(campus, a, "mat")
    got = {"section student": _dl(a.student, path), "other-section student": _dl(a.student2, path),
           "uploader teacher": _dl(a.teacher, path), "tpo": _dl(a.tpo, path), "admin": _dl(a.admin, path),
           "recruiter": _dl(rec, path), "other institute student": _dl(b.student, path),
           "other institute admin": _dl(b.admin, path)}
    assert got == {"section student": 200, "other-section student": 403, "uploader teacher": 200,
                   "tpo": 200, "admin": 200, "recruiter": 403, "other institute student": 403,
                   "other institute admin": 403}, got


def test_files_leave_proof_and_internship_certificate(campus, mock_storage):
    a = campus.a
    proof = _upload(a.student, "proof", "medical.pdf")
    lv = a.student.post("/v1/leave", json={"request_type": "leave", "category": "medical", "title": "Sick",
                                           "start_date": "2031-04-01", "end_date": "2031-04-01",
                                           "proof_url": proof})
    assert lv.status_code == 201, _d(lv)
    cert = _upload(a.student, "certificate", "cert.pdf")
    it = a.student.post("/v1/internships", json={"organization": "Org", "role_title": "Intern",
                                                 "internship_type": "internship", "certificate_url": cert})
    assert it.status_code == 201, _d(it)
    got = {"leave owner": _dl(a.student, proof), "leave teacher": _dl(a.teacher, proof),
           "leave admin": _dl(a.admin, proof), "leave other student": _dl(a.student2, proof),
           "cert owner": _dl(a.student, cert), "cert tpo": _dl(a.tpo, cert),
           "cert admin": _dl(a.admin, cert), "cert other student": _dl(a.student2, cert)}
    assert got == {"leave owner": 200, "leave teacher": 200, "leave admin": 200,
                   "leave other student": 403, "cert owner": 200, "cert tpo": 200,
                   "cert admin": 403, "cert other student": 403}, got


def test_files_unknown_unattached_malformed_and_resume_denied(campus, mock_storage):
    a = campus.a
    unattached = _upload(a.student, "proof")                         # never referenced
    resume = _upload(a.student, "resume", "cv.pdf")                  # never attached to a version
    random_ok_format = f"{a.tenant}/proof/{a.student.id}/{secrets.token_hex(16)}-x.pdf"
    for p in (unattached, resume, random_ok_format, f"{a.tenant}/proof/x.pdf",
              f"{a.tenant}/weird/{a.student.id}/{'0' * 32}-x.pdf", "", "../etc/passwd"):
        code = _dl(a.student, p) if p else a.student.post("/v1/files/sign-download", json={"path": p}).status_code
        assert code in (403, 422), (p[:40], code)
    assert mock_storage == []  # nothing unauthorized reached storage


# ==========================================================================
# Calendar: recruiters excluded, internal roles preserved
# ==========================================================================
def test_calendar_recruiter_excluded_on_both_aliases(campus):
    a = campus.a
    rec = _recruiter(campus, a, "cal5r")
    day = str(date.today() + timedelta(days=7))
    ids = [a.admin.post("/v1/calendar", json={"title": f"E {aud}", "event_date": day,
                                              "audience": aud}).json()["id"]
           for aud in ("all", "student", "teacher", "tpo")]
    for prefix in ALIASES:
        r = rec.get(f"{prefix}/calendar")
        assert r.status_code == 200 and r.json() == [], prefix
    vis = lambda who: {x["id"] for x in who.get("/v1/calendar").json()} & set(ids)  # noqa: E731
    assert vis(a.student) == {ids[0], ids[1]}
    assert vis(a.teacher) == {ids[0], ids[2]}
    assert vis(a.tpo) == {ids[0], ids[3]}
    assert vis(a.admin) == set(ids)
    assert rec.post("/v1/calendar", json={"title": "x", "event_date": day}).status_code == 403


# ==========================================================================
# Skills + ECA: only students create claims; staff review preserved
# ==========================================================================
def test_claims_only_students_create(campus, mock_ai, count_rows):
    a = campus.a
    rec = _recruiter(campus, a, "claims")
    others = [a.teacher, a.tpo, a.admin, rec]
    for prefix in ALIASES:
        for who in others:
            s = who.post(f"{prefix}/skills", json={"name": f"x-{who.role}"})
            e = who.post(f"{prefix}/eca", json={"title": f"x-{who.role}", "category": "sports"})
            assert s.status_code == 403 and e.status_code == 403, (prefix, who.role, _d(s), _d(e))
    ids = [w.id for w in others]
    assert count_rows(a.tenant, "skills", "student_id = ANY(:ids)", {"ids": ids}) == 0
    assert count_rows(a.tenant, "extracurriculars", "student_id = ANY(:ids)", {"ids": ids}) == 0
    sk = a.student.post("/skills", json={"name": "Legacy alias skill"})       # student, legacy path
    ec = a.student.post("/v1/eca", json={"title": "Chess", "category": "other"})
    assert sk.status_code == 201 and ec.status_code == 201
    assert a.teacher.patch(f"/v1/skills/{sk.json()['id']}/decision", json={"status": "verified"}).status_code == 200
    assert a.tpo.patch(f"/v1/eca/{ec.json()['id']}/decision", json={"status": "flagged"}).status_code == 200
