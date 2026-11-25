# Phase 9 — Institute and Placement Management (specification r1)

_Written 2026-11-24 (Phase 9.0 planning). Branch `feature/ai-mock-interview`, base `7d4147c`. Owner authorized planning and
implementation within the approved scope; decisions marked **[default]** were taken with safe engineering defaults
and can be changed by the owner. Decisions marked **[owner]** must be answered before the batch that needs them._

Phase 8 status is unchanged by this phase: **G2 is NOT passed**, the `code-runner/` stays disconnected, no student code
is executed, all 24 coding problems remain drafts, Batch 4 is deferred.

---

## 1. Existing architecture (audit, 2026-11-24)

```
Browser ─▶ Next.js 16 (Vercel) ─/v1 proxy─▶ FastAPI backend ─▶ Postgres (Supabase, RLS)      AI worker (stateless)
```

- **Tenancy.** `tenants` = institute. Every domain row has `tenant_id`. `get_current_user` stamps the caller's tenant
  GUC on the request session; 23 legacy tables + all mock-interview tables have `ENABLE/FORCE RLS` +
  `tenant_isolation` (migrations `d4e5f6a7b8c9`, `e7f8a9b0c1d2`, …). `users` deliberately has **no** RLS; user-by-id
  lookups must go through `get_scoped_user`.
- **Roles** (`UserRole`): student, teacher, tpo, admin, recruiter. Checks via `require_roles` on the backend.
- **Audit.** `audit_logs` + `record_audit(db, actor, action, summary, target_type, target_id)` (same transaction).
- **Notifications.** `notifications` table + `services/notifications.notify(...)` (in-app only; no email/scheduler).
- **Pagination.** Plain-list bodies + `X-Total-Count` (`pagination.py`, max 200).
- **Alembic head:** `b6d8f0a2c4e3` (Phase 8.4 company profiles).

## 2. Feature gap matrix

Class: **1** complete—reuse · **2** partial—improve · **3** missing—build · **4** defer (reason given).

| Feature | Existing implementation | Missing work | Class | Phase 9 action |
|---|---|---|---|---|
| Institute record & config | `tenants` (name, slug, active); `/tenants` signup + invites; admin `/institute/dashboard` KPIs | Institute placement settings (e.g. timezone, policies) | 2 | Per-drive policies in 9.1; institute-level policy table deferred until a second policy needs it |
| Departments / sections | `departments`, `sections(year)`, admin CRUD (`/admin/departments…`), `users.section_id` | "Course/programme" entity | 1 / 4 | Reuse; a course entity is deferred (department+section already model it; no data needs it yet) |
| Institute role-based access | `require_roles`, admin/TPO/teacher roles, tenant invites | — | 1 | Reuse |
| Tenant isolation / RLS | GUC + RLS on all domain tables | `placement_analytics.overview` and `analytics.section*` rely on RLS only (no app-layer tenant filter) | 2 | 9.1 hardening: add explicit tenant filters (no change on Postgres) |
| Student profile | `users` (name, email, section) + verified moat tables (skills, projects, ECA, internships), results → CGPA, attendance | Roll number, graduation year (batch), 10th/12th/diploma %, active backlogs, placement opt-out / debarred | 3 | 9.1 `student_placement_profiles` (TPO/admin-entered) |
| Student status & progress | Risk analytics (`/analytics`), applications list | Placement status (eligible / applied / placed / opted out) per student | 2 | 9.1 directory + detail API; 9.2 UI |
| Secure student data | Owner-only reads, staff role checks | Profile visibility rules | 3 | 9.1: TPO/admin read+write, student reads own, teacher/recruiter none |
| Company records | `recruiters` (company_name, website, about, status) + HR users + invites | Company record without inviting an HR | 2 / 4 | Reuse; "company without HR" needs a new `recruiter_status` value → deferred (P9-D10) |
| Recruiter relationships | `recruiter_users`, invites, linked drives, candidate view, recruiter decision, offers | — | 1 | Reuse unchanged |
| Job descriptions | `drives.description`, `role_title`, `package_lpa`, `location`, `required_skills` | — | 1 | Reuse |
| Placement drives | `drives` (create, open/close, list, detail), `is_open`, `deadline` (date, **not enforced**) | Lifecycle (draft → published → closed → completed / cancelled), application window, department / graduation-year / 10th / 12th / backlog criteria, one-offer policy, academic year | 2 | 9.1 `placement_drive_settings` + `placement_drive_departments` + lifecycle API |
| Eligibility | Verified CGPA, attendance, verified skills, verified projects, per-criterion reasons; N+1 queries (≈5/student) | New criteria; batched computation | 2 | 9.1 eligibility v2 (superset of legacy reasons; batched, constant query count) |
| Apply | `POST /drives/{id}/apply`: open + eligible + unique; race → 500 on duplicate | Window enforcement, profile status, one-offer policy, 409 on race | 2 | 9.1 apply v2; legacy route delegates when the flag is on (no bypass) |
| Status workflow | `PATCH /drives/applications/{id}/status`: any → any (except back to applied), note, notify | Transition rules, history, audit, race safety | 2 | 9.1 transitions + compare-and-set + `placement_application_events` + audit |
| Withdraw application | — | Needs a new `application_status` value | 3 | 9.2 (P9-D8 **approved** 2026-11-24) |
| Bulk shortlist / reject | — | Bounded, all-or-nothing, audited | 3 | 9.2 |
| Bulk academic import | Admin bulk user import (CSV) exists | Academic-profile CSV import | 3 | 9.2 (dry-run + commit, reuse bulk-import patterns) |
| Offers | `offers` (extend/withdraw/accept/decline) | — | 1 | Reuse; "placed" counts accepted offers |
| Notifications / deadlines | In-app notify on status change | Notify eligible students on publish; deadline reminders (no scheduler) | 2 / 4 | 9.2 publish notification (bounded); reminders deferred (needs a scheduler — Phase 10 infra) |
| TPO dashboard | Pages: drives, applications, placement-analytics, recruiters | Pipeline overview, pending actions, student directory | 2 | 9.2 (directory/drive settings UI), 9.3 dashboard |
| Faculty campaigns | Planned only: `mock_interview_sessions.campaign_id` (plain int), plan §IMPLEMENTATION_PLAN | Campaign tables, assignment to sections, progress | 3 | 9.3 (P9-D7 **approved**: completion status only) |
| Placement analytics | `/placement/analytics/overview`: funnel, rate (placed ÷ active students), per-drive, company leaderboard; admin institute KPIs | Filters (academic year, graduation year, department, drive, company), department-wise, cohort rate, CSV export | 2 | 9.4 new endpoints; existing overview unchanged |
| Mock-interview integration | P8.4 company profiles can be created from an **open** drive; sessions carry `company_name`, `job_description` | Drive → "prepare" link, prep progress for TPO (consent) | 2 | 9.4 (reuse P8 APIs; no duplication) |

## 3. Batch breakdown

| Batch | Scope | Depends on |
|---|---|---|
| **9.1 Placement core v2 (backend)** | Flags; migration P9-A (4 tables); student placement profiles API; drive settings + lifecycle; eligibility v2 (batched); apply v2 + legacy delegation; status transitions (compare-and-set) + event history + audit + notification; pre-existing tenant-filter hardening | — |
| **9.2 Management UI + bulk operations** | Frontend flag; TPO student directory + profile editor; drive settings/lifecycle editor; student eligibility + window + history UI; bulk status (≤ 200, all-or-nothing); academic CSV import; publish notification; company directory view; **student withdrawal (P9-D8)** | 9.1 committed |
| **9.3 Faculty campaigns + TPO dashboard** | `placement_campaigns` + `placement_campaign_students` (students, picked by section or placement filters; migration `e1a3c5b7d9f4`, §13) — *corrected 2026-11-25 (L5, §20): this row originally planned `mock_interview_campaigns` + `…_campaign_targets` (sections), names that were never created*; teacher/TPO/admin assign; student "assigned" list; progress (**completion status only**, P9-D7); TPO dashboard home (pipeline, deadlines, pending decisions) | 9.1, P9-D7 |
| **9.4 Analytics v2 + interview integration** | Filtered analytics (academic year, graduation year, department, drive, company), department- and company-wise, cohort placement %, CSV export (formula-injection safe, audited); drive → interview preparation; prep progress (consent) | 9.1–9.3 |
| **9.5 Hardening & acceptance** | Security review, Postgres RLS suite on a local throwaway DB, 2 000-student performance check, accessibility pass, full regression, runbook | all |

## 4. Decisions

