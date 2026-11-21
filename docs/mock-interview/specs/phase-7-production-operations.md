# Phase 7 — Production Operations and Monitoring Foundation

Status: **design approved 2026-11-20 (decisions P7-D1 to P7-D9, §11). Phase 7.0 (decisions, roadmap, worker
baseline) done and committed (`ee48610`); §13 is its record. Phase 7.1 (AI privacy and request tracing)
implementation is complete; §14 is its record. 7.2–7.10 not started.** This document records implementation
status and evidence; Git history tracks commit status.
Base: `feature/ai-mock-interview` @ `f69ee1d` (7.0 committed as `ee48610`). Overall status: `../PROJECT_STATE.md`.

> **Numbering note.** The roadmap was re-numbered on 2026-11-20 (P7-D1): 7 = production operations, 8 = advanced AI
> interview intelligence (incl. coding round and company/full simulation), 9 = institute and placement management
> (incl. faculty campaigns), 10 = enterprise integrations, platform-level security and final acceptance. The
> old-to-new mapping is in `../IMPLEMENTATION_PLAN.md` §6a. Separately, `deploy/README.md`, `config.py` and
> `main.py` use "Phase 7" for an *older, platform-wide* hardening phase (closed `/docs` and `/metrics` in
> production); that is unrelated to this one.

---

## 1. Audit: what already exists (do not rebuild)

| Area | Exists | Where | Gap for Phase 7 |
|---|---|---|---|
| Structured logs | One JSON line per request with `request_id`, method, path, status, duration; `X-Request-ID` echoed | `backend/app/observability/observability.py` (byte-identical copy in `ai-worker/`, enforced by CI `scripts/sync_observability.py --check`) | `request_id` is **not forwarded** backend → worker, so the two logs can't be joined. Incoming `X-Request-ID` is not length/charset-validated. |
| Error tracking | Sentry, opt-in via `SENTRY_DSN`, `send_default_pii=False`, 10 % traces | same file | **Verified in 7.0 (sentry-sdk 2.63.0, both services):** `include_local_variables=True` and `max_request_body_size="medium"` (≈10 KB) by default, and the Starlette integration attaches the JSON request body **regardless of `send_default_pii`**. With a DSN set, an error on `/answer` (answer ≤5,000 chars) or on a worker `/interview/*` route (resume, JD, transcript) could send interview content to Sentry. Fixed in 7.1 (§12). |
| Uniform error envelope | JSON error + request id | `app/observability/errors.py` | none |
| Prometheus metrics | HTTP count / latency / in-flight, route-template labels | `app/observability/metrics.py` (+ worker copy) | **Disabled in production** (`Settings.metrics_enabled`, "not overridable"; `deploy/README.md` lists access as a pending decision). No AI- or LLM-specific metrics. |
| Health | `/health` liveness; `/health/ready` (DB required, Redis optional) | `app/observability/health.py` | Worker/LLM availability not visible anywhere (correctly not part of readiness). |
| Alerts | `emit_alert()`: structured `campus.alert` log + optional `ALERT_WEBHOOK_URL` | `app/observability/alerts.py` | Synchronous `httpx.post` (3 s) — unsafe on a request path; no de-duplication/throttling. Only used for readiness. |
| LLM tracing | Langfuse, opt-in via `LANGFUSE_*` keys; interview steps call `trace_config("interview_<step>")` | `ai-worker/tracing.py`, `interview/llm_io.py` | **Privacy gap:** with keys set, full interview prompts (resume, JD, answers) go to Langfuse. `prod.env.example` lists the keys. No content opt-out per feature. |
| Provider abstraction | `llm.py` Groq \| Gemini by `LLM_PROVIDER`; worker returns `provider` in plan/turn/report responses; reports store `provider` | `ai-worker/llm.py`, `interview/router.py`, `mock_interview_reports.provider` | Single provider, no fallback. **No token usage, latency or per-call outcome is captured or returned.** Model name not recorded. |
| Worker resilience | Generic `_worker_request` (3 tries, 5xx/timeout retry); mock interview overrides to 1 retry on connect errors only (no double LLM spend); `InterviewEngineUnavailable(status_code, timed_out)` | `backend/ai_client.py`, `app/services/mock_interview_ai.py` | No circuit breaker: when the worker/provider is down, every student still waits up to 60–120 s. No metrics on these calls. |
| Interview fallbacks | Deterministic plan, heuristic evaluation, fixed closing message; report all-or-nothing | `ai-worker/interview/*` (Phase 3–4) | Fallback use is logged only; not counted. |
| Session reliability | Lease (CAS on `updated_at`), idempotent answers, `time_up` auto-close, answers saved before worker call | `app/services/mock_interview.py` | none (reuse for diagnostics) |
| Per-student limits | 1 active session; ≤10 sessions / 24 h (hard-coded `MAX_SESSIONS_PER_DAY`); 30 turns; 20 AI steps/min/student/route and a report limit (slowapi, Redis-shared when `REDIS_URL`) | `services/mock_interview.py`, `routers/mock_interview.py` | No per-institute caps, no token/cost budgets, limits not configurable per tenant. Student can't see remaining quota. |
| Feature flags / rollout | Env flags (master, tenant allow-list, voice, video + fail-closed video allow-list), worker flag, two frontend build flags; `/status` drives nav | `config.py`, `routers/mock_interview.py` | Any change needs an env edit + restart/redeploy; institute admins can't pause or restrict the feature for their own institute. |
| Audit log | `audit_logs` (tenant, actor, action, target, summary), `record_audit()` in the caller's transaction, admin-only `GET /audit?action=` + `/dashboard/audit` UI | `app/routers/audit.py`, `models/operations.py` | Mock interview writes **no** audit rows. List has no date/target filters. |
| Usage data | Sessions (mode, type, status, timestamps, `campaign_id`), turns (`answer_source`), reports (score, `provider`, `rubric_version`) — enough for counts, completion/abandon rates, mode mix | Phase 2 tables | No aggregation endpoint or UI; no index on `(tenant_id, created_at)`. |
| Analytics UIs | Student risk analytics, placement analytics | `routers/analytics.py`, `placement_analytics.py` | Nothing for interview usage/cost. |
| Tenancy / roles | Tenants (`name`, `slug`, `is_active`), roles student/teacher/tpo/admin/recruiter, RLS everywhere | `models/identity.py`, `core/security.py` | **No platform-operator role**: `tenants.platform_operator_only` fails closed for everyone. Cross-tenant ops UI is therefore impossible without a new auth decision. |
| Background jobs | RQ when `REDIS_URL`, else in-process `BackgroundTasks` | `backend/jobs.py`, `worker.py` | none (reuse for alert dispatch) |
| Ops docs | `deploy/README.md` (staging limits, endpoint exposure, release/migration runbook), `IMPLEMENTATION_PLAN.md` §9 rollout | `deploy/`, `docs/mock-interview/` | No mock-interview runbooks (provider outage, cost spike, kill switch, incident investigation, data requests). |
| CI | backend pytest, observability sync check, tsc, lint, gitleaks | `.github/workflows/ci.yml` | Worker tests not in CI. |

