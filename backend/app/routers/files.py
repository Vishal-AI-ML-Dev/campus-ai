"""File upload/download signing routes (Section 7, Part B).

These endpoints never touch the file bytes. The browser talks to Supabase
Storage directly using the short-lived signed URLs minted here; the backend
just does auth + validation + tenant-scoped path construction.

  POST /files/sign-upload    -> {path, upload_url, token}
  POST /files/sign-download  -> {download_url, expires_in}

When Supabase is not configured (storage_enabled() is False) both routes return
503 so the feature can ship dark and be switched on later via env vars.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import storage
from app.core.db import get_db
from app.models import (
    ExtraCurricular,
    Internship,
    LeaveRequest,
    Material,
    Resume,
    Skill,
    Submission,
    User,
    UserRole,
)
from app.core.security import get_current_user

router = APIRouter(prefix="/files", tags=["files"])

# Allowed file kinds -> permitted extensions. The `kind` becomes part of the
# storage path and gates what a caller may upload. Keep this conservative;
# widen deliberately as new upload surfaces are added.
ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "certificate": {".pdf", ".jpg", ".jpeg", ".png", ".webp"},
    "proof": {".pdf", ".jpg", ".jpeg", ".png", ".webp"},
    "resume": {".pdf"},
    "material": {
        ".pdf", ".ppt", ".pptx", ".doc", ".docx",
        ".txt", ".csv", ".zip", ".png", ".jpg", ".jpeg",
    },
}


class SignUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)


class SignUploadOut(BaseModel):
    path: str
    upload_url: str
    token: str


class SignDownloadIn(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    expires_in: int | None = Field(default=None, ge=1, le=86_400)


class SignDownloadOut(BaseModel):
    download_url: str
    expires_in: int


def _require_storage() -> None:
    if not storage.storage_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured on this server.",
        )


@router.post("/sign-upload", response_model=SignUploadOut)
def sign_upload(
    payload: SignUploadIn,
    current_user: User = Depends(get_current_user),
) -> SignUploadOut:
    """Mint a one-time signed URL the caller can PUT a single file to.

    The returned ``path`` is tenant-scoped; the client stores it in the
    entity's existing ``*_url`` column and later calls /sign-download to view.
    """
    _require_storage()

    kind = payload.kind.strip().lower()
    allowed = ALLOWED_EXTENSIONS.get(kind)
    if allowed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported upload kind '{payload.kind}'.",
        )
    # Phase 5T: a resume file is a student's own document (attachable only to
    # their own resume version, downloadable only by them).
    if kind == "resume" and current_user.role != UserRole.student:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only students can upload resume files.",
        )

    ext =os.path.splitext(payload.filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"File type '{ext or 'unknown'}' is not allowed for {kind}. "
                f"Allowed: {', '.join(sorted(allowed))}."
            ),
        )

    path = storage.build_object_path(
        tenant_id=current_user.tenant_id,
        kind=kind,
        user_id=current_user.id,
        filename=payload.filename,
    )
    try:
        signed = storage.create_signed_upload_url(path)
    except storage.StorageError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not create an upload URL. Please try again.",
        )
    return SignUploadOut(**signed)


# --- Download authorization (Phase 5R) --------------------------------------
# Server-generated object path: {tenant}/{kind}/{uploader_id}/{32-hex}-{name}
_PATH_RE = re.compile(
    r"^(?P<tenant>\d+)/(?P<kind>[a-z]+)/(?P<uploader>\d+)/[0-9a-f]{32}-[^/]+$"
)
_STAFF = (UserRole.teacher, UserRole.admin, UserRole.tpo)
_REVIEWERS = (UserRole.teacher, UserRole.tpo)


def _authorized_by_record(db: Session, user: User, path: str, uploader: int) -> bool:
    """True only if a record IN THE CALLER'S INSTITUTE stores exactly `path`,
    that record's owner is the uploader encoded in the server-generated path
    (so a client cannot attach someone else's file to its own record), and the
    caller passes THAT record's existing read rule. Anything else: deny.

    Read rules mirror each resource's existing API:
      skills / extracurriculars / internships evidence: owner + teacher/TPO
      leave proof: the student + staff (teacher/admin/TPO)
      assignment submissions: the student + teacher/admin
      study materials (shared): staff + students of the material's section
      resume files (Phase 5S): the resume's owner ONLY - mirrors the resume
        API, where versions are readable by their owner alone
    """
    tid, uid, role = user.tenant_id, user.id, user.role

    for model, col, owner_col, readers in (
        (Skill, Skill.evidence_url, "student_id", _REVIEWERS),
        (ExtraCurricular, ExtraCurricular.evidence_url, "student_id", _REVIEWERS),
        (Internship, Internship.certificate_url, "student_id", _REVIEWERS),
    ):
        for rec in db.scalars(select(model).where(model.tenant_id == tid, col == path)):
            if getattr(rec, owner_col) == uploader and (
                rec.student_id == uid or role in readers
            ):
                return True

    for rec in db.scalars(select(LeaveRequest).where(
        LeaveRequest.tenant_id == tid, LeaveRequest.proof_url == path
    )):
        if uploader in (rec.student_id, rec.applied_by_id) and (
            rec.student_id == uid or role in _STAFF
        ):
            return True

    for rec in db.scalars(select(Submission).where(
        Submission.tenant_id == tid, Submission.link == path
    )):
        if rec.student_id == uploader and (
            rec.student_id == uid or role in (UserRole.teacher, UserRole.admin)
        ):
            return True

    for rec in db.scalars(select(Material).where(
        Material.tenant_id == tid, Material.link == path
    )):
        if rec.uploaded_by_id == uploader and (
            role in _STAFF
            or (role == UserRole.student and user.section_id == rec.section_id)
        ):
            return True

    for rec in db.scalars(select(Resume).where(
        Resume.tenant_id == tid, Resume.file_path == path
    )):
        if rec.student_id == uploader and rec.student_id == uid:
            return True

    return False  # unknown / unattached / unresolvable ownership


@router.post("/sign-download", response_model=SignDownloadOut)
def sign_download(
    payload: SignDownloadIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignDownloadOut:
    """Return a short-lived URL to view/download a stored file.

    Phase 5R: the institute prefix alone no longer authorizes anything. The
    path must be a well-formed server-generated path in the caller's
    institute AND be referenced by a record the caller may read (see
    _authorized_by_record). Unknown, unattached or malformed paths are denied
    (403, uniform so the response does not reveal whether a file exists).
    """
    _require_storage()

    path = payload.path.lstrip("/")
    m = _PATH_RE.match(path)
    if (
        m is None
        or ".." in path
        or int(m.group("tenant")) != current_user.tenant_id
        or m.group("kind") not in ALLOWED_EXTENSIONS
        or not _authorized_by_record(db, current_user, path, int(m.group("uploader")))
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this file.",
        )

    expires_in = payload.expires_in or storage.DEFAULT_DOWNLOAD_EXPIRY
    try:
        url = storage.create_signed_download_url(path, expires_in)
    except storage.StorageError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not create a download URL. Please try again.",
        )
    return SignDownloadOut(download_url=url, expires_in=expires_in)
