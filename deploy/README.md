# Campus AI - deployment notes (Phase 7)

Status: the files in this folder are **templates** for a self-hosted Compose
deployment, and that deployment has not been run. Separately, a **public
free-tier staging demo** exists on hosted services (see below). A production
host, domain, proxy topology and cookie policy are **not decided**; no value
below is a guess at them.

| File | Purpose |
|---|---|
| `compose.prod.yml` | Production/staging Compose template: prebuilt digest-pinned images, read-only containers, all caps dropped, owner DB role only in the one-off `migrate` job, no data services, ai-worker unpublished. |
| `prod.env.example` | Key names only. Real values go in the host secret store. Comments on their own lines (see the file header). |
| `check_compose_prod.py` | Policy check over the resolved config (use a **synthetic** env file). The dev `docker-compose.yml` fails it by design. |

`../docker-compose.yml` is the **local dev stack only** (superuser DB, dev
secrets, `ENVIRONMENT=development`). Never deploy it.

## Free-tier public staging: known limitations

Staging uses free plans: Vercel (frontend), Render web services (backend and
ai-worker), Supabase (Postgres + Storage), and Qdrant Cloud, with synthetic
data only. Since 2026-11-17 the production frontend URL is **public** (Vercel
Deployment Protection set to Standard Protection). The backend still answers
only requests carrying the staging-gate header, which the frontend's `/v1`
proxy adds server-side, and the ai-worker still requires its token.

Where each item below was checked:

- **On the hosted services (2026-11-16 and 2026-11-17):**
  - Production mode: `/docs`, `/openapi.json` and `/metrics` return 404.
  - Backend gate: 403 without the gate header.
  - Worker token: 401 without it, or with a wrong one.
  - Public frontend and its `/v1` proxy.
  - AI proof-scoring of one synthetic skill claim, run in-process.
  - One mentor question.
  - One résumé generation and PDF upload.
  - The Storage deletion diagnostic.
  - Face routes disabled.
  - The RLS and role-permission checks.
- **Locally only (2026-11-16):** the memory figures and the 170 s
  cold-start timing at 0.1 CPU.
  These were measured on images built from commit `f6de3d5`, with each
  container capped at 512 MB RAM and the ai-worker also capped at 0.1 CPU.
  They have not been measured on Render.

- **`ENVIRONMENT=production` on staging.** The setting accepts only
  `development`, `test` or `production`. Staging runs as `production` so the
  startup secret checks, the closed `/docs` and `/metrics`, and Secure cookies
  all apply.
- **No Redis and no separate job worker.** Render does not offer background
  workers on its free plan. With `REDIS_URL` unset, AI proof-scoring runs
  in-process as FastAPI `BackgroundTasks` (see `backend/jobs.py`).
  - **Limitation:** a job is lost if the API instance restarts, redeploys or
    goes to sleep while the job is running. The claim then stays `pending`
    with no `ai_score`. Nothing retries it. A teacher still reviews and
    decides every claim, so the score is advisory only.
  - Rate-limit counters are kept in memory, per instance, and reset on
    restart.
- **The ai-worker is a public Render URL protected only by its token.**
  Private networking is not available on the free plan.
  - Every path except `/` and `/health` returns 401 without the correct
    `X-Worker-Token`.
  - In production, the worker refuses to start if the token is shorter than
    32 characters.
- **Mentor cold start.** Free Render services sleep after 15 minutes idle.
  After a wake, the first mentor request downloads and loads the embedding
  model. On Render (2026-11-17) the first question took about 49 s
  server-side and succeeded, close to the backend's 60 s worker timeout. In
  the local test at 0.1 CPU it took about 170 s. So the first mentor question
  after a wake may fail; timing varies after each wake. Later requests took
  about 1 s locally. To warm the worker up, send one mentor question and retry
  after about 3 minutes if it fails.
- **Memory measured locally at 512 MB:**
  - backend: about 120 MiB
  - ai-worker: about 85 MiB idle and about 305 MiB peak during mentor
    retrieval
  - Not measured yet: LLM scoring with a real key, and resume/ATS.
