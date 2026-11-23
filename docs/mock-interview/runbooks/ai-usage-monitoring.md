# 1. AI usage monitoring

## Where the numbers come from

| Source | Scope | What it holds |
|---|---|---|
| `ai_usage_events` (backend DB, RLS) | per institute | One row per AI invocation: step (`plan`/`turn`/`report`/`create`), outcome, HTTP status, error **class**, provider, model, LLM calls, provider-reported tokens (NULL = unknown), latency, request id, invocation id (`event_id`). No interview content. |
| Interview Ops page (`/dashboard/interview-operations`) | own institute (admin, TPO) | Usage tiles, daily chart, breakdowns by step / model / outcome / mode, cost status, AI health, per-student counts, diagnostics |
| `GET /v1/mock-interview/admin/usage?days=N` | own institute | Same data as JSON (N = 1..90) |
| Prometheus (`/metrics`, token in production) | whole process, **no tenant labels** | Backend: `mock_interview_worker_calls_total{step,outcome}`, `mock_interview_worker_call_seconds{step}`, `ai_usage_ledger_writes_total{result}`, `ai_circuit_state{step}`, `mock_interview_quota_blocks_total{reason}`. Worker: `mock_interview_llm_steps_total{step,provider,model,outcome}`, `mock_interview_llm_step_seconds{step,provider}`, `mock_interview_llm_tokens_total{provider,model,direction}` |

## Routine checks

1. **Failure rate** — `rate(mock_interview_worker_calls_total{outcome=~"provider_error|worker_error|timeout|unreachable"}[15m]) / rate(mock_interview_worker_calls_total[15m])`. Investigate above ~10 % sustained.
2. **Latency** — p95 of `mock_interview_worker_call_seconds` per step. Turns normally take seconds; reports up to ~2 min.
3. **Fallbacks** — `outcome="fallback"` (deterministic plan, heuristic evaluation, rule-based report items). A rising share means the provider returns unusable structured output.
4. **Ledger health** — `ai_usage_ledger_writes_total{result=~"lost|queued"}`. `lost` > 0 means rows were dropped (DB unavailable and no Redis queue); `queued` means writes went through RQ.
5. **Unknown tokens** — the Ops page shows "call(s) with unknown usage". If the provider stops reporting usage, token budgets under-count; see `pricing.md`.

## Interpreting outcomes

| Outcome | Meaning | Counts against breaker |
|---|---|---|
| `ok` | Worker answered, no fallback | no (success) |
| `fallback` | Worker answered using a fallback path | no (success) |
| `provider_error` | Worker 502: the LLM step failed | yes |
| `worker_error` | Other worker status / invalid response (`http_status` tells which; 4xx = healthy worker) | 5xx/none yes, 4xx no |
| `timeout` / `unreachable` | No answer in time / no connection | yes |
| `circuit_open` | Refused by the breaker, no worker call | — |
| `quota_blocked` | New interview refused by an institute control (`error_class` = reason) | — |

Token totals ignore NULL (unknown) values; they are never treated as 0 in cost.
