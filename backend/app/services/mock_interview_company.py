"""Company profiles and company context (Phase 8.4; decisions P8-D9..D11).

* **Ownership (P8-D9).** Profiles belong to an institute. Admins and TPOs of
  that institute create, edit, publish and archive them (either may publish),
  also "from drive" for an OPEN drive of their own institute. Every write adds
  one audit row with a CONTENT-FREE summary (ids, status, counts). Profiles are
  archived, never deleted. Teachers and recruiters have no access (403);
  another institute's profile or drive is 404. Students see published
  profiles of their own institute only (drafts/archived -> 404).
* **Sources (P8-D10).** Publishing needs >= 1 source record. Allowed kinds:
  ``drive`` (an open drive of the same institute), ``jd``, ``institute_note``
  and ``public_url``. A URL is a citation only: never fetched, never scraped,
  shown to students as label + domain in plain text. Recruiter-written text
  (``recruiters.about``) is never used. Institute text is labelled "From your
  institute".
* **Context (P8-D11).** A session created with a profile and/or role template
  keeps a FROZEN copy in ``plan_json["company"]`` (later edits never change
  it). It is sent to the AI worker only for question generation, as delimited
  untrusted data; the evaluation payload and the hr-v1 / technical-v1 rubrics
  are unchanged, and there is no company-fit or competency score.

Privacy: JD and profile text never appear in logs, metrics, the usage ledger
or audit rows (R1/R2/R5); log lines carry ids and counts only.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Drive, MockInterviewCompanyProfile as Profile, User
from app.routers.audit import record_audit
from app.routers.mentor import _build_verified_profile
from app.schemas.mock_interview import MockInterviewSessionCreate
from app.schemas.mock_interview_company import (
    ATTRIBUTION,
    DISCLAIMER,
    GENERIC_DISCLAIMER,
    CompanyProfileIn,
    JDSkillsIn,
    url_domain,
)
from app.services import ai_usage
from app.services import mock_interview as svc
from app.services import mock_interview_ai as engine
from app.services import role_templates

logger = logging.getLogger("campus_ai.mock_interview.company")

TARGET_TYPE = "mock_interview_company_profile"
ACTION = "mock_interview.company_profile_{}"
_PAST = {"create": "created", "update": "updated", "publish": "published", "archive": "archived"}
MAX_LIST = 200
JD_CONTEXT_CHARS = 3_000
SNAPSHOT_VERSION = 1

MSG_NOT_FOUND = "Company profile not found"
MSG_DRIVE_NOT_FOUND = "Drive not found"
MSG_ARCHIVED = "This profile is archived and can no longer be changed."
MSG_TEMPLATE = "Choose a role template from the list"
MSG_UNAVAILABLE_PROFILE = "That company profile is not available"
MSG_JD_DOWN = (
    "We couldn't read the job description right now. Nothing was saved; please try again in a moment."
)
JD_NOTE = (
    "Skills are read from the job description by AI; each one quotes the text it came from. "
    "Matches use your verified skills only."
)


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _now():
    return svc.utcnow()


# ---------------------------------------------------------------------------
# Lookups (tenant-scoped; anything outside the caller's institute is 404)
# ---------------------------------------------------------------------------


def get_profile(db: Session, tenant_id: int, profile_id: int) -> Profile:
    p = db.scalar(select(Profile).where(Profile.id == profile_id, Profile.tenant_id == tenant_id))
    if p is None:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_FOUND)
    return p


def published_profile(db: Session, tenant_id: int, profile_id: int) -> Profile:
    p = db.scalar(
        select(Profile).where(
            Profile.id == profile_id, Profile.tenant_id == tenant_id, Profile.status == "published"
        )
    )
    if p is None:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_NOT_FOUND)
    return p


def open_drive(db: Session, tenant_id: int, drive_id: int) -> Drive:
    """An OPEN drive of the caller's institute (closed or foreign -> 404)."""
    d = db.scalar(
        select(Drive).where(Drive.id == drive_id, Drive.tenant_id == tenant_id, Drive.is_open.is_(True))
    )
    if d is None:
        raise _http(status.HTTP_404_NOT_FOUND, MSG_DRIVE_NOT_FOUND)
    return d