| Id | Decision | Status |
|---|---|---|
| P9-D1 | Flags: backend `PLACEMENT_MANAGEMENT_ENABLED` (off) + `PLACEMENT_MANAGEMENT_TENANT_IDS` (**empty = no institute**, fail closed); frontend `NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED` (9.2). Flag off ⇒ every new route 404 and every legacy route behaves exactly as today | [default] |
| P9-D2 | Academic records (roll no., graduation year, 10th/12th/diploma %, active backlogs, placement status) are entered by **TPO/admin only**; students read their own. Rationale: eligibility must use trusted data (same principle as the verified-data moat); no student self-report workflow needed | [default] |
| P9-D3 | Collected fields are limited to §5.1. **Not collected:** DOB, gender, category/caste, phone, address, photos, ID numbers | [default] |
| P9-D4 | One-offer policy is per drive (`exclude_placed`, default false). "Placed" = has a `selected` application **or** an `accepted` offer in the institute | [default] |
| P9-D5 | Status transitions (§5.4); corrections out of `selected`/`rejected` need a note | [default] |
| P9-D6 | For a flagged institute, the legacy `/drives/...` apply, status and open/close routes enforce the v2 rules (no bypass). Flag off ⇒ legacy rules (rollback) | [default] |
| P9-D7 | Faculty see **only whether a student's mock interview was completed and its completion status**. Never the score, transcript, recording, AI feedback, full report or the student's responses. Enforced in the backend API (response schemas carry no such fields), not only hidden in the UI | **[owner] approved 2026-11-24 (option A)** — for 9.3 |
| P9-D8 | Students may withdraw their own application. New `application_status` value `withdrawn` (additive enum migration). Allowed only from `applied` and `shortlisted`; never from `selected`, from an application with an accepted offer, or once placed; `withdrawn` is final (no transition out). Same compare-and-set, history event, audit row and institute isolation as staff changes; TPO/admin lists show it as a distinct status (not counted as rejected) | **[owner] approved 2026-11-24** — implement in 9.2, not 9.1 |
| P9-D9 | Cohort placement % = distinct placed students ÷ students of that graduation year whose placement status is `active` or placed; students without a profile reported separately as "unassigned"; existing overview formula unchanged | [default] (used in 9.4) |
| P9-D10 | Company records without an HR invite (new `recruiter_status` value) | deferred |

## 5. Batch 9.1 requirements

### 5.1 Data (migration P9-A, revises `b6d8f0a2c4e3`; additive only; SQLite-compatible types)

**`student_placement_profiles`** — one row per student.
| Column | Type | Rule |
|---|---|---|
| id, tenant_id (FK tenants CASCADE), student_id | int | composite FK `(student_id, tenant_id)` → `users(id, tenant_id)` CASCADE; unique `student_id` |
| roll_number | String(40) null | unique per tenant when set (partial unique index); trimmed, `[A-Za-z0-9/_.-]` |
| graduation_year | int null | 2000–2100 |
| tenth_percent, twelfth_percent, diploma_percent | float null | 0–100 |
| active_backlogs | int null | 0–99 |
| placement_status | String(20) | `active` (default) · `opted_out` · `debarred`; CHECK |
| status_note | String(300) null | |
| updated_by_id | FK users SET NULL | |
| created_at, updated_at | tz datetime | |

**`placement_drive_settings`** — 1:1 with a drive (`drive_id` unique, FK drives CASCADE).
| Column | Rule |
|---|---|
| lifecycle | `draft` · `published` · `closed` · `completed` · `cancelled`; CHECK |
| applications_open_at, applications_close_at | tz datetime null; open < close; API requires timezone-aware input |
| academic_year | String(9) null, `YYYY-YY` with consecutive years |
| graduation_years | JSON list of ints (≤ 6, 2000–2100); empty = any |
| min_tenth_percent, min_twelfth_percent | float null 0–100 |
| max_active_backlogs | int null 0–99 |
| exclude_placed | bool default false |
| created_by_id, updated_by_id, created_at, updated_at | |

**`placement_drive_departments`** — `(tenant_id, drive_id, department_id)`, unique `(drive_id, department_id)`,
FKs CASCADE. Empty set = all departments. Department ids are validated against the caller's institute.

**`placement_application_events`** — append-only status history: `tenant_id`, `application_id` (FK CASCADE),
`from_status` (null for creation), `to_status`, `actor_id` (SET NULL), `actor_role`, `note` (≤ 500), `created_at`.
No update/delete route exists.

All four: `ENABLE + FORCE ROW LEVEL SECURITY` + `tenant_isolation` (Postgres only). `drives`, `applications` and
every other existing table are **not altered**.

### 5.2 Gate

`placement_management_enabled_for(user)` = `PLACEMENT_MANAGEMENT_ENABLED` and `user.tenant_id ∈
PLACEMENT_MANAGEMENT_TENANT_IDS` (empty/invalid list ⇒ nobody). Router-level dependency: 404 when closed;
recruiters 403 on every new route.

### 5.3 API (prefix `/placement`, mounted at `/v1` and unversioned)

| Method + path | Who | Behaviour |
|---|---|---|
| `GET /placement/students` | tpo, admin | Paged directory; filters `department_id`, `section_id`, `graduation_year`, `placement_status`, `placed`, `q` (name/email/roll, ≤ 100 chars, bound parameter) |
| `GET /placement/students/{id}` | tpo, admin | Profile + verified metrics + placed flag + application summary; other institute / non-student → 404 |
| `PUT /placement/students/{id}/profile` | tpo, admin | Upsert; audit `placement.profile_update` listing changed **field names only** |
| `GET /placement/me/profile` | student | Own profile (null fields when none) + verified metrics + placed flag |
| `POST /placement/drives` | tpo | Create a drive as **draft** (`is_open = false`) with its settings; audit |
| `PUT /placement/drives/{id}` | tpo | Full replacement of the drive's own fields (not when completed/cancelled); audit lists changed field names |
| `GET /placement/drives/{id}/settings` | tpo, admin | Settings or defaults derived from the legacy drive (`lifecycle` = published/closed from `is_open`) |
| `PUT /placement/drives/{id}/settings` | tpo | Upsert criteria + window + departments (not lifecycle); audit |
| `POST /placement/drives/{id}/lifecycle` | tpo | `{to}`; §5.4 rules; syncs `drives.is_open`; audit; 409 on invalid |
| `GET /placement/drives/{id}/eligibility` | tpo, admin | Paged; `eligible_only`; summary counts in headers `X-Eligible-Count`; batched queries |
| `GET /placement/drives/{id}/my-eligibility` | student | Reasons + `window` state + `already_applied` + `can_apply` |
| `POST /placement/drives/{id}/apply` | student | v2 rules; 201 `ApplicationOut` (same shape as legacy) |
| `PATCH /placement/applications/{id}/status` | tpo | Transition + compare-and-set + event + audit + notification |
| `GET /placement/applications/{id}/history` | tpo, admin, owning student | Events, oldest first |

Drives without a settings row keep legacy semantics everywhere (v2 eligibility then evaluates only legacy criteria plus
the student's placement status).

### 5.4 Rules

- **Drive lifecycle:** draft→published|cancelled; published→closed|cancelled; closed→published|completed|cancelled;
  completed and cancelled are terminal. `is_open` = (lifecycle == published). A drive without settings is
  "published" when `is_open` else "closed"; the first lifecycle call creates the settings row.
- **Apply (v2):** drive in caller's institute (else 404) · lifecycle published · now within window (open_at ≤ now <
  close_at, each bound optional) · profile `placement_status == active` (missing profile counts as active) ·
  `exclude_placed` ⇒ not placed · all eligibility criteria pass · not already applied (409; a unique-constraint race
  also returns 409, never 500). Creates the application **and** its first event in one transaction.
- **Eligibility v2 criteria** = legacy four (CGPA, attendance, verified projects, required skills — identical numbers)
  + department, graduation year, 10th %, 12th % (diploma % accepted as an alternative to 12th when 12th is missing),
  active backlogs. A criterion whose drive setting is empty is skipped. A required academic value that is missing
  from the profile **fails** that criterion ("not recorded").
- **Application transitions:** applied→shortlisted|rejected|selected; shortlisted→selected|rejected;
  rejected→shortlisted (note required); selected→rejected (note required). Same status ⇒ 409. Performed as
  `UPDATE … WHERE id = :id AND status = :from` — 0 rows ⇒ 409 "changed by someone else". Only on drives whose
  lifecycle is not `cancelled`.
- **Notifications:** status change notifies the student (as legacy). No new notification types in 9.1.

### 5.5 Pre-existing hardening (behaviour on production Postgres unchanged)
- `placement_analytics.overview`: filter drives/applications by the caller's `tenant_id`.
- `analytics.section*`: 404 for a section of another institute (app-layer check).
- Legacy apply: unique-constraint race → 409 instead of 500.

## 6. Security and tenant-isolation plan

- Every new query filters `tenant_id`; users resolved with `get_scoped_user(role=student)`; RLS on all new tables.
- Cross-institute ids (drive, application, student, department) ⇒ 404, never 403 (no existence oracle).
- Role matrix: student (own profile, own eligibility, apply, own history) · tpo (all writes) · admin (reads; profile
  writes) · teacher (none in 9.1) · recruiter (403 everywhere new; existing recruiter routes unchanged).
- Race safety: unique constraint for applications; compare-and-set status update; settings upsert keyed by unique
  `drive_id` (race ⇒ 409).
- Logs/audit: summaries contain ids and field names, never marks, notes or student names.
- Input: Pydantic bounds on every field; `q` search uses bound parameters with escaped `LIKE` wildcards.
- No new dependencies. No AI call in 9.1.

## 7. Acceptance criteria (9.1)

1. Flag off (default) ⇒ all new routes 404; legacy suites pass unchanged.
2. Flag on, institute not listed ⇒ 404; empty list ⇒ 404.
3. Every new route enforces the role matrix and returns 404 cross-institute (tests for each).
4. Eligibility v2 legacy numbers equal the legacy engine for the same student (parity test) and the TPO list uses a
   constant number of queries independent of student count (query-count test).
5. Apply v2 rejects: draft/closed/cancelled drive, before window, after window, opted-out, debarred, placed when
   `exclude_placed`, each failing criterion, duplicate (409), and the legacy apply route enforces the same rules
   for flagged institutes.
6. Status transitions follow §5.4; stale `from` ⇒ 409; every change writes exactly one event and one audit row.
7. Migration P9-A upgrades, downgrades and re-upgrades on a throwaway SQLite DB; RLS test file for Postgres written.
8. Full backend SQLite suite passes with no new failures.

