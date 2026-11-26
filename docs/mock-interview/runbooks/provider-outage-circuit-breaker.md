# 2. AI provider outage and the circuit breaker

## Symptoms

- Students see "…Nothing you typed was lost…" (503) when starting, answering or asking for the next question; reports say they could not be generated.
- Ops page → AI service health shows a step as **Paused after failures** (breaker open) or a high last-hour error rate.
- Alert `ai_circuit_open` (step = plan / turn / report).

## How the breaker behaves (decision P7-D4, on by default)

- Per step (`plan`, `turn`, `report`), **per backend process** (not shared across processes, even with Redis).
- Opens after `AI_CIRCUIT_FAILURE_THRESHOLD` (5) consecutive failures, or when ≥ `AI_CIRCUIT_MIN_CALLS` (10) calls in `AI_CIRCUIT_WINDOW_SECONDS` (120 s) fail at ≥ `AI_CIRCUIT_ERROR_RATE` (50 %).
- While open (`AI_CIRCUIT_OPEN_SECONDS`, 60 s) calls are refused immediately: the student gets the same 503 at once instead of after a 60–120 s timeout; a `circuit_open` ledger row is written; no worker call is made.
- Then one **trial** call is let through (half-open). Success closes the breaker (alert `ai_circuit_closed`); failure re-opens it.
- Answers are always saved before the worker is called, so students lose nothing; they retry with "Try again".
- Inert while `AI_MOCK_INTERVIEW_ENABLED` is off. `AI_CIRCUIT_ENABLED=false` disables it.
- There is **no backup provider** (P7-D6): the breaker never switches models.

## Triage

1. Ops page → Interview diagnostics for an affected interview, or `ai_usage_events` by request id: which outcome?
   - `unreachable` → the AI worker is down or not reachable (Render/HF status, worker logs, `AI_WORKER_URL`).
   - `timeout` → worker overloaded or provider very slow (worker logs, `mock_interview_llm_step_seconds`).
   - `provider_error` → the worker reached the LLM provider and it failed (provider status page, API key/quota on the worker, `mock_interview_llm_steps_total{outcome="provider_error"}`).
   - `worker_error` with 401 → `AI_WORKER_TOKEN` mismatch between backend and worker.
2. Worker logs: filter by `invocation_id` / `request_id` (see `incident-investigation.md`). Logs carry error classes only.
3. Fix the cause (restart worker, rotate key, wait for the provider). The breaker recovers by itself on the next successful trial.

## Manual actions

| Action | How | Effect |
|---|---|---|
| Stop new interviews everywhere | `AI_MOCK_INTERVIEW_ENABLED=false` (restart) | Feature hidden (404); data kept |
| Stop new interviews for one institute | Ops page (admin) → Pause, or `PUT /v1/mock-interview/admin/controls {"paused": true}` | Running interviews finish (R8) |
| Disable the breaker | `AI_CIRCUIT_ENABLED=false` (restart) | Students wait for full timeouts again |
| Tune thresholds | `AI_CIRCUIT_*` (restart) | — |

Do not lower thresholds to hide a real outage; the breaker only shortens failures.
