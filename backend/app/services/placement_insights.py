"""Placement dashboard, analytics, CSV exports, interview preparation (9.3/9.4).

Spec: docs/mock-interview/specs/phase-9-placement-institute-management.md §13-§14.

Metric definitions (every one institute-scoped; STUDENTS are counted
DISTINCT, so a student with several applications is counted once):

* registered   - active student accounts in scope (department / graduation
                 year filters apply; deactivated accounts are excluded).
* eligible     - without a drive filter: registered students whose placement
                 status is ``active`` (no profile counts as active) and who
                 are not yet placed ("available for placement"). With a drive
                 filter: students passing eligibility v2 for that drive now.
* applications - application rows in scope (drive + student scope + status
                 filter); ``by_status`` splits them, withdrawn included.
* applicants / shortlisted / selected - distinct students with at least one
                 application in scope (currently) in that state.
* offers       - offer rows on applications in scope, by offer status.
* placed       - distinct students in scope with a ``selected`` application
                 or an ``accepted`` offer on a drive in scope (P9-D4).
* placement %  - placed / (registered students whose placement status is
                 active, plus placed students of any status) (P9-D9).

Preparation (9.4) reuses the AI Mock Interview: it never creates sessions; it
reports whether practice is available to this student, the drive's published
company profile (Phase 8.4) when company profiles are enabled, the student's
OWN completed-practice count and their assigned campaigns for the drive.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationStatus,
    Department,
    Drive,
    MockInterviewCompanyProfile,
    MockInterviewSession,
    Offer,
    OfferStatus,
    PlacementCampaign,
    PlacementCampaignStudent,
    PlacementDriveSettings,
    Section,
    StudentPlacementProfile,
    User,
    UserRole,
)
from app.services import placement_campaigns as campaigns_svc
from app.services import placement_management as pm

STATUS_KEYS = [s.value for s in ApplicationStatus]
OFFER_KEYS = [s.value for s in OfferStatus]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Scope --------------------------------------------------------------------------
@dataclass
class Filters:
    academic_year: str | None = None
    department_id: int | None = None
    graduation_year: int | None = None
    company: str | None = None
    drive_id: int | None = None
    status: ApplicationStatus | None = None

    def names(self) -> list[str]:
        return [k for k, v in self.__dict__.items() if v not in (None, "")]


@dataclass
class Scope:
    students: dict[int, User]
    profiles: dict[int, StudentPlacementProfile]
    dept_of: dict[int, tuple[int | None, str | None, str | None]]
    drives: dict[int, Drive]
    cfgs: dict[int, PlacementDriveSettings]
    apps: list[Application]
    offers: list[Offer]
    placed: set[int]


def load_scope(db: Session, tenant_id: int, f: Filters) -> Scope:
    stmt = select(User).where(User.tenant_id == tenant_id, User.role == UserRole.student, User.is_active.is_(True))
    if f.department_id is not None:
        stmt = stmt.where(
            User.section_id.in_(
                select(Section.id).where(Section.department_id == f.department_id, Section.tenant_id == tenant_id)
            )
        )
    students = {u.id: u for u in db.scalars(stmt)}
    profiles = pm.profiles_for(db, tenant_id, None)
    if f.graduation_year is not None:
        students = {
            sid: u for sid, u in students.items()
            if sid in profiles and profiles[sid].graduation_year == f.graduation_year
        }
    dept_of = pm.departments_for(db, tenant_id, list(students.values()))

    dstmt = select(Drive).where(Drive.tenant_id == tenant_id)
    if f.drive_id is not None:
        dstmt = dstmt.where(Drive.id == f.drive_id)
    if f.company:
        dstmt = dstmt.where(func.lower(func.trim(Drive.company_name)) == f.company.strip().lower())
    drives = {d.id: d for d in db.scalars(dstmt)}
    cfgs = {
        c.drive_id: c
        for c in db.scalars(select(PlacementDriveSettings).where(PlacementDriveSettings.tenant_id == tenant_id))
    }
    if f.academic_year:
        drives = {i: d for i, d in drives.items() if i in cfgs and cfgs[i].academic_year == f.academic_year}

    apps: list[Application] = []
    if drives and students:
        astmt = select(Application).where(
            Application.tenant_id == tenant_id, Application.drive_id.in_(list(drives))
        )
        apps = [a for a in db.scalars(astmt) if a.student_id in students]
    all_apps_for_placed = apps
    if f.status is not None:
        apps = [a for a in apps if a.status == f.status]
    app_ids = {a.id for a in all_apps_for_placed}
    offers = (
        [o for o in db.scalars(select(Offer).where(Offer.tenant_id == tenant_id)) if o.application_id in app_ids]
        if app_ids
        else []
    )
    placed = {a.student_id for a in all_apps_for_placed if a.status == ApplicationStatus.selected} | {
        o.student_id for o in offers if o.status == OfferStatus.accepted and o.student_id in students
    }
    if f.status is not None:
        visible_offer_apps = {a.id for a in apps}
        offers = [o for o in offers if o.application_id in visible_offer_apps]
    return Scope(students, profiles, dept_of, drives, cfgs, apps, offers, placed)


def _status_of(scope: Scope, sid: int) -> str:
    p = scope.profiles.get(sid)
    return p.placement_status if p is not None else "active"


def _pct(n: int, d: int) -> float | None:
    return round(n / d * 100, 1) if d else None


def _distinct(apps: list[Application], st: ApplicationStatus | None = None) -> set[int]:
    return {a.student_id for a in apps if st is None or a.status == st}


# --- Analytics ------------------------------------------------------------------------
def analytics(db: Session, tenant_id: int, f: Filters) -> dict:
    s = load_scope(db, tenant_id, f)
    registered = set(s.students)
    if f.drive_id is not None and f.drive_id in s.drives:
        ctx = pm.drive_context(db, s.drives[f.drive_id])
        metrics = pm.bulk_metrics(db, tenant_id, list(registered))
        all_placed = pm.placed_student_ids(db, tenant_id, list(registered))
        eligible = {
            sid for sid in registered
            if pm.evaluate(ctx, metrics.get(sid) or pm.empty_metrics(), s.profiles.get(sid), s.dept_of[sid],
                           sid in all_placed)[0]
        }
        eligible_basis = "eligibility v2 for the selected drive"
    else:
        all_placed = pm.placed_student_ids(db, tenant_id, list(registered))
        eligible = {sid for sid in registered if _status_of(s, sid) == "active" and sid not in all_placed}
        eligible_basis = "placement status active and not yet placed"
    by_status = {k: 0 for k in STATUS_KEYS}
    for a in s.apps:
        by_status[a.status.value] += 1
    offers_by = {k: 0 for k in OFFER_KEYS}
    for o in s.offers:
        offers_by[o.status.value] += 1
    denominator = {sid for sid in registered if _status_of(s, sid) == "active"} | s.placed
    summary = dict(
        registered_students=len(registered),
        eligible_students=len(eligible),
        eligible_basis=eligible_basis,
        applications=len(s.apps),
        applications_by_status=by_status,
        applicants=len(_distinct(s.apps)),
        shortlisted_students=len(_distinct(s.apps, ApplicationStatus.shortlisted)),
        selected_students=len(_distinct(s.apps, ApplicationStatus.selected)),
        offers=len(s.offers),
        offers_by_status=offers_by,
        placed_students=len(s.placed),
        placement_denominator=len(denominator),
        placement_percentage=_pct(len(s.placed), len(denominator)),
        drives=len(s.drives),
    )

    # Company-wise (case-insensitive company name).
    company_rows: dict[str, dict] = {}
    drive_company = {i: d.company_name.strip() for i, d in s.drives.items()}
    for i, name in drive_company.items():
        key = name.lower()
        company_rows.setdefault(key, dict(company=name, drives=0, applications=0, _app=set(), _short=set(),
                                          _sel=set(), _placed=set(), offers_accepted=0))
        company_rows[key]["drives"] += 1
    for a in s.apps:
        row = company_rows[drive_company[a.drive_id].lower()]
        row["applications"] += 1
        row["_app"].add(a.student_id)
        if a.status == ApplicationStatus.shortlisted:
            row["_short"].add(a.student_id)
        if a.status == ApplicationStatus.selected:
            row["_sel"].add(a.student_id)
    app_drive = {a.id: a.drive_id for a in s.apps}
    for o in s.offers:
        if o.application_id in app_drive and o.status == OfferStatus.accepted:
            company_rows[drive_company[app_drive[o.application_id]].lower()]["offers_accepted"] += 1
    placed_by_drive = _placed_by_drive(s)
    for i, sids in placed_by_drive.items():
        company_rows[drive_company[i].lower()]["_placed"] |= sids
    companies = []
    for row in sorted(company_rows.values(), key=lambda r: (-len(r["_placed"]), r["company"].lower())):
        companies.append(dict(
            company=row["company"], drives=row["drives"], applications=row["applications"],
            applicants=len(row["_app"]), shortlisted_students=len(row["_short"]),
            selected_students=len(row["_sel"]), offers_accepted=row["offers_accepted"],
            placed_students=len(row["_placed"]),
        ))

    # Department-wise.
    dept_rows: dict[str, dict] = {}
    for sid in registered:
        _did, dname, _sec = s.dept_of[sid]
        key = dname or "Not assigned"
        r = dept_rows.setdefault(key, dict(department=key, registered=0, _den=0, _app=set(), _sel=set(), _placed=0))
        r["registered"] += 1
        if sid in s.placed:
            r["_placed"] += 1
        if _status_of(s, sid) == "active" or sid in s.placed:
            r["_den"] += 1
    for a in s.apps:
        key = s.dept_of[a.student_id][1] or "Not assigned"
        dept_rows[key]["_app"].add(a.student_id)
        if a.status == ApplicationStatus.selected:
            dept_rows[key]["_sel"].add(a.student_id)
    departments = [
        dict(department=r["department"], registered_students=r["registered"], applicants=len(r["_app"]),
             selected_students=len(r["_sel"]), placed_students=r["_placed"],
             placement_percentage=_pct(r["_placed"], r["_den"]))
        for r in sorted(dept_rows.values(), key=lambda r: r["department"].lower())
    ]

    # Drive-wise.
    per_drive: dict[int, dict[str, int]] = {i: {k: 0 for k in STATUS_KEYS} for i in s.drives}
    for a in s.apps:
        per_drive[a.drive_id][a.status.value] += 1
    drives_out = []
    for i, d in sorted(s.drives.items(), key=lambda kv: (kv[1].created_at or _now(), kv[0]), reverse=True):
        cfg = s.cfgs.get(i)
        drives_out.append(dict(
            drive_id=i, company=d.company_name, role_title=d.role_title,
            lifecycle=pm.effective_lifecycle(d, cfg), academic_year=cfg.academic_year if cfg else None,
            applications=sum(per_drive[i].values()), by_status=per_drive[i],
            placed_students=len(placed_by_drive.get(i, set())),
        ))
    return dict(filters=f.names(), summary=summary, companies=companies, departments=departments, drives=drives_out)


def _placed_by_drive(s: Scope) -> dict[int, set[int]]:
    """drive_id -> students placed THROUGH that drive (selected or accepted offer)."""
    out: dict[int, set[int]] = defaultdict(set)
    for a in s.apps:
        if a.status == ApplicationStatus.selected:
            out[a.drive_id].add(a.student_id)
    for o in s.offers:
        if o.status == OfferStatus.accepted and o.drive_id in s.drives and o.student_id in s.students:
            out[o.drive_id].add(o.student_id)
    return out


# --- CSV export -------------------------------------------------------------------------
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")


def safe_cell(value) -> str:
    """CSV/spreadsheet formula-injection guard (OWASP): text starting with a
    formula character is prefixed with a single quote. Numbers stay numbers."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if value.tzinfo else value.strftime("%Y-%m-%d %H:%M")
    text = str(value).replace("\x00", "")
    return "'" + text if text.startswith(_FORMULA_START) else text


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([safe_cell(h) for h in header])
    for r in rows:
        w.writerow([safe_cell(c) for c in r])
    return buf.getvalue()