## 8. Testing plan

- `tests/test_placement_management.py` (SQLite): gate, RBAC, isolation, profiles, settings, lifecycle, eligibility
  (parity + each criterion + query count), apply rules, transitions, history, audit content, legacy delegation,
  hardening fixes.
- `tests/test_placement_management_rls_postgres.py` (opt-in, local throwaway Postgres only).
- Migration up/down/up on a scratch SQLite DB (scratchpad), never a shared DB.
- Full backend suite once per batch (never two runs at once). Frontend `tsc`/`lint`/tests from 9.2.

## 9. Dependencies and risks

| Risk | Mitigation |
|---|---|
| Legacy `deadline` never enforced; changing it would alter live behaviour | v2 window lives in settings; legacy drives unchanged |
| Bypass of v2 criteria through legacy routes | P9-D6 delegation + tests |
| Academic data is sensitive | TPO/admin only, no teacher/recruiter access, field-name-only audit, no logs of values |
| Eligibility at scale (N+1) | Batched service, query-count test, 2 000-student check in 9.5 |
| RLS only verified on SQLite by default | Opt-in Postgres test; must run before production (9.5 / Phase 10) |
| Owner decisions P9-D7/D8 | Both approved 2026-11-24; P9-D10 still deferred |
| Phase 8 G2 not passed | Phase 9 has no dependency on the runner; nothing executes code |

## 10. Batch 9.1 record (2026-11-24; implemented — later committed as `2ccc8b9`)

**Files.** New: `backend/app/models/placement_management.py`, `backend/alembic/versions/c8e2a4f6b1d9_placement_management_core.py`,
`backend/app/schemas/placement_management.py`, `backend/app/services/placement_management.py`,
`backend/app/routers/placement_management.py`, `backend/tests/test_placement_management.py`,
`backend/tests/test_placement_management_rls_postgres.py`. Modified: `app/core/config.py` (2 flags),
`app/models/__init__.py`, `main.py` (router registered), `app/routers/placement.py` (P9-D6 delegation; duplicate-apply
race → 409), `app/routers/placement_analytics.py` and `app/routers/analytics.py` (§5.5 hardening).

**Deviations from §5.** `POST /placement/drives` (draft create) and `PUT /placement/drives/{id}` added (no drive edit
route existed). Lifecycle body is `{to}` only (a note was accepted but never stored — removed in review). Students
get 404 (not 400) for a draft drive on every route (no existence oracle). Legacy `GET /drives/{id}`,
`/drives/{id}/my-eligibility` and the TPO `/drives/{id}/eligibility` also use v2 for flagged institutes.

**Tests (actual).**
- Focused `test_placement_management.py`: 30 passed (first run); after review fixes, focused + adjacent suites
  (`test_tenant_isolation`, `test_analytics`, `test_mock_interview_company`, `test_users_tenant_isolation`):
  67 passed, 5 skipped (Postgres-only), 0 failed.
- Full backend SQLite suite (before the three review fixes below): **685 passed, 29 skipped, 0 failed** (baseline
  655 / 24; +30 new, +5 new Postgres-only skips).
- Migration `c8e2a4f6b1d9` on a scratch SQLite DB (pre-9.1 schema from the ORM, `stamp b6d8f0a2c4e3` → `upgrade head`
  → `downgrade -1` → `upgrade head`): all succeeded; 4 tables and 9 indexes created, dropped, recreated; index names
  match the ORM.
- **Not run:** `test_placement_management_rls_postgres.py` (no local Postgres; needs a throwaway instance).
  *(Update 2026-11-24: run and passed later — §16.)*

**Review (self-review, not an independent agent).** Fixed: lifecycle note silently discarded (field removed);
whitespace-only note satisfied "note required" via the legacy route (now stripped); concurrent profile create
reported a misleading roll-number error (explicit roll-number pre-check + generic race message). Checked: every
query tenant-filtered; 404 cross-institute for drives, applications, students, departments (same message for
unknown and foreign department ids); recruiters 403; audit summaries contain ids, statuses and field names only;
LIKE wildcards escaped; compare-and-set for status and lifecycle; unique constraints map to 409.

**Known issues.** Legacy TPO applicant list (`GET /drives/{id}/applications`) still computes the legacy "eligible"
flag. Applications created before an institute is flagged have no history events (history starts at the next
change). Eligibility list evaluates all students in memory (fine for thousands; measured in 9.5).
## 11. Batch 9.1 final verification (2026-11-24; at the time uncommitted — later committed as `2ccc8b9`)

**Owner decisions (2026-11-24).** P9-D8 student withdrawal **approved for 9.2** (not added to 9.1). P9-D7 **approved,
option A**: faculty see completion status only, enforced in the API. Batch 9.2 is **authorized but not started**; it
starts only after 9.1 is separately staged and committed.

**Added in finalization (tests only, no product code changed).** `test_placement_flag_defaults_off` (both flags default
off / empty). `test_teacher_cannot_read_student_mock_interview_data`: with every mock-interview, ops, coding and
placement flag ON, a teacher of the same institute gets 403/404 from the session, report (GET and POST), list,
active, history, coding feedback, ops usage, per-student usage and diagnostics routes and the placement student
directory/detail, and no response contains the student's answer text or score; the owning student does get the
report (control). Faculty campaign progress itself is built in 9.3 and must expose completion fields only.

**Results (actual, latest code).**
- Full backend SQLite suite: **687 passed, 29 skipped, 0 failed, exit code 0** (685 + the 2 tests above).
- Focused suites (`test_placement_management`, `test_placement_management_rls_postgres`, `test_analytics`,
  `test_tenant_isolation`, `test_users_tenant_isolation`, `test_mock_interview_sessions`, `test_mock_interview_admin`):
  **102 passed, 5 skipped (Postgres-only), 0 failed, exit code 0**.
- Migration `c8e2a4f6b1d9` on scratch SQLite: stamp `b6d8f0a2c4e3` → upgrade → downgrade → upgrade all OK; one head.
- `git diff --check`: clean (tracked and new files).
- **PostgreSQL: BLOCKED / NOT RUN.** No `RLS_TEST_DATABASE_URL`, nothing listening on 5432, no running container.
  Starting one needs owner approval. RLS policies, FORCE RLS and the composite foreign key are therefore verified
  only by reading the migration and the opt-in test, not by execution.
  *(Update 2026-11-24: executed and passed on a disposable PostgreSQL 16.16 container — §16.)*
- Concurrency: the duplicate-apply race (unique constraint → 409, legacy and v2) and the stale status write
  (compare-and-set → 409, no event written) are proven by simulated interleavings on SQLite. True parallel
  execution on Postgres has not been run.

**Feature flag.** Default OFF; an empty or invalid institute list enables nobody. OFF: all 14 `/placement/*` v2 routes
404 and legacy `/drives` routes keep their behaviour (`test_flag_off_legacy_unchanged`, gate tests). The two
hardening changes apply to everyone: the duplicate-apply race returns 409 instead of 500, and two analytics views
filter by institute in the app. ON (listed institute): lifecycle, window, new criteria, transitions, history and audit
enforced on both v2 and legacy routes.

