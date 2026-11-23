# AI Mock Interview — operations runbooks (Phase 7)

Operational guides for the AI Mock Interview production-operations foundation
(spec: `../specs/phase-7-production-operations.md`). Every procedure here is
**manual and needs an explicit, approved change** before it touches a shared
environment. Nothing in these runbooks has been executed against staging or
production; live drills are part of the Phase 10 final-acceptance milestone.

| # | Runbook | Use it when |
|---|---|---|
| 1 | [ai-usage-monitoring.md](ai-usage-monitoring.md) | Daily/weekly checks of AI usage, failures, latency, tokens |
| 2 | [provider-outage-circuit-breaker.md](provider-outage-circuit-breaker.md) | Interviews fail with "nothing you typed was lost"; breaker open |
| 3 | [alerts.md](alerts.md) | Configuring or troubleshooting alert delivery |
| 4 | [institute-controls-quotas.md](institute-controls-quotas.md) | Pausing an institute, setting limits, quota complaints |
| 5 | [metrics-access.md](metrics-access.md) | Setting up the Prometheus scrape of `/metrics` |
| 6 | [pricing.md](pricing.md) | Supplying the approved model price table |
| 7 | [privacy-observability.md](privacy-observability.md) | What telemetry contains (and never contains) |
| 8 | [data-retention.md](data-retention.md) | The 400-day usage-ledger purge |
| 9 | [deployment-migrations.md](deployment-migrations.md) | Rolling Phase 7 out, flags, migrations, rollback |
| 10 | [incident-investigation.md](incident-investigation.md) | Tracing one failed interview by request id; kill switch |

## Flags and settings at a glance

| Setting | Service | Default | Effect |
|---|---|---|---|
| `AI_MOCK_INTERVIEW_ENABLED` | backend | off | Master switch (unchanged) |
| `AI_MOCK_INTERVIEW_OPS_ENABLED` | backend | **off** | Institute ops API, quotas, `/usage/me` |
| `AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX` / `…_TOKEN_MAX` | backend | empty = no cap | Platform ceilings for institute quotas |
| `AI_CIRCUIT_ENABLED` + `AI_CIRCUIT_*` | backend | **on** (approved exception P7-D4) | Circuit breaker; inert while the master switch is off |
| `AI_ALERT_THROTTLE_SECONDS` | backend | 300 | Min gap between identical alerts |
| `AI_PRICE_TABLE` | backend | empty | Approved price table; empty = cost "not configured" |
| `METRICS_TOKEN` | backend + worker | empty | Dedicated bearer token for production `/metrics` |
| `NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED` | frontend build | off | Interview Ops page + nav, remaining-quota line |
| `ALERT_WEBHOOK_URL`, `SENTRY_DSN` | existing | empty | Unchanged meaning; see alerts / privacy runbooks |
