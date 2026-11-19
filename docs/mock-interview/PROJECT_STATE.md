# AI Mock Interview — Project State

_Last updated: 2026-11-19 (Phase 3 committed locally; **live LLM NOT RUN**, accepted by you as a documented limitation; **not production-ready**)_

## Where the work lives
| | |
|---|---|
| Worktree | `D:/Campus_Ai-mock-interview` |
| Branch | `feature/ai-mock-interview`, based on `7f99e0b` |
| Base commit `7f99e0b` | "Require admin approval for student self-registration" on `feature/student-approval` (local only; `main` is its parent `2d0d9ac`) |
| Alembic head | **`b4d8f2a6c9e1`** (Phase 2, single head) → `a7c3e9d2b4f1` |
| Original folder | `D:/Campus_Ai`, on `feature/student-approval`; its `backend/.venv` is the interpreter used for worktree tests (the worktree has no venv or `.env`) |

Phase 1 is committed locally (`f17d2bd`, docs `3bce105`). Phase 2 is committed locally as `0fadaa8` (8 files, staged by name).
**Phase 3 is committed locally** as `926e59c` "feat(mock-interview): add text-based AI interview engine" (26 files, staged by name; `.claude/` and the `next dev`-generated `frontend/AGENTS.md` deliberately excluded). Nothing is pushed, merged or deployed, and all feature flags stay OFF.

## Completed phases
- **Phase 0:** audit docs and baseline (see BASELINE_TEST_RESULTS.md).
- **Isolation:** student-approval work on its own branch; worktree created.
- **Phase 1:** feature isolation and flags (`/mock-interview/status`, `/catalog`, worker ping, frontend notice). Details in git `f17d2bd`.
- **Phase 2:** models + additive RLS migration for sessions, turns, reports. Done; acceptance criteria below verified.
- **Phase 3:** text HR + technical interview engine. **Implemented and committed locally (`926e59c`); not production-ready.**
  - Verified:
    - SQLite with a stubbed LLM;
    - real Postgres as the NOBYPASSRLS `app_user`, including real-thread concurrency;
    - a headless-browser UI smoke test with a scripted engine.
  - **NOT fully verified:** the live Gemini/Groq check was NOT RUN, because no development LLM credential is configured. You accepted this as a documented limitation on 2026-11-19. See "Validation".
  - Also unverified: frontend production build (`next build`), real-device and screen-reader testing, and multi-worker load testing.

## Phase 3 — text interview engine

**Final verification (2026-11-19 08:56–09:12):**
- Worktree `D:/Campus_Ai-mock-interview`, branch `feature/ai-mock-interview`, HEAD `0ac3efc`. The changes match the checkpoint (6 modified, 19 new files + `specs/`).
- No `python.exe` was running before the run; the full backend suite ran as the only test process.
- **Imports resolve to the worktree.** The shared interpreter `D:/Campus_Ai/backend/.venv` (and `ai-worker/.venv`) only supplies third-party packages; its only `.pth` is `_virtualenv.pth`. Checked module paths:
  - `main`, `ai_client`, `app`, `app.services.mock_interview`, `app.routers.mock_interview`, `conftest` → `D:\Campus_Ai-mock-interview\backend\...`
  - worker `main`, `interview.graph` → `D:\Campus_Ai-mock-interview\ai-worker\...`
- One transient event: the first import attempt was refused by Windows Application Control (`_sqlite3` DLL blocked). It loaded normally on immediate retries; nothing was changed.
- `ai-worker/qdrant_data/`: the existing `test_worker_security` face test recreates an empty local face store on every run (here at 09:10:46: `.lock`, `meta.json`, a 12 KB empty `storage.sqlite`). It was removed again after inspection; nothing else was deleted.
- `D:/Campus_Ai` is untouched (the same branch, commit and untracked files as at the checkpoint).

**Recovery checks (2026-11-19, before the final run):**
- The worktree is on `feature/ai-mock-interview`, with HEAD `0ac3efc` (Phase 2 docs commit); the Phase 1 and Phase 2 commits are intact.
- **No `python.exe` process is running** (checked with `Win32_Process`), so no test run holds the shared `%TEMP%\campus_ai_test.db`.
  - The interrupted run was `backend/.venv` (from `D:/Campus_Ai`) `python -m pytest -p no:cacheprovider -q -rfEs`, started in `D:/Campus_Ai-mock-interview/backend` and writing to the session scratchpad file `p3_backend_full.txt`.
  - It used the default SQLite DB `%TEMP%\campus_ai_test.db` (last written 2026-11-18 22:54:52).
