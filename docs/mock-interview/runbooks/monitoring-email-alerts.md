# 14. Monitoring and e-mail alerts on the free tier (Phase 10.5, D5 + OQ-4)

Owner decisions: alerts go by **e-mail** (D5), delivered by **free external monitors**, so no SMTP credentials live
in this repository (OQ-4). The owner enters the alert e-mail address in each provider dashboard; it is never written
to the repo, an env file or a log. Free-plan limits of every provider below are **UNVERIFIED** until the owner reads
them when creating the account.

## What the application provides (implemented, tested)

| Signal | Where | Content |
|---|---|---|
| Liveness | `GET /health` (backend and worker) | `{"status": "healthy"}` |
| Readiness | `GET /health/ready` (backend) | 503 when the database is down (`readiness_failed` alert); optional `redis`, `queue` (`in_process` = no Redis), `ai` (`degraded` + reason codes while new AI calls are refused) |
| Structured logs | every request, JSON, `request_id` + `invocation_id` (correlation) | method, path, status, duration; no bodies |
| Alerts | `campus.alert` log lines (+ optional webhook) | names/ids/counts only - see `alerts.md` |
| Metrics | `/metrics` with `METRICS_TOKEN` (production) | request counts/latency, AI calls/outcomes, governance refusals, ledger writes, circuit state, `frontend_errors_total` |
| Frontend errors | `POST /client-errors` (flags `CLIENT_ERROR_REPORTING_ENABLED` + `NEXT_PUBLIC_CLIENT_ERROR_REPORTING_ENABLED`, both off) | kind, error class name, normalised route, release, digest - never messages or stacks |
| Backup failure | `backup_all.sh` heartbeat ping + `backup_failed` line | stage, exit code |

## Monitors to create (owner action; nothing is configured yet)

| D5 alert | Monitor | Target | Fires when |
|---|---|---|---|
| Critical service outage | Uptime monitor (HTTP) | backend `/health`, worker `/health`, frontend `/` | non-200 / timeout for 2 checks; **recovery e-mail** when it returns |
| Database connectivity | Uptime monitor (HTTP) | backend `/health/ready` | 503 |
| AI quota exhaustion | Uptime keyword monitor | backend `/health/ready` | body contains `"degraded"`; plus Sentry rule on `ai_quota_exhausted` / `ai_platform_quota_reached` |
| Repeated backend errors | Sentry (free plan) alert rule | backend + worker projects (`SENTRY_DSN`) | error events > N in 5 min (5xx, `campus.alert` errors) |
| Backup failure | Heartbeat (cron) monitor | `BACKUP_HEARTBEAT_URL` | no ping within 26 h, or a `/fail` ping |

Notes:
- The staging gate (`STAGING_GATE_TOKEN`) returns 403 for every path except `GET /health`; an external monitor can
  therefore watch `/health` only, unless the owner approves sending the gate header from the monitor (it would then
  hold that secret). Record the choice.
- Render free services sleep after 15 minutes idle; an uptime monitor pinging every few minutes keeps them awake
  and uses the free monthly instance hours - check the current free-plan terms before enabling it (OQ-5).
- Sentry privacy filters are already in place (`privacy-observability.md`); interview LLM calls never reach Sentry AI
  integrations.

## Severity levels (proposal for gate G15; owner to confirm and add the contact path)

| Level | Examples | Response target |
|---|---|---|
| SEV1 | Platform down, database unreachable, suspected data exposure or cross-institute leak, any sign of paid AI usage | Immediately: kill switch if AI-related, follow `incident-investigation.md` |
| SEV2 | AI unavailable for everyone (outage, quota, cap), backup failed, repeated 5xx | Same day |
| SEV3 | One institute's cap reached, single-feature errors, frontend error spikes | Next working day |

Owner contact path (who is e-mailed, who decides a rollback): **to be written by the owner** — not invented here.

## Drill (10.9)

Each D5 alert type must be **delivered by e-mail once** (and the outage recovery e-mail once) with dated evidence -
gate G8. In this repository the alert *generation* is tested in isolation (`backend/tests/test_monitoring_phase10.py`:
every D5 alert type is emitted and is content-free). **E-mail delivery is BLOCKED until the owner creates the
monitors and enters the e-mail address.**