## 2. Genuine gaps (what Phase 7 must add)

1. **Correlation:** request id backend → worker; per-AI-step outcome records linkable to logs and Sentry.
2. **LLM telemetry:** per call/step provider, model, latency, outcome (ok / fallback / provider_error / unparseable / timeout), token counts.
3. **Durable usage ledger** (per tenant, per student, per session, per step) — the base for analytics, cost and quotas.
4. **Cost estimation** from tokens × a versioned, configurable price table (no hard-coded prices).
5. **Per-institute quotas/budgets** and tenant-configurable per-student limits, enforced at session creation only.
6. **Provider failure handling:** circuit breaker with fast, friendly 503; recovery detection; throttled alerts.
7. **Production metrics access** that doesn't publish data (token-protected or private network).
8. **Institute-level operational controls:** pause / restrict voice & video / set limits, *within* env ceilings, audited.
9. **Incident tooling:** content-free session diagnostics timeline, audit filters, runbooks.
10. **Privacy hardening of telemetry:** Langfuse content opt-out for interviews; Sentry local-variable scrubbing.

Explicitly **not** gaps (already done, reuse): JSON logs, Sentry wiring, HTTP metrics, readiness, error envelope,
lease/idempotency, per-student daily limit, slowapi + Redis, env flags/allow-lists, `record_audit`, RQ.

## 3. Sub-phases

Each sub-phase: one commit-sized change, focused tests, `ruff` + `tsc` + `lint` (+ `npm run test:unit` if frontend),
migration up/down/up on throwaway SQLite when it has a migration, PROJECT_STATE update, **stop for review**.

| # | Scope | Migration | Touches shared code? |
|---|---|---|---|
| **7.0** | **Done 2026-11-20 (§13).** Decisions §11 approved; roadmap re-numbered with old-to-new mapping; isolated worker venv; worker baseline; Sentry defaults verified | — | docs only |
| **7.1** | **Implementation complete 2026-11-20 (§14).** Correlation + telemetry privacy: forward `X-Request-ID` and a per-call `X-AI-Invocation-ID` to the worker; validate incoming ids (≤64 chars, `[A-Za-z0-9._-]`, at least one letter/digit, otherwise a fresh id); Sentry `include_local_variables=False`, `max_request_body_size="never"`, and allow-list scrubbing of events, transactions and breadcrumbs (applied to all routes, see §14.3); **interview LLM steps and graph runs never reach Langfuse or LangSmith** (no flag can re-enable it — mandatory rule R1, §12) | — | `ai_client._worker_request` (additive optional `headers=` kwarg); both `observability.py` copies (kept in sync, P7-D8); `ai-worker/interview/llm_io.py` |
| **7.2** | Worker LLM telemetry: a LangChain callback collects per-step `{provider, model, llm_calls, input_tokens, output_tokens, latency_ms, outcome, fallback_used}`; returned as an **additive optional `usage` field** on `/interview/plan|turn|report`; new worker Prometheus metrics (`ai-worker/llm_metrics.py`, no tenant labels) | — | worker `interview/llm_io.py`, `interview/router.py`, `interview/schemas.py` |
| **7.3** | Usage ledger: `ai_usage_events` table (§5) written by the backend after every worker call (success **and** failure), durable and idempotent per §12 R4 (pre-generated `event_id`, unique key, insert-or-ignore, own short tenant-scoped transaction, RQ retry via `jobs.py` when `REDIS_URL` is set), never blocking or failing the student path; backend AI metrics (`app/observability/ai_metrics.py`, new module so the synced files stay untouched) | **yes** (additive) | `services/mock_interview_ai.py` |
| **7.4** | Cost estimation: `AI_PRICE_TABLE` setting (JSON, empty = cost "unknown"), versioned; cost computed at write time in micro-units + `price_version`; raw tokens kept so re-costing is possible | — | config (new optional field) |
| **7.5** | Circuit breaker + provider health: per-step breaker in the backend (opens after N consecutive worker/provider failures or ≥X % in a window; half-open probe by real traffic; Redis-shared when available, in-process otherwise); fast 503 with the existing "nothing you typed was lost" message; throttled alerts via a background-dispatched wrapper around `emit_alert` (`app/observability/ai_alerts.py`) | — | `services/mock_interview_ai.py` |
| **7.6** | Tenant controls + quotas: `ai_feature_controls` table (§5); effective policy = env ceiling ∩ tenant row (tenant can only restrict); limits: sessions/student/day (≤ env max), sessions/tenant/month, tokens/tenant/month (soft 80 % alert, hard 100 % block of **new** sessions only); checked in `create`; `GET /mock-interview/usage/me` (remaining quota) | **yes** (additive + `(tenant_id, created_at)` index on sessions) | `services/mock_interview.py` create path |
| **7.7** | Admin ops API, own institute only (P7-D3: every `GET` = `admin` + `tpo`; `PUT …/admin/controls` = `admin` only): `GET /mock-interview/admin/usage` (daily series, mode mix, completion/abandon/failure, tokens, est. cost), `GET …/admin/usage/students` (counts only, paginated), `GET/PUT …/admin/controls` (audited), `GET …/admin/sessions/{id}/diagnostics` (step timeline: step, outcome, latency, error class, request id — **no content**), `GET …/admin/health` (breaker state + last-hour error rate from the ledger). Additive audit filters (`target_type`, `since`, `until`) on `GET /audit` | — | `routers/audit.py` (additive query params) |
| **7.8** | Admin ops UI: `/dashboard/interview-operations` (outside `/dashboard/mock-interview` so the camera policy never applies): usage tiles + charts, cost, quota/controls form, provider health, diagnostics lookup; nav for admin/tpo behind the build flag + `/status`; student setup form shows remaining sessions via `/usage/me` | — | `lib/auth.tsx` nav (flag-gated) |
| **7.9** | Production metrics access: `METRICS_TOKEN` (empty = unchanged 404 in production); bearer-protected `/metrics` on backend and worker; constant-time compare | — | `main.py` both services, `config.py` |
| **7.10** | Runbooks + retention: `docs/mock-interview/runbooks/` (provider outage, worker down, cost spike, quota exhaustion, kill switch & rollback, incident investigation by request id, student data/usage request, alert reference); manual, auditable retention script for `ai_usage_events` (400 days, P7-D5: dry-run by default, `--apply` required, per-tenant counts printed, one `audit_logs` row per affected tenant with `action="ai_usage.purge"` and `actor_email="system:purge_ai_usage"`; never scheduled); Phase 7 acceptance record; Phase 10 final-acceptance checklist skeleton | — | docs + `backend/scripts/` |