- `ai-worker/qdrant_data/` is **absent** from the worktree.
  - It was an empty local face store, created at 22:34 by the existing `test_worker_security` face kill-switch test during this phase's worker test run. It was removed in the previous session.
  - Nothing else was deleted.
- **The original `D:/Campus_Ai` is untouched:**
  - still on `feature/student-approval` at `7f99e0b`;
  - its untracked files (`CAMPUS_AI_COMPLETE_GAP_ANALYSIS.md`, `ai-worker/qdrant_data/`, `backend/pgtest.out`, `docs/`, `frontend/AGENTS.md`) all date from 09:27–18:41 on 2026-11-18, before this phase's work.
  - Its `.venv`s were only used as interpreters.

### Work completed so far (uncommitted)
Spec: `specs/phase-3-interview-engine.md` (design, then "6a. Changes after the independent reviews").

**AI worker** (`ai-worker/interview/`): a stateless LangGraph engine.
- Plan graph: `profile_loader → resume_context_loader → jd_analyzer → planner`.
- Turn graph: `interview_controller → answer_processor → answer_evaluator → follow_up_decider → question_generator`.
- Deterministic follow-up and difficulty policy.
- Structured output with a JSON fallback.
- Prompt-injection containment; resume/JD grounding verified in code.
- Routes `POST /interview/plan` and `/interview/turn`, still mounted only when `MOCK_INTERVIEW_ENABLED`.

**Backend:**
- Routes `/options`, `/sessions` (create/list), `/sessions/active`, `/sessions/{id}`, and `/start`, `/answer`, `/next`, `/finish`, `/abandon` for a session. All are behind `require_mock_interview` and students only, with tenant + owner 404s.
- Resume owner and tenant are checked on create and on every load (Phase 2 finding M1).
- Answers are saved before the AI call; `turn_seq` is the idempotency key.
- A per-session lease (compare-and-set on `updated_at`) allows one AI step at a time, and finish/abandon win over late AI results.
- Per-student rate limit (20/min per route) on the AI steps.
- Overdue sessions close automatically; the profile is capped.
- **No migration, no change to existing tables/env vars/API responses.** `/status` and `/catalog` are unchanged.

**Frontend:** setup form → instructions → interview room → finished view, with:
- progress bar and countdown;
- answer drafts kept per tab;
- retry banner when the next question failed;
- end with confirmation;
- refresh recovery via `/sessions/active`.

### Files
**Created:**
- AI worker:
  - `ai-worker/interview/{schemas,safety,topics,policy,llm_io,prompts,graph}.py`
  - `ai-worker/tests/test_interview_engine.py`
- Backend:
  - `backend/app/services/mock_interview.py`, `backend/app/services/mock_interview_ai.py`
  - `backend/tests/test_mock_interview_sessions.py`
  - `backend/tests_pg_api/test_mock_interview_pg.py` (real-Postgres Phase 3 tests: opt-in, like the rest of `tests_pg_api`)
- Frontend: `frontend/src/components/mock-interview/{mockInterviewApi.ts, InterviewSetupForm.tsx, InterviewInstructions.tsx, InterviewRoom.tsx, InterviewFinished.tsx}`
- Docs: `docs/mock-interview/specs/phase-3-interview-engine.md`, `docs/mock-interview/API.md`, `docs/mock-interview/AI_INTERVIEW_ARCHITECTURE.md`

**Modified:**
- `ai-worker/interview/__init__.py` (docstring), `ai-worker/interview/router.py` (+plan/turn)
- `backend/app/routers/mock_interview.py` (+Phase 3 routes; Phase 1 routes unchanged)
- `backend/app/schemas/mock_interview.py` (+Phase 3 schemas)
- `frontend/src/app/dashboard/mock-interview/page.tsx` (the workspace replaces the "coming soon" empty state)
- this file

