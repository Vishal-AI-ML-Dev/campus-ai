# AI Mock Interview — Safety Audit (Phase 0)

Date: 2026-11-18 · Mode: read-only inspection · Author: Claude Code session

## 1. Repository state at audit time

| Item | Value |
|---|---|
| Branch | `main` (tracks `origin/main`; only local branch) |
| HEAD | `2d0d9ac` "Show a notice on face pages when face recognition is off" |
| Stashes / worktrees | none / one (`D:/Campus_Ai`) |
| Uncommitted work | **Yes — an in-progress "student approval" feature** (see below) |

Uncommitted, modified (must not be touched or overwritten):
`backend/app/models/identity.py`, `backend/app/routers/{academics,admin,analytics,attendance,auth,face,people}.py`,
`backend/app/schemas/identity.py`, `backend/tests/test_auth.py`, `backend/tests/test_versioning.py`,
`backend/tests_pg_api/test_remaining_features.py`, `frontend/src/app/dashboard/users/page.tsx`, `frontend/src/app/login/page.tsx`.

Uncommitted, untracked: `CAMPUS_AI_COMPLETE_GAP_ANALYSIS.md`, `ai-worker/qdrant_data/`,
`backend/alembic/versions/a7c3e9d2b4f1_users_approval_pending.py`, `backend/pgtest.out`,
`backend/tests/test_student_approval.py`, `backend/tests_pg_api/test_student_approval_pg.py`,
`docs/` (2 HTML files), `frontend/AGENTS.md`, `frontend/src/app/register/`.

**Finding S1 — the Alembic head is uncommitted.** `alembic heads` = `a7c3e9d2b4f1`, which exists only as an
untracked file. A mock-interview migration chained to it would depend on unfinished, unreviewed work; one
chained to the committed head (`f0a1b2c3d4e5`) would create a second head once the approval work lands.
→ Decision required before Phase 2 (see IMPLEMENTATION_PLAN §1).

## 2. Deployment risks

| Risk | Evidence | Consequence for this work |
|---|---|---|
| **Vercel deploys every push to `main`** | README §Staging Deployment | Never commit/push mock-interview work to `main`. Work on a feature branch; merging is your decision. |
| Render may auto-deploy backend + AI service from the repo | README (auto-deploy status unstated) | Same rule. Backend flag must default OFF so a merge is inert. |
| Staging runs `ENVIRONMENT=production` | README §6 | Production startup gates (secret checks, RLS role check) apply to staging. New settings must not add required env vars. |
| Free plans, 512 MB memory cap | README (backend ~120 MiB, AI service ~305 MiB peak) | Pipecat / server-side STT/TTS / a code sandbox **cannot** share the existing Render services. Voice/video/coding need either browser-side processing or separate services. |
| Migrations are manual (`--profile migrate run --rm migrate`) | `deploy/compose.prod.yml`, `backend/Dockerfile` CMD runs only uvicorn | Good: nothing runs migrations on startup. Keep it that way. |
| Frontend flags are **build-time** (`NEXT_PUBLIC_*` baked into the bundle) | `FaceUnavailableNotice.tsx`, compose comment | Frontend flag defaults to OFF when unset; enabling needs a rebuild. Tenant-level gating must come from the API. |
| No Redis on staging → jobs run in-process, not durable | README | Long report generation must tolerate a restart (status column + retry), not rely on a queue. |

## 3. Sensitive files (do not print, copy or edit)

- `backend/.env`, `ai-worker/.env`, `frontend/.env*` and any `prod.env` — secrets. Only key **names** may be listed.
- `deploy/compose.prod.yml`, `deploy/prod.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.gitleaksignore` — infra/CI; no changes planned.
- `backend/app/core/{config,security,csrf,db,staging_gate,security_headers}.py` — auth, tenant GUC, CSRF. Only **additive** settings fields in `config.py` are planned.
- `ai-worker/main.py` token middleware — new routers are covered by it automatically; do not modify it.

## 4. Behaviour that must remain unchanged