**Code review (self-review of the full 9.1 diff; no new defects found).** Checked: cross-institute 404s, TPO/admin
roles, teachers/recruiters 403 on academic data, eligibility only from verified records plus TPO-entered profiles,
compare-and-set for status and lifecycle, events/audit written in the same transaction, content-free audit summaries.
Notes: a student's own history shows the TPO's note (the same text is already sent in the status notification);
status changes stay allowed on a `completed` drive (only `cancelled` freezes); if the flag is later switched OFF,
draft drives fall back to legacy closed drives (visible to staff, not in students' open list).

**Known limitations (unchanged).** The legacy TPO applicant list (`GET /drives/{id}/applications`) still shows the
legacy "eligible" flag. Applications created before an institute is enabled start their history at their next status
change. The eligibility list evaluates all students in memory (performance check in 9.5).

---

## 12. Batch 9.2 — management UI, bulk status, withdrawal, academic import (2026-11-24; implemented — committed locally as `9217d8d`)

**Decisions taken with safe defaults (no new owner decision needed).**
- New UI lives in one flag-gated area `/dashboard/placement-hub` (build flag `NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED`,
  default off, plus the server probe `GET /placement/status`). With the flag off no request is made, no nav entry
  appears and the legacy `/dashboard/drives`, `/applications`, `/placements` pages are unchanged (they only gained a
  style entry for the `withdrawn` status).
- Withdrawal (P9-D8) "once placed" is read literally: a student who is placed (selected anywhere or accepted offer)
  cannot withdraw any application; an open (`extended`) offer on the application also blocks it (decline first).
- Bulk status = `shortlisted` or `rejected` only (selection stays a deliberate single action), ≤ 200 rows, preview
  (`dry_run`, default) then apply; per-record compare-and-set against the status the TPO saw; per-record results.
- Academic import is stateless: preview returns a SHA-256; commit re-uploads the same file (hash must match) and is
  re-validated against the current DB, then every valid row is written in ONE transaction (unique-constraint race ⇒
  409, nothing saved). Blank cells never overwrite; placement status and verified metrics are not importable;
  students matched by email (or an existing roll number) inside the caller's institute only. XLSX is parsed with the
  standard library (zip member/size/ratio limits, DOCTYPE/ENTITY rejected) — **no new dependency**.
- First publish of a draft notifies eligible students in-app (≤ 2 000; `drive_published`, plain string type).

**Migration P9-B `d9f1a3c5e7b2`** (revises `c8e2a4f6b1d9`): Postgres `ALTER TYPE application_status ADD VALUE
IF NOT EXISTS 'withdrawn'` (autocommit block); the two CHECKs on `placement_application_events` widened. Downgrade
restores them and **refuses** while any withdrawn application/event exists (verified); the enum value cannot be
dropped on Postgres (harmless).

**API (all under the 9.1 gate; recruiters 403; cross-institute 404).**

| Route | Who | Notes |
|---|---|---|
| `GET /placement/status` | any hub role | probe |
| `GET /placement/drives` | tpo, admin | lifecycle, window, status counts; `q`, `lifecycle`, `academic_year`; paged |
| `GET /placement/drives/{id}/applicants` | tpo, admin | CURRENT eligibility v2 + failed criteria, offer status; `status`, `eligible`, `q`; `X-Status-Counts`; constant queries |
| `POST /placement/drives/{id}/applications/bulk-status` | tpo | `{to_status, items[{application_id, expected_status}], note, dry_run}` |
| `POST /placement/applications/{id}/withdraw` | student (own) | optional `expected_status`; history + audit |
| `GET /placement/me/drives` | student | `scope=open|all`; own eligibility, window, applied state |
| `GET /placement/me/applications` | student | `can_withdraw` + reason |
| `GET /placement/import/template`, `POST /placement/import/preview`, `POST /placement/import/commit` | tpo, admin | 2 MB, 5 000 rows, .csv/.xlsx |

Legacy changes: `GET /drives/{id}/applications` shows the v2 verdict for flagged institutes (known limitation 1 of
9.1 closed; shape unchanged); the legacy status route can never set or leave `withdrawn` (flag on or off).
CORS now exposes `X-Total-Count`, `X-Request-ID`, `X-Status-Counts`, `X-Eligible-Count`, `X-Completion-Counts`
(additive; needed when the frontend calls the API cross-origin; same-origin proxy deployments are unaffected).

**Frontend.** `src/components/placement-hub/*` (API/types, pure logic, gate frame, drive form, applicants panel
with bulk preview/confirm, eligibility panel, student hub) and pages `placement-hub/` (home), `drives`,
`drives/[id]`, `students`, `import`, `opportunities/[id]`. `lib/api.ts` gained `getWithHeaders` (additive).

## 13. Batch 9.3 — faculty campaigns + TPO home dashboard (2026-11-24; implemented — committed locally as `9217d8d`)

**Migration P9-C `e1a3c5b7d9f4`** (revises `d9f1a3c5e7b2`): `placement_campaigns` (title, description, status
draft→active|cancelled, active→closed|cancelled, closed→active|archived, cancelled→archived; optional interview type,
target role, drive link; `starts_at < due_at`) and `placement_campaign_students` (composite FK keeps the student in
the institute; unique per campaign). RLS + FORCE + `tenant_isolation` on both. `mock_interview_sessions.campaign_id`
is **not** touched.

**Completion (P9-D7 option A, enforced in the service/SQL, not the UI).** A campaign never creates interviews.
Completion = the student's `completed` mock-interview session with `completed_at ≥ starts_at` (on time ≤ due_at,
otherwise "completed late"), matching the campaign's interview type when set. The campaign code reads ONLY
`student_id, status, interview_type, completed_at, tenant_id` from `mock_interview_sessions`; a test spies on every
SQL statement of the progress and list routes and proves `mock_interview_reports`/`mock_interview_turns` are never
queried, and that seeded secret answer text and scores never appear in responses. Progress schema is
`extra="forbid"` with six fields only.

**Roles.** Teacher: own campaigns only; assigns only active students of sections they teach (timetable
`teacher_id`); cannot link drives or use placement filters (403). TPO/admin: all campaigns; candidates filterable by
department, section, graduation year, drive applicants and application status. Students: `/placement/me/campaigns`
(active/closed, own completion). Activation notifies assigned students (`campaign_assigned`); later assignments to an
active campaign notify the new students. Audit: create, update (field names), status, assign (counts), unassign.

**Dashboard** `GET /placement/dashboard?academic_year=` (tpo, admin): drives by lifecycle, deadlines in 14 days
(window close or legacy deadline), applications by status, shortlisting progress (decided ÷ non-withdrawn),
registered / available-for-placement / placed / selected students, students without academic records, active
campaign completion, pending actions (drafts, published drives with a closed window, applied applications on
closed/completed drives, missing records, overdue campaigns). Student counts are institute-wide; the year filter
applies to drives and their applications (stated in the response `notes`).

## 14. Batch 9.4 — analytics, CSV export, AI Mock Interview connection (2026-11-24; implemented — committed locally as `9217d8d`)

**Analytics** `GET /placement/analytics` (tpo, admin), filters `academic_year`, `department_id`, `graduation_year`,
`company` (case/whitespace-insensitive), `drive_id`, `status`; foreign/unknown department or drive ⇒ 404. Metric
definitions (module docstring of `services/placement_insights.py`): registered = active student accounts in scope;
eligible = active placement status and not placed (or eligibility v2 for the selected drive); applications =
rows in scope (status filter applies), applicants / shortlisted / selected = DISTINCT students; offers by status;
placed = distinct students with a selected application or accepted offer on a drive in scope (P9-D4); placement % =
placed ÷ (active-status students ∪ placed) (P9-D9). Company-, department- and drive-wise breakdowns use the same
distinct-student sets. `GET /placement/analytics/options` returns filter choices.

**CSV export** `GET /placement/analytics/export?kind=summary|companies|departments|drives|applications` (+ the same
filters): tpo/admin only; every cell passes `safe_cell` (leading `= + - @ TAB CR LF` ⇒ `'` prefix; numbers stay
numbers); UTF-8 BOM, `no-store`, `nosniff`; the student list carries name, roll number, department, graduation year,
company, role, drive id, status and dates — **no email, phone or marks**. Audited `placement.export` with kind, row
count and filter NAMES only.

**AI Mock Interview connection (no duplication).** `GET /placement/drives/{id}/preparation` (student, drafts 404)
reuses the existing gates (`mock_interview_enabled_for`, `company_enabled_for`) and returns: practice availability,
the drive's PUBLISHED company profile (Phase 8.4) when company profiles are on, a link
`/dashboard/mock-interview?prep_role=…&prep_company=…[&prep_profile=…]`, the student's OWN completed-practice count
and their campaigns linked to the drive. `InterviewSetupForm` reads those parameters (bounded, control characters
stripped) only in builds with the placement flag and only as initial values; the student still reviews and submits
and the server validates as before. TPO side: campaigns can be linked to a drive and target its applicants by status.
Faculty never receive interview content (§13).

## 15. Batch 9.5 — security, PostgreSQL, performance, frontend quality, acceptance (2026-11-24)

**Alembic head:** `e1a3c5b7d9f4` (single head). Chain: `b6d8f0a2c4e3` → `c8e2a4f6b1d9` (9.1) → `d9f1a3c5e7b2` (9.2)
→ `e1a3c5b7d9f4` (9.3).

### 15.1 Test evidence (actual; code version = working tree on top of `2ccc8b9`, before commit `9217d8d`)

| # | Command / check | Result | Exit | Still valid? |
|---|---|---|---|---|
| 1 | Focused `test_placement_management_92.py` (first run) | 25 passed | 0 | superseded by 3, 4 |
| 2 | Affected suites: 9.1 + 9.2 placement, analytics, tenant isolation, notifications, moat, invites | 87 passed, 0 failed | 0 | yes (no later change to those modules except CORS, see 4) |
| 3 | `test_placement_campaigns_insights.py` | 13 passed (after 2 test-only fixes and 1 product fix: `paginate_rows` row unpacking) | 0 | yes |
| 4 | **Full backend SQLite suite** (implementation frozen) | **725 passed, 34 skipped, 0 failed** (49 min) | 0 | yes, except `main.py` CORS change made afterwards → covered by 5 |
| 5 | `test_csrf.py`, `test_config_hardening.py`, `test_placement_management_92.py` (incl. new CORS test) after the CORS change | 78 passed, 0 failed | 0 | yes |
| 6 | Migration on scratch SQLite: ORM schema minus new tables → stamp `c8e2a4f6b1d9` → upgrade head → downgrade ×2 → upgrade head; `alembic heads` | all OK; one head `e1a3c5b7d9f4`; 9 index names match the ORM; withdrawn CHECK added/removed | 0 | yes |
| 7 | Downgrade refusal with a withdrawn row present | `RuntimeError: Cannot downgrade d9f1a3c5e7b2 …`; data untouched | 1 (expected) | yes |
| 8 | Frontend unit suite `npm run test:unit` | **200 / 200 passed** (187 existing + 13 new) | 0 | yes (later frontend edits were CSS classes + one link; tsc/lint/build rerun) |
| 9 | `npx tsc --noEmit` (final code) | no errors | 0 | yes |
| 10 | `npm run lint` | 0 errors, 2 pre-existing warnings in untouched `e2e/smoke.spec.ts` | 0 | yes |
| 11 | `next build` with `NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED=true` (final code) | compiled; 8 hub routes built | 0 | yes |
| 12 | Browser smoke (Playwright library, headless Chromium, local throwaway servers + synthetic SQLite seed; 4 roles × 10/3/2/3 pages × 320/390/768/1366 px + flows) | 94 checks: 84 passed, 10 failed — **all 10 pre-existing** (legacy `/dashboard/applications`: 2 unlabeled selects ×4 widths + 8 px; 3 px at 320 px from the existing `AppShell` header, also on legacy `/dashboard/placements`). All 12 flow/keyboard checks passed in this final run: keyboard focus (4 roles), student withdraw via UI, practice link pre-filled with role and company, TPO bulk preview (8 movable) + confirm, import preview (1 valid / 2 errors) + commit, CSV export download, teacher view shows completion only | 1 (pre-existing items) | yes |
| 13 | Synthetic performance (2 000 students, 30 drives, 6 000 applications, 500-student campaign; in-process; 8 threads) | see 15.3 | 0 | yes |
| 14 | `git diff --check` + trailing-whitespace scan of new files | clean | 0 | yes |
| 15 | **PostgreSQL** (disposable PostgreSQL 16.16; details §16): migrations 61-chain empty → `e1a3c5b7d9f4` + up/down/up; RLS suites incl. `test_placement_management_rls_postgres.py` + `test_placement_phase9_rls_postgres.py`; full `tests_pg_api`; ad-hoc Phase 9 API check | migrations OK; RLS **34 passed / 0 failed**; `tests_pg_api` **237 passed / 2 skipped (Redis) / 0 failed**; ad-hoc **5 passed** | 0 | yes (was BLOCKED / NOT RUN at first; run after owner approval) |