**Deferred (P7-D6):** secondary-provider fallback (`LLM_FALLBACK_PROVIDER`). It changes which model scores a report,
so it needs a rubric-consistency decision first. Revisit no earlier than Phase 8.

## 4. Architecture

```
Browser ──X-Request-ID──▶ Backend request_logging_middleware (validated id)
                            │
   create ── policy.effective(tenant) ── quota check (sessions + usage ledger sums) ── 429/403 or proceed
                            │
   AI step ── breaker.allow(step)? ── no ─▶ fast 503 (+ ledger row outcome=circuit_open)
                            │ yes
                            ▼ mock_interview_ai._post (+X-Request-ID)
                        AI worker request_logging_middleware (same id)
                            │ interview graph ── UsageCollector callback (per LLM call)
                            │                    └─ llm_metrics (provider, model, step, outcome)
                            ◀── response + optional `usage` {...}
   ledger.record(...)  (own session + commit, best-effort, never raises)
   ai_metrics.observe(...) ; breaker.record(outcome) ; ai_alerts.maybe(...)
                            │
Admin UI ── /mock-interview/admin/* ── services/mock_interview_usage.py (pure query layer, tenant-filtered)
Operator ── /metrics (token) → Prometheus/Grafana ; Sentry ; logs by request_id ; runbooks
```

Design rules:
- **Backend owns persistence** (worker stays stateless; it only *reports* usage in its response).
- **Prometheus labels stay low-cardinality and tenant-free** (`step`, `outcome`, `provider`, `model`); per-tenant and
  per-student numbers come only from the RLS-protected ledger.
- **Telemetry never blocks or fails a student request.** Ledger write failure → log + counter, nothing else.
- **Quotas are checked only when a session is created**, never mid-interview; finishing and the report are always
  allowed (consistent with D12 for video).
- **Env flags stay the ceiling.** DB controls can only switch off or tighten. Rollback = env flags off, unchanged.
- New code lives in new modules; the CI-synced observability files change only in 7.1 and in both copies together.

## 5. Database changes (two additive migrations; Alembic head today `b4d8f2a6c9e1`)

