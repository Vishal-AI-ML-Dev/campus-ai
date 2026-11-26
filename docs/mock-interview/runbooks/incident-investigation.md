# 10. Incident investigation, worker down, kill switch

## Trace one failed interview

1. Get the **request id**: the student's error box shows "Reference: <id>" (`X-Request-ID`), or take the
   interview number from the student.
2. Ops page → Interview diagnostics → interview number: the timeline lists every AI step with outcome, HTTP status,
   error class, latency, tokens and `request_id`. (API: `GET /v1/mock-interview/admin/sessions/{id}/diagnostics`.)
3. Backend logs: filter `request_id = <id>`; the worker-call line also carries `invocation_id`.
4. Worker logs: filter the same `request_id`, or `invocation_id` (= ledger `event_id`), including the report
   scorer's threads.
5. Sentry: tags `request_id` / `invocation_id`; events show exception types and stack frames, messages are
   `[redacted]` by design.

Nothing in these sources contains interview content; do not ask students to paste their answers into tickets.

## Worker down

- Symptoms: `unreachable` outcomes, breaker open for all steps, `/health` on the worker failing.
- Check the worker's host status, `AI_WORKER_URL`, `AI_WORKER_TOKEN` (401 → `worker_error` with status 401).
- Students' answers are saved; they can retry after recovery. Interviews over time are closed automatically
  (`time_up`).

## Kill switch and rollback

| Scope | Action |
|---|---|
| Everything | `AI_MOCK_INTERVIEW_ENABLED=false` → all mock-interview routes 404 except `/status`; data kept |
| One institute | Admin pause (Ops page) or remove it from `AI_MOCK_INTERVIEW_TENANT_IDS` |
| Voice / video | Their existing flags, or per-institute controls |
| Operations features only | `AI_MOCK_INTERVIEW_OPS_ENABLED=false` |

## Student data / usage request

Usage: `ai_usage_events` by `user_id` (ids, counts, outcomes only). Interview content requests are handled from
the interview tables, outside the operations tooling.