### 15.2 Security review (self-review of the full 9.2–9.4 diff; not an independent agent)
Checked per new route: gate (flag + allow-list, 404 off) and tenant GUC on all three routers; recruiters 403;
role matrix (tests per route); every query filtered by `tenant_id`; foreign drive/application/student/department/
section/campaign ids ⇒ 404 or the same 422 message as unknown ids (no oracle); students only own records; teachers
only own campaigns and their timetable sections; academic data writable only by TPO/admin (profile editor, import)
and never by students; verified metrics and placement status not importable; bulk and withdrawal race-safe
(compare-and-set; parallel duplicate apply ⇒ one 201); `withdrawn` unreachable for staff on every route; audit rows
content-free (ids, statuses, counts, field/filter NAMES); no logging of spreadsheet content; CSV formula guard on
every cell; export without contact details; XLSX parser bounded (no entity expansion); LIKE wildcards escaped;
React renders all data as text (unit test forbids `dangerouslySetInnerHTML`). Faculty interview privacy proven at
response and SQL level (§13). Defects found and fixed during 9.5: `paginate_rows` rows not unpacked (campaign list
and candidates 500), legacy deadline would have been wiped by a full-replacement drive edit (form now keeps it),
company filter not whitespace-insensitive, count headers invisible cross-origin (CORS `expose_headers`), narrow
viewport overflow from hub scrollers and an escaping visually-hidden header (inline-size containment + `relative`).

### 15.3 Performance (SQLite, single process, Windows dev machine; indicative only, not a production benchmark)

| Endpoint | p50 ms | p95 ms |
|---|---|---|
| Staff drive list | 12 | 609 (first-request warm-up) |
| Eligibility list, 2 000 students | 118 | 230 |
| Applicants v2 (~200) | 26 | 147 |
| Student opportunities / my-eligibility | 14 / 12 | 17 / 14 |
| TPO dashboard | 51 | 164 |
| Analytics (no filter / drive filter with v2 eligibility) | 193 / 97 | 258 / 229 |
| Campaign progress (500 assigned) | 14 | 17 |
| CSV export, ~6 000 application rows | 303 | 306 |
| Apply ×64 at 8 threads | 174 | 243 (38×201, 26×403 ineligible, no 5xx; 1.4 s wall) |
| Bulk preview / apply 200 | 12 / 141 | — (200/200 applied) |
| Import 2 000 rows preview / commit | 84 / 204 | 190 / — |

Postgres latency, multi-worker load and network are NOT measured (Phase 10).

### 15.4 PostgreSQL gate — PASSED (see §16)
*Original status at 9.5 time, kept for history:* BLOCKED / NOT RUN. No `RLS_TEST_DATABASE_URL`, nothing listening on 5432, no running container, no local `psql`. Nothing was
installed or started (owner approval required). Not verified by execution: RLS/FORCE/policies on the six Phase 9
tables, the composite FKs, the `withdrawn` enum migration on Postgres (`ALTER TYPE … ADD VALUE` in an autocommit
block), true parallel compare-and-set. Required: a local throwaway Postgres 15+, migrated to head as the owner role,
tests run as the NOBYPASSRLS app role: `RLS_TEST_DATABASE_URL=… pytest tests/test_placement_management_rls_postgres.py
tests/test_placement_phase9_rls_postgres.py`. **Resolved 2026-11-24:** run on an owner-approved disposable
container and passed (§16).

### 15.5 Acceptance
Phase 9 functionality (9.1–9.4) is implemented and verified on SQLite, in the browser and by synthetic load.
~~Phase 9 is NOT fully accepted: the PostgreSQL gate (15.4) is not run.~~ **Updated 2026-11-24:** the PostgreSQL gate
passed (§16), so all Phase 9 acceptance gates in this spec have passed. Still outside Phase 9: real-device /
screen-reader checks and the production integration audit (Phase 10). The 10 browser-smoke failures are pre-existing
and were NOT fixed. Nothing is committed, pushed or deployed; the deployed CampusAI site is unchanged.
**Superseding status (2026-11-24, §19):** implementation committed (`9217d8d`); **final acceptance/closure pending** —
the verification gates above passed, but the §19 closure tasks are open, so Phase 9 is NOT marked complete.

### 15.6 Known limitations
1. ~~PostgreSQL gate not run (15.4).~~ Resolved: PostgreSQL gate passed (§16). The 2 Redis queue tests in
   `tests_pg_api` stay skipped (isolated Redis required) — not run, not passed.
2. ~~Pre-existing: fixed sidebar, header 3 px, unlabeled selects~~ **Resolved 2026-11-25 (§20.6, §20.7).** Original text: the dashboard `AppShell` sidebar is fixed (240 px), so every dashboard page — legacy and hub — scrolls
   horizontally below ~768 px; the header adds 3 px at 320 px; the legacy applications page has two unlabeled
   selects. Hub content itself fits 320–1366 px when the shell allows it. Making the shell responsive changes the
   existing site layout ⇒ Phase 10 decision.
3. Applications made before an institute was enabled have no history until their next change (unchanged from 9.1).
4. Campaign completion is a time-window rule: one completed practice can satisfy overlapping campaigns.
5. A practice link carrying a company profile that is archived before the student submits is rejected by the server
   with a visible message (the student can choose "none").
6. Import commit is last-writer-wins per field against concurrent single-profile edits (the whole import is atomic).
7. Placement analytics/dashboard load the institute's rows in memory (fine at 2 000 students / 6 000 applications;
   re-measure on Postgres at larger scale).
8. Browser checks are automated (keyboard focus, labels, names, overflow, errors); no screen-reader or real-device
   test (Phase 10 checklist).

## 16. PostgreSQL verification (2026-11-24; owner-approved disposable container)

**Environment.** One disposable local container from the already-present `postgres:16` image: **PostgreSQL 16.16**,
`--rm`, data on tmpfs (memory), no volumes or mounts, port bound to `127.0.0.1` only; random test-only owner
credentials, app-role password and database name, never printed and deleted afterwards. Every database variable the
tests read (`DATABASE_URL`, `MIGRATION_DATABASE_URL`, `RLS_TEST_DATABASE_URL`, `EXPECT_DATABASE`, `EXPECT_ROLE`,
`EXPECT_SYSID`) pointed only at this container; `tests_pg_api`'s fail-closed identity check (database, role, cluster
system identifier) passed. Migrations ran as the owner role; tests as `app_user` created per
`backend/scripts/app_role.template.sql` and verified NOSUPERUSER / NOBYPASSRLS / NOCREATEROLE / NOCREATEDB, owning no
tables. No repository file was changed by the run.

| Check | Result |
|---|---|
| Migrations, empty database → head (second throwaway database in the same container, dropped afterwards) | 61 migrations, exit 0, head `e1a3c5b7d9f4` |
| `alembic downgrade -2` → `upgrade head` (incl. `withdrawn` enum migration in an autocommit block) | passed; single head |
| RLS posture | all six Phase 9 tables (`student_placement_profiles`, `placement_drive_settings`, `placement_drive_departments`, `placement_application_events`, `placement_campaigns`, `placement_campaign_students`) RLS enabled + forced, `tenant_isolation` policy; enum value `withdrawn` present |
| RLS suites as `app_user`: `test_placement_management_rls_postgres.py`, `test_placement_phase9_rls_postgres.py`, `test_rls_postgres.py`, `test_mock_interview_rls_postgres.py`, `test_mock_interview_coding_rls_postgres.py`, `test_mock_interview_batch2_rls_postgres.py` | **34 passed, 0 failed, 0 skipped** (all 10 Phase 9 tests passed) |
| Full `tests_pg_api` as `app_user` (`pytest tests_pg_api --confcutdir=tests_pg_api`) | **237 passed, 2 skipped, 0 failed** (24 min). Skips: the existing Redis queue tests in `test_core_flows.py` (`RQ_TEST_REDIS_URL` not set; isolated Redis required) — **not run, not passed** |
| **Ad-hoc Phase 9 API verification — a scratchpad file, NOT a committed repository test** (reuses `tests_pg_api` fixtures as a plugin) | **5 passed**: flag gating; v2 lifecycle + apply under RLS; cross-institute 404s (drives, applicants, bulk, withdraw, history, campaigns, analytics); withdrawal + history; campaign completion-only progress; import / analytics / export / dashboard isolation; truly parallel requests: **8 duplicate applications ⇒ 1 success + 7 rejections**; **simultaneous bulk shortlist vs withdrawal ⇒ exactly one winner and one recorded history event** |