Both tables: `tenant_id` FK (CASCADE, indexed), `ENABLE/FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy
(copy `a1b2c3d4e5f7_…`), SQLite-compatible types, no change to any existing column.

**7.3 — `ai_usage_events`** (append-only ledger; generic so mentor/resume and Phase 8 features can adopt it)

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `event_id` | str(36), **unique** | UUID generated by the backend *before* the worker call; makes every write and retry idempotent (§12 R4) |
| `tenant_id` | FK tenants | |
| `user_id` | FK users, SET NULL, nullable | survives user deletion |
| `feature` | str(40) | `mock_interview` now; `mentor`, `resume`, Phase 8 keys later |
| `step` | str(40) | `plan`, `turn`, `report` (free string, no CHECK, so new steps need no migration) |
| `subject_type` / `subject_id` | str(40) / str(40), nullable | e.g. `mock_interview_session` / `123` (no FK: generic) |
| `outcome` | str(30) | `ok`, `fallback`, `provider_error`, `unparseable`, `worker_error`, `timeout`, `unreachable`, `circuit_open`, `quota_blocked` |
| `http_status` | int nullable | worker status if any |
| `error_class` | str(80) nullable | class name only, never a message |
| `provider`, `model` | str(40) / str(80), nullable | |
| `llm_calls`, `input_tokens`, `output_tokens` | int, default 0 | from worker `usage`; 0 when unknown |
| `latency_ms` | int | backend-measured round trip |
| `cost_micros` | bigint nullable | null = no price configured |
| `price_version` | str(20) nullable | |
| `request_id` | str(64) nullable | joins to logs/Sentry |
| `created_at` | timestamptz | indexes: `(tenant_id, created_at)`, `(tenant_id, user_id, created_at)`, `(subject_type, subject_id)` |

**7.6 — `ai_feature_controls`** (one row per tenant + feature; absent row = env defaults)

`id`, `tenant_id`, `feature` (str 40), `paused` (bool), `voice_allowed` / `video_allowed` (bool, can only narrow env),
`limits_json` (`{student_sessions_per_day, tenant_sessions_per_month, tenant_tokens_per_month}`), `updated_by_id`
(SET NULL), `updated_at`, `created_at`; unique `(tenant_id, feature)`. Plus index
`ix_mock_interview_sessions_tenant_created (tenant_id, created_at)` on the existing table (additive index only).

**Not needed:** no rollup tables (volume ≈ 15 ledger rows per interview; add rollups in Phase 10 if measured),
no change to sessions/turns/reports, no new column on `tenants`, no platform-level table.

## 6. Security and privacy

- **Content never enters telemetry.** Ledger, metrics, alerts, diagnostics and audit rows hold ids, counts, classes,
  timings and statuses only — never prompts, answers, resumes, JDs, questions or provider error messages. Enforced by
  tests that scan ledger rows and diagnostics responses for planted sentinel text.
- **Langfuse:** interview steps never attach the Langfuse callback, whatever the `LANGFUSE_*` keys say; there is no
  opt-in flag (R1). Other worker features (mentor, resume) keep today's behaviour. **Sentry:** locals off, request
  bodies never attached, `before_send` scrub on interview paths (R2).
- **Tenant isolation:** RLS on both new tables; every admin query filters `tenant_id`; cross-tenant ids → 404;
  cross-tenant tests on SQLite and the opt-in Postgres suite.
- **RBAC (P7-D3):** viewing institute ops data = `admin` + `tpo`; editing controls = `admin` only (TPO → 403);
  teachers, students, recruiters → 403; another institute's data → 404. Per-student usage lists show
  counts, not content; no admin endpoint exposes transcripts or reports (that is Phase 9 scope with its own consent design).
- **Controls are audited** (`mock_interview.controls_update`, before/after summary without PII) in the same transaction.
- **Metrics endpoint:** 404 in production unless `METRICS_TOKEN` is set; token compared in constant time; never logged.
- **Request id:** validated and capped to prevent log pollution; not trusted for anything else.
- **Abuse:** admin endpoints rate-limited; diagnostics/usage paginated with caps; quota messages reveal no other tenant data.
- **Retention:** ledger default 400 days (P7-D5); deletion via the retention script, never automatic in Phase 7.
- **Cost figures are estimates** and labelled so in the UI (price table may lag provider pricing).

## 7. Testing and acceptance

Per sub-phase (focused, per your testing strategy): new unit tests; affected existing test files;
`ruff`; worker tests for worker changes; `npx tsc --noEmit`, `npm run lint`, `npm run test:unit` for frontend changes;
Alembic `upgrade head → downgrade -1 → upgrade head` on a throwaway SQLite DB (and local Postgres + RLS suite when one is
available). **One** full SQLite backend run (≈18 min, never two at once) at 7.3, 7.6 and 7.10, because those touch the
shared create/AI path. Flag-off tests every sub-phase: with all new flags/settings at defaults, every existing API
response and the OpenAPI shapes of existing routes are unchanged (snapshot test).

Key acceptance criteria:
1. A worker call made with request id R logs R in both services and the ledger row stores R.
2. Every AI step — success, fallback, 5xx, timeout, unreachable, breaker-open — produces exactly one ledger row; a ledger
   write failure does not change the student response.
3. Token counts from a stubbed LLM callback reach the ledger; unknown usage records 0 and `cost_micros = null`.
4. Breaker opens after the configured failures, returns 503 in < 100 ms, half-opens, closes on success; one alert per window.
5. Tenant caps block new sessions with a clear message; in-progress sessions finish and reports generate.
6. A tenant control can only narrow env flags; changing it writes one audit row; only `admin` may change it (TPO,
   teacher, student, recruiter → 403); `admin` and `tpo` can read; other tenant → 404.
7. Sentinel-content scan: no planted answer/resume/JD text appears in ledger, diagnostics, metrics output, alerts or logs.
8. With all defaults, existing behaviour, responses and the full backend suite are unchanged.

**Deferred to the Phase 10 final acceptance milestone (documented, not run in Phase 7):** live LLM end-to-end;
staging drills (provider outage → breaker → recovery, quota exhaustion, kill switch, metrics scrape with token);
load test with multiple backend workers + Redis; Phase 5 real-device checks (`phase-5-voice-interview.md` §8);
Phase 6 real-device matrix RD-01…RD-24 (`phase-6-video-interview.md` §10.4); full SQLite + Postgres + worker regression;
`next build`.

## 8. Expected files

**Backend new:** `app/observability/ai_metrics.py`, `app/observability/ai_alerts.py`, `app/services/ai_usage.py`
(ledger + cost), `app/services/ai_circuit.py`, `app/services/mock_interview_policy.py` (effective controls + quotas),
`app/services/mock_interview_usage.py` (query layer), `app/routers/mock_interview_admin.py` (added to `FEATURE_ROUTERS`),
`app/models/ai_operations.py`, `app/schemas/ai_operations.py`, two migrations, `scripts/purge_ai_usage.py`,
tests `test_ai_usage_ledger.py`, `test_ai_circuit.py`, `test_mock_interview_quotas.py`, `test_mock_interview_admin.py`,
`test_telemetry_privacy.py`, `test_request_id_propagation.py`, `test_metrics_token.py`, plus an RLS case in the opt-in Postgres file.
**Backend modified (small, additive):** `ai_client.py` (`headers=` kwarg), `app/services/mock_interview_ai.py`,
`app/services/mock_interview.py` (create-path policy call), `app/routers/mock_interview.py` (`/usage/me`),
`app/routers/audit.py` (filters), `app/core/config.py` (new optional fields), `main.py` (router + metrics token),
`app/models/__init__.py`, both `observability.py` copies (7.1 only).
**Worker:** new `llm_metrics.py`, `interview/usage.py` (callback); modified `interview/llm_io.py`, `interview/router.py`,
`interview/schemas.py`, `config.py`, `main.py` (metrics token); tests `test_interview_usage.py`, `test_interview_no_langfuse.py`.
**Frontend new:** `src/app/dashboard/interview-operations/page.tsx`, `src/components/interview-operations/`
(`opsApi.ts`, `UsageSummary.tsx`, `UsageChart.tsx`, `CostPanel.tsx`, `ControlsForm.tsx`, `ProviderHealth.tsx`,
`SessionDiagnostics.tsx`), `tests/unit/interviewOperations.test.mjs`. **Modified:** `lib/auth.tsx` (flag-gated nav),
`components/mock-interview/InterviewSetupForm.tsx` (remaining-quota line).
**Docs:** this spec, `runbooks/*.md`, `API.md`, `AI_INTERVIEW_ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `PROJECT_STATE.md`, `deploy/README.md` (metrics access), `CLAUDE.md` flag line.

**New settings (all optional, safe defaults):** backend `AI_PRICE_TABLE` (empty), `AI_CIRCUIT_*` thresholds,
`METRICS_TOKEN` (empty), `AI_MOCK_INTERVIEW_OPS_ENABLED` (default **off**: admin routes 404, no quota enforcement beyond
today's), `AI_MOCK_INTERVIEW_TENANT_MONTHLY_SESSION_MAX` / `…_TOKEN_MAX` ceilings (empty = no cap); worker
`METRICS_TOKEN`; frontend `NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED`.
Ledger writes and metrics run whenever the interview feature runs (they are invisible to users).

**Explicit exception to the default-OFF rule (P7-D4):** the circuit breaker is **on by default**
(`AI_CIRCUIT_ENABLED=true`) with conservative, configurable thresholds (`AI_CIRCUIT_FAILURE_THRESHOLD=5` consecutive
failures, or `AI_CIRCUIT_ERROR_RATE=0.5` over ≥`AI_CIRCUIT_MIN_CALLS=10` calls in `AI_CIRCUIT_WINDOW_SECONDS=120`;
open for `AI_CIRCUIT_OPEN_SECONDS=60`, then one half-open trial). Reason: it only ever *shortens* a failure the student
would get anyway (fast 503 instead of a 60–120 s wait) and never blocks a healthy worker. It is inert while
`AI_MOCK_INTERVIEW_ENABLED` is off, and `AI_CIRCUIT_ENABLED=false` disables it. This is the only Phase 7 behaviour
that is on by default; it must be recorded as such in `CLAUDE.md` when 7.5 lands.

## 9. Phase 8–10 compatibility

| Future need | How Phase 7 keeps it open | Risk to watch |
|---|---|---|
| 8: more LLM calls per turn, new graph nodes, possibly multiple models | `step`/`model` are free strings; usage callback is per-call so new nodes are counted automatically; cost from tokens | Token budgets must be sized before Phase 8 multiplies calls; breaker per step may need per-model granularity |
| 8: coding round (old Phase 7) / sandbox | `feature` key + controls row per feature; ledger can record non-LLM steps (sandbox runs) | Coding round is in Phase 8 (P7-D1); the sandbox decision (Judge0/Piston on an isolated host) is still open and must precede any code execution |
| 9: faculty campaigns, placement analytics | Sessions already have `campaign_id`; the query layer returns plain dicts keyed by tenant/section-ready ids; admin+tpo RBAC pattern reused | Phase 9 must keep usage (operational) and performance (scores) analytics separate, and design staff access to transcripts with consent |
| 10: enterprise, platform operator, SSO, billing, exports | Everything tenant-keyed; raw tokens + price version allow re-billing; no tenant ids in Prometheus; cross-tenant view deliberately **not** built (needs the platform-operator role, P7-D2) | Rollup tables and partitioning if volume grows; export API shape |
| 10: final acceptance | §7 deferred list is the seed of the final checklist | Real-device backlog keeps growing (Phase 5 + 24 Phase 6 rows) |

## 10. Exact implementation sequence

1. **7.0** decisions §11 → update `IMPLEMENTATION_PLAN.md`/`PROJECT_STATE.md` numbering → worker venv + baseline worker
   suite → stop. **Done 2026-11-20 (§13).**
2. **7.1** correlation + Sentry/Langfuse privacy (R1, R2) → focused tests + worker tests + sync check → stop.
3. **7.2** worker usage callback + `usage` field + worker metrics → worker tests (stubbed LLM) → stop.
4. **7.3** migration A + ledger + backend AI metrics → migration up/down/up + focused + **full backend suite** → stop.
5. **7.4** price table + cost → focused tests → stop.
6. **7.5** circuit breaker + throttled alerts → focused tests (fake clock) → stop.
7. **7.6** migration B + controls + quotas + `/usage/me` → migration checks + focused + **full backend suite** → stop.
8. **7.7** admin API + audit filters → RBAC/tenant/privacy tests → stop.
9. **7.8** admin UI + student quota line → tsc, lint, unit tests, simulated-browser smoke → stop.
10. **7.9** metrics token → tests on both services → stop.
11. **7.10** runbooks, retention script, acceptance record, Phase 10 checklist skeleton → **full backend suite** + worker
    suite + frontend checks → stop for Phase 7 review.

## 11. Approved decisions (2026-11-20)

| # | Decision (approved) |
|---|---|
| P7-D1 | Adopt the new roadmap: 7 = production operations and monitoring; 8 = advanced AI interview intelligence, **including the coding round and company/full interview simulations**; 9 = institute and placement management, **including faculty campaigns**; 10 = enterprise integrations, platform-level security and final acceptance. Old-to-new mapping kept in `IMPLEMENTATION_PLAN.md` §6a; no planned feature is dropped. |
| P7-D2 | No cross-institute operations dashboard in Phase 7. Deferred until the platform-operator role is designed in Phase 10. Platform-wide view meanwhile = Prometheus (token), Sentry, logs, runbooks. |
| P7-D3 | Institute `admin` can edit controls. `admin` and `tpo` can view **their own institute's** operational data only. Teachers, students, recruiters: 403; other institutes: 404. |
| P7-D4 | Circuit breaker **enabled by default** with conservative, configurable thresholds — an explicit, documented exception to the default-OFF rule (§8). |
| P7-D5 | Usage-event retention 400 days. Deletion only through a manual, auditable script (§3, 7.10). |
| P7-D6 | Backup LLM provider deferred. |
| P7-D7 | Production `/metrics` requires a dedicated secret token (`METRICS_TOKEN`, separate from `AI_WORKER_TOKEN` and every other secret; empty = 404 as today). |
| P7-D8 | Small, justified, additive changes allowed to `backend/ai_client.py` and both copies of `observability.py` (kept identical; CI sync check). |
| P7-D9 | No AI model prices are invented. Cost stays **unknown** (`cost_micros = null`, UI "not configured") until you supply an approved price table; tokens are always recorded so costs can be computed later. |

## 12. Mandatory privacy and reliability requirements (approved 2026-11-20)

| # | Requirement | Where enforced | Proof |
|---|---|---|---|
| R1 | Langfuse never receives interview prompts, resume content, job descriptions or candidate answers. | 7.1: `interview/llm_io.py` passes no Langfuse callback for any interview step (plan, turn, report), independent of `LANGFUSE_*` keys; no flag re-enables it | Worker test: with tracing forced on, interview invokes carry no callbacks |
| R2 | Sentry never receives interview content. | 7.1: `include_local_variables=False`, `max_request_body_size="never"`, `before_send` scrub of request data, frame vars and extra on `/mock-interview` and `/interview` paths, in both `observability.py` copies | Tests on `init_sentry` options and on `before_send` with a planted sentinel |
| R3 | AI usage recording never blocks or fails a student's interview. | 7.3: ledger write after the student result is committed, in its own short session with a tight timeout, every exception caught → log + `ai_usage_record_failures_total` | Test: ledger write raising / DB lock → student response identical |
| R4 | Usage events are durable and idempotent. | 7.3: `event_id` UUID generated before the worker call; unique key; insert-or-ignore; on a failed direct write, re-enqueued via the existing `jobs.py` (RQ when `REDIS_URL`, otherwise one in-process retry, then counted as lost); worker retries never create a second row | Tests: duplicate `event_id` → one row; queued retry → one row |
| R5 | Telemetry never contains interview content or camera/audio/video data. | Ledger, metrics, alerts, diagnostics and audit rows hold ids, counts, classes, timings and statuses only | Sentinel scan test across ledger, diagnostics, `/metrics`, alerts and logs (§7 #7); existing Phase 6 media guards unchanged |
| R6 | Env flags remain the upper limit. | 7.6: effective policy = env ∩ tenant row; a tenant row can never enable what env disables, add a tenant to an allow-list, or raise a limit above the env ceiling | Tests per flag/limit combination |
| R7 | Institute controls only tighten access and quotas. | 7.6 validation rejects loosening values (422) | Tests |
| R8 | An interview in progress is never interrupted by a quota or a pause. | Quotas and `paused` are checked only on `POST /sessions`; start/answer/next/finish/report are exempt (same principle as Phase 6 D12) | Tests: quota reached / pause set mid-session → session completes and its report generates |

## 13. Phase 7.0 record (2026-11-20)

**Repository:** branch `feature/ai-mock-interview`, HEAD `f69ee1d` (unchanged). Nothing staged, committed or pushed.

**Worker dependency definitions inspected:** `ai-worker/pyproject.toml` (Python ≥3.13; runtime deps only, **no test
dependencies declared**), `ai-worker/uv.lock` (lock v1, rev 3), `ai-worker/.python-version` (`3.13`). Neither was changed.

**Isolated worker environment:**
- No environment existed in `ai-worker/`, so `ai-worker/.venv` was created with `uv sync --frozen --python 3.13`
  (uv 0.11.12, the already-installed uv-managed CPython 3.13.13; exact versions from `uv.lock`; nothing global).
  `.venv/` is git-ignored.
- `pytest==9.1.1` (the backend lock's version) plus its two deps (`iniconfig 2.3.1`, `pluggy 1.7.0`) were installed
  **into this venv only** with `uv pip install`, because the worker declares no test dependencies. `pyproject.toml`
  and `uv.lock` are unchanged; a later `uv sync` would remove pytest again (re-add it the same way).
- Key installed versions: sentry-sdk 2.63.0, langfuse 4.12.0, langchain-core 1.4.8.
- Run: `cd ai-worker && .venv/Scripts/python -m pytest -p no:cacheprovider -q tests`.

**Worker baseline (2026-11-20, 59 tests collected):**

| Selection | Result |
|---|---|
| Whole `tests/` under pytest | **54 passed, 5 failed** (44.5 s) |
| Interview subset (`test_interview_engine`, `test_interview_report`, `test_interview_flag`, `test_worker_security`) | **48 passed, 0 failed** — matches the 48/48 recorded in Phases 3–5 |
| `test_face_tenant_filter.py` | 6 passed |
| `test_face_qdrant_server.py` under pytest | **5 failed — pre-existing, not a regression** |
| `test_face_qdrant_server.py` run as a script (its documented mode) | `SKIP: QDRANT_TEST_URL not set` |

The 5 failures: that file is a script-style suite for a **real Qdrant server**, gated by `_guard()` only in its
`__main__` block (unchanged since the initial public release `f6de3d5`). Under pytest the guard never runs, so the
tests fall back to the embedded store (`QDRANT_LOCAL_PATH="qdrant_data"`) and fail with
`RuntimeError: Storage folder qdrant_data is already accessed by another instance of Qdrant client`. Earlier phases
avoided this by running the four interview files only. Side effect of this run: it created the untracked
`ai-worker/qdrant_data/` (embedded store files; not in `.gitignore`, which lists `qdrant_storage/` and `qdrant_local*/`).
It was not deleted (no destructive deletion without approval); it holds only synthetic test vectors. **Phase 7 worker
regression gate = interview subset 48/48 + face filter 6/6**; the server file stays excluded until it gets a pytest
skip marker (out of Phase 7 scope unless you approve).

**Sentry SDK verification (both venvs, 2.63.0):** defaults are `include_local_variables=True`,
`max_request_body_size="medium"`, `send_default_pii=None`, `event_scrubber=None`, `before_send=None`. The Starlette
integration adds the request body whenever it is within the size bound; only cookies and user info depend on
`send_default_pii`. Our `init_sentry` sets only `send_default_pii=False` and `traces_sample_rate`. Conclusion: with
`SENTRY_DSN` set, interview content could reach Sentry (request bodies ≤ ~10 KB and frame locals). Today `SENTRY_DSN`
is optional and the mock interview is off everywhere, so there is no known exposure; R2 closes it in 7.1, **before any
pilot enables the feature with Sentry on**.

**Langfuse verification:** `interview/llm_io.py` passes `trace_config("interview_<step>")` to every structured and
fallback call, so with `LANGFUSE_*` keys set the full prompts would be sent. R1 closes it in 7.1.

**Known dependencies and risks:**
- 7.1 must land before any environment enables the mock interview together with `SENTRY_DSN` or `LANGFUSE_*`.
- Token counts depend on providers filling LangChain `usage_metadata`; when absent, tokens are 0 and flagged "unknown".
- Without `REDIS_URL`, circuit-breaker state and the ledger retry queue are per process (documented degradation).
- Tenant-month caps can be overshot slightly by concurrent creates in different student rows (accepted; documented).
- Phase 8 multiplies LLM calls per turn, so token budgets need sizing before Phase 8 rollout.
- The real-device backlog (Phase 5 §8, Phase 6 RD-01…RD-24) carries to the Phase 10 final acceptance milestone.
- `test_face_qdrant_server.py` is not pytest-safe (above).

## 14. Phase 7.1 record: AI privacy and request tracing (2026-11-20; implementation complete)

Phase 7.1 implementation is complete. This document records implementation status and evidence; Git history tracks
commit status.

Base: HEAD `ee48610`. Requirements: R1 (Langfuse), R2 (Sentry), R5 (content-free telemetry); the "id before the call"
part of R4. **No ledger table** (that is 7.3), no migration, no dependency or lock-file change, no API, payload,
scoring or UI change.

### 14.1 Request and invocation ids
- **Shared validator** (`campus_obs/observability.py`, synced to both services): an id is accepted only if it is
  1–64 chars of `[A-Za-z0-9._-]` with at least one letter or digit (`safe_correlation_id`). A malformed or
  content-bearing incoming `X-Request-ID` is replaced by a fresh id instead of being logged and echoed.
- **Backend:** `mock_interview_ai._post` creates a fresh invocation id (`uuid4().hex`) **before** the call and outside
  the tenacity retry loop. It sends `X-AI-Invocation-ID` plus `X-Request-ID` (the API request's id, or the invocation id
  when there is no request) through a new optional `headers=` argument on `ai_client._worker_request`; the worker
  token always wins over a same-named entry. A connect-error retry sends the **same** ids; every new call (including a
  student's `/next` retry after a failure) gets a new invocation id. The 5xx/read-timeout no-retry policy is unchanged.
  `InterviewEngineUnavailable.invocation_id` carries the id. The invocation id is set in a context var for the call, so
  the backend's JSON log lines (e.g. "worker call failed") include `invocation_id` and `request_id`. It is not
  exposed to the browser.
- **Worker:** new `correlation.py` middleware (outside the logging middleware) puts a valid `X-AI-Invocation-ID` into
  the logging context and echoes it; invalid values are ignored and never logged. The report scorer's thread pool now
  runs each chunk in a copy of the request context, so its log lines keep both ids.

### 14.2 Langfuse: interview workflows excluded (R1)
- Audit: Langfuse is used only in the worker, only as a LangChain callback handler via `tracing.trace_config`
  (interview, mentor, resume, scoring). langfuse 4.12.0 registers no LangChain configure hook; its OpenTelemetry
  exporter only exports Langfuse/gen_ai spans, and no OpenTelemetry LLM instrumentation is installed. A second,
  indirect path was found: LangChain's own LangSmith tracer, enabled purely by environment variables.
- New `interview/no_tracing.py`: every interview LLM call (`llm_io.invoke_structured`, structured and JSON-fallback
  paths) and every interview graph run (`router.py` plan/turn/report) runs with `callbacks=[]` inside
  `tracing_disabled()`, which (a) detaches from any parent runnable context (LangGraph **merges** inherited callback
  managers with `callbacks=[]`, found by a test), (b) forces LangSmith tracing off via
  `langsmith.tracing_context(enabled=False)`, (c) clears LangChain's `tracing_v2_callback_var`. The guard is inside
  `invoke_structured`, so it also holds in the report scorer's threads.
- `tracing.trace_config` returns `{}` for any name starting with "interview", even when Langfuse is configured
  (guard against future misuse). No flag or setting re-enables interview tracing. Mentor, resume and proof-scoring
  tracing is unchanged.

### 14.3 Sentry privacy (R2), verified against sentry-sdk 2.63.0 in both venvs
- `init_sentry` now uses `sentry_options()`: `send_default_pii=False`, `include_local_variables=False`,
  `max_request_body_size="never"`, `before_send` / `before_send_transaction = scrub_event`,
  `before_breadcrumb = scrub_breadcrumb`. Still off unless `SENTRY_DSN` is set.
- `scrub_event` is an allow-list for **all** routes: request keeps method, URL without query/fragment/userinfo and
  the headers `content-type`, `content-length`, `accept`, `user-agent`, valid `x-request-id`; body, cookies, query
  string, env, other headers (Authorization, Cookie, X-Worker-Token, ...) are dropped. Exception **types and stack
  frames are kept, messages are replaced by `[redacted]`** (they can echo input), frame `vars` removed. `user` and
  `extra` dropped; contexts limited to runtime/os/trace/app/device (trace to ids/op/status); log events keep the
  format string only (no params/formatted text); span data and breadcrumbs reduced to method/status/URL without
  query. A scrubber exception drops the event. `request_id` / `invocation_id` are added as tags (set on the
  per-request Sentry scope by the middlewares, because unhandled errors are reported after the logging middleware
  has exited).
- Trade-off: exception messages are redacted on every route, not only interview routes; types, stack and request id
  remain for diagnosis.
- **AI integrations disabled (pre-staging audit follow-up).** sentry-sdk 2.63.0 auto-enables integrations for
  installed libraries; in the worker that was `langchain`, `langgraph` and `huggingface_hub`. The LangChain one wraps
  `langchain_core.callbacks.manager._configure` and attaches a Sentry handler to every LangChain call, interview calls
  included, emitting `gen_ai.*` spans. While it is active the SDK *suppresses* `openai`, `anthropic` and
  `google_genai` (`_INTEGRATION_DEACTIVATES`), so disabling LangChain alone would switch the Google GenAI SDK
  integration on under the Gemini provider. Mechanism: the SDK option **`disabled_integrations`** (it takes
  precedence over default and auto-enabling integrations in `setup_integrations`), filled by
  `observability.disabled_ai_integrations()` from a fixed list of Sentry AI integrations (`langchain`, `langgraph`,
  `google_genai`, `openai`, `anthropic`, `huggingface_hub`, `cohere`, `litellm`, `openai_agents`, `pydantic_ai`,
  `mcp`). Each is imported if possible; a Sentry integration module raises `DidNotEnable` on import when its library
  is missing, and such an integration can never activate, so it is skipped. Effect (measured with a fake DSN):
  **worker** active integrations lose `langchain`, `langgraph`, `huggingface_hub`, and `google_genai` stays off;
  **backend** is unchanged (none of those libraries is installed; its list is identical before and after). Automatic
  discovery itself stays on (`auto_enabling_integrations=True`), so ordinary integrations (starlette, fastapi,
  logging, httpx, and on the backend sqlalchemy, redis, rq) keep working.

### 14.4 Files
- Shared (edited in `packages/campus_obs/observability.py`, copied by `scripts/sync_observability.py` to
  `backend/app/observability/observability.py` and `ai-worker/observability.py`; `--check` in sync): validator,
  `invocation_id_ctx`, log field, Sentry options (incl. `disabled_integrations`) and scrubbers, scope tagging.
- Backend: `ai_client.py` (additive `headers=`), `app/services/mock_interview_ai.py` (ids, logging, exception field).
- Worker: `correlation.py` (new), `main.py` (registers it), `interview/no_tracing.py` (new), `interview/llm_io.py`,
  `interview/router.py`, `interview/report.py` (context copy), `tracing.py` (interview-name guard).
- Tests (new): `backend/tests/test_mock_interview_correlation.py`, `backend/tests/test_sentry_privacy.py`,
  `ai-worker/tests/test_interview_no_langfuse.py`, `ai-worker/tests/test_worker_correlation.py`,
  `ai-worker/tests/test_sentry_privacy.py`, `ai-worker/tests/test_sentry_ai_integrations.py`, helper
  `packages/campus_obs/sentry_privacy_probe.py` (not synced).

**Interview-call regression test** (`ai-worker/tests/test_sentry_ai_integrations.py`, 8 tests): each scenario runs in
a fresh subprocess that imports the real `main.py` with `SENTRY_DSN` set (fake DSN), so the real `init_sentry` runs
at import with automatic discovery on; only the transport (in-memory) and the trace sample rate (1.0, worst case) are
changed. It drives the real `/interview/plan` and `/interview/turn` routes and LangGraph graphs with a real LangChain
chat model: resume, JD and answer sentinels in the prompts, a sentinel in the model's response and question, a
sentinel-bearing provider error (502 path) and a deliberately failing route. It asserts:
- no AI integration active; LangChain's `_configure` is not Sentry-wrapped; no handler attached to any interview LLM call;
- no `gen_ai.*`/`ai.*` span; none of the 6 sentinels (or their first token) in any captured envelope;
- Sentry's other output channels are off (`debug`, `enable_logs`, profiling, Spotlight), so errors, transactions,
  sessions and client reports, which all go through `Transport.capture_envelope`, are the whole payload;
- safe diagnostics still work: the route error and the interview 502 are reported with exception types
  (`… LLMUnavailable → QuestionGenerationFailed → HTTPException`), stack frames, method, path and request-id tag,
  messages `[redacted]`; transactions for `/interview/plan` and `/interview/turn` are still sent.

A **control** scenario (same probe, integrations not disabled) must show `langchain`/`langgraph` active, the wrapped
`_configure`, a Sentry handler on interview calls and `gen_ai.*` spans (observed: 15 spans: `gen_ai.chat`,
`gen_ai.invoke_agent`, `gen_ai.create_agent`; no sentinel leaked even then, thanks to the PII gate and the scrubber).

### 14.5 Tests run (2026-11-20)

| Command | Result |
|---|---|
| `backend: pytest tests/test_sentry_privacy.py tests/test_mock_interview_correlation.py` | **31 passed**, 0 failed |
| `backend: pytest tests/test_mock_interview_*.py tests/test_error_handler.py tests/test_health.py tests/test_mentor_memory.py tests/test_sentry_privacy.py` | **210 passed, 8 skipped, 0 failed** (skips = opt-in Postgres RLS file; no Postgres configured) |
| `ai-worker: pytest` interview subset + face filter + the 3 new files | **75 passed**, 0 failed (48 + 6 baseline, 10 Langfuse, 5 correlation, 6 Sentry) |
| `ai-worker: pytest tests` (whole folder) | 76 passed, 4 failed — all in `test_face_qdrant_server.py` (pre-existing, not pytest-safe, §13; 5 failed at baseline, one passed this time, same root cause). It wrote to `ai-worker/qdrant_data/` again |
| Mutation: Langfuse guard replaced by the pre-7.1 `trace_config` call (scratch pytest plugin, no repo change) | 6 of 10 Langfuse tests **fail** as intended (the 4 guard/control tests are unaffected) |
| Mutation: pre-7.1 Sentry options in the real probe | **all 8 sentinel types leak** (resume, JD, answer, bearer token, cookie, worker token, query, e-mail); with 7.1 options none |
| `ruff check` on every changed/new Python file (backend ruff 0.15.19) | clean |
| `python scripts/sync_observability.py --check` | in sync |
| **R2 gap closure (after the pre-staging audit):** | |
| `ai-worker: pytest -rA tests/test_sentry_ai_integrations.py tests/test_sentry_privacy.py tests/test_interview_no_langfuse.py tests/test_worker_correlation.py tests/test_interview_engine.py tests/test_interview_report.py tests/test_interview_flag.py tests/test_worker_security.py tests/test_face_tenant_filter.py` | **83 passed, 0 failed, 0 skipped** (8 new + 6 + 10 + 5 + 21 + 19 + 2 + 6 + 6) |
| `backend: pytest -rA tests/test_sentry_privacy.py tests/test_mock_interview_correlation.py tests/test_error_handler.py tests/test_health.py` | **39 passed, 0 failed, 0 skipped** (6 + 25 + 4 + 4) |
| `python scripts/sync_observability.py --check`; `ruff check` on the shared module copies and the new test | in sync; clean |

Not run: the full backend suite (~18 min; scheduled at 7.3, 7.6 and 7.10 per §7), the Postgres suites (no
throwaway Postgres), frontend checks (no frontend change), live LLM / live Sentry / live Langfuse (by design: tests
are offline with a fake DSN and in-memory transport).

### 14.6 Limitations and deferred items
- Mentor, resume and proof-scoring worker calls don't send correlation headers yet, and keep their Langfuse tracing
  when keys are set (unchanged; outside the interview scope). Resume drafting prompts contain resume text: worth a
  separate privacy decision.
- Parent-context inheritance is tested with a Langfuse-style handler; an inherited LangSmith tracer goes through the
  same mechanism (detached the same way) but has no separate test, to keep tests off the network.
- Starlette/FastAPI events and the interview LangChain/LangGraph path are verified end to end; spans and
  breadcrumbs of other auto-enabled integrations (httpx, SQLAlchemy, Redis) are verified on synthetic payloads
  through the same allow-list.
- The AI-integration list is fixed for sentry-sdk 2.63.0. A future SDK upgrade that adds a new AI integration needs
  the list reviewed; `test_sentry_ai_integrations.py` fails if a LangChain/LangGraph/Google GenAI integration becomes
  active again, but cannot know about integrations that don't exist yet.
- The full backend suite was not re-run after the shared Sentry change; the backend's active integration list was
  measured identical before and after, and the backend Sentry tests pass.
- The invocation id is not yet persisted; 7.3 uses it as the ledger's `event_id` source.
