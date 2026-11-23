# 9. Deployment and migration considerations (Phase 7)

**Documentation only — nothing here has been run against staging or production.** Each step needs explicit
approval. No push, merge or deploy is implied.

## Migrations (additive only)

| Revision | Adds | Downgrade |
|---|---|---|
| `c5e9a3b7d2f4` | `ai_usage_events` (+ indexes, unique `event_id`, RLS enabled + forced, `tenant_isolation`) | drops the ledger (usage history only) |
| `d6f0b4c8e3a5` | `ai_feature_controls` (+ RLS) and index `ix_mock_interview_sessions_tenant_created` on the existing sessions table | drops controls (institutes fall back to defaults) and the index |

- Head before Phase 7: `b4d8f2a6c9e1`; after: `d6f0b4c8e3a5` (single head).
- Verified on a throwaway Postgres 16 (full chain from empty, `downgrade -2`, `upgrade head`) and on SQLite for
  the two new revisions (spec §15). Never run Alembic against anything else without approval.
- Run with the **owner** role (`MIGRATION_DATABASE_URL`, existing `migrate` profile). The runtime role gets
  privileges on the new tables through the owner's `ALTER DEFAULT PRIVILEGES` (`scripts/app_role.template.sql`);
  confirm `app_user` can SELECT/INSERT on both tables afterwards.

## Rollout order

1. Deploy backend + worker + frontend with all new flags at defaults → no behaviour change except:
   the circuit breaker (inert while the master switch is off), ledger writes and metrics while the feature runs,
   and production `/metrics` stays 404 until `METRICS_TOKEN` is set.
2. Run the two migrations (owner role) — before or with the backend deploy; the backend tolerates a missing ledger
   table (writes fail → counted `lost`, never breaking a request), but run them first.
3. Optional: set `METRICS_TOKEN` (backend and worker) and configure Prometheus (`metrics-access.md`).
4. Optional: `AI_PRICE_TABLE` once an approved table exists (`pricing.md`).
5. Enable institute operations for a pilot: `AI_MOCK_INTERVIEW_OPS_ENABLED=true` (backend) and build the frontend
   with `NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED=true`.
   **Docker note:** `frontend/Dockerfile` declares `ARG NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED=false` in the builder
   stage (added 2026-11-23). Build with `--build-arg NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=true --build-arg
   NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED=true`; the UI needs both, and the values are inlined at build time.
6. Watch `ai-usage-monitoring.md` checks; widen.

## Rollback

| Problem | Rollback |
|---|---|
| Ops UI/API | `AI_MOCK_INTERVIEW_OPS_ENABLED=false` (routes 404, quotas off); frontend flag off |
| Breaker misbehaving | `AI_CIRCUIT_ENABLED=false` |
| Whole feature | `AI_MOCK_INTERVIEW_ENABLED=false` (unchanged kill switch) |
| Schema | Downgrade only after an explicit decision and a backup |

## Worker

The worker gained the additive `usage` block on `/interview/*` responses and `METRICS_TOKEN`. An older backend
ignores the `usage` field; a newer backend works with an older worker (ledger rows then have unknown usage).
