# AI Mock Interview — Project State

_Last updated: 2026-11-18 (end of Phase 1)_

## Where the work lives
| | |
|---|---|
| Worktree | `D:/Campus_Ai-mock-interview` |
| Branch | `feature/ai-mock-interview`, based on `7f99e0b` |
| Base commit `7f99e0b` | "Require admin approval for student self-registration" on `feature/student-approval` (local only; `main` is its parent `2d0d9ac`) |
| Alembic head | `a7c3e9d2b4f1` (single head, committed). Phase 1 adds no migration. |
| Original folder | `D:/Campus_Ai`, now on `feature/student-approval`, same files on disk as before; non-approval untracked files left as they were |

Nothing is pushed, merged or deployed. Phase 1 changes are **uncommitted** in the worktree, waiting for review.

## Completed phases
- **Phase 0:** audit docs and baseline (see BASELINE_TEST_RESULTS.md).
- **Isolation:** student-approval work committed on its own branch (18 files, after 34 related tests passed); worktree created; ancestry verified (`main` → `7f99e0b` → worktree).
- **Phase 1:** feature isolation and flags. Done; acceptance criteria below verified.

## Phase 1 — changes
Created:
- `backend/app/routers/mock_interview.py`: `GET /mock-interview/status` (authenticated, always 200 `{enabled}`); `GET /mock-interview/catalog` (static choices, gated); `require_mock_interview` gate that every future route must use; tenant allow-list parser (ASCII digits only, fails closed).
- `backend/app/schemas/mock_interview.py`, `backend/tests/test_mock_interview_flags.py`
- `ai-worker/interview/__init__.py`, `ai-worker/interview/router.py` (`GET /interview/ping`), `ai-worker/tests/test_interview_flag.py`
- `frontend/src/components/mock-interview/mockInterviewFlags.ts`, `MockInterviewUnavailableNotice.tsx`, `frontend/src/app/dashboard/mock-interview/page.tsx`
- `CLAUDE.md` (concise project rules)

Modified (additive only):
- `backend/app/core/config.py`: +`AI_MOCK_INTERVIEW_ENABLED=False`, +`AI_MOCK_INTERVIEW_TENANT_IDS=""`
- `backend/main.py`: +1 import, +1 `FEATURE_ROUTERS` entry
- `ai-worker/config.py`: +`MOCK_INTERVIEW_ENABLED=False`; `ai-worker/main.py`: router imported and mounted only when that flag is true
- `frontend/src/components/AppShell.tsx`: appends one nav item when the hook returns one (never while the build flag is off; `NAV_BY_ROLE` untouched)

Database: **none.** API: two new routes, `/v1/mock-interview/{status,catalog}` (plus the unversioned alias, like every router). No existing contract changed. No new required env vars.

## Behaviour by flag
| State | Backend | Worker | Frontend |
|---|---|---|---|
| All off (default) | `/status` → `{enabled:false}`; `/catalog` → 404 | `/interview/*` not mounted (404) | No nav entry, **no extra request**; the page shows a "not available" notice |
| Backend on, no allow-list | Students/teachers/TPOs/admins of every institute enabled; recruiters never | — | Needs the build flag too |
| Backend on + `TENANT_IDS=3,7` | Only those institutes; malformed list → nobody (fail closed) | — | — |

## Tests (actual results)
| Suite | Result |
|---|---|
| Backend full regression (worktree, SQLite), run before review fixes | **183 passed, 4 skipped, 0 failed** (170 baseline + 13 new) |
| After fixes: `test_mock_interview_flags.py` + `test_versioning.py` + `test_config_hardening.py` | **43 passed, 0 failed** (the flag tests now number 17) |
| AI worker flag tests (run directly; the venv has no pytest) | 2/2 passed (off → 404; on → 200 with token, 401 without) |
| Frontend `tsc --noEmit` / `npm run lint` | pass / 0 errors, same 2 warnings as baseline |
| Frontend `next build` | **Not verified**: Windows Application Control blocked the freshly installed native SWC binary in the worktree. Not a code error; CI doesn't run a build either. |
| Postgres suites | Not run (no route touches the DB in Phase 1) |

## Security review (independent read-only agent)
No high or medium findings. Low findings fixed: (1) a superscript digit in the allow-list crashed requests (`isdigit` → `isascii and isdecimal`, with tests added); (3) flag tests depended on a developer `.env` (now set explicitly); (4) dropped the needless `"use client"` in `mockInterviewFlags.ts`. Accepted and documented: (2) `/status` is visible while off, and unauthenticated callers get 401 rather than 404.

## Known issues
- `next build` not verified locally (see above).
- The backend suite shares `%TEMP%\campus_ai_test.db`; never run two at once.
- A stray test-output file `D:\mi_t.txt` was created by mistake at the drive root (safe to delete by hand).
- `frontend/node_modules` installed in the worktree (git-ignored).

## Remaining tasks
Phases 2–10 per IMPLEMENTATION_PLAN.md. Decisions still open: voice provider (Phase 5), code sandbox host (Phase 7), recording retention.

## Rollback
Phase 1: keep the flags off (the default) for no effect, or discard the worktree branch. Nothing to undo in any database.

## Next exact action
You review the Phase 1 diff in `D:/Campus_Ai-mock-interview` and decide whether I commit it to `feature/ai-mock-interview`. Then **Phase 2**: `mock_interview_sessions`, `_turns` and `_reports` models plus an additive, RLS-protected migration on top of `a7c3e9d2b4f1`, tested on throwaway SQLite and a local docker Postgres.