# ---------------------------------------------------------------------------
# Staff: create / update / publish / archive / from drive (audited)
# ---------------------------------------------------------------------------


def _sources(db: Session, user: User, incoming, existing) -> list[dict]:
    """Source records as stored. New drive sources must be OPEN drives of the
    same institute; a drive source kept from before must still be in it."""
    old = existing if isinstance(existing, list) else []
    out: list[dict] = []
    for s in incoming:
        match = next(
            (
                o for o in old
                if isinstance(o, dict) and o.get("kind") == s.kind and o.get("label") == s.label
                and o.get("url") == s.url and o.get("drive_id") == s.drive_id
            ),
            None,
        )
        if s.kind == "drive":
            if match is None:
                open_drive(db, user.tenant_id, s.drive_id)
            elif db.scalar(
                select(Drive.id).where(Drive.id == s.drive_id, Drive.tenant_id == user.tenant_id)
            ) is None:
                raise _http(status.HTTP_404_NOT_FOUND, MSG_DRIVE_NOT_FOUND)
        out.append(
            {
                "kind": s.kind,
                "label": s.label,
                "url": s.url,
                "domain": url_domain(s.url) if s.kind == "public_url" else None,
                "drive_id": s.drive_id,
                "recorded_by": match.get("recorded_by") if match else user.id,
                "recorded_at": match.get("recorded_at") if match else _now().isoformat(),
            }
        )
    return out


def _check_template(key: str) -> None:
    if role_templates.get(key) is None:
        raise _http(422, MSG_TEMPLATE)


def _apply(p: Profile, payload: CompanyProfileIn, sources: list[dict], user: User) -> None:
    p.name = payload.name
    p.role_title = payload.role_title
    p.role_template_key = payload.role_template_key
    p.summary = payload.summary
    p.job_description = payload.job_description
    p.focus_skills_json = [s.model_dump() for s in payload.focus_skills]
    p.competencies_json = list(payload.competencies)
    p.sources_json = sources
    p.updated_by_id = user.id
    p.updated_at = _now()


def _audit(db: Session, user: User, verb: str, p: Profile, extra: str = "") -> None:
    sources = len(p.sources_json) if isinstance(p.sources_json, list) else 0
    record_audit(
        db,
        user,
        ACTION.format(verb),
        # Content-free: ids, status and counts only (never names, JD or notes).
        f"Company profile #{p.id} {_PAST[verb]} (status {p.status}, {sources} source(s){extra})",
        target_type=TARGET_TYPE,
        target_id=p.id,
    )


def create(db: Session, user: User, payload: CompanyProfileIn, *, drive_id: int | None = None) -> Profile:
    _check_template(payload.role_template_key)
    now = _now()
    p = Profile(
        tenant_id=user.tenant_id,
        status="draft",
        drive_id=drive_id,
        created_by_id=user.id,
        created_at=now,
    )
    _apply(p, payload, _sources(db, user, payload.sources, []), user)
    db.add(p)
    db.flush()
    _audit(db, user, "create", p, f", from drive #{drive_id}" if drive_id else "")
    db.commit()
    db.refresh(p)
    logger.info("company profile %s created in tenant %s", p.id, p.tenant_id)
    return p


def update(db: Session, user: User, p: Profile, payload: CompanyProfileIn) -> Profile:
    if p.status == "archived":
        raise _http(status.HTTP_409_CONFLICT, MSG_ARCHIVED)
    _check_template(payload.role_template_key)
    if p.status == "published" and not payload.sources:
        # A published (student-visible) profile must keep >= 1 source (P8-D10).
        raise _http(422, "A published profile must keep at least one source record.")
    _apply(p, payload, _sources(db, user, payload.sources, p.sources_json), user)
    _audit(db, user, "update", p)
    db.commit()
    db.refresh(p)
    return p