EXPORT_KINDS = ("summary", "companies", "departments", "drives", "applications")


def export_rows(db: Session, tenant_id: int, f: Filters, kind: str) -> tuple[list[str], list[list]]:
    if kind == "applications":
        s = load_scope(db, tenant_id, f)
        header = ["Student", "Roll number", "Department", "Graduation year", "Company", "Role", "Drive id",
                  "Status", "Applied at", "Decided at"]
        rows = []
        for a in sorted(s.apps, key=lambda a: (s.drives[a.drive_id].company_name.lower(), a.id)):
            st = s.students[a.student_id]
            prof = s.profiles.get(a.student_id)
            d = s.drives[a.drive_id]
            rows.append([st.full_name, prof.roll_number if prof else None, s.dept_of[a.student_id][1],
                         prof.graduation_year if prof else None, d.company_name, d.role_title, d.id,
                         a.status.value, pm._aware(a.created_at), pm._aware(a.decided_at)])
        return header, rows
    data = analytics(db, tenant_id, f)
    if kind == "summary":
        sm = data["summary"]
        rows = [[k, v] for k, v in sm.items() if not isinstance(v, dict)]
        rows += [[f"applications_{k}", v] for k, v in sm["applications_by_status"].items()]
        rows += [[f"offers_{k}", v] for k, v in sm["offers_by_status"].items()]
        return ["Metric", "Value"], rows
    if kind == "companies":
        keys = ["company", "drives", "applications", "applicants", "shortlisted_students", "selected_students",
                "offers_accepted", "placed_students"]
        return keys, [[r[k] for k in keys] for r in data["companies"]]
    if kind == "departments":
        keys = ["department", "registered_students", "applicants", "selected_students", "placed_students",
                "placement_percentage"]
        return keys, [[r[k] for k in keys] for r in data["departments"]]
    keys = ["drive_id", "company", "role_title", "lifecycle", "academic_year", "applications", "placed_students"]
    return keys + STATUS_KEYS, [[r[k] for k in keys] + [r["by_status"][k] for k in STATUS_KEYS] for r in data["drives"]]