**Safety.** The temporary container was removed by its exact ID (`9828efa9727e`); its database lived in memory.
All 16 pre-existing Docker containers were unchanged (IDs, names, statuses); no volume, image or network was created
or removed; no production database, credentials or settings were touched.

**Incidents (resolved).** The first migration attempt was blocked by a Windows Application Control policy loading
the psycopg DLL; each step was re-run cleanly and the full chain proven on a fresh database. The first ad-hoc run
loaded the repository-root SQLite conftest by mistake and errored at fixture setup before touching Postgres; it was
re-run with the correct `tests_pg_api` conftest.

**Observed, pre-existing (not Phase 9).** `users`, `leads`, `feedback`, `refresh_tokens` and `tenant_invites` are not
RLS-forced (`users` deliberately; app-layer guards). Phase 8 G2 status is unchanged (NOT passed).

**Acceptance (superseded by §19: final acceptance/closure pending).** With this gate passed, the verification gates in this spec had passed: SQLite backend
725 / 34 / 0; frontend 200/200, tsc, lint, build; browser smoke 84/94 (10 pre-existing failures, not fixed; all 12
placement flow/keyboard checks passed); synthetic load at 2 000 students; PostgreSQL as above. Remaining for Phase 10:
real devices / screen readers, the pre-existing fixed `AppShell` sidebar (hub content fits 320 px, but the shell
scrolls horizontally below 768 px), and the production integration audit. Batches 9.2–9.5 are committed locally
as `9217d8d` (§18).

## 17. Final review fixes M1 and L1 (2026-11-24; owner-approved; verified — committed locally as `9217d8d`)

The read-only review of the 50 staged files (verdict PASS WITH WARNINGS; no critical/high findings) raised M1 and
L1; the owner approved fixing these two only. L2–L5 (cosmetic legacy buttons on withdrawn rows, 4 KB DOCTYPE scan
window mitigated by ENTITY rejection, query-count growth in three lists, stale table name in §3) remain recorded,
not fixed. At the time of this record the fixes were unstaged on top of the 50 staged files; they were then
staged with owner approval and committed together with 9.2–9.5 in `9217d8d` (§18).

**M1 — legacy placement analytics (`backend/app/routers/placement_analytics.py`).** Root cause: `/placement/analytics/
overview` loaded every application, so `withdrawn` rows inflated `total_applications`, `unique_applicants`, per-drive
`applicants` and the selection-rate denominator while the funnel buckets (applied / shortlisted / selected /
rejected) ignored them. Fix: the query excludes `withdrawn` (`Application.status != withdrawn`), so totals,
applicants, per-drive and company totals and selection-rate denominators come from the same population as the
buckets. Response shape unchanged; institutes without placement management never have withdrawn rows, so their
numbers are identical. Phase 9 analytics (§14) untouched.

