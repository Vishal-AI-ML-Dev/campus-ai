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
| **9.3 Faculty campaigns + TPO dashboard** | `mock_interview_campaigns` + `…_campaign_targets` (sections); teacher/TPO/admin assign; student "assigned" list; progress (**completion status only**, P9-D7); TPO dashboard home (pipeline, deadlines, pending decisions) | 9.1, P9-D7 |
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

## 10. Batch 9.1 record (2026-11-24; implemented, uncommitted)

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

**Review (self-review, not an independent agent).** Fixed: lifecycle note silently discarded (field removed);
whitespace-only note satisfied "note required" via the legacy route (now stripped); concurrent profile create
reported a misleading roll-number error (explicit roll-number pre-check + generic race message). Checked: every
query tenant-filtered; 404 cross-institute for drives, applications, students, departments (same message for
unknown and foreign department ids); recruiters 403; audit summaries contain ids, statuses and field names only;
LIKE wildcards escaped; compare-and-set for status and lifecycle; unique constraints map to 409.

**Known issues.** Legacy TPO applicant list (`GET /drives/{id}/applications`) still computes the legacy "eligible"
flag. Applications created before an institute is flagged have no history events (history starts at the next
change). Eligibility list evaluates all students in memory (fine for thousands; measured in 9.5).
## 11. Batch 9.1 final verification (2026-11-24; still uncommitted, nothing staged)

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