# --- TPO dashboard ---------------------------------------------------------------------
def dashboard(db: Session, tenant_id: int, academic_year: str | None) -> dict:
    now = _now()
    drives = list(db.scalars(select(Drive).where(Drive.tenant_id == tenant_id)))
    ctxs = pm.drive_contexts(db, drives)
    if academic_year:
        drives = [d for d in drives if ctxs[d.id].cfg is not None and ctxs[d.id].cfg.academic_year == academic_year]
    lifecycle = {d.id: pm.effective_lifecycle(d, ctxs[d.id].cfg) for d in drives}
    by_lifecycle = {k: 0 for k in ("draft", "published", "closed", "completed", "cancelled")}
    for lc in lifecycle.values():
        by_lifecycle[lc] += 1

    horizon = now + timedelta(days=14)
    upcoming = []
    for d in drives:
        if lifecycle[d.id] != "published":
            continue
        cfg = ctxs[d.id].cfg
        closes = pm._aware(cfg.applications_close_at) if cfg else None
        if closes is not None and now <= closes <= horizon:
            upcoming.append(dict(drive_id=d.id, company_name=d.company_name, role_title=d.role_title,
                                 closes_at=closes, kind="window"))
        elif closes is None and d.deadline is not None:
            dl = datetime(d.deadline.year, d.deadline.month, d.deadline.day, 23, 59, tzinfo=timezone.utc)
            if now <= dl <= horizon:
                upcoming.append(dict(drive_id=d.id, company_name=d.company_name, role_title=d.role_title,
                                     closes_at=dl, kind="deadline"))
    upcoming.sort(key=lambda r: r["closes_at"])

    drive_ids = [d.id for d in drives]
    by_status = {k: 0 for k in STATUS_KEYS}
    selected_students: set[int] = set()
    awaiting = 0
    if drive_ids:
        for drive_id, st, sid in db.execute(
            select(Application.drive_id, Application.status, Application.student_id).where(
                Application.tenant_id == tenant_id, Application.drive_id.in_(drive_ids)
            )
        ).all():
            by_status[st.value] += 1
            if st == ApplicationStatus.selected:
                selected_students.add(sid)
            if st == ApplicationStatus.applied and lifecycle[drive_id] in ("closed", "completed"):
                awaiting += 1
    total = sum(by_status.values())
    decided = by_status["shortlisted"] + by_status["selected"] + by_status["rejected"]

    students = list(db.scalars(select(User.id).where(
        User.tenant_id == tenant_id, User.role == UserRole.student, User.is_active.is_(True))))
    profiles = pm.profiles_for(db, tenant_id, None)
    placed = pm.placed_student_ids(db, tenant_id, students)
    available = sum(
        1 for sid in students
        if (profiles[sid].placement_status if sid in profiles else "active") == "active" and sid not in placed
    )
    missing_profiles = sum(1 for sid in students if sid not in profiles)

    active_campaigns = list(db.scalars(select(PlacementCampaign).where(
        PlacementCampaign.tenant_id == tenant_id, PlacementCampaign.status == "active")))
    counts = campaigns_svc.counts_for(db, active_campaigns)
    overdue = sum(1 for c in active_campaigns if campaigns_svc._utc(c.due_at) < now)

    window_closed = sum(
        1 for d in drives
        if lifecycle[d.id] == "published" and pm.window_state(ctxs[d.id].cfg, now) == "closed"
    )
    actions = [
        dict(kind="drafts", count=by_lifecycle["draft"], label="Draft drives not yet published"),
        dict(kind="window_closed", count=window_closed, label="Published drives whose application window has closed"),
        dict(kind="awaiting_decision", count=awaiting, label="Applications awaiting a decision on closed drives"),
        dict(kind="profiles_missing", count=missing_profiles, label="Students without academic records"),
        dict(kind="campaigns_overdue", count=overdue, label="Active campaigns past their due date"),
    ]
    return dict(
        academic_year=academic_year,
        drives=dict(total=len(drives), by_lifecycle=by_lifecycle, active=by_lifecycle["published"]),
        upcoming_deadlines=upcoming[:10],
        applications=dict(total=total, by_status=by_status),
        shortlisting=dict(decided=decided, pending=by_status["applied"],
                          decided_percentage=_pct(decided, total - by_status["withdrawn"])),
        students=dict(registered=len(students), available_for_placement=available, placed=len(placed),
                      selected=len(selected_students), without_academic_records=missing_profiles),
        campaigns=dict(active=len(active_campaigns), assigned=sum(c[0] for c in counts.values()),
                       completed=sum(c[1] + c[2] for c in counts.values())),
        pending_actions=[a for a in actions if a["count"]],
        notes=["Student counts cover the whole institute; the academic-year filter applies to drives and their applications."],
    )


