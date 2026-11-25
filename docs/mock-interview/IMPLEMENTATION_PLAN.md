# AI Mock Interview — Implementation Plan

Companion to SAFETY_AUDIT.md and ARCHITECTURE_AUDIT.md. Small, independently testable phases; each ends with
regression tests, a PROJECT_STATE.md update and a stop for review.

## 1. Isolation strategy (decision required before any code)

Your uncommitted student-approval work sits on `main` and owns the Alembic head (`a7c3e9d2b4f1`, untracked).

| Option | How | Pros | Cons |
|---|---|---|---|
| **A (recommended)** | You commit the approval work first (its own branch or `main`, your call). I then create `feature/ai-mock-interview` from that commit, in a **separate git worktree** (`D:/Campus_Ai-mock-interview`). | Your working tree is never touched again; migration chains cleanly onto a committed head; tests run in isolation. | Needs one action from you. |
| B | Worktree from committed `HEAD` (`2d0d9ac`); my migration revises `f0a1b2c3d4e5`. | Zero action needed from you now. | Two Alembic heads once both land → needs a merge migration later; my tree won't include your approval changes. |
| C | `git switch -c` in the current tree. | Simple. | Mixes my files with your uncommitted work in one tree — **not recommended**. |

No branch has been created yet. Nothing gets pushed under any option.

## 2. Feature flags (Phase 1)

| Layer | Flag | Default | Effect when off |
|---|---|---|---|
| Backend | `AI_MOCK_INTERVIEW_ENABLED: bool` | `False` | Every `/v1/mock-interview/*` route returns **404** (feature invisible). |
| Backend | `AI_MOCK_INTERVIEW_TENANT_IDS: str` (comma list, optional) | `""` = all tenants once enabled | Restricts enablement to selected institutes for a pilot. |
| Backend | `GET /v1/mock-interview/status` → `{enabled: bool}` | — | Always 200 for authenticated users, so the UI can hide nav per-tenant. This is the only always-on route. |
| AI worker | `MOCK_INTERVIEW_ENABLED: bool` | `False` | Interview router is not mounted. |
| Frontend | `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true"` | off | No nav entry; pages render a "not available" notice (same pattern as `FaceUnavailableNotice`). Nav is shown only if build flag **and** `/status` both say enabled. |

Rollback = set flags false (no data deleted). No new **required** env vars; existing startup gates unchanged.

## 3. Data model (Phase 2) — new tables only

All tables: `id`, `tenant_id` (FK tenants, CASCADE, indexed), timestamps, `ENABLE + FORCE RLS` with the standard
`tenant_isolation` policy. Dialect-neutral types (JSON stored as `Text`/`sa.JSON`, verified on SQLite + Postgres).

| Table | Purpose | Key columns |
|---|---|---|
| `mock_interview_sessions` | One interview attempt | `student_id`, `interview_type` (hr/technical/coding/company/full), `mode` (text/voice/video), `status` (setup/in_progress/evaluating/completed/abandoned/failed), `target_role`, `company_name`, `difficulty`, `language` (en/hi/hinglish), `duration_minutes`, `resume_id` (FK resumes, SET NULL), `job_description` (Text, nullable), `plan_json`, `campaign_id` (nullable), `started_at`, `completed_at` |
| `mock_interview_turns` | Question/answer pairs, in order | `session_id`, `seq`, `topic`, `question`, `is_follow_up`, `answer_text`, `answer_source` (typed/stt), `answered_at`, `turn_eval_json` |
| `mock_interview_reports` | Final evaluation | `session_id` (unique), `overall_score`, `category_scores_json`, `strengths_json`, `weaknesses_json`, `recommendations_json`, `rubric_version`, `provider` |
| `mock_interview_campaigns` | Faculty assignment (Phase 9) | `created_by`, `title`, `interview_type`, config, `deadline` |
| `mock_interview_campaign_targets` | Campaign ↔ section | `campaign_id`, `section_id` |
| `mock_interview_code_submissions` | Coding round (originally Phase 7; Phase 8 since 2026-11-20) | `session_id`, `turn_id`, `language`, `source`, `result_json` (Phase 8.0 proposal: `attempt_id` instead of `turn_id`, plus `mock_interview_code_attempts`, company-profile and simulation tables; **no hidden test data in any table**; spec §6) |

