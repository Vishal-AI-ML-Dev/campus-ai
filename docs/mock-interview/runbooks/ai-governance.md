# 12. AI governance: Rs 0 budget, caps, kill switches (Phase 10.3)

Owner decisions **D4** (Rs 0/month, free tier only, stop new AI calls when the free quota is used up, non-AI features
keep working) and **OQ-2** (off in code, **required in production**). Code: `backend/app/services/ai_governance.py`,
governed calls in `backend/ai_client.py` and `backend/app/services/mock_interview_ai.py`; worker budget in
`ai-worker/llm.py`, `ai-worker/provider_errors.py`.

## What is protected

Every AI path: mock interview (plan, turn, report, coding hint/review/follow-up, JD skills), mentor chat, mentor
stream, resume draft, ATS score, skill/project proof scoring. Face attendance is not an LLM and is not governed (it
stays disabled).

| Control | Setting | Scope | Notes |
|---|---|---|---|
| Global kill switch | `AI_KILL_SWITCH=true` | every new AI call | Works even with governance off. 503 `kill_switch`. |
| Per-feature switch | `AI_DISABLED_FEATURES=mentor,ats` | listed features | Names: `mock_interview, mentor, resume, ats, proof_scoring`. An unknown name blocks a production start. |
| Platform daily requests | `AI_PLATFORM_DAILY_REQUEST_MAX` | all institutes, UTC day | **Mandatory in production.** Reserved atomically BEFORE the worker call in `ai_platform_usage_daily` - failed/timed-out calls count. 503 `platform_requests`. |
| Platform daily tokens | `AI_PLATFORM_DAILY_TOKEN_MAX` | all institutes, UTC day | **Mandatory in production.** Provider-reported tokens; checked before each call. 503 `platform_tokens`. |
| Institute daily requests | `AI_TENANT_DAILY_REQUEST_MAX` | one institute | Counted from the RLS ledger. 429 `tenant_requests`. |
| Student daily requests | `AI_USER_DAILY_REQUEST_MAX` | one student, mentor+resume+ATS | Interviews keep their own per-student limits. 429 `user_requests`. |
| Concurrency | `AI_MAX_CONCURRENT_CALLS` (3), `AI_CONCURRENCY_WAIT_SECONDS` (10) | per backend process | Waits up to the limit, then 503 `busy`. One in-flight mentor/resume/ATS call per student (429 `user_busy`). |
| Provider quota cooldown | `AI_QUOTA_COOLDOWN_SECONDS` (900) | per backend process | Set when the provider answers 429 / reports quota exhaustion; new calls get 503 `provider_quota` without a provider call. Alert `ai_quota_exhausted`. |
| Safe retries | built in | governed calls | One retry ONLY when the worker could not be reached. A 5xx or read timeout is never repeated (an LLM call may already have run). The worker's SDK retries: `LLM_MAX_RETRIES` 0 or 1 (mandatory in production). |
| Provider timeout | `LLM_TIMEOUT_SECONDS` (worker, 1-45, mandatory in production) | each LLM call | Below the backend's 60/90/120 s worker timeouts. |
| Idempotency | `Idempotency-Key` header, `AI_IDEMPOTENCY_TTL_SECONDS` | mentor chat, resume, ATS | Same key + same user replays the stored answer; while running -> 409. |
| Circuit breaker | `AI_CIRCUIT_*` (Phase 7.5) | per step | Now also guards `mentor_chat`, `mentor_stream`, `resume_draft`, `ats_score`, `proof_score` when governance is on. |

Responses use the normal error envelope with `error.type = "ai_unavailable"` and `error.reason`, plus `Retry-After`.
The text is fixed and content-free. Everything that is not AI keeps working.

## Choosing the caps (D4)

1. Read the provider's **current** free-tier limits in its dashboard (requests/day, tokens/day, requests/minute).
   Never assume a free tier is unlimited. Confirm **no billing method** is attached to the provider account.
2. Set `AI_PLATFORM_DAILY_REQUEST_MAX` and `AI_PLATFORM_DAILY_TOKEN_MAX` **below** those limits (suggested 70-80 %).
3. For the D8 pilot (1 institute, 100 students, 3 simultaneous interviews): `AI_MAX_CONCURRENT_CALLS=3`, a per-student
   daily cap (e.g. 20) and an institute cap below the platform cap.
4. Keep `AI_PRICE_TABLE` empty (prices are never invented; under D4 cost is controlled by counts).

## Procedures

| Situation | Action | Recovery |
|---|---|---|
| Free quota exhausted (alert `ai_quota_exhausted`) | Nothing urgent: new AI calls are refused for the cooldown. If it repeats, lower the platform caps. | Automatic after the cooldown; a restart clears it early (the next 429 re-arms it). |
| Platform cap reached (alert `ai_platform_quota_reached`) | Expected protection. Do not raise the cap above the free-tier limit. | 00:00 UTC. |
| Suspected abuse or provider billing risk | Set `AI_KILL_SWITCH=true` (or the feature in `AI_DISABLED_FEATURES`) and restart/redeploy. | Unset and restart. |
| `ai_governance_unavailable` alert | The counter table could not be read: calls are refused (fail closed). Check `/health/ready` (database). | Automatic once the database answers. |

`GET /health/ready` shows `checks.ai.status = "degraded"` with reason codes (`kill_switch`, `provider_quota`) while new
AI calls are refused; it never makes readiness fail. Metrics: `ai_governance_refusals_total{feature,reason}`,
`ai_feature_calls_total{feature,outcome}`.

## Limits of this design (documented)

- Concurrency, the in-flight guard, the cooldown and the idempotency cache are **per process** (free tier = one
  process). The daily caps are durable and shared.
- The per-user/institute counters read the ledger; under heavy parallel traffic a cap can be exceeded by at most the
  concurrency limit. The platform cap is exact (atomic reservation, proven on Postgres by `test_ai_governance_pg.py`).
- Mentor streaming tokens are not reported by the worker, so stream calls count as requests but add no tokens.
