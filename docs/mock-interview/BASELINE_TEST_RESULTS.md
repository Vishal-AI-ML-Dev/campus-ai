# Baseline Test Results (before any mock-interview code)

Date: 2026-11-18 · Commit: `2d0d9ac` **plus your uncommitted student-approval changes** (the working tree as found).
No application code had been changed by this work when these ran.

| Suite | Command | Result |
|---|---|---|
| Backend (SQLite, offline; same as CI) | `backend/.venv/Scripts/python -m pytest -q` | **170 passed, 4 skipped, 0 failed** (174 collected) |
| Backend real-Postgres suites (`tests_pg_api/`, `test_rls_postgres.py`) | opt-in | **Not run.** Needs a local throwaway Postgres (docker-compose `postgres:16`). Will run from Phase 2 onwards. |
| AI worker | `ai-worker/.venv/Scripts/python -m pytest tests` | **Not run.** `pytest` isn't installed in the worker venv; I didn't install it, to avoid changing your environment. |
| Frontend typecheck | `npx tsc --noEmit` | **Pass** (exit 0) |
| Frontend lint | `npm run lint` | **Pass**: 0 errors, 2 warnings (unused `eslint-disable` in `e2e/smoke.spec.ts:224,236`), pre-existing |

## Notes
- **Flaky environment, not code:** the first full backend run ended with many `sqlalchemy.exc.OperationalError` errors,
  including in pure-config tests. The same tests passed alone and in a clean rerun. Cause: every backend run shares
  one SQLite file (`%TEMP%\campus_ai_test.db`), so overlapping runs collide. **Never run two backend test sessions at once.**
- The 4 skips are the opt-in Postgres tests.
- Full backend run takes roughly 10+ minutes on this machine.
