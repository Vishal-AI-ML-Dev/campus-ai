# 4. Institute controls and quotas

Requires `AI_MOCK_INTERVIEW_ENABLED=true` **and** `AI_MOCK_INTERVIEW_OPS_ENABLED=true` (and the institute in
`AI_MOCK_INTERVIEW_TENANT_IDS` when that list is set). With ops off, stored controls are ignored and only the
built-in per-student limits apply.

## Who can do what (decision P7-D3)

| Role | View usage / controls / diagnostics | Change controls |
|---|---|---|
| Institute admin | own institute | yes (audited) |
| TPO | own institute | **no** (403) |
| Teacher, student, recruiter | no (403) | no |
| Another institute | no (404 / not counted) | no |

There is no cross-institute view (P7-D2; planned with the Phase 10 platform-operator role).

## Controls

| Control | Effect | Limits |
|---|---|---|
| Pause new interviews | New sessions → 403 "paused" | Running interviews always finish and get reports (R8) |
| Allow voice / video | Off removes the mode from the setup form and rejects it (422) | Can only turn **off**; the platform flags stay the ceiling (R6) |
| Interviews per student per day | Lower than the built-in 10 | 1..10 |
| Interviews per month (institute) | Cap on new sessions, calendar month UTC | ≤ `AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX` if set |
| AI tokens per month (institute) | Cap on provider-reported tokens (unknown usage counts as 0) | ≤ `AI_MOCK_INTERVIEW_TENANT_MONTHLY_TOKEN_MAX` if set |

- Values above the platform limit are rejected (422); institutes can only tighten (R7).
- Limits are checked **only when a new interview is created**. Starting, answering, finishing and the report are never blocked (R8).
- Concurrency: institute caps are checked under a row lock on the institute (Postgres), so parallel creates cannot exceed the cap (verified on Postgres, see spec §15).
- At 80 % of a monthly cap a `mock_interview_quota_near_limit` alert is sent.
- Token budgets count **provider-reported tokens only** (decision 2026-11-23, spec §15.12 option a); the monthly
  session cap is the hard limit. Unknown and partly reported calls are shown on the Interview Ops page
  (`calls_with_unknown_tokens`, `calls_with_partial_tokens`) and, above a share of calls, raise the
  `mock_interview_token_usage_unreported` warning (`runbooks/alerts.md`). Nothing is estimated and no student is blocked by it.
- Every blocked create is a `quota_blocked` ledger row with the reason in `error_class` (`paused`, `voice_disabled`, `video_disabled`, `student_daily_limit`, `institute_monthly_sessions`, `institute_monthly_tokens`).
- Every change writes one audit row `mock_interview.controls_update` with a before → after summary (Audit Log page, or Interview Ops → Control changes).

## Students

The setup form shows "You can start N more practice interviews today", or why not (paused / daily limit /
institute limit) - from `GET /v1/mock-interview/usage/me`, which returns only the student's own numbers.

## Common requests

| Request | Do |
|---|---|
| "Stop interviews during exams" | Admin → Pause; un-pause later |
| "Student says they are blocked" | Ops page → Students (counts) and the student's `/usage/me` reason; check the audit trail for recent changes |
| "We need a higher monthly cap" | Raise the institute value up to the platform ceiling; a higher ceiling is a platform env change |
