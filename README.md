# Campus AI

A multi-tenant campus management and placement platform with AI features
built in. A single deployment serves many institutes. Each institute's data is
isolated at the database level with PostgreSQL Row-Level Security (RLS).

Students, teachers, training & placement officers (TPOs), institute admins and
external recruiters each get a role-specific dashboard. The dashboards cover
academics, attendance, placements, and AI career tools such as a résumé
builder, ATS scoring and a career mentor.

> **Project status:** in active development. Security reviews and automated
> tests have been carried out during development (see
> [Security design](#security-design) and [Tests](#tests)). Even so, **the
> current version is not production-ready**, and **there is no verified live
> deployment** or public demo. This repository holds the source code. See
> [Current limitations](#current-limitations).

---

## Architecture

```
                ┌──────────────────────┐
  Browser ────▶ │  frontend (Next.js)  │
                └──────────┬───────────┘
                           │ HTTPS, cookie auth + CSRF, /v1 API
                ┌──────────▼───────────┐        ┌──────────────┐
                │  backend (FastAPI)   │──────▶ │  PostgreSQL  │  RLS per tenant
                │  REST API, RBAC      │        └──────────────┘
                └───┬──────────────┬───┘        ┌──────────────┐
                    │ enqueue      └──────────▶ │    Redis     │  rate limits, cache, job queue
                ┌───▼──────────────┐            └──────▲───────┘
                │ worker (RQ jobs) │───────────────────┘
                └───┬──────────────┘
                    │ X-Worker-Token (internal only)
                ┌───▼──────────────────────┐    ┌──────────────┐
                │ ai-worker (FastAPI)      │──▶ │    Qdrant    │  vectors (RAG, faces)
                │ LLM, RAG, face matching  │    └──────────────┘
                └──────────────────────────┘
```

| Component | Path | Stack |
| --- | --- | --- |
| Web UI | `frontend/` | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| API | `backend/` | FastAPI, SQLAlchemy 2, Alembic, Pydantic, python-jose (JWT), slowapi |
| Job worker | `backend/worker.py` | RQ on Redis (falls back to in-process tasks when Redis is absent) |
| AI worker | `ai-worker/` | FastAPI, LangChain / LangGraph (Groq or Gemini), fastembed, Qdrant, InsightFace + ONNX Runtime |
| Shared observability | `packages/campus_obs/` | JSON logs with request IDs, Prometheus metrics, optional Sentry, consistent error format |
| Deployment templates | `deploy/` | Hardened Compose template and a policy checker (templates only, not deployed) |
| Backup scripts | `scripts/backup/` | Postgres backup/restore with restore verification, Qdrant snapshots |

## Features

**Roles:** `student`, `teacher`, `tpo`, `admin`, `recruiter`. API routes
enforce role-based access control (RBAC).

- **Academics:** departments, timetable, assignments, gradebook, study
  materials, doubts (Q&A), calendar, announcements, leave / on-duty requests.
- **Attendance:** manual attendance plus optional face-recognition
  attendance. Students enrol their faces, and the AI worker matches photos
  against enrolled students within the same institute.
- **Placements:** placement drives, applications, offers, internships,
  placement analytics for TPOs, and a recruiter portal where invited
  recruiters review candidates and manage offers.
- **AI career tools:**
  - The AI worker uses an LLM to score the evidence students submit for skills
    and projects. Scoring runs as background jobs.
  - The AI Career Mentor is a chat grounded in the student's verified profile,
    using retrieval-augmented generation (RAG) over Qdrant.
  - A résumé builder scores résumés against a job description (ATS scoring).
- **Institute admin:** institute sign-up, user invitations and bulk CSV
  import, audit log, notifications, analytics.

## Security design

These controls are implemented in code. They were checked through internal
security reviews during development and are covered by the test suites below.
There has been no independent external audit or penetration test.

- **Tenant isolation:** PostgreSQL RLS policies on tenant-scoped tables.
  Production uses a restricted `NOBYPASSRLS` application role; only one-off
  migration jobs use the owner role.
- **Auth:** a JWT access token and a rotating, single-use refresh token, both
  in `httpOnly` cookies. Reusing a refresh token revokes its whole token
  family. State-changing requests require CSRF protection.
- **Invite tokens:** stored as SHA-256 digests, never in plaintext.
- **Hardening:**
  - Rate limiting.
  - Strict security headers and CSP on the API and frontend.
  - In production, `/docs`, `/openapi.json` and `/metrics` are not exposed.
  - The AI worker requires a shared token, compared in constant time.
  - Containers run as non-root users.
- **Startup checks:** with `ENVIRONMENT=production`, the API refuses to start
  if `SECRET_KEY` is weak or `AI_WORKER_TOKEN` is missing.

## Running locally (development only)

> ⚠️ **The root `docker-compose.yml` is for local development only. Never
> deploy it or expose it to a network.**
> - It uses throwaway, publicly visible credentials for the database, the
>   `SECRET_KEY` and the worker token.
> - It connects to Postgres as the superuser, so **RLS is not enforced** in
>   this stack.
> - It sets `ENVIRONMENT=development`.
>
> Its ports are bound to `127.0.0.1` only. For a hardened setup, see
> `deploy/` instead.

**Requirements:** Docker with Compose v2.

```bash
# Optional: enable LLM features (the stack starts without them; AI calls will fail)
cp .env.example .env        # then add your own GROQ_API_KEY or GOOGLE_API_KEY

docker compose up --build

# The API does not run migrations on startup. Create the schema once:
docker compose run --rm backend alembic upgrade head
```

Then open:

- Frontend: <http://localhost:3000>
- API docs: <http://localhost:8000/docs> (available in development only)

To create demo data, run `backend/scripts/seed_demo.py`. It requires your own
`DEMO_SEED_PASSWORD` (at least 12 characters) and has no built-in accounts.
See `backend/scripts/README.md` for details.

Each service also has an env template for running outside Docker:
`backend/.env.example`, `ai-worker/env.example`, `frontend/demo.env.example`.
**Never commit real `.env` files.** The repo's `.gitignore` excludes them.

## Tests

| Suite | Location | What it covers | How to run |
| --- | --- | --- | --- |
| Backend unit/API | `backend/tests/` | Auth, refresh rotation, CSRF, RBAC, tenant isolation, rate limiting, security headers, config hardening, invite hashing, job queue | `cd backend && uv run pytest` (throwaway SQLite DB, offline, AI calls stubbed) |
| Backend on real Postgres | `backend/tests_pg_api/` | Cross-tenant isolation under real RLS as a `NOBYPASSRLS` role | Requires a **disposable** Postgres. The suite refuses to run against anything else. See the docstring in `tests_pg_api/conftest.py` |
| AI worker | `ai-worker/tests/` | Worker token check, production gates, tenant filtering for face search | `cd ai-worker && python tests/test_worker_security.py` (others need pytest) |
| Frontend E2E | `frontend/e2e/` | Playwright smoke tests and user flows | Needs a running stack and test accounts supplied via env vars. See `frontend/e2e/README.md` |
| Compose policy | `deploy/check_compose_prod.py` | Checks the production Compose template against security rules | Pipe the resolved config into it: `docker compose -f deploy/compose.prod.yml --env-file <synthetic.env> --profile migrate config --format json \| python deploy/check_compose_prod.py` |

Note that a plain `pytest` in `backend/` runs only `tests/`. The real-Postgres
suite is opt-in.

## Current limitations

These are stated honestly so the project is not mistaken for more than it is:

- **Not production-ready, and no verified live deployment.** There is no
  public demo. The `deploy/` folder holds templates only. The hosting
  platform, domain and cookie policy are still open decisions.
- **No CI pipeline** is configured in this repository yet.
- **Some test suites need extra setup.** The end-to-end specs need test
  accounts, and the real-Postgres suite needs a disposable test database. Some
  E2E specs still need updating for cookie-based auth (see
  `frontend/e2e/README.md`).
- **Weak defaults to know about:**
  - Access tokens default to a 24-hour lifetime.
  - `ENVIRONMENT` defaults to `development`, so a deployment must set it
    explicitly or the production startup checks do not apply.
- **AI features need external services:** an LLM API key (Groq or Gemini) and
  Qdrant. Without them, those features return errors and the rest of the app
  keeps working.
- **Face recognition** stores face embeddings, which are biometric data. Any
  real use would need consent flows, a retention policy and a legal review
  that are not part of this project.
- **No license has been chosen yet.** Until one is added, default copyright
  applies.

## Repository layout

```
frontend/          Next.js app (src/app/*: public pages, dashboard/*: role dashboards)
backend/           FastAPI API (app/routers, models, schemas, core), Alembic migrations, tests, scripts
ai-worker/         AI microservice (LLM scoring, mentor RAG, résumé/ATS, face)
packages/campus_obs/  Canonical observability modules synced into backend and ai-worker
deploy/            Production Compose template, env key template, policy checker
scripts/           Backup/restore scripts and the observability sync script
docker-compose.yml Local development stack (development only, see above)
```
