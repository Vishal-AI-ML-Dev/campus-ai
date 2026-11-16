"""Phase 5S - public feedback visibility + resume-file ownership.

MOCKED: file storage (storage.* replaced; fake URLs, no Supabase call) and the
resume-builder AI call (app.routers.resume.generate_resume replaced). Signed
URLs are only compared, never printed. Real storage / real AI: NOT TESTED.
Requires migration 5e1f0a7b9c3d (resumes.file_path) on the test cluster.
"""

from __future__ import annotations

import secrets

import pytest

from flowkit import EMAIL_DOMAIN, _d
from test_policies_5r import (  # noqa: F401  (fixtures re-used by name)
    ALIASES,
    _dl,
    _recruiter,
    _upload,
    mock_ai,
    mock_storage,
)

FB_MSG = "Synthetic feedback 5S"


# ==========================================================================
# Public feedback
# ==========================================================================
def test_feedback_public_submit_unclaimed_hidden_claimed_scoped(campus, sql):
    a, b = campus.a, campus.b
    anon = campus.new_client()
    try:
        ids = []
        for prefix in ALIASES:  # public submission works on both paths, no login
            r = anon.post(f"{prefix}/feedback", json={"message": FB_MSG, "rating": 4})
            assert r.status_code == 201 and r.json().get("tenant_id") in (None,), _d(r)
            ids.append(r.json()["id"])
        for who in (a.teacher, a.tpo, a.student, anon):
            assert who.get("/v1/feedback").status_code in (401, 403)
        # Unclaimed feedback: hidden from every institute admin.
        for adm in (a.admin, b.admin):
            seen = {x["id"] for x in adm.get("/v1/feedback?limit=200").json()}
            assert not (seen & set(ids))
            # No route can claim or change feedback.
            for verb, url in (("patch", f"/v1/feedback/{ids[0]}/claim"), ("patch", f"/v1/feedback/{ids[0]}"),
                              ("delete", f"/v1/feedback/{ids[0]}")):
                assert getattr(adm, verb)(url).status_code in (404, 405), url
        # Not deleted, still unclaimed.
        rows = sql("SELECT count(*) AS n FROM feedback WHERE id = ANY(:i) AND tenant_id IS NULL", {"i": ids})
        assert rows[0]["n"] == 2
        # Feedback ALREADY claimed into A (set up directly; no API claims):
        sql("UPDATE feedback SET tenant_id = :t WHERE id = :i", {"t": a.tenant, "i": ids[0]})
        assert ids[0] in {x["id"] for x in a.admin.get("/v1/feedback?limit=200").json()}
        assert ids[0] not in {x["id"] for x in b.admin.get("/v1/feedback?limit=200").json()}
        assert ids[1] not in {x["id"] for x in a.admin.get("/v1/feedback?limit=200").json()}
    finally:
        sql("DELETE FROM feedback WHERE message = :m", {"m": FB_MSG})  # synthetic rows only


# ==========================================================================
# Resume files
# ==========================================================================
@pytest.fixture
def mock_resume_ai(monkeypatch):
    """MOCKED resume builder (no AI provider/worker call)."""
    import app.routers.resume as resume_router

    monkeypatch.setattr(resume_router, "generate_resume",
                        lambda profile: {"markdown": "# Synthetic resume", "provider": "mock"})


def _version(t, student, title):
    sk = student.post("/v1/skills", json={"name": f"Skill {secrets.token_hex(3)}"})
    assert sk.status_code == 201, _d(sk)
    assert t.teacher.patch(f"/v1/skills/{sk.json()['id']}/decision",
                           json={"status": "verified"}).status_code == 200
    g = student.post("/v1/resume/generate", json={"title": title})
    assert g.status_code == 200 and g.json()["version_id"], _d(g)
    return g.json()["version_id"]