- **Deleted files stay downloadable until each issued link expires.** Supabase
  serves signed downloads through Cloudflare's cache. In the 2026-11-17
  diagnostic, a file deleted from Storage was still served from that cache
  through a link issued before the delete, until the link expired. After
  expiry the link returned 400 (`InvalidJWT`, the expiry check). A new link
  for the deleted path was refused. So the window lasts as long as the link
  does: 10 minutes by default and up to 24 hours (`backend/storage.py`).
  Shortening it would need a code change.
- **Face attendance is disabled and UNTESTED.** The Render ai-worker runs
  with `FACE_ENABLED=false`. Verified on 2026-11-17 with requests that cannot
  reach the face handlers: `/face/match`, `/face/enroll` and `/face/embed`
  return 503 ("Face attendance is disabled on this deployment.") before any
  model loads. Qdrant held only `career_kb` before and after the check; no
  face collection exists. The InsightFace model, its licence terms and its
  real behaviour have not been verified. Its memory use at 512 MB is unknown,
  and loading it may crash the worker. Keep it disabled.
- **Tables without forced RLS.** On 2026-11-17, at migration head
  `f0a1b2c3d4e5`, 31 of the 39 tables in `public` had RLS enabled and forced.
  The other 8 did not:
  - With a `tenant_id` column: `feedback`, `leads`, `users`,
    `tenant_invites` and `refresh_tokens` (the last has RLS enabled with one
    policy, but not forced).
  - Without one: `tenants`, `alembic_version` and `face_enrollments` (keyed
    by `student_id`).

  `app_user` can read all 8. It can also insert, update and delete in all of
  them except `alembic_version`, where it can only read. On these tables
  isolation therefore depends on the application code filtering by tenant,
  not on the database.
- **Restricted-role permissions observed (2026-11-17, read-only checks).**
  - All 39 tables are owned by `campus_owner` (not superuser, not
    `BYPASSRLS`).
  - `app_user` (not superuser, not `BYPASSRLS`) has `SELECT` on all 39
    tables, `INSERT`/`UPDATE`/`DELETE` on 38, `TRUNCATE` on none, and no
    `CREATE` on schema `public`.
  - Supabase's API roles (`anon`, `authenticated`, `service_role`) have
    `USAGE` on schema `public` but **no table privileges** on any of the 39
    tables. There are no `PUBLIC` table grants. None of them, nor
    `authenticator`, is a member of `app_user` or `campus_owner`.
    `service_role` has `BYPASSRLS`, but that does not grant table
    privileges.
  - Supabase's admin role `postgres` is a member of both `app_user` and
    `campus_owner`.
  - **Supabase Data API: not disabled.** The REST endpoint is still
    reachable. During the check, every request (including the API root)
    returned 503 `PGRST002` (schema cache could not be loaded), so no table
    data was returned. This is a failure state, not a deliberate setting, and
    may change.
  - These are point-in-time observations. Re-run the privilege check after
    any migration or role change. The optional `REVOKE` in
    `backend/scripts/app_role.template.sql` has not been applied.

## Public endpoint exposure (production)

| Endpoint | Production | Notes |
|---|---|---|
| `/docs`, `/redoc`, `/openapi.json` (API + ai-worker) | **404** | Not registered when `ENVIRONMENT=production`. |
| `/metrics` (API + ai-worker) | **404** unless `METRICS_TOKEN` is set | Decided in AI Mock Interview Phase 7.9 (P7-D7): with a valid dedicated `METRICS_TOKEN` (>= 32 chars, different from every other secret) the endpoint is registered and requires `Authorization: Bearer <METRICS_TOKEN>` (constant-time compare, 401 otherwise; on the worker it is exempt from `X-Worker-Token`). Empty or invalid token = not registered, as before. See `docs/mock-interview/runbooks/metrics-access.md`. |
| `/health` | 200 `{"status":"healthy"}` | Liveness. Dependency-free; safe to expose. Use for container restarts. |
| `/health/ready` | 200/503 | Readiness: DB required, Redis optional. Reveals only status, latency and an exception class name. Keep it for platform health checks; restrict at the proxy if the platform allows private health checks. |
| `/db-check` | **404** | Not registered in production (Phase 7A decision); dev/test only. Use `/health/ready` for DB readiness. |
| `/`, `/version` | 200 | App name and API version only. |
| ai-worker `/`, `/health` | 200 | The only open worker paths in production. Everything else needs `X-Worker-Token`, compared in constant time. |