def blockers(p: Profile) -> list[str]:
    out = []
    if not (isinstance(p.sources_json, list) and p.sources_json):
        out.append("Add at least one source record before publishing.")
    if role_templates.get(p.role_template_key) is None:
        out.append(MSG_TEMPLATE)
    if p.status == "archived":
        out.append(MSG_ARCHIVED)
    return out


def publish(db: Session, user: User, p: Profile) -> Profile:
    if p.status == "archived":
        raise _http(status.HTTP_409_CONFLICT, MSG_ARCHIVED)
    if p.status == "published":
        return p  # idempotent, no second audit row
    problems = blockers(p)
    if problems:
        raise _http(422, problems[0])
    now = _now()
    p.status, p.published_at, p.updated_by_id, p.updated_at = "published", now, user.id, now
    _audit(db, user, "publish", p)
    db.commit()
    db.refresh(p)
    return p


def archive(db: Session, user: User, p: Profile) -> Profile:
    if p.status == "archived":
        return p  # idempotent
    now = _now()
    p.status, p.archived_at, p.updated_by_id, p.updated_at = "archived", now, user.id, now
    _audit(db, user, "archive", p)
    db.commit()
    db.refresh(p)
    return p


def _skills_from_drive(raw: str | None) -> list[dict]:
    out: list[dict] = []
    for part in (raw or "").split(","):
        name = " ".join(part.split())[:60]
        if name and name.lower() not in {s["name"].lower() for s in out}:
            out.append({"name": name, "weight": 2})
    return out[:20]


def create_from_drive(db: Session, user: User, drive_id: int) -> Profile:
    """Pre-fill a DRAFT from an open drive of the caller's institute."""
    d = open_drive(db, user.tenant_id, drive_id)
    payload = CompanyProfileIn(
        name=(d.company_name or "Company")[:150],
        role_title=(d.role_title or "Role")[:150],
        role_template_key="sde_fresher",
        job_description=(d.description or None) and d.description[:10_000],
        focus_skills=_skills_from_drive(d.required_skills),
        competencies=[],
        sources=[{"kind": "drive", "label": f"Placement drive #{d.id}", "drive_id": d.id}],
    )
    return create(db, user, payload, drive_id=d.id)


def list_staff(db: Session, tenant_id: int) -> list[Profile]:
    return list(
        db.scalars(
            select(Profile)
            .where(Profile.tenant_id == tenant_id)
            .order_by(Profile.updated_at.desc(), Profile.id.desc())
            .limit(MAX_LIST)
        )
    )


