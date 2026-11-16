# Campus AI - deployment notes (Phase 7)

Status: **templates only.** Nothing here has been deployed. Hosting, domain,
proxy topology and cookie policy are **not decided**; no value below is a guess
at them.

| File | Purpose |
|---|---|
| `compose.prod.yml` | Production/staging Compose template: prebuilt digest-pinned images, read-only containers, all caps dropped, owner DB role only in the one-off `migrate` job, no data services, ai-worker unpublished. |
| `prod.env.example` | Key names only. Real values go in the host secret store. Comments on their own lines (see the file header). |
| `check_compose_prod.py` | Policy check over the resolved config (use a **synthetic** env file). The dev `docker-compose.yml` fails it by design. |

`../docker-compose.yml` is the **local dev stack only** (superuser DB, dev
secrets, `ENVIRONMENT=development`). Never deploy it.

## Public endpoint exposure (production)

| Endpoint | Production | Notes |
|---|---|---|
| `/docs`, `/redoc`, `/openapi.json` (API + ai-worker) | **404** | Not registered when `ENVIRONMENT=production`. |
| `/metrics` (API + ai-worker) | **404** | Not collected or registered. Monitoring access is a pending decision (options: private scrape network, or a bearer-token protected route). |
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