**L1 — offer / withdrawal race (`services/placement_management.py`, `routers/recruiters.py`).** Root cause: the
withdrawal checked offers and placement before its update, and the offer/placement writers took no shared lock, so a
competing writer could commit in between. Fix: `lock_student_for_placement()` takes `SELECT … FOR UPDATE` on the
student's `users` row (same pattern as `mock_interview.create_session`). Every conflicting writer takes it first and
re-checks state after acquiring it:
- student withdrawal (re-reads the application, offers and placed state);
- recruiter offer extension / re-extension (re-checks the application is still visible; withdrawn ⇒ 403, no offer);
- student offer acceptance (re-reads the offer; no longer `extended` ⇒ 409);
- TPO status change incl. selection (if the application changed while waiting ⇒ the existing 409 "changed by someone
  else"; a request is never reinterpreted against another staff member's decision — found and fixed during this work).
Lock order is always student row first, then application/offer rows (deadlock-free). Not locked, by design: bulk
shortlist/reject (cannot place a student; same-application races stay covered by compare-and-set) and the legacy
status route with the flag OFF (withdrawal is 404 when OFF, so it cannot compete). Result: no application can end
withdrawn while it has an open or accepted offer; a withdrawal still records exactly one history event and one
audit row; a refused operation records none.

**Files (committed in `9217d8d`):** modified `backend/app/routers/placement_analytics.py`, `backend/app/routers/recruiters.py`,
`backend/app/services/placement_management.py`; new `backend/tests/test_placement_review_fixes.py` (12 tests),
`backend/tests_pg_api/test_placement_offer_withdraw_lock_pg.py` (2 opt-in Postgres tests).

| Check (final code) | Result |
|---|---|
| New M1/L1 tests (SQLite) | **12 passed**: 2 M1 (mixed applied/shortlisted/selected/rejected/withdrawn on two drives — totals = sum of buckets, withdrawn-only students not applicants, selection rates 25 % / 100 % not 1/6 / 1/2; unchanged results without withdrawn rows), 9 L1 (offer first ⇒ withdraw 409, app stays active; withdrawal first ⇒ offer 403, no offer; offer re-check after lock; acceptance and selection elsewhere ⇒ "already placed"; acceptance re-check after lock; end-to-end state never withdrawn-with-live-offer; all 5 writer paths take the lock), 1 regression (concurrent decision never overridden) |
| Affected suites (all Phase 9 files, analytics, tenant isolation, recruiter/offer, mock-interview admin/flags) | **139 passed, 0 failed** (an earlier run found the reinterpretation regression above; fixed and re-run) |
| **Real PostgreSQL lock tests** — disposable PostgreSQL 16.16 container (`--rm`, tmpfs, 127.0.0.1 only, random credentials deleted afterwards, removed by exact ID; 16 pre-existing containers unchanged), 61 migrations to `e1a3c5b7d9f4`, run as NOBYPASSRLS `app_user` with the fail-closed identity check | **2 passed, 0 skipped**. A second connection held the student-row lock; the withdrawal was observed **waiting on a lock in `pg_stat_activity`** and had not returned; after the holder committed (open offer / selection elsewhere) it resumed, re-checked and returned 409; application unchanged, no withdrawal event |
| **Full backend suite** (run after the final M1/L1 fixes; Postgres variables unset; isolated temp SQLite) | **737 passed, 34 skipped, 0 failed, 0 errors** (55 min 29 s). The 34 skips are the opt-in PostgreSQL-specific tests |
| Ruff | clean on the 3 changed source files and the PostgreSQL test; the new SQLite test file has 23 `F811` notices (the repository's fixture-import pattern, as in the existing Phase 9 test files) |
| Python compilation / `git diff --check` / `git diff --cached --check` | passed / clean / clean |

**Still not run / limitations.** The two Redis queue tests in the full `tests_pg_api` run (§16) remain unrun (no
isolated Redis). No real production traffic or multi-worker production load was tested. Earlier items are unchanged:
browser smoke 84/94 with 10 pre-existing failures (not fixed); the pre-existing fixed `AppShell` sidebar scrolls
horizontally below 768 px (hub content fits at 320 px); real devices / screen readers and the production integration
audit are Phase 10. Phase 8 G2 remains NOT passed.

## 18. Phase 9 commit (2026-11-24; local only)

| | |
|---|---|
| Commit | `9217d8d2ffc5200146cb7ad08f10471cc2b9d794` |
| Subject | `feat(placement): add placement hub, campaigns, analytics and acceptance checks` |
| Parent | `2ccc8b9c8b136c2d14d5b586c705d3d0df6b2c65` (Batch 9.1, which remains in that commit) |
| Branch | `feature/ai-mock-interview` |
| Content | 54 files, 10 218 insertions, 21 deletions — Batches 9.2–9.5, review fixes M1/L1 with their tests, Phase 9 docs |
| Status | committed locally; **not pushed, merged or deployed**; live CampusAI unchanged |

Evidence at commit time: full backend suite 737 passed / 34 skipped (PostgreSQL-specific) / 0 failed / 0 errors;
PostgreSQL gate passed (§16) and PostgreSQL lock tests 2 passed (§17); frontend 200/200, tsc, lint, build (§15).
Still unrun: the 2 Redis queue tests in `tests_pg_api`. Still open: review items L2–L5; 10 pre-existing browser-smoke
failures; the fixed `AppShell` sidebar scrolls horizontally below 768 px; real devices / screen readers and the
production integration audit (Phase 10). Not in the commit by design: the user's Phase 5 `frontend/Dockerfile` /
`frontend/next.config.ts` edits and all local-only files. Phase 8 G2 remains NOT PASSED.

## 19. Phase 9 status and remaining closure tasks (2026-11-24)

**Phase 9 — IMPLEMENTATION COMMITTED, FINAL ACCEPTANCE/CLOSURE PENDING.** Not marked complete.

**Superseded 2026-11-25 (§20): PHASE 9 — FINAL ACCEPTANCE COMPLETE** — tasks 1–7 below done and verified (§20);
task 8 (documentation commit) awaits owner approval together with the uncommitted closure code. The original task
list is kept below as history.

**Completed and committed.** 9.1 placement core (`2ccc8b9`); 9.2–9.5 main implementation — Placement Hub, TPO and
student workflows, faculty campaigns and TPO dashboard, placement analytics and exports — plus M1 (legacy analytics
exclude withdrawn) and L1 (shared per-student offer/withdrawal lock), main implementation commit
`9217d8d2ffc5200146cb7ad08f10471cc2b9d794` (§18). M1 and L1 are closed.

**Evidence.** Full backend 737 passed / 34 skipped / 0 failed; PostgreSQL lock tests 2 passed; PostgreSQL RLS 34
passed; full PostgreSQL API suite 237 passed, 2 Redis-related tests unrun; frontend unit 200/200; TypeScript, lint and
production build passed; browser smoke 84/94 (10 failures reported as pre-existing, unresolved); Placement Hub pages fit
at 320 px but the existing sidebar scrolls horizontally below 768 px.

**Open closure tasks (none complete):**

| # | Task | Notes |
|---|---|---|
| 1 | L2 — disable inappropriate action buttons on withdrawn rows in the legacy TPO pages | `dashboard/drives/[id]/page.tsx`, `dashboard/applications/page.tsx`; server already refuses (409) |
| 2 | L3 — review/improve the spreadsheet DOCTYPE scan limitation | `services/placement_import.py` (4 KB DOCTYPE window; ENTITY rejection mitigates) |
| 3 | L4 — review query count / performance | campaign list `counts_for`, legacy applicant list `_V2Eligibility`, analytics offer loading `load_scope`; fix only if evidence shows a meaningful issue, with tests |
| 4 | L5 — correct the outdated campaign table name | §3 says `mock_interview_campaigns`; actual `placement_campaigns` / `placement_campaign_students` (left uncorrected on purpose until this task) |
| 5 | Run the 2 Redis queue tests with an isolated Redis test service | `tests_pg_api/test_core_flows.py` (`RQ_TEST_REDIS_URL`) |
| 6 | Investigate the 10 browser-smoke failures; determine which are genuinely pre-existing | §15.1 row 12 |
| 7 | Fix and verify mobile sidebar horizontal scrolling below 768 px | existing `AppShell` |
| 8 | Commit the post-implementation documentation changes separately | `PROJECT_STATE.md` and this spec have uncommitted updates after `9217d8d` |

**Recommended order:** A. L2 + L3 → B. L4 (if evidence confirms; with tests) → C. L5 docs → D. Redis queue tests
(isolated) → E. browser-smoke investigation → F. mobile sidebar fix + test → G. rerun relevant regression and
acceptance tests → H. update docs with final evidence → I. review and separately approve the documentation commit →
J. only then assess whether the Phase 9 acceptance criteria are fully satisfied.

**Boundaries.** Phase 10 is not started. Phase 8 G2 remains **NOT PASSED**; student code execution is not approved.

## 20. Phase 9 closure tasks L2–L8 (2026-11-25; implemented and verified — UNCOMMITTED, not staged)

Base: HEAD `9217d8d2ffc5200146cb7ad08f10471cc2b9d794`. All changes below are working-tree changes only (nothing staged,
committed, pushed or deployed). No migration, no schema/RLS change, no API response change, no new dependency, no
flag change (all placement flags still default OFF).

### 20.1 L2 — withdrawn rows on the legacy TPO pages
`dashboard/drives/[id]` and `dashboard/applications` now render their Shortlist / Select / Reject row through one
shared component `components/applications/LegacyDecisionButtons.tsx`; rules in `lib/legacyApplicationActions.ts`.
Withdrawn ⇒ all three natively `disabled` (not focusable, not activatable by mouse/Enter/Space), a visible note
"Withdrawn by the student. Withdrawal is final…" referenced by `aria-describedby`, and `decide()` returns early for a
withdrawn row (no request even if called). Every other status keeps the existing rule (only the same-status button
is disabled). Status badge and verified snapshot unchanged. The applications page gained a "Withdrawn" status-filter
option (the backend already accepted it). Backend contract unchanged (it already refused with 409).
Tests: `frontend/tests/unit/legacyApplicationActions.test.mjs` (7) + browser checks (§20.6).

### 20.2 L3 — spreadsheet XML / DOCTYPE security (`services/placement_import.py`)
Pipeline: upload → extension/content-type/size(2 MB)/magic checks → CSV (`csv` module, no XML) or XLSX (`zipfile`
with member/size/ratio limits → `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/sharedStrings.xml`, first
worksheet, each parsed by stdlib `ElementTree`). Only these four parts are ever parsed; all go through `_xml()`.
**Defects found (reproduced before fixing):** (a) DOCTYPE scanned in the first 4 KB only — a DOCTYPE (incl. an
external `SYSTEM`/`PUBLIC` DTD) after 4 KB of prolog was accepted; (b) both byte scans were ASCII-only — a **UTF-16**
part with `<!DOCTYPE …[<!ENTITY …>]>` passed both checks and the entity **was expanded** by ElementTree.
Stdlib expat did not fetch external resources in either case, but such input must never be accepted.
**Fix (stdlib only, no new dependency):** `_guard_xml()` runs before `ElementTree` on the WHOLE part: the cheap byte
scan now covers the whole part, then a `pyexpat` pass (decodes BOM / encoding declaration exactly like the real
parse) aborts on the first DOCTYPE, ENTITY, NOTATION or unparsed-entity declaration and on any external entity
reference; parameter-entity parsing is set to NEVER. Malformed XML ⇒ "damaged" (400). Limits unchanged.
Tests: `backend/tests/test_placement_closure_import_xml.py` — **31 passed**: DOCTYPE in / after the first 4 KB, mixed
case, external DTD (SYSTEM/PUBLIC, after 4 KB and after PIs), external / parameter / internal entities, NOTATION,
billion laughs, UTF-16 (BOM, LE, BE) DTDs, DTD in sharedStrings / workbook / rels, DOCTYPE after the root, malformed
parts, legitimate UTF-8 / UTF-8-BOM / UTF-16 / no-declaration parts, escaped `&lt;!DOCTYPE` text in a cell (no false
positive), Excel-shaped workbook, unchanged limits, refused uploads (preview AND direct commit with a matching hash)
change no record and write no audit row, valid XLSX commit with blank cells keeping stored values, valid CSV import.
Against the old guard (monkeypatched in, scratchpad only) **9 of these tests fail**, so they detect the defect.

### 20.3 L4 — placement query performance
Baseline and result measured on synthetic SQLite data only (institute A: 2 000 students, 30 drives, 6 000
applications, 814 offers, 1 457 sessions, 25 campaigns × 120 students; institute B: 600 students, 1 200 applications,
173 offers, 6 campaigns). Query counts are deterministic; timings (p50, Windows dev box) are indicative and noisy.

| Endpoint | Queries before → after | Rows / notes | p50 ms before → after |
|---|---|---|---|
| Campaign list, 25 per page | **30 → 6** | was 5 + 1 per campaign | 31.6 → 16.5 |
| Campaign list, 10 per page | 15 → 6 | constant | 16.3 → 12.3 |
| TPO dashboard (20 active campaigns) | **31 → 12** | was 11 + 1 per ACTIVE campaign (unbounded) | 49.1 → 41.7 |
| Legacy applicant list, flag ON, 200 applicants | **411 → 13** | was ~2 per applicant | 145.9 → 18.3 |
| Legacy applicant list, flag OFF, 200 applicants | 1 203 → 1 004 | user lookup batched; the remaining per-applicant legacy profile queries are the untouched flag-off path | 319 → 287 |
| Analytics, drive filter | 16 → 16 | **offer rows read 814 → 21** | 60.7 → 55.5 |
| Analytics, company filter | 10 → 10 | offer rows 814 → 79 | 48.0 → 43.5 |
| Analytics / export, no drive-narrowing filter | unchanged | 814 offers (all in scope — plain query kept) | noise only |

Changes: `services/placement_campaigns.counts_for` — two queries for any number of campaigns (assignments, then the
assigned students' completed sessions since the earliest start), per-campaign rule applied in memory; a first
single-SQL-join version was rejected because SQLite planned it via the low-selectivity `tenant_id` index (campaign
list 31 → 304 ms). `routers/placement.drive_applications` — one tenant-scoped user query (same id+tenant rule as
`get_scoped_user`, foreign rows still skipped) and `_V2Eligibility` resolves departments once.
`services/placement_insights.load_scope` — with a drive/company/academic-year filter only offers on applications of
in-scope drives are read; the Python student-scope filter is kept, so the set is identical.
Not changed (evidence: bounded): a student's preparation card loops over that student's campaigns for one drive.
**Correctness:** an equivalence run on the same data compared old (HEAD code) vs new — `counts_for` identical for all
31 campaigns of both institutes; 5 campaign-list/dashboard bodies byte-identical; offer sets identical for 6 filter
combinations; 5 analytics/export bodies byte-identical. Tests: `backend/tests/test_placement_closure_queries.py`
— **6 passed** (counts = per-campaign reference incl. late completions and interview types; institute-B rows that
reuse A student ids never count; constant query counts for counts_for, list, dashboard and the legacy list; foreign
rows skipped; drive filter reads only in-scope offers, never another institute's; per-drive offer totals add up).
The 2 000-student load evidence of §15.3 is kept; the table above is the fresh check for the changed queries.

### 20.4 L5 — specification table name
§3 row 9.3 corrected to `placement_campaigns` + `placement_campaign_students` (verified against
`app/models/placement_campaigns.py` and migration `e1a3c5b7d9f4`; no `mock_interview_campaign*` table exists); the
original planned names are kept in that row as history. No other wrong reference in this spec. No table renamed, no
migration. Note: `IMPLEMENTATION_PLAN.md` (original pre-Phase-7 plan, kept as history) still lists the old planned names.

### 20.5 L6 — Redis queue tests (isolated)
The two skipped tests: `tests_pg_api/test_core_flows.py::test_queue_path_job_carries_server_tenant_and_persists`
and `::test_queue_path_mismatched_or_missing_tenant_job_does_nothing` (they need `RQ_TEST_REDIS_URL` AND the
disposable Postgres). Environment: one container from the already-local Redis image `7f7d30b7fdd0` (Redis 7.4.12,
untagged; no pull) with a random `requirepass`, `--save "" --appendonly no`, `/data` on tmpfs, port on 127.0.0.1 only;
one container from the local `postgres:16` (PostgreSQL 16.16), tmpfs data, 127.0.0.1 only, random database name and
credentials (kept in a private scratchpad file, never printed). 61 migrations empty → `e1a3c5b7d9f4` as the owner;
`app_user` created per `scripts/app_role.template.sql` (NOSUPERUSER / NOBYPASSRLS / NOCREATEROLE / NOCREATEDB, owns
no tables); six Phase 9 tables RLS enabled + forced; fail-closed identity check (database, role, system id) passed.
Result: **2 passed, 0 skipped** (the first proves a queued job carries the server tenant — not a smuggled body value —
and persists under RLS; the second proves jobs with a foreign or missing tenant do nothing).
Then, on the same disposable cluster as `app_user`: **full `tests_pg_api` 241 passed, 0 skipped, 0 failed** (the
§16 run's 237 + the 2 L1 lock tests added in `9217d8d` + the 2 Redis tests, now run) and the six RLS suites
(`test_placement_management_rls_postgres.py`, `test_placement_phase9_rls_postgres.py`, `test_rls_postgres.py`,
`test_mock_interview_rls_postgres.py`, `test_mock_interview_coding_rls_postgres.py`,
`test_mock_interview_batch2_rls_postgres.py`) **34 passed, 0 failed, 0 skipped**. The suite's cleanup left 0 marked rows.
**Docker safety:** temporary containers `e071d3505dc8` (Postgres) and `9f982b551b21` (Redis) removed by exact ID and
verified gone; credential files deleted. Before/after: the same 16 containers (IDs, names, exit status), 31 volumes,
65 images and 9 networks (names); 8 non-default networks keep their IDs. The daemon-managed default `bridge` network
has a new ID: the Docker engine (re)created it at 02:27:07 UTC, one second before the first `docker run` (engine
wake-up); no network command was issued. Nothing pulled, no volume attached, Compose stack untouched.

### 20.6 L7 — the 10 browser-smoke failures
The original result file of the 84/94 run (`hub_smoke_results.json`, previous session scratchpad) was recovered, so
the ten failures are known exactly. Attribution: `git diff 7d4147c 9217d8d` shows Phase 9 changed only the status
type/style of `dashboard/applications/page.tsx` and added one nav entry to `AppShell.tsx`; the two selects, the
decision-button row and the shell header all come from the initial release `f6de3d5`.

| # | Original failure | Cause | Class | Resolution |
|---|---|---|---|---|
| 1 | tpo `/dashboard/applications` @320: content overflow 8 px | non-wrapping Shortlist/Select/Reject row + 32 px page padding (initial release) | B — existing unrelated | fixed: shared button row wraps (L2), mobile padding 16 px (L8) |
| 2–5 | tpo `/dashboard/applications` @320/390/768/1366: two unlabeled `<select>` | no label/aria-label since initial release | B — existing unrelated | fixed: `aria-label` "Drive" / "Filter by status" |
| 6 | teacher `/dashboard/placement-hub` @320: overflow 3 px | `AppShell` header (32 px padding + role pill) too wide for "teacher dashboard" | B | fixed by the L8 shell change |
| 7 | teacher `/dashboard/placement-hub/campaigns` @320: 3 px | same header | B | fixed (L8) |
| 8 | student `/dashboard/placement-hub` @320: 3 px | same header | B | fixed (L8) |
| 9 | student `/dashboard/placement-hub/opportunities/1` @320: 3 px | same header | B | fixed (L8) |
| 10 | student `/dashboard/placements` (legacy) @320: 3 px | same header (legacy page — proves not hub-specific) | B | fixed (L8) |

None was caused by Phase 9 (class A: 0), none flaky (C: 0; deterministic layout/attribute facts), none
environment (D: 0). No test was skipped, weakened or changed: the **original script was rerun unchanged** on the final
code with the same synthetic seed — **84 checks, 84 passed, 0 failed** (the 10 failure records no longer occur; all
12 flow/keyboard checks passed: keyboard focus ×4, bulk preview 8 movable + confirm, import preview 1 valid / 2 errors
+ commit, CSV download, teacher completion-only view, student withdraw, practice link pre-filled), and its
informational "shell overflow" list went from **36 cases to 0**. Production build with the placement and mock
interview flags ON passed before the runs.

### 20.7 L8 — mobile navigation (`components/AppShell.tsx`, one shared change)
From 768 px (`md`) the sidebar is the same static 240 px column. Below 768 px it is an off-canvas drawer, `hidden`
(not visible, not focusable) until the header menu button opens it (`aria-label` "Open navigation",
`aria-expanded`, `aria-controls="app-sidebar"`); the drawer is labelled "Main navigation" and has a "Close
navigation" button. On open focus moves into the drawer and Tab/Shift+Tab stay inside it; the main column is
`inert`; Escape, the backdrop, the close button or a link closes it and focus returns to the menu button (restored
after the close commits — the first version focused the still-inert button; found by the browser check and fixed);
resizing to ≥ 768 px closes it. Main column `min-w-0`, mobile padding 16 px, header title truncates, role pill from
640 px (the title already names the role). Active link carries `aria-current="page"`.
**Browser checks (separate scratchpad script, local throwaway servers, synthetic data): 280 checks, 280 passed** —
4 roles (student, teacher, TPO, admin) × 2–5 pages (hub and legacy) × widths **320, 360, 375, 390, 414, 767** (no
horizontal page scroll with the sidebar NOT hidden by the test; drawer closed; menu button shown) and **768, 1024,
1366** (static 240 px sidebar, no menu button, no backdrop); per role: Enter opens the drawer (expanded, backdrop,
focus inside, page inert), 25 Tab + 25 Shift+Tab never leave it, Escape / backdrop / close button close it with focus
restored, a drawer link navigates and closes it, resize to desktop leaves no overlay, no page errors; plus the L2
checks on both legacy pages (withdrawn row: 3 disabled buttons, note + `aria-describedby`, badge kept, Tab never
lands on them, `focus()` cannot focus them, Enter/Space/forced click send **0** status requests; applied /
shortlisted / selected rows keep the old rule; "Withdrawn" filter works; Shortlist on an applied row still sends
exactly 1 request). Two first-run failures were test-script defects, fixed without loosening any assertion: the
keyboard step left focus on another row's enabled button (it now blurs first and asserts focus did not land on the
disabled button), and a list was read before its reload finished (now waits). Not done: real devices and screen
readers (Phase 10 checklist).

### 20.8 Final verification (final code)
| Check (final code) | Result |
|---|---|
| Full backend SQLite suite (PostgreSQL/Redis variables unset) | **774 passed, 34 skipped (opt-in PostgreSQL tests), 0 failed** — 49 min 34 s (= 737 before + 31 L3 + 6 L4) |
| Affected suites (all placement files incl. new ones, analytics, notifications, tenant + user isolation) | 147 passed, 10 skipped (opt-in PostgreSQL), 0 failed |
| PostgreSQL 16.16 (disposable): 61 migrations empty → `e1a3c5b7d9f4`; RLS suites; full `tests_pg_api` incl. Redis + L1 lock tests | migrations OK; **RLS 34 / 0 / 0**; **`tests_pg_api` 241 passed / 0 skipped / 0 failed** |
| Redis queue tests (isolated Redis 7.4.12) | **2 passed** |
| Frontend unit (`npm run test:unit`) | **207 / 207** (200 + 7 new) |
| `npx tsc --noEmit` / `npm run lint` | clean / 0 errors (2 pre-existing warnings in untouched `e2e/smoke.spec.ts`) |
| `next build` with placement + mock-interview flags ON (final code) | passed; 8 hub routes |
| Browser: original Phase 9 smoke script, unchanged | **84 / 84** (was 84 passed + 10 failure records); shell overflow cases 36 → 0 |
| Browser: closure checks L2 + L8 | **280 / 280** |
| L4 synthetic benchmark + old-vs-new equivalence (scratchpad, not repository tests) | see §20.3; all compared results identical |
| Ruff (4 changed source files + 2 new test files) / `py_compile` / `git diff --check` | clean / passed / clean |
| Read-only review of every change of this task (self-review, not an independent agent) | no defects found; checked: tenant filters kept on every new query, role gates untouched, no response shape change, flags untouched, XML guard runs before any parse, campaign code still reads only completion columns (privacy test passes), withdrawal/lock code untouched (M1/L1 tests pass), desktop layout classes equivalent |

**Accepted limitations (not Phase 9 blockers).** Real devices and screen readers are not tested (Phase 10 checklist);
browser checks are automated only. The legacy applicant list with the placement flag OFF still issues ~5 queries per
applicant (pre-existing legacy path, deliberately unchanged; flag-on path fixed). Timings are single-machine SQLite and
indicative only; PostgreSQL-scale latency remains a Phase 10 measurement. Earlier limitations 3–6 of §15.6 unchanged.

**Acceptance decision (2026-11-25): PHASE 9 — FINAL ACCEPTANCE COMPLETE** (with the accepted limitations above):
L2–L5 done and tested; both Redis tests passed; all 10 browser failures investigated, classified (all class B) and
resolved; mobile navigation fixed and tested at 320–767 px and ≥ 768 px; backend, PostgreSQL, frontend, build and
browser suites pass on the final code. **The closure changes are UNCOMMITTED working-tree changes** (nothing staged,
committed, pushed or deployed) and await the owner's explicit approval to stage and commit. Phase 8 G2 remains
**NOT PASSED**; Phase 10 is **NOT STARTED**.