Campaign/coding tables ship in their own later migrations so earlier phases stay small. No existing table is altered.
No audio/video is stored by default; only transcripts.

## 4. API surface (all new, under `/v1/mock-interview`, also mounted unversioned like every router)

Student (own sessions only, tenant + `student_id` checks, 404 otherwise):
`GET /status` · `GET /options` (resumes, drives, languages) · `POST /sessions` · `GET /sessions` · `GET /sessions/{id}` ·
`POST /sessions/{id}/start` → first question · `POST /sessions/{id}/answer` → next question or `done` ·
`POST /sessions/{id}/finish` · `GET /sessions/{id}/report`.
Faculty (teacher/tpo/admin, same tenant; Phase 9): campaigns CRUD, `GET /campaigns/{id}/progress`, analytics, CSV export.
LLM-costly routes get `@limiter.limit`. Payload limits: answer ≤ 5,000 chars, JD ≤ 10,000 chars.

## 5. Interview engine (AI worker, `ai-worker/interview/`)

Stateless LangGraph, invoked per turn by the backend (same pattern as the mentor):

```
plan:   profile_loader → resume_analyzer → jd_analyzer → planner          (POST /interview/plan)
turn:   answer_evaluator → follow_up_decider → question_generator        (POST /interview/turn)
report: report_generator (per-type rubric)                              (POST /interview/report)
```

Grounding rules in prompts and in code: resume questions cite resume text the backend sent; no invented experience;
company prep uses only the JD, drive data and general public knowledge, never "leaked" questions. Every score carries an
`evidence` quote from the answer; a missing answer scores 0 with "no answer given". Reports include a disclaimer that
practice scores don't predict selection. Structured output with JSON-fallback (same as `resume._ats_invoke`).

## 6. Phases

| # | Scope | Touches existing files? | Exit criteria |
|---|---|---|---|
| 0 | Audit, baseline tests, these docs | No | Done when BASELINE_TEST_RESULTS.md exists |
| 1 | Flags, empty router with `/status`, worker flag, frontend notice page | `main.py` (+router), `config.py` (+2 fields), `ai-worker/main.py` (+conditional include), `auth.tsx` (flag-gated nav) | Flag-off tests prove 404 + unchanged nav; full suite green |
| 2 | Models + migration (sessions, turns, reports) | `models/__init__.py` (+import) | Upgrade/downgrade on throwaway SQLite **and** local docker Postgres; RLS cross-tenant test |
| 3 | Text HR + technical interview end-to-end | — | Ownership/tenant tests; worker graph unit tests with stubbed LLM |
| 4 | Evaluation, report, history UI | — | Evidence-backed scoring tests |
| 5 | Voice | — | Provider **decided 2026-11-19: browser Web Speech** (§7 a). Implemented, committed locally (`34fd3a9`); manual real-device verification pending (see PROJECT_STATE.md) |
| 6 | Video (camera preview, captions, timer — no face/emotion analysis) | — | Same engine as text/voice. Design **approved 2026-11-19** (`specs/phase-6-video-interview.md`, D1–D12). **6.1 foundations committed (`1ad5628`)**; **6.2 camera infrastructure implemented**; **6.3 lobby, room and lifecycle implemented** (also the 6.4 wiring and the 6.5 D9 note/history label); **6.4 audit and automated-gap closure done** (spec §10); **6.5 report parity audited and proven by tests** (spec §11); **6.6 automated hardening done** (spec §12); **Phase 6 not complete: the real-device checklist (spec §10.4, 24 rows) is pending** |
| ~~7~~ | ~~Coding~~ → **moved to Phase 8** (see §6a) | — | **Sandbox decision needed**; editor-only until a sandbox exists |
| ~~8~~ | ~~Company-based + full placement simulation (multi-round session)~~ → **stays in Phase 8** (§6a) | — | — |
| ~~9~~ | ~~Faculty campaigns + analytics + export~~ → **stays in Phase 9** (§6a) | — | RBAC tests: students/recruiters denied, cross-tenant denied |
| ~~10~~ | ~~Security review + full regression + deployment doc~~ → **Phase 10** (§6a) | — | DoD checklist |