**Untouched:** `ai_client.py`, `main.py`, `config.py`, models and migrations. `.claude/` was untracked before Phase 3 and is not part of this work.

### Tests (final results on the final code, 2026-11-19)
| Suite | Command | Result |
|---|---|---|
| **Full backend regression (SQLite)**, run alone | `cd backend && D:/Campus_Ai/backend/.venv/Scripts/python -m pytest -p no:cacheprovider -rfEs` | **Exit 0: 243 passed, 12 skipped, 0 failed** (1 warning: the pre-existing Starlette/httpx deprecation), 803 s, 08:56:24–09:09:53. Phase 2 had 212 passed, so this adds 31 Phase 3 tests, which include the mentor regression (`test_mentor_memory.py`) and the Phase 1 flag tests. The 12 skips are the same opt-in Postgres tests as Phase 2 (8 `test_mock_interview_rls_postgres.py` + 4 `test_rls_postgres.py`), which need `RLS_TEST_DATABASE_URL` |
| AI worker (no pytest in the worker venv; a plain runner calls each `test_*`) | `ENVIRONMENT=test ai-worker/.venv python run_worker_tests.py test_interview_engine test_interview_flag test_worker_security` | **Exit 0: 29 passed, 0 failed, 0 skipped** (21 engine + 2 flag + 6 existing security) |
| Frontend typecheck (whole project) | `npx tsc --noEmit` | **Exit 0** |
| Frontend lint (whole project) | `npm run lint` | **Exit 0: 0 errors**, 2 pre-existing warnings in `e2e/smoke.spec.ts` (untouched file) |
| App import, flag off and on | `import main` + `configure_mappers()`, `SAWarning` treated as an error | **Pass** (2026-11-18, after the backend fixes; no backend code changed since) |

Phase 3 backend tests (`test_mock_interview_sessions.py`) cover:
- the flag off (all routes 404, both mounts);
- staff 403 and anonymous 401;
- validation;
- `/options` lists only the caller's own resumes;
- resume owner and institute checks (create + re-check on every load);
- JD grounding passed to the engine;
- the HR flow with an adaptive follow-up, ordering and a graceful end;
- the technical config;
- time-up;
- duplicate and conflicting answers;
- idempotent start;
- worker failure keeps the answer and `/next` recovers;
- start-failure retry reuses the plan;
- cross-student and cross-tenant 404s;
- refresh recovery and the one-active rule;
- finish/abandon;
- the daily limit;
- the lease (409);
- finish winning over an in-flight AI step;
- overdue auto-close;
- the profile cap;
- the hard-cap evaluation;
- the per-student rate limit.

### Validation: Postgres, live LLM, browser (2026-11-19)

**Environment and safety:**
- Docker Desktop was not running, so I started it.
- The 9 existing containers (all stopped, from other projects and the local stack) were not touched.
- **Throwaway Postgres:**
  - container `campus_p3_pgtest`, image `postgres:16@sha256:63e83b5a…` (pinned local digest);
  - data on `tmpfs` only, started with `--rm`;
  - bound to `127.0.0.1:15433`, database `campus_rls_test`;
  - no volumes created (volume list compared before and after);
  - removed after the run.
- Credentials: random throwaway owner and `app_user` passwords, kept in session scratch files and passed only through a helper that redacts them from output. Deleted afterwards.
- **Incident:** a first role-creation attempt failed, and SQLAlchemy echoed the bound parameter, which printed the throwaway `app_user` password once into the session transcript.
  - That password was rotated before the role was ever created.
  - The helper then switched to `hide_parameters=True` plus output scrubbing.
  - Checked afterwards: no secret appears in any test log.
  - Production or real credentials were never involved.

