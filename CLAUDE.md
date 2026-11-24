# Campus AI — notes for Claude Code

Multi-tenant campus platform: `backend/` (FastAPI, SQLAlchemy 2, Alembic, Postgres RLS), `ai-worker/` (separate FastAPI
service: LangChain/LangGraph, Groq|Gemini, Qdrant), `frontend/` (Next.js 16 — read `frontend/AGENTS.md` first).

## Hard rules
- Never commit to or push `main`: Vercel deploys every push to `main`. No deploys, no pushes without explicit approval.
- Never read or print `.env` values (list key names only). Never connect to staging/production databases.
- Never run Alembic against anything but a throwaway local DB. Migrations are additive only.
- Don't change existing API responses, tables, columns or env vars. New features go behind a flag that defaults OFF.
- Never run `git reset --hard`, `git clean`, force-push or force-checkout. Stage files explicitly.

## Conventions
- Routers: `backend/app/routers/<feature>.py`, added once to `FEATURE_ROUTERS` in `backend/main.py` (mounted at `/v1` and unversioned).
- Tenant-scoped routers use `dependencies=[Depends(apply_tenant_guc)]` and filter every query by `tenant_id`; objects outside the caller's tenant or ownership return 404.
- New tenant tables: `tenant_id` FK + `ENABLE/FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy (copy `a1b2c3d4e5f7_mentor_conversations_and_messages.py`). Types must also work on SQLite.
- The AI worker is stateless; the backend owns persistence and calls the worker through `ai_client._worker_request`.
- Frontend: pages in `src/app/dashboard/<feature>/page.tsx`, panels and API helpers in `src/components/<feature>/`; use `useApiResource` and `components/ui/states.tsx`.

## Testing
- Backend: `cd backend && .venv/Scripts/python -m pytest` (SQLite, offline, ~10+ min). All runs share `%TEMP%\campus_ai_test.db`, so **never run two at once**.
- Postgres suites (`tests_pg_api/`, `test_rls_postgres.py`) are opt-in and need a local throwaway Postgres.
- Frontend: `npx tsc --noEmit` and `npm run lint`.
- Write test output to the session scratchpad, never to a drive root.

## AI Mock Interview (in progress)
State, plan and audits: `docs/mock-interview/` — start with `PROJECT_STATE.md`.
Flags (all default off): backend `AI_MOCK_INTERVIEW_ENABLED`, `AI_MOCK_INTERVIEW_TENANT_IDS`, `AI_MOCK_INTERVIEW_VOICE_ENABLED`,
`AI_MOCK_INTERVIEW_VIDEO_ENABLED` + `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS` (empty = no institute); worker `MOCK_INTERVIEW_ENABLED`;
frontend build `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED`, `NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED`.
Phase 8 (coding, review-only — no code is ever executed): backend `AI_MOCK_INTERVIEW_CODING_ENABLED` +
`AI_MOCK_INTERVIEW_CODING_TENANT_IDS` (empty = no institute), dev-only `AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS`
(ignored in production); frontend `NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED`. Hidden tests/reference solutions never
go in this repo (`backend/app/coding_bank/` is public material only).
Phase 8.3/8.4 (Batch 2): backend `AI_MOCK_INTERVIEW_CODING_AI_ENABLED` (AI hints, typed explanation turns,
review-only feedback in `mock_interview_code_reviews` — never a score) and `AI_MOCK_INTERVIEW_COMPANY_ENABLED`
(company profiles; generation only); worker `MOCK_INTERVIEW_CODING_ENABLED`, `MOCK_INTERVIEW_COMPANY_ENABLED`. All off.
Phase 8.5 (Batch 3): `code-runner/` is a standalone Docker + gVisor runner (not connected to the backend; no flag yet).
Its hostile fixtures (`code-runner/tests/escape/`) run ONLY on a host that passes `sandbox.self_check` (pinned gVisor);
never on plain Docker or a dev machine. Hidden tests/references live in a separate private test-pack repository.
`code-runner/runner/packformat.py` must stay byte-identical to that repository's `tools/packformat.py`.
Phase 7 (operations): backend `AI_MOCK_INTERVIEW_OPS_ENABLED` (off), `AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX` /
`…_TOKEN_MAX` (empty = no cap), `AI_PRICE_TABLE` (empty = cost not configured; never invent prices), `METRICS_TOKEN`
(backend + worker; empty = production `/metrics` 404), frontend `NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED` (off).
**Exception (approved P7-D4):** the AI circuit breaker `AI_CIRCUIT_ENABLED` is ON by default (inert while
`AI_MOCK_INTERVIEW_ENABLED` is off). Interview LLM calls must never reach Langfuse/LangSmith/Sentry AI integrations.
Runbooks: `docs/mock-interview/runbooks/`.