Rows 7–10 above are the **original** plan, kept for history. They were re-numbered on 2026-11-20 (decision P7-D1);
the current roadmap is §6a.

## 6a. Roadmap from Phase 7 (approved 2026-11-20, P7-D1)

| # | Scope | Spec / status |
|---|---|---|
| **7** | **Production operations and monitoring:** request correlation, LLM telemetry, usage ledger, cost (unknown until a price table is approved), circuit breaker, institute quotas and controls, institute ops API/UI, token-protected `/metrics`, runbooks, retention | `specs/phase-7-production-operations.md`. **7.0** (`ee48610`) and **7.1** (`863a8aa`) done; **7.2–7.10 implementation complete** (spec §15); evidence and acceptance record in spec §15.10–§15.11; numeric cost pending an approved price table; token budgets count reported tokens only, with visibility and an alert for unknown usage (decision a, §15.12); Next.js 16.4.6 security upgrade and the Dockerfile ops-flag ARG done (§15.10 C); **Phase 7 not fully accepted** — live drills and device tests are Phase 10 |
| **8** | **Advanced AI interview intelligence:** coding round (sandbox decision first; editor + LLM review without execution until a sandbox exists), company-based and full placement simulation (multi-round), deeper interview intelligence | `specs/phase-8-advanced-ai-intelligence.md` (**revision 2, 2026-11-23**). **8.0 planning done. G1 (Batch 1, narrower) approved 2026-11-23; Batch 1 = 8.1 + 8.2 review-only coding round implemented and tested (spec §16, migration `e7a1c4d9b2f6`).** P8-D7, D9…D13 and all sandbox decisions remain *Recommended — awaiting user approval* (spec §14). Sub-phases: 8.1 coding foundations (migration A) → 8.2 coding UI (review-only) → 8.3 AI coding review (unverified) → 8.4 company profiles + JD skills (migration B) → 8.5a private test pack → 8.5b runner code (local/CI) → 8.5c pilot VM → 8.6 execution integration → 8.7 full simulation (migration C) → 8.8 hardening. Approval gates G1–G5 (spec §15): G1 unlocks all no-execution work; G3 = VM provisioning and cost; **no untrusted code runs before G4** |
| **9** | **Institute and placement management:** faculty campaigns (`mock_interview_campaigns`, `…_campaign_targets`), faculty/placement analytics, CSV export | Not started |
| **10** | **Enterprise integrations, platform-level security and final acceptance:** platform-operator role and cross-institute ops view (deferred by P7-D2), integrations, security review, deployment doc, **full regression and all real-device testing** | Batch 10.1 (docs) drafted 2026-11-25: `specs/phase-10-production-readiness.md` (plan 10.1–10.10 = production readiness; platform-operator role and integrations pending owner scope decision OQ-1). **2026-11-25: 10.2–10.10 executed locally (uncommitted)** — AI governance on every AI path, config templates + policy, CI workflow (not yet run on GitHub), monitoring additions, encrypted backup/restore drilled locally, local load tests and drills; final verdict **NOT PRODUCTION READY** (spec §10–§11); platform-operator role and integrations not implemented. **2026-11-25 (later): backend code freeze; full backend suite 835 passed / 0 failed / 34 skipped (B-1 resolved); local integration smoke PARTIAL; final-tree Postgres/RLS run, GitHub CI, staging and Phase 8 G2–G5 pending; status in `PROJECT_STATE.md` and `docs/CAMPUSAI_UNIFIED_RELEASE_HANDOFF.md`** |

**Old → new mapping (nothing dropped):**

| Original item | Original phase | New phase |
|---|---|---|
| Coding round + sandbox (`mock_interview_code_submissions`) | 7 | **8** |
| Company-based + full placement simulation | 8 | **8** |
| Faculty campaigns + analytics + export | 9 | **9** |
| Security review + full regression + deployment doc | 10 | **10** (final acceptance) |
| Production rollout monitoring (§9 step 4: error rate, worker latency, LLM cost) | §9, undated | **7** (built), executed at rollout |
| Phase 5 real-device checks (`phase-5-voice-interview.md` §8) and Phase 6 RD-01…RD-24 (`phase-6-video-interview.md` §10.4) | 5, 6 (deferred, D1) | **10** final acceptance milestone |
| *New:* operations/monitoring foundation | — | **7** |
| *New:* platform-operator role, cross-institute ops view | — | **10** |

