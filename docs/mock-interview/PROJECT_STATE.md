# AI Mock Interview — Project State

_Last updated: 2026-11-18 (end of Phase 2)_

## Where the work lives
| | |
|---|---|
| Worktree | `D:/Campus_Ai-mock-interview` |
| Branch | `feature/ai-mock-interview`, based on `7f99e0b` |
| Base commit `7f99e0b` | "Require admin approval for student self-registration" on `feature/student-approval` (local only; `main` is its parent `2d0d9ac`) |
| Alembic head | **`b4d8f2a6c9e1`** (Phase 2, single head) → `a7c3e9d2b4f1` |
| Original folder | `D:/Campus_Ai`, on `feature/student-approval`; its `backend/.venv` is the interpreter used for worktree tests (the worktree has no venv or `.env`) |

Phase 1 is committed locally (`f17d2bd`, docs `3bce105`). Phase 2 is committed locally as `0fadaa8` (8 files, staged by name).
Nothing is pushed, merged or deployed.

## Completed phases
- **Phase 0:** audit docs and baseline (see BASELINE_TEST_RESULTS.md).
- **Isolation:** student-approval work on its own branch; worktree created.
- **Phase 1:** feature isolation and flags (`/mock-interview/status`, `/catalog`, worker ping, frontend notice). Details in git `f17d2bd`.
- **Phase 2:** models + additive RLS migration for sessions, turns, reports. Done; acceptance criteria below verified.

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
Phases 3–10 per IMPLEMENTATION_PLAN.md. Open decisions: voice provider (Phase 5), code sandbox host (Phase 7), recording retention.

## Next exact action
Wait for your go-ahead to start **Phase 3**: text HR + technical interview end-to-end.
   - **Backend:** routes `/options`, `/sessions` CRUD, `/start`, `/answer`, `/finish`, all behind `require_mock_interview`, with ownership and tenant 404s, including the resume check from M1.
   - **Worker:** `POST /interview/plan` and `/interview/turn`.
   - **Tests:** worker graph tests with a stubbed LLM.