def list_published(db: Session, tenant_id: int) -> list[Profile]:
    return list(
        db.scalars(
            select(Profile)
            .where(Profile.tenant_id == tenant_id, Profile.status == "published")
            .order_by(Profile.name.asc(), Profile.id.asc())
            .limit(MAX_LIST)
        )
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _template_label(key: str | None) -> str | None:
    t = role_templates.get(key)
    return t["label"] if t else None


def staff_view(p: Profile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "role_title": p.role_title,
        "role_template_key": p.role_template_key,
        "drive_id": p.drive_id,
        "summary": p.summary,
        "job_description": p.job_description,
        "focus_skills": [s for s in _list(p.focus_skills_json) if isinstance(s, dict)],
        "competencies": [c for c in _list(p.competencies_json) if isinstance(c, str)],
        "sources": [
            {k: s.get(k) for k in ("kind", "label", "url", "domain", "drive_id", "recorded_at")}
            for s in _list(p.sources_json) if isinstance(s, dict)
        ],
        "status": p.status,
        "can_publish": p.status == "draft" and not blockers(p),
        "publish_blockers": blockers(p) if p.status == "draft" else [],
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "published_at": p.published_at,
        "archived_at": p.archived_at,
    }


def student_sources(sources) -> list[dict]:
    """Plain-text citations: never the URL itself (label + domain only)."""
    return [
        {"kind": s.get("kind"), "label": s.get("label"), "domain": s.get("domain")}
        for s in _list(sources)
        if isinstance(s, dict) and s.get("kind") in ("drive", "jd", "institute_note", "public_url")
    ]


def disclaimer_for(name: str | None) -> str:
    return DISCLAIMER.format(company=name) if name else GENERIC_DISCLAIMER


def brief_view(p: Profile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "role_title": p.role_title,
        "role_template_label": _template_label(p.role_template_key) or "",
    }


def student_view(p: Profile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "role_title": p.role_title,
        "role_template_key": p.role_template_key,
        "role_template_label": _template_label(p.role_template_key) or "",
        "summary": p.summary,
        "focus_skills": [s["name"] for s in _list(p.focus_skills_json) if isinstance(s, dict) and s.get("name")],
        "competencies": [c for c in _list(p.competencies_json) if isinstance(c, str)],
        "sources": student_sources(p.sources_json),
        "attribution": ATTRIBUTION,
        "disclaimer": disclaimer_for(p.name),
        "has_job_description": bool(p.job_description),
    }


# ---------------------------------------------------------------------------
# Session context (frozen copy in plan_json; generation only)
# ---------------------------------------------------------------------------


def build_snapshot(db: Session, user: User, payload: MockInterviewSessionCreate) -> dict:
    """The frozen company/role context for a new HR/technical session."""
    profile = None
    if payload.company_profile_id is not None:
        profile = db.scalar(
            select(Profile).where(
                Profile.id == payload.company_profile_id,
                Profile.tenant_id == user.tenant_id,
                Profile.status == "published",
            )
        )
        if profile is None:
            # Same answer for unknown, draft, archived and other institutes.
            raise _http(422, MSG_UNAVAILABLE_PROFILE)
    template_key = payload.role_template_key or (profile.role_template_key if profile else None)
    if payload.role_template_key and role_templates.get(payload.role_template_key) is None:
        raise _http(422, MSG_TEMPLATE)
    if profile and payload.role_template_key and payload.role_template_key != profile.role_template_key:
        raise _http(422, "The role template must match the company profile")
    template = role_templates.get(template_key)
    profile_skills = [s["name"] for s in _list(profile.focus_skills_json)
                      if isinstance(s, dict) and s.get("name")] if profile else []
    merged: list[str] = []
    for name in [*(payload.focus_skills or []), *profile_skills, *(template["focus_skills"] if template else [])]:
        if name.lower() not in {m.lower() for m in merged}:
            merged.append(name)
    return {
        "version": SNAPSHOT_VERSION,
        "profile_id": profile.id if profile else None,
        "name": profile.name if profile else None,
        "role_title": profile.role_title if profile else payload.target_role,
        "role_template_key": template_key,
        "role_template_label": template["label"] if template else None,
        "summary": profile.summary if profile else None,
        "competencies": (
            [c for c in _list(profile.competencies_json) if isinstance(c, str)] if profile else []
        ) or (list(template["competencies"]) if template else []),
        "focus_skills": merged[:20],
        "student_focus_skills": list(payload.focus_skills or []),
        "job_description": (profile.job_description or "")[:JD_CONTEXT_CHARS] if profile else "",
        "sources": student_sources(profile.sources_json) if profile else [],
        "frozen_at": _now().isoformat(),
    }


def snapshot_of(meta: dict) -> dict | None:
    snap = meta.get("company") if isinstance(meta, dict) else None
    return snap if isinstance(snap, dict) else None


def worker_context(snap: dict | None) -> dict | None:
    """What the AI worker gets for question generation (untrusted data)."""
    if not snap:
        return None
    return {
        "name": snap.get("name"),
        "role_title": snap.get("role_title") or "",
        "role_template": snap.get("role_template_label"),
        "summary": snap.get("summary"),
        "competencies": [c for c in _list(snap.get("competencies")) if isinstance(c, str)][:8],
        "focus_skills": [c for c in _list(snap.get("focus_skills")) if isinstance(c, str)][:20],
        "job_description": snap.get("job_description") or None,
    }


def context_out(snap: dict | None) -> dict | None:
    if not snap:
        return None
    return {
        "profile_id": snap.get("profile_id"),
        "name": snap.get("name"),
        "role_title": snap.get("role_title") or "",
        "role_template_label": snap.get("role_template_label"),
        "sources": [s for s in student_sources(snap.get("sources"))],
        "attribution": ATTRIBUTION,
        "disclaimer": disclaimer_for(snap.get("name")),
        "evaluation": "standard_rubric",
    }


# ---------------------------------------------------------------------------
# JD skill map (worker /interview/jd/skills; evidence re-checked here)
# ---------------------------------------------------------------------------

_MD = re.compile(r"[*_`#>|]")
_WS = re.compile(r"\s+")
_ALIASES = {
    "js": "javascript", "node": "node.js", "nodejs": "node.js", "reactjs": "react", "react.js": "react",
    "postgres": "postgresql", "psql": "postgresql", "cpp": "c++", "c plus plus": "c++",
    "golang": "go", "ml": "machine learning", "dsa": "data structures and algorithms",
    "data structures": "data structures and algorithms", "oop": "object-oriented programming",
    "oops": "object-oriented programming", "ts": "typescript", "k8s": "kubernetes",
    "rest": "rest apis", "rest api": "rest apis", "restful apis": "rest apis", "ms excel": "excel",
    "py": "python", "python3": "python",
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return _WS.sub(" ", _MD.sub(" ", text)).strip()


def canonical(skill: str) -> str:
    key = _norm(skill).strip(" .,:;")
    return _ALIASES.get(key, key)


def jd_skills(db: Session, user: User, payload: JDSkillsIn) -> dict:
    if payload.job_description is not None:
        source, jd = "job_description", payload.job_description
    elif payload.drive_id is not None:
        source, jd = "drive", open_drive(db, user.tenant_id, payload.drive_id).description or ""
    else:
        source, jd = "profile", published_profile(db, user.tenant_id, payload.profile_id).job_description or ""
    if not jd.strip():
        raise _http(422, "There is no job description to read")
    verified = {canonical(s) for s in (_build_verified_profile(db, user.id).get("verified_skills") or [])}
    try:
        with ai_usage.usage_context(user.tenant_id, user.id, subject_type="jd_skills"):
            raw = engine.jd_skills({"job_description": jd})
    except engine.InterviewEngineUnavailable:
        logger.warning("jd skills unavailable for tenant %s", user.tenant_id)
        raise _http(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_JD_DOWN) from None
    jd_norm = _norm(jd)
    skills: list[dict] = []
    seen: set[str] = set()
    for item in _list(raw.get("skills"))[:25]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("skill") or "").split())[:60]
        evidence = " ".join(str(item.get("evidence") or "").split())[:300]
        importance = item.get("importance")
        if not name or not evidence or len(_norm(evidence)) < 4 or _norm(evidence) not in jd_norm:
            continue  # every skill must quote the JD verbatim
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 3:
            importance = 2
        key = canonical(name)
        if key in seen:
            continue
        seen.add(key)
        category = item.get("category") if item.get("category") in ("technical", "tool", "soft", "domain") else "technical"
        skills.append({"skill": name, "category": category, "importance": importance,
                       "evidence": evidence, "matched": key in verified})
    matched = [s["skill"] for s in skills if s["matched"]]
    missing = [s["skill"] for s in skills if not s["matched"]]
    by_importance = sorted(skills, key=lambda s: (s["matched"], -s["importance"]))
    return {
        "source": source,
        "skills": skills,
        "matched": matched,
        "missing": missing,
        "focus_order": [s["skill"] for s in by_importance][:20],
        "note": JD_NOTE,
    }
