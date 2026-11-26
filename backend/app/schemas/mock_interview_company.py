"""Pydantic schemas for company profiles and company context (Phase 8.4).

Decisions P8-D9..D11 (approved 2026-11-23):
* staff (admin/TPO) write institute-owned profiles with SOURCE RECORDS
  (``drive | jd | institute_note | public_url``; >= 1 to publish);
* a ``public_url`` source is a citation only: the server never fetches it, and
  students see its label and domain as plain text, never a link;
* students see published profiles of their own institute, labelled "From
  your institute", never presented as verified company fact;
* company context changes question generation only - there is no
  company-fit or competency score anywhere.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.mock_interview import (  # noqa: F401 - re-exported
    JD_MAX_CHARS,
    CompanyContextOut,
    CompanySourceStudentOut,
    _clean,
)

SourceKind = Literal["drive", "jd", "institute_note", "public_url"]
ProfileStatus = Literal["draft", "published", "archived"]

MAX_SOURCES = 10
MAX_FOCUS_SKILLS = 20
MAX_COMPETENCIES = 8
SUMMARY_MAX_CHARS = 2_000
ATTRIBUTION = "From your institute"
DISCLAIMER = (
    "Questions are generated in the style of the role and the information your institute "
    "provided. They are not actual questions from {company}."
)
GENERIC_DISCLAIMER = (
    "Questions are generated in the style of the role. They are not actual questions from any company."
)
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


def _line(value: str | None, max_chars: int) -> str:
    return " ".join((_clean(value) or "").split())[:max_chars]


def url_domain(url: str | None) -> str | None:
    """Host of an http(s) URL, lower-cased, without 'www.'; None if invalid."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or not _DOMAIN_RE.match(host):
        return None
    return host[4:] if host.startswith("www.") else host


class CompanySourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    label: str = Field(min_length=2, max_length=200)
    # public_url only: a citation the author read. Never fetched or scraped.
    url: str | None = Field(default=None, max_length=500)
    # drive only: an open placement drive of the author's own institute.
    drive_id: int | None = Field(default=None, ge=1)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = _line(v, 200)
        if len(v) < 2:
            raise ValueError("Describe the source (at least 2 characters)")
        return v

    @model_validator(mode="after")
    def _kind_rules(self) -> "CompanySourceIn":
        if self.kind == "public_url":
            if url_domain(self.url) is None:
                raise ValueError("A public source needs a valid http(s) address")
            self.url = self.url.strip() if self.url else None
        elif self.url is not None:
            raise ValueError("Only a public source may have an address")
        if self.kind == "drive":
            if self.drive_id is None:
                raise ValueError("A drive source needs a drive")
        elif self.drive_id is not None:
            raise ValueError("Only a drive source may name a drive")
        return self


class FocusSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    weight: int = Field(default=2, ge=1, le=3)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _line(v, 60)
        if not v:
            raise ValueError("Skill names cannot be empty")
        return v


class CompanyProfileIn(BaseModel):
    """Create / replace a profile (staff). Publishing is a separate action."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150)
    role_title: str = Field(min_length=2, max_length=150)
    role_template_key: str = Field(min_length=1, max_length=60)
    summary: str | None = Field(default=None, max_length=SUMMARY_MAX_CHARS)
    job_description: str | None = Field(default=None, max_length=JD_MAX_CHARS)
    focus_skills: list[FocusSkill] = Field(default_factory=list, max_length=MAX_FOCUS_SKILLS)
    competencies: list[str] = Field(default_factory=list, max_length=MAX_COMPETENCIES)
    sources: list[CompanySourceIn] = Field(default_factory=list, max_length=MAX_SOURCES)

    @field_validator("name", "role_title")
    @classmethod
    def _short(cls, v: str) -> str:
        v = _line(v, 150)
        if len(v) < 2:
            raise ValueError("At least 2 characters")
        return v

    @field_validator("summary", "job_description")
    @classmethod
    def _long(cls, v: str | None) -> str | None:
        return _clean(v) or None

    @field_validator("competencies")
    @classmethod
    def _competencies(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            item = _line(item, 80)
            if item and item.lower() not in {x.lower() for x in out}:
                out.append(item)
        return out

    @field_validator("focus_skills")
    @classmethod
    def _unique_skills(cls, v: list[FocusSkill]) -> list[FocusSkill]:
        seen: set[str] = set()
        out = []
        for s in v:
            if s.name.lower() not in seen:
                seen.add(s.name.lower())
                out.append(s)
        return out


class CompanySourceStaffOut(BaseModel):
    kind: SourceKind
    label: str
    url: str | None = None
    domain: str | None = None
    drive_id: int | None = None
    recorded_at: datetime | None = None


class CompanyProfileStaffOut(BaseModel):
    id: int
    name: str
    role_title: str
    role_template_key: str
    drive_id: int | None
    summary: str | None
    job_description: str | None
    focus_skills: list[FocusSkill]
    competencies: list[str]
    sources: list[CompanySourceStaffOut]
    status: ProfileStatus
    can_publish: bool
    publish_blockers: list[str]
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    archived_at: datetime | None


class CompanyProfileBriefOut(BaseModel):
    id: int
    name: str
    role_title: str
    role_template_label: str


class CompanyProfileStudentOut(BaseModel):
    id: int
    name: str
    role_title: str
    role_template_key: str
    role_template_label: str
    summary: str | None
    focus_skills: list[str]
    competencies: list[str]
    sources: list[CompanySourceStudentOut]
    attribution: str
    disclaimer: str
    has_job_description: bool


class RoleTemplateOut(BaseModel):
    key: str
    label: str
    description: str
    focus_skills: list[str]
    competencies: list[str]


class JDSkillsIn(BaseModel):
    """Exactly one source: a JD the student pastes, an open drive or a published profile."""

    model_config = ConfigDict(extra="forbid")

    job_description: str | None = Field(default=None, max_length=JD_MAX_CHARS)
    drive_id: int | None = Field(default=None, ge=1)
    profile_id: int | None = Field(default=None, ge=1)

    @field_validator("job_description")
    @classmethod
    def _jd(cls, v: str | None) -> str | None:
        return _clean(v) or None

    @model_validator(mode="after")
    def _one(self) -> "JDSkillsIn":
        given = [x for x in (self.job_description, self.drive_id, self.profile_id) if x is not None]
        if len(given) != 1:
            raise ValueError("Send exactly one of job_description, drive_id or profile_id")
        return self


class JDSkillOut(BaseModel):
    skill: str
    category: str
    importance: int
    # A verbatim quote from the job description (items without one are dropped).
    evidence: str
    matched: bool


class JDSkillsOut(BaseModel):
    source: Literal["job_description", "drive", "profile"]
    skills: list[JDSkillOut]
    matched: list[str]
    missing: list[str]
    focus_order: list[str]
    note: str