1. Auth: login/refresh/logout, httpOnly cookies + Bearer, refresh rotation, CSRF origin check.
2. RBAC roles `student|teacher|tpo|admin|recruiter`; `require_roles`, `get_scoped_user`.
3. Multi-tenancy: app-layer `tenant_id` filters **and** Postgres RLS via `app.current_tenant_id()` (`apply_tenant_guc`).
4. Dual mounting of every router at `/v1/...` and the deprecated unversioned path.
5. Resume builder/ATS (`/resume/*`), AI Mentor (`/mentor/*`, SSE streaming + stored memory).
6. AI worker contract: `X-Worker-Token`, existing `/score`, `/mentor`, `/resume`, `/face` routes, `LLM_PROVIDER` groq|gemini.
7. Student sidebar (`NAV_BY_ROLE`) — new entry only when the flag is on.
8. `/version` currently reports `0.35.0`; `tests/test_versioning.py` may assert on routes/version — check before touching `API_VERSION`.

## 5. High-risk integration points (and the planned touch)

| Point | Planned change | Risk control |
|---|---|---|
| `backend/main.py` `FEATURE_ROUTERS` | +1 import, +1 list entry | Router gated by a dependency → 404 when disabled. Covered by a "disabled by default" test. |
| `backend/app/core/config.py` | +2 optional fields, default OFF | No new required env var; existing `test_config_hardening.py` must still pass. |
| `backend/app/models/__init__.py` | +1 import block | Importing registers tables in `Base.metadata`; the SQLite suite runs `create_all`, so new models must be SQLite-compatible (no JSONB/ARRAY). |
| `ai-worker/main.py` | +1 `include_router` (conditional on a worker flag) | Token middleware unchanged. |
| `frontend/src/lib/auth.tsx` `NAV_BY_ROLE` | Nav entry injected only when flag on | Build-time default off ⇒ identical sidebar for everyone. |
| Alembic | New file(s), new tables only | Additive; tested on throwaway SQLite + a local Postgres; never run against staging/prod by me. |

## 6. Tests that protect current features

- `backend/tests/` (SQLite, offline, run in CI): auth, refresh, CSRF, RBAC, tenant isolation, users isolation, RLS
  (SQLite-side), mentor memory, versioning, config hardening, security headers, staging gate, jobs, files, notifications…
- `backend/tests_pg_api/` + `test_rls_postgres.py`: real-Postgres suites, **opt-in**, skipped in CI. Need a local throwaway Postgres (docker-compose `postgres:16`).
- `ai-worker/tests/`: worker token security, face tenant filter.
- Frontend: `tsc --noEmit` + `eslint` only (no unit/e2e tests).

Gap: there are no tests for the resume and mentor HTTP flows beyond `test_mentor_memory.py`; the plan adds no changes there, so no characterization tests are needed unless a later phase reuses their internals.

## 7. Safe integration boundaries

- **New code only** in: `backend/app/mock_interview/` (or `app/routers/mock_interview.py` + `app/models/mock_interview.py` + `app/schemas/mock_interview.py`, matching the existing per-domain split), `ai-worker/interview/`, `frontend/src/app/dashboard/mock-interview/`, `frontend/src/components/mock-interview/`.
- **Reuse read-only**: `get_current_user`, `require_roles`, `apply_tenant_guc`, `get_db`, `ai_client._worker_request`, `Resume` rows, `mentor._build_verified_profile` (imported, not modified), worker `llm.get_chat_model/get_structured_model`, `tracing.trace_config`, `ui/states.tsx`, `lib/api.ts`, `useApiResource`.
- **Never**: modify existing tables/columns, existing endpoint responses, auth/CSRF/RLS code, CI, deploy files, env files.

## 8. Rules I will follow in this work

No push, no deploy, no commits to `main`, no `reset --hard` / `clean` / force operations, no connection to staging/prod
databases, no migrations except against throwaway local databases, no reading of secret values, no execution of
student code on the API or AI-worker host.