# --- Interview preparation (student) -----------------------------------------------------
def preparation(db: Session, student: User, drive: Drive) -> dict:
    from app.routers.mock_interview import mock_interview_enabled_for
    from app.routers.mock_interview_company import company_enabled_for

    practice_on = mock_interview_enabled_for(student)
    company_on = practice_on and company_enabled_for(student)
    profile = None
    if company_on:
        p = db.scalar(
            select(MockInterviewCompanyProfile)
            .where(
                MockInterviewCompanyProfile.tenant_id == student.tenant_id,
                MockInterviewCompanyProfile.drive_id == drive.id,
                MockInterviewCompanyProfile.status == "published",
            )
            .order_by(MockInterviewCompanyProfile.id.desc())
            .limit(1)
        )
        if p is not None:
            profile = dict(id=p.id, name=p.name)
    params = {"prep_role": drive.role_title[:150], "prep_company": drive.company_name[:150]}
    if profile is not None:
        params["prep_profile"] = str(profile["id"])
    completed = db.execute(
        select(func.count(MockInterviewSession.id), func.max(MockInterviewSession.completed_at)).where(
            MockInterviewSession.tenant_id == student.tenant_id,
            MockInterviewSession.student_id == student.id,
            MockInterviewSession.status == "completed",
        )
    ).one()
    mine = list(
        db.scalars(
            select(PlacementCampaign)
            .join(
                PlacementCampaignStudent,
                (PlacementCampaignStudent.campaign_id == PlacementCampaign.id)
                & (PlacementCampaignStudent.tenant_id == student.tenant_id),
            )
            .where(
                PlacementCampaign.tenant_id == student.tenant_id,
                PlacementCampaign.drive_id == drive.id,
                PlacementCampaignStudent.student_id == student.id,
                PlacementCampaign.status.in_(("active", "closed")),
            )
        )
    )
    campaigns = []
    for c in mine:
        at = campaigns_svc.completion_map(db, c, [student.id]).get(student.id)
        campaigns.append(dict(id=c.id, title=c.title, due_at=campaigns_svc._utc(c.due_at),
                              completion=campaigns_svc.completion_of(c, at)))
    return dict(
        drive_id=drive.id,
        practice_available=practice_on,
        company_profile=profile,
        suggested_role=drive.role_title,
        suggested_company=drive.company_name,
        practice_link=("/dashboard/mock-interview?" + urlencode(params)) if practice_on else None,
        my_completed_practice=int(completed[0] or 0),
        my_last_practice_at=pm._aware(completed[1]),
        campaigns=campaigns,
    )


def departments_for_filter(db: Session, tenant_id: int) -> list[dict]:
    return [dict(id=i, name=n) for i, n in db.execute(
        select(Department.id, Department.name).where(Department.tenant_id == tenant_id).order_by(Department.name)
    ).all()]