def test_resume_file_owner_only(campus, mock_storage, mock_ai, mock_resume_ai):
    a, b = campus.a, campus.b
    vid = _version(a, a.student, "Main")
    path = _upload(a.student, "resume", "cv.pdf")
    assert _dl(a.student, path) == 403                       # unattached: denied
    att = a.student.put(f"/v1/resume/versions/{vid}/file", json={"path": path})
    assert att.status_code == 200 and att.json()["file_path"] == path, _d(att)
    # Existing workflow intact: read / list / rename.
    assert a.student.get(f"/v1/resume/versions/{vid}").json()["file_path"] == path
    listed = {v["id"]: v for v in a.student.get("/v1/resume/versions").json()}
    assert listed[vid]["file_path"] == path                 # summary shows the attachment
    assert vid not in {v["id"] for v in a.student2.get("/v1/resume/versions").json()}
    assert a.student.patch(f"/v1/resume/versions/{vid}", json={"title": "Renamed"}).status_code == 200

    rec = _recruiter(campus, a, "res")
    for prefix in ALIASES:
        got = {"owner": _dl(a.student, path, prefix), "teacher": _dl(a.teacher, path, prefix),
               "tpo": _dl(a.tpo, path, prefix), "admin": _dl(a.admin, path, prefix),
               "another student": _dl(a.student2, path, prefix), "recruiter": _dl(rec, path, prefix),
               "other institute admin": _dl(b.admin, path, prefix),
               "other institute student": _dl(b.student, path, prefix),
               "hinted": _dl(a.student2, path, prefix, owner_id=a.student.id, tenant_id=a.tenant)}
        assert got == {"owner": 200, "teacher": 403, "tpo": 403, "admin": 403, "another student": 403,
                       "recruiter": 403, "other institute admin": 403,
                       "other institute student": 403, "hinted": 403}, (prefix, got)
    assert mock_storage == [path, path]  # owner x 2 aliases only

    # Detach: the file is no longer signable by anyone (version kept).
    det = a.student.delete(f"/v1/resume/versions/{vid}/file")
    assert det.status_code == 200 and det.json()["file_path"] is None
    assert _dl(a.student, path) == 403
    assert a.student.get(f"/v1/resume/versions/{vid}").status_code == 200


def test_resume_file_attach_rules(campus, mock_storage, mock_ai, mock_resume_ai, sql):
    a, b = campus.a, campus.b
    v1 = _version(a, a.student, "One")
    v2 = _version(a, a.student, "Two")
    s2v = _version(a, a.student2, "S2")
    bv = _version(b, b.student, "B")
    mine = _upload(a.student, "resume", "cv.pdf")
    proof = _upload(a.student, "proof", "x.pdf")
    other_students = _upload(a.student2, "resume", "cv2.pdf")
    b_path = _upload(b.student, "resume", "cvb.pdf")

    put = lambda who, v, p: who.put(f"/v1/resume/versions/{v}/file", json={"path": p}).status_code  # noqa: E731
    assert put(a.student2, s2v, mine) == 403            # someone else's upload
    assert put(a.student2, v1, other_students) == 404    # someone else's version
    assert put(a.student, v1, proof) == 403              # wrong kind
    assert put(a.student, v1, b_path) == 403             # other institute's path
    assert put(b.student, bv, mine) == 403               # cross-tenant attach
    assert put(a.student, v1, f"{a.tenant}/resume/{a.student.id}/../x.pdf") == 403
    for staff in (a.teacher, a.tpo, a.admin):
        assert put(staff, v1, mine) == 403                # students only
    assert put(b.admin, v1, mine) == 403
    assert put(a.student, v1, mine) == 200
    assert put(a.student, v2, mine) == 409               # one version per file
    assert put(b.student, v1, b_path) in (403, 404)       # B can't touch A's version
    r = sql("SELECT student_id, file_path FROM resumes WHERE id = ANY(:i) ORDER BY id",
            {"i": [v1, v2, s2v]}, tenant=a.tenant)
    assert [(row["student_id"], row["file_path"]) for row in r] == [(a.student.id, mine), (a.student.id, None),
                                               (a.student2.id, None)]
    assert _dl(a.student2, other_students) == 403         # their own, but unattached
    assert mock_storage == []


def test_resume_upload_url_students_only(campus, mock_storage):
    """Phase 5T: only students may request a `resume` upload URL; other
    kinds keep their existing behaviour for staff."""
    a, b = campus.a, campus.b
    rec = _recruiter(campus, a, "resup")
    for prefix in ALIASES:
        for who in (a.teacher, a.tpo, a.admin, rec, b.admin):
            r = who.post(f"{prefix}/files/sign-upload", json={"filename": "cv.pdf", "kind": "resume"})
            assert r.status_code == 403, (prefix, who.role, _d(r))
            assert "upload_url" not in r.text
        ok = a.student.post(f"{prefix}/files/sign-upload", json={"filename": "cv.pdf", "kind": "RESUME"})
        assert ok.status_code == 200 and ok.json()["path"].startswith(f"{a.tenant}/resume/{a.student.id}/"), _d(ok)
    assert a.teacher.post("/v1/files/sign-upload", json={"filename": "n.pdf", "kind": "material"}).status_code == 200
    assert a.student.post("/v1/files/sign-upload", json={"filename": "cv.docx", "kind": "resume"}).status_code == 415
