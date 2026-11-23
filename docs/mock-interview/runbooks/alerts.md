# 3. Alert delivery and troubleshooting

## Channels

Phase 7 reuses the existing `emit_alert` (`backend/app/observability/alerts.py`):

1. **Always:** one structured log line on logger `campus.alert` with `alert=true`, `alert_name`, `severity` and the alert's fields. Log-based alerting can trigger on it.
2. **Optional:** an HTTP POST `{"text": "[SEVERITY] name: message", ...fields}` to `ALERT_WEBHOOK_URL` (Slack/Discord/generic), 3 s timeout.

Phase 7 alerts go through `app/observability/ai_alerts.py`:
- **throttled** per (name, key) — at most one per `AI_ALERT_THROTTLE_SECONDS` (default 300 s) per backend process;
- **delivered on a background thread** — a slow or failing webhook never delays or fails a student's request;
- **content-free** — fixed messages plus step, institute id, percentages; no names, e-mails or interview text.

## Alert reference

| Name | Severity | Key | Fields | Meaning / action |
|---|---|---|---|---|
| `ai_circuit_open` | error | step | step, consecutive_failures, window_calls | Breaker opened → `provider-outage-circuit-breaker.md` |
| `ai_circuit_closed` | warning | step | step | Recovered after a successful trial |
| `mock_interview_quota_near_limit` | warning | institute:kind:month | tenant_id, kind (`sessions`/`tokens`), percent | An institute used ≥ 80 % of a monthly cap → tell the institute admin / review limits |
| `mock_interview_token_usage_unreported` | warning | institute:month | tenant_id, percent, calls | Only when the institute has a token budget: ≥ `AI_MOCK_INTERVIEW_UNREPORTED_TOKENS_ALERT_PERCENT` % (default 10) of the month's AI calls (once there are ≥ `…_ALERT_MIN_CALLS`, default 20) had unknown or partial token usage, so the budget undercounts. Never blocks. → check provider/model usage reporting (`by_model`, session diagnostics); the session cap stays the hard limit (spec §15.12) |
| `readiness_failed` | critical | — | components | Existing (database unreachable) |

## Troubleshooting

| Problem | Check |
|---|---|
| No webhook messages | `ALERT_WEBHOOK_URL` set on the **backend**? Log line `alert webhook failed` with `error` = exception class (the URL itself is never logged - it contains a secret). |
| Too many messages | Raise `AI_ALERT_THROTTLE_SECONDS`; remember throttling is per process. |
| Messages missing after a restart | Throttle state is in memory; a restart can repeat one alert. |
| Need history | Search logs for `"alert": true`. |

Never paste a webhook URL into tickets or chat: the URL is the credential.
