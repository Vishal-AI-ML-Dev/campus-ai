# 8. Data retention and deletion (decision P7-D5)

## Policy

- `ai_usage_events` keeps rows for **400 days**.
- Deletion is **manual and audited** with `backend/scripts/purge_ai_usage.py`. It is never scheduled.
- The script deletes **only** `ai_usage_events` rows older than the cutoff. It never touches interview
  sessions, turns, reports, users or audit logs.
- Interview content retention (sessions/turns/reports) is unchanged by Phase 7 and out of its scope.

## Procedure (needs an approved change for any shared environment)

```bash
cd backend
# 1. Dry run (default): per-institute counts, nothing changes
uv run python -m scripts.purge_ai_usage
#    optional: --tenant-id <id>   one institute only
#              --days <n>         n >= 400 (shorter is refused)
# 2. Apply: pass the TOTAL printed by the dry run
uv run python -m scripts.purge_ai_usage --apply --expected-count <TOTAL>
```

- `--apply` without `--expected-count`, or with a different number, deletes nothing (exit code 3) — this stops a
  stale or mistyped command.
- Per institute, the delete and one `audit_logs` row (`action = ai_usage.purge`,
  `actor_email = system:purge_ai_usage`, summary with the count, cutoff date and retention days) are committed in
  one transaction.
- It runs under Postgres RLS with the normal runtime role (it sets each institute's tenant context).
- It reads `DATABASE_URL` like the app: double-check which database it points at before `--apply`.

## Data-subject requests

Usage rows contain the student's user id (set to NULL if the user is deleted) but no content. To find a
student's rows: `ai_usage_events WHERE tenant_id = … AND user_id = …` (tenant context required under RLS).