**Testing strategy from Phase 7 on:** each sub-phase runs focused unit tests, type checks and static checks (ruff,
`tsc`, lint, worker interview subset, migration up/down/up). The comprehensive end-to-end regression and the
physical-device tests run once, as the Phase 10 final acceptance milestone.

## 7. Decisions needed later (not blocking Phases 1–4)

- **Voice (Phase 5).** Pipecat + server STT/TTS doesn't fit the 512 MB free-tier services. Options: (a) browser Web Speech API for STT/TTS, answers posted as text to the same engine — zero new infra, weaker Hindi/Hinglish quality on some browsers; (b) a separate Pipecat service with hosted STT/TTS (e.g. Deepgram/ElevenLabs/Google) — better quality, adds cost, keys and a new deployment. Recommendation: (a) first, behind the same flag, with (b) as a later adapter. **Decided 2026-11-19: (a), behind its own sub-flag `AI_MOCK_INTERVIEW_VOICE_ENABLED`.** Option (b), and any server-side audio, stays deferred and needs separate privacy approval.
- **Video privacy and consent (Phase 6).** **Decided 2026-11-19:** browser-only camera preview, no recording/upload/storage, per-session acknowledgement not stored server-side, no external provider, separate video flag + fail-closed video allow-list. See `specs/phase-6-video-interview.md` §1.
- **Coding sandbox (originally Phase 7; Phase 8 since 2026-11-20).** Self-hosted Judge0 or Piston on a separate host with no network, cgroup CPU/memory/pid limits and a time limit. Until one exists, the coding round offers editor + LLM review of the code **without running it**.
  **Updated 2026-11-23 (Phase 8.0, proposal, not approved):** the threat model and option comparison are in
  `specs/phase-8-advanced-ai-intelligence.md` §4. Recommendation: a dedicated, secret-free Linux VM running a thin
  `code-runner` service that starts one gVisor (`runsc`) container per submission with no network (Judge0 ≥ 1.13.1 on
  the same dedicated VM only as fallback: it needs a privileged container). Render free services cannot host it.
  Decisions P8-S1…S6; review-only mode remains the default until they are approved and the VM exists.
  **Revision 2 (2026-11-23, proposal, not approved):** hidden tests and reference solutions move to a runner-private
  test pack in a separate private repository (P8-S7); the runner accepts problem/test-set identifiers, never expected
  outputs; 20-threat model, per-language limits, token rotation and approval gates G1–G5 in spec §5, §4.4, §4.6, §15.
  No VM is provisioned and no cost is incurred without explicit approval (gate G3).
- **Recording retention.** Default: no audio/video stored. If wanted later: Supabase private bucket, owner + authorized faculty only, with a retention period.

## 8. Testing per phase

1. New tests for the phase. 2. Full `backend/tests` (SQLite). 3. `ai-worker/tests`. 4. `npx tsc --noEmit` + `npm run lint`.
5. From Phase 2: `alembic upgrade head` / `downgrade -1` / `upgrade head` on a throwaway DB, plus `tests_pg_api` on local
docker Postgres when available. 6. Import check (`python -c "import main"`) with the flag on and off. 7. `git diff --stat`
review limited to the planned files. Results are compared with BASELINE_TEST_RESULTS.md, and pre-existing failures are
reported separately.

## 9. Production rollout (documentation only, never executed by me)

Expand–contract: (1) deploy code with all flags off → no behaviour change; (2) run the additive migration with the
owner role via the existing `migrate` profile; (3) enable `AI_MOCK_INTERVIEW_ENABLED` + `AI_MOCK_INTERVIEW_TENANT_IDS=<pilot>`
and the worker flag, rebuild the frontend with the public flag; (4) monitor error rate, worker latency and LLM cost;
(5) widen. Rollback: flags off (instant, data kept). Schema removal (downgrade) only after an explicit decision and a backup.