| Check | Result |
|---|---|
| Migration chain as owner: `upgrade head` (full chain from an empty database to `b4d8f2a6c9e1`) → `downgrade -1` → `current` = `a7c3e9d2b4f1` → `upgrade head` → single head | **PASS** |
| `app_user` attributes: not superuser, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB; 3 interview tables RLS enabled + forced | **PASS** |
| `tests/test_rls_postgres.py` + `tests/test_mock_interview_rls_postgres.py` as `app_user` (Phase 2 RLS, CHECK, composite tenant FK and cascade tests) | **PASS: 12 passed** |
| **New** `tests_pg_api/test_mock_interview_pg.py` (6 tests, real API, real threads, own TestClient and DB connection per thread) | **PASS: 6 passed**; concurrency tests repeated 3× = 3/3 pass |
| ↳ flow + ownership + resume + RLS: other student/institute 404 on every route; teacher 403; classmate's and other institute's resume 404; `/options` lists only own resumes; rows visible only under the owning tenant's setting (0 under the other tenant, 0 with none = default deny) | PASS |
| ↳ parallel creates (6 threads): exactly one 201, five 409, one active row | PASS |
| ↳ parallel starts (5 threads): planned once, opening question once, a single turn 1 | PASS |
| ↳ one AI step at a time: 6 parallel `/next` → exactly 1 engine call, seqs `[1, 2]`, lease released | PASS |
| ↳ concurrent answers (6 threads, 2 different texts): one text stored, every other-text request 409, exactly 1 engine call, 2 turns | PASS |
| ↳ finish beats a late AI result: AI held mid-step, finish → completed; late result dropped (no turn 2) | PASS |
| **Mutation checks** (scratch-only plugins, one run each, to prove the tests detect the bugs): `FOR UPDATE` disabled → parallel-create test **fails** (6×201); lease check disabled → parallel-start (2 plans) and one-AI-step (2 calls) tests **fail** | PASS (the tests are meaningful) |
| **Full `tests_pg_api`** as `app_user` (includes the 6 new tests) | **PASS: exit 0, 222 passed, 2 skipped** (the same Redis-queue skips as Phase 2; Phase 2 had 216 passed); preflight and cleanup checks OK (non-RLS counts back to baseline, 0 marked rows left) |
| **Live LLM smoke test** (HR + technical, structured output, relevance, follow-ups, no fabrication, Hindi/Hinglish, latency, timeouts) | **NOT RUN.** No development LLM credential exists: no `.env` in the worktree or `D:/Campus_Ai` (only `.env.example`), and no `GROQ_*`, `GOOGLE_API_KEY`, `GEMINI_*` or `LLM_PROVIDER` variable in the shell, user or machine environment (names checked only). Nothing was invented or configured |
| **Browser smoke test** (headless Chromium 1248 via the project's Playwright; local backend on a scratch SQLite file with the flag on and a **scripted fake engine**; local `next dev`) | **PASS for 21 of 21 functional checks.** Details below |

**Browser checks that passed:**
- login, then the sidebar entry appears;
- the setup form renders: options, own resume listed, empty-role validation;
- the instructions screen summarizes the configuration;
- Start shows a loading state, then the opening question;
- countdown and progress bar are visible;
- the "thinking" indicator shows while the AI works;
- a labelled follow-up appears after a short answer;
- Ctrl+Enter submits;
- an injected AI failure shows the friendly 503 message plus the "answer is saved" banner, and the answer stays visible in the transcript;
- "Get next question" recovers;
- after a refresh, both the draft answer and the transcript are restored;
- "End interview" with confirmation leads to the completion screen and the transcript;
- after completion, a refresh shows a new setup form.

Screenshots are in the session scratchpad `ui/shots/`.

**Two checks were reported as failing and are explained, not Phase 3 defects:**
- **Console error:** the browser logs the 503 response that the test injected on purpose.
- **Phone-width horizontal overflow (390 px):** caused by the existing `AppShell`, which keeps its 220 px sidebar on screen at phone width.
  - Every page overflows: `/dashboard` 153 px, `/dashboard/skills` 153, `/dashboard/mentor` 194, `/dashboard/resume` 275.
  - The mock-interview setup page and the room, loaded at 390 px (with a transcript), match the 153 px baseline: they add **no** overflow of their own.
  - Seen once and not investigated: about 45 px of extra overflow after resizing a desktop-width window down to 390 px mid-session.

**Side effects:**
- `next dev` auto-generated an untracked `frontend/AGENTS.md` (Next.js agent notes, 09:58).
- My attempt to delete it was blocked by the permission system, so **it is still in the worktree**.
- It is not Phase 3 work; keep it, delete it or ignore it, your choice.

**Explained log noise:** the full `tests_pg_api` log has 5 server 500s ("value too long for varchar(60)" on `audit_logs.action`). They come from existing tests that deliberately force an oversized audit action to prove a delete rolls back when its audit write fails. That is expected behaviour, and those tests pass.

### Phase 3 acceptance criteria
| Criterion | Status | Evidence |
|---|---|---|
| A student can create a text interview | **Met** | SQLite + Postgres + browser |
| HR questions work | **Met with a stubbed LLM; live NOT RUN** | engine/flow tests. Real question quality unverified |
| Technical questions work | **Met with a stubbed LLM; live NOT RUN** | same |
| Follow-ups adapt to answers | **Met with a stubbed LLM; live NOT RUN** | policy tests + browser follow-up. Real follow-up quality unverified |
| Resume and JD grounded and ownership-verified | **Met** | grounding tests; resume 404 on SQLite **and Postgres** |
| Answers and turns persist correctly | **Met** | ordering, duplicates and concurrent answers on Postgres |
| An active session can be recovered | **Met** | API test + browser refresh (transcript and draft restored) |
| Tenant isolation enforced | **Met** | app-layer 404s + RLS under `app_user` on Postgres |
| Feature flag OFF by default | **Met** | flag tests |
| Existing features keep working | **Met** | SQLite 243/0 failed; Postgres API 222/0 failed; lint/tsc clean |
| Regression and security tests pass | **Met** | all suites above |
| Documentation updated | **Met** | spec, API.md, AI_INTERVIEW_ARCHITECTURE.md, this file |

### Security and code-quality review (two independent read-only agents)
No High tenant-isolation or IDOR findings. Everything below is **fixed in code**, and the fixes are covered by the tests above, except where noted.

**Abuse and concurrency:**
- (H1) Parallel `/next` or `/start` multiplied LLM calls and tied up threads. Fixed with the per-session lease, the per-student limit, and no retry on worker 5xx/timeouts.
- (M1) Parallel creates bypassed the one-active rule. Fixed with a user-row `FOR UPDATE` lock. **Verified on Postgres** (real threads + mutation check).
- (L2) finish/abandon raced with `advance`. Fixed by the compare-and-set finalize.

**Prompt hardening:**
- (M2) The replayed transcript was not sanitized in the question prompt.
- (M3) Role/company went into the system prompt. Now limited to a character allow-list; the follow-up angle and grounding moved into a `<context_note>` data tag.
- (L1) Invisible characters and unterminated tags slipped past tag neutralization.
- (L3) Worker schema caps were missing.

**Code-quality findings:**
- the uncapped profile caused a permanent 503;
- answer 30 was never evaluated;
- the follow-up allowance used the adaptive level instead of the configured difficulty;
- curly-apostrophe "don’t know" was missed;
- overdue sessions blocked new ones;
- a duplicated "Self introduction" topic;
- stale drafts and the 409 resync in the frontend.

**Open / accepted:**
- (L4) With Langfuse configured, prompts (including resume, JD and answers) are traced, the same as the existing mentor and resume features. This needs a privacy note before rollout.
- Two concurrent first-time `/start` calls are serialized by the lease; **verified on Postgres** (5 parallel starts → 1 plan).

### Not verified (reported separately)
- **Live LLM: NOT RUN** (no development credential). This leaves unverified: real Groq/Gemini question relevance, follow-up quality, structured-output reliability in practice, fabrication behaviour, English/Hindi/Hinglish wording, and latency under a 60 s timeout. Engine logic and provider-failure handling are covered by stubbed tests only.
- **Browser (partly):**
  - the browser run used a scripted fake engine, not the AI worker;
  - headless Chromium only, no real phone or screen-reader test;
  - `next build` not verified;
  - there are no frontend tests in this project.
- **Pre-existing, not Phase 3:** the `AppShell` sidebar overflows at phone width on every dashboard page. A mobile shell fix is out of scope.
- **Production-like run:** a multi-worker server under load was not run; the Postgres concurrency tests use in-process threads.
- **Worker under pytest:** the worker tests run through a plain runner because the worker venv has no pytest; they are not collected by pytest itself.
- **Not addressed:**
  - (L4) Langfuse tracing includes candidate text when configured; this needs a privacy note before rollout.
  - `/catalog` (Phase 1) still lists every type, mode and duration; Phase 3 uses `/options`. This is intentional, so an existing response doesn't change.
- **Test side effect:** the existing worker face test leaves an empty `ai-worker/qdrant_data/` behind. It isn't in `.gitignore`, which only lists `qdrant_storage/` and `qdrant_local*/`; I removed it by hand each time.

### Open items after the Phase 3 commit (accepted limitations, not blockers)
1. **Live LLM smoke test (NOT RUN; accepted).** It needs a development-only Groq or Gemini key, which you provide in a local, uncommitted worker env. Then one HR and one technical interview in English and Hinglish, checking: relevance, follow-ups, no fabrication, latency, and recovery from timeouts.
2. Frontend production build (`next build`), real-device and screen-reader testing, and multi-worker load testing are still unverified.
3. The untracked `frontend/AGENTS.md` (generated by `next dev`) was deliberately left out of the commit and is still in the worktree for your decision.
4. **Before any production rollout:** the live LLM check above, the Langfuse privacy note, and the rollout steps under "Production rollout". Phase 3 is **not** production-ready.

## Phase 2 — changes
Created:
- `backend/app/models/mock_interview.py`: `MockInterviewSession`, `MockInterviewTurn`, `MockInterviewReport`; allowed values as `*_VALUES` constants.
- `backend/alembic/versions/b4d8f2a6c9e1_mock_interview_sessions_turns_reports.py`
- `backend/tests/test_mock_interview_models.py` (SQLite), `backend/tests/test_mock_interview_rls_postgres.py` (opt-in Postgres)

Modified (additive):
- `backend/app/models/__init__.py`: +3 imports / `__all__` entries
- `backend/tests/test_rls_postgres.py`: the 3 tables are appended to `RLS_TABLES` (the default-deny check now covers them)
- `backend/tests/test_mock_interview_flags.py`: the Phase 1 guard "no interview tables" became "exactly the 3 Phase 2 tables"

## Database changes (migration `b4d8f2a6c9e1`, additive only)
Three new tables. No existing table, column, policy or grant is changed.

| Table | Notes |
|---|---|
| `mock_interview_sessions` | `tenant_id`, `student_id`, `interview_type`, `mode`, `status`, `target_role`, `company_name`, `difficulty`, `language`, `duration_minutes` (5–120), `resume_id` (FK resumes, SET NULL), `job_description`, `plan_json`, `campaign_id` (plain int; FK in Phase 9), `started_at`, `completed_at`, timestamps |
| `mock_interview_turns` | `session_id`, `seq` (≥1, unique per session), `topic`, `question`, `is_follow_up`, `answer_text`, `answer_source` (typed/stt), `answered_at`, `turn_eval_json` |
| `mock_interview_reports` | `session_id` (unique), `overall_score` (0–100), `category_scores_json`, `strengths_json`, `weaknesses_json`, `recommendations_json`, `rubric_version`, `provider` |

- All three tables: `ENABLE` + `FORCE ROW LEVEL SECURITY` + the standard `tenant_isolation` policy. `app_user` gets access through the owner's existing default privileges (verified).
- **Tenant-safe foreign keys** (Postgres checks FKs without RLS):
  - turns and reports use `(session_id, tenant_id) → sessions(id, tenant_id)`;
  - sessions use `(student_id, tenant_id) → users(id, tenant_id)`, which reuses the existing `uq_users_id_tenant`.
  - So a row can never point at another institute's session or student.
- **Allowed values:** enforced by named CHECK constraints (no Postgres ENUM types). They match `/catalog` exactly, and a test checks that.
- **Cascades:** deleting a tenant, student or session removes its turns and report; deleting a resume sets `resume_id` to NULL.
- **Downgrade** drops the three tables and **all interview data**. Use it only after an explicit decision and a backup.

API: **no changes** in Phase 2. Env vars: none.

## Tests (actual results, Phase 2)
| Suite | Result |
|---|---|
| Backend full regression (SQLite, worktree) | **212 passed, 12 skipped, 0 failed** (Phase 1: 183 passed). Skips = 4 baseline Postgres tests + 8 new opt-in Postgres tests |
| Migration on throwaway SQLite | **Pass.** upgrade → downgrade → upgrade, then compared with the models: **0 differences**. Base schema built from the models and stamped at `a7c3e9d2b4f1`, because the historical chain can't run on SQLite: `1d61da8d89e7` uses ALTER constraint, which predates this work. |
| Full migration chain on throwaway Postgres 16 (docker, pinned digest, tmpfs, localhost:15433) | **Pass.** All 53 migrations upgrade; `downgrade -1` leaves 0 tables and 0 policies; upgrade again; a 2-step downgrade and upgrade also pass. Compared with the models: 0 differences for the new tables (1 unrelated pre-existing difference). |
| `test_mock_interview_rls_postgres.py` + `test_rls_postgres.py` (as NOBYPASSRLS `app_user`) | **12 passed.** Covers: forced RLS with an exact policy expression; cross-tenant read, update and delete blocked; WITH CHECK (`42501`); a turn or report can't attach to another tenant's session; the student must be in the same tenant; CHECK constraints; DB cascades; default-deny. |
| `tests_pg_api` (full real-Postgres API suite, run on the migrated DB) | **216 passed, 2 skipped** (Redis queue tests; no isolated Redis), 0 failed |
| `import main` with the flag off/on, SQLAlchemy warnings treated as errors | Pass (no relationship-overlap warnings) |

The container was removed after the run; its throwaway passwords lived only in the scratchpad and were deleted.
Frontend and worker are untouched in Phase 2, so they were not re-run.

## Security review (independent read-only agent)
No high findings.
- **Fixed:**
  - (M2) the WITH CHECK test accepted any DB error; it now asserts SQLSTATE `42501` plus the "row-level security" message.
  - (L4) added a direct session-delete cascade test on Postgres.
  - (L5) the policy test now checks the exact USING and WITH CHECK expressions.
  - (L6) dropped 2 redundant indexes: `student_id` is covered by `(student_id, created_at)`, and `session_id` by `uq(session_id, seq)`.
  - (L8) CHECK value quoting now uses explicit SQL escaping.
- **Deferred to Phase 3, by design:**
  - (M1) `resume_id` is a single-column FK. A database-level fix would need a new unique key on the existing `resumes` table, which the project rules forbid. **Phase 3 must check the resume's owner and tenant in the app layer, with a test.**
  - (L3) delete-orphan plus unique keys: replace a report or turn by updating it in place, or flush the delete first.
  - (L7) the composite student FK has no ON UPDATE. Changing a user's `tenant_id` would fail once they have sessions; no code does that today.

## Known issues
- **Not verified:**
  - `next build` (Windows Application Control blocks SWC in the worktree; from Phase 1).
  - The Redis queue path in `tests_pg_api`.
- **Test environment:**
  - The backend suite shares `%TEMP%\campus_ai_test.db`; never run two at once.
  - Windows reserves port 55432 on this machine; use 15433 for the throwaway Postgres.
- **Cleanup:** stray file `D:\mi_t.txt` from Phase 1 (safe to delete by hand).

## Rollback
Phase 2 code is unused while the flags are off. In a database the migration has reached, the normal rollback is flags off (data kept).
`alembic downgrade a7c3e9d2b4f1` removes the tables and their data, so use it only after a backup.

## Production rollout (not done; documentation only)
Deploy code with flags off → run `b4d8f2a6c9e1` with the owner role (`migrate` profile) → confirm `app_user` grants and RLS
on the 3 tables (`app_role.template.sql` audit query) → enable flags for a pilot tenant in a later phase.

## Remaining tasks
- Close Phase 3 (see "Remaining before Phase 3 can be closed" above).
- Phases 4–10 per IMPLEMENTATION_PLAN.md. **Not started**; Phase 4 must not begin until Phase 3 is reviewed and committed.
- Open decisions: voice provider (Phase 5), code sandbox host (Phase 7), recording retention.

## Next exact action
Phase 3 is committed locally. Waiting for your instruction; nothing starts automatically.
- Optional, any time: the live LLM smoke test, once you provide a development-only key (local, uncommitted).
- **Phase 4 (evaluation, report, history UI) is not started** and needs your explicit go-ahead.

Docker Desktop was started for this validation and is still running; I didn't stop it.
