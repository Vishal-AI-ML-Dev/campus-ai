# AI Mock Interview — Architecture Audit (Phase 0)

What exists today, and what the mock interview module will reuse from it.

## 1. System shape

```
Browser ─▶ Next.js 16.4 (Vercel) ─same-origin /v1 proxy + staging gate header─▶ FastAPI backend (Render)
                                                                                 │  httpx + X-Worker-Token, retry (tenacity)
                                                                                 ▼
                                                              FastAPI AI worker (Render): LangChain/LangGraph,
                                                              Groq|Gemini, Qdrant RAG, InsightFace
Postgres (Supabase) with RLS ◀── backend only.   Redis optional (RQ jobs, rate limits, cache).
```

The AI worker is **stateless and has no DB access**; the backend owns all persistence and replays history to the worker
(see `backend/app/routers/mentor.py` docstring). The interview engine will follow the same split.

## 2. Backend (`backend/`)

| Concern | Where | Convention to follow |
|---|---|---|
| App + router mount | `main.py` `FEATURE_ROUTERS` | Each router mounted at `/v1` **and** the deprecated unversioned path. |
| Settings | `app/core/config.py` (pydantic-settings, `.env`, `extra="ignore"`) | Optional fields with safe defaults; off-switches documented inline (cf. Supabase storage ⇒ `/files` returns 503). |
| DB | `app/core/db.py` — SQLAlchemy 2.x, `Base`, `get_db`, `set_current_tenant` + `after_begin` GUC re-apply | Use `Mapped[...]`, `mapped_column`; dialect-neutral types (SQLite tests). |
| Auth / RBAC | `app/core/security.py` — `get_current_user`, `get_current_tenant_id`, `require_roles(*UserRole)`, `get_scoped_user`, `apply_tenant_guc` | Router-level `dependencies=[Depends(apply_tenant_guc)]`; app-layer `tenant_id` filters on every query; 404 for objects outside tenant/ownership. |
| Roles | `app/models/enums.py` `UserRole`: student, teacher, tpo, admin, recruiter | Faculty dashboard ⇒ teacher/tpo/admin. Recruiters get **no** access. |
| Models | `app/models/<domain>.py`, re-exported in `app/models/__init__.py` | New `app/models/mock_interview.py`. |
| Schemas | `app/schemas/<domain>.py` | New `app/schemas/mock_interview.py`. |
| Migrations | `alembic/versions/*`, owner-role URL via `MIGRATION_DATABASE_URL` | New tables + `ENABLE/FORCE RLS` + `tenant_isolation` policy (Postgres-only block), exactly as `a1b2c3d4e5f7_mentor_conversations_and_messages.py`. |
| AI calls | `ai_client.py` `_worker_request` (retry on transport/5xx), per-call timeouts | Add mock-interview client functions in a **new** module that imports `_worker_request`; don't edit `ai_client.py`. |
| Background jobs | `jobs.py` (RQ when `REDIS_URL`, else in-process) | Report generation can use it; must be restart-safe (status column). |
| Errors / logging | `app/observability/*` uniform JSON error envelope + request id | Raise `HTTPException`; no custom handlers. |
| Rate limiting | `app/core/rate_limit.py` slowapi `limiter` | Apply `@limiter.limit` to LLM-costly endpoints (answer submit, start). |

### Reusable data sources (read-only)
- **Resume**: `resumes` table (`markdown`, `target_role`, `is_primary`, `file_path`), owner + tenant scoped. Interview grounding uses the student's chosen resume `markdown` — no new resume upload/parsing.
- **Verified profile**: `mentor._build_verified_profile(db, student_id)` → verified skills, verified project contributions, attendance %, CGPA.
- **Placement drives**: `drives` (company/role/JD-like data) — candidate source for "company-based" interviews from data the institute already holds.

## 3. AI worker (`ai-worker/`)

| Concern | Where |
|---|---|
| Provider factory | `llm.py` `get_chat_model()` (groq/gemini via `LLM_PROVIDER`), `get_structured_model(schema)` |
| LangGraph usage | `mentor.py`: `MentorState` TypedDict → `_retrieve_node` → `_generate_node`, compiled once in `_get_graph()` |
| Structured output + fallback | `resume.py` `_ats_invoke`: structured output first, JSON-extract fallback |
| Tracing | `tracing.trace_config(name, provider=...)` (Langfuse optional) |
| Auth | `main.py` constant-time `X-Worker-Token` middleware on all non-open paths |
| Deps | langgraph ≥1.0, langchain ≥1.3, langchain-groq, langchain-google-genai, fastembed, qdrant-client — **no new Python deps needed for text interviews** |

The interview engine will be `ai-worker/interview/` with a LangGraph whose nodes are pure functions over an `InterviewState`. It
is called **one turn at a time** by the backend (stateless, like the mentor): the backend sends the plan + transcript so far, and
the worker returns the next question / evaluation. Voice, video and text all call the same endpoints.

## 4. Frontend (`frontend/`)

- Next.js **16.4.4**, React 19.2 — `AGENTS.md` warns APIs differ from older Next; read `node_modules/next/dist/docs/` before writing routes.
- App Router pages under `src/app/dashboard/<feature>/page.tsx`; feature panels + `<feature>Api.ts` under `src/components/<feature>/`.
- Shared: `lib/api.ts` (`api`, `ApiError`), `lib/useApiResource.ts`, `components/ui/states.tsx` (`LoadingState`, `ErrorState`, `EmptyState`, `Skeleton*`), `lib/auth.tsx` (`useCurrentUser`, `NAV_BY_ROLE`).
- Feature flag precedent: `NEXT_PUBLIC_FACE_ENABLED === "true"` + `FaceUnavailableNotice`.
- Checks: `npx tsc --noEmit`, `npm run lint`. No frontend tests.

## 5. Gaps relevant to the requested scope

| Requirement | Present? | Note |
|---|---|---|
| LangGraph + provider adapters | Yes | Reuse. |
| Resume content | Yes (Markdown) | Reuse; no re-parsing. |
| Job-description analysis | Partial (ATS scoring takes a JD) | New worker node, prompt pattern from `resume.py`. |
| Faculty batches | Sections/departments exist (`sections`, `users.section_id`) | Campaign "batch" = one or more sections. |
| STT/TTS/WebRTC/Pipecat | **No** | Needs a provider decision and a separate service; see plan Phase 5. |
| Code sandbox | **No** | Needs a separate isolated service (e.g. self-hosted Judge0/Piston); see plan Phase 7. |
| Object storage for recordings | Supabase signed URLs (`/files`, off on staging) | Default: **do not record audio/video**; store transcripts only. |