## Security headers

- **API:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`, a strict `default-src 'none'` CSP, COOP and
  Permissions-Policy on every response, including 403/404/429 responses.
  HSTS is sent only when `HSTS_MAX_AGE > 0`.
- **Frontend:** CSP built at image build time from `NEXT_PUBLIC_API_BASE_URL`
  plus `CSP_CONNECT_SRC_EXTRA`. The storage origin must be listed there, or
  signed-URL uploads are blocked. Other headers: `X-Frame-Options: SAMEORIGIN`,
  `nosniff`, `Referrer-Policy`, Permissions-Policy, COOP. HSTS only with
  `HSTS_MAX_AGE` set at build time.
- **HSTS rollout:** first confirm HTTPS-only on the final domain. Start with
  `HSTS_MAX_AGE=300`, then move to `31536000`. `includeSubDomains` and preload
  are separate decisions.

## Migration `f0a1b2c3d4e5`: invite tokens stored as SHA-256 digests

- **Chain:** `e7f8a9b0c1d2` → `5e1f0a7b9c3d` (`resumes.file_path`, additive)
  → `f0a1b2c3d4e5` (invite token hashing).
- **What it changes:** renames `tenant_invites.token` and
  `recruiter_invites.token` to `token_hash`, and hashes every existing value
  in place. It adds a `CHECK` constraint for a 64-character hex digest, renames
  the indexes, and rebuilds the `invite_token_lookup` RLS policy to use
  `app.invite_token_hash`. Everything runs in one transaction.
- **Existing pending invites keep working:** the stored digest equals what the
  new code computes from the link the user already has. Verified on the
  isolated test cluster with synthetic rows, through the real API.
- **API contract is unchanged:** the create response still returns the token
  once, and accept still takes the raw token.
- **Code/schema coupling:** new code needs the new schema, and old code fails
  against it (column renamed). Deploy order: back up → migrate → roll out the
  new images. Pending invites cannot be accepted in the gap between those
  steps, so keep the gap short.
- **Verified (Phase 7A)** on the production image `prod-7a-rc1` against a disposable
  database. After a downgrade the OLD backend refuses a pending invite (400). After
  re-upgrading, the NEW backend accepts the same invite (200), with no double hash.
- **Downgrade:** structural only. Values stay digests because hashing is
  irreversible, so invites still pending at downgrade time cannot be accepted
  by the old code and must be re-issued. Nothing is deleted. Upgrading again
  skips rows that are already digests (no double hash), so those invites work
  again. Prefer rolling forward.

## Release / migration runbook (each step needs explicit approval)

1. **Target check:** confirm the database identity (host, database, `pg_control_system()`
   system identifier) and the current head (`alembic current`). Never point at
   the old compromised project.
2. **Backup:** run `scripts/backup/pg_backup.sh` with the **owner** role and
   record the file plus its sha256. Prove the backup restores **into a scratch
   database** (`verify_restore.sh`) before migrating. Never restore over a
   live DB.
3. **Migrate:** run `docker compose ... --profile migrate run --rm migrate`.
   Confirm the head is `f0a1b2c3d4e5`.
4. **Roll out:** backend, then worker, then ai-worker, then frontend. Smoke
   test `/health`, `/health/ready`, login, one tenant-scoped read, and an
   invite create + accept.
5. **Rollback:**
   - If the app is bad but the schema is fine, redeploy the previous image
     digests. Those need the old schema, so `alembic downgrade 5e1f0a7b9c3d`
     first, accepting the pending-invite caveat above.
   - If data is damaged, restore the step-2 backup into a **new** database and
     repoint the app. The damaged DB is kept for forensics and not dropped.
