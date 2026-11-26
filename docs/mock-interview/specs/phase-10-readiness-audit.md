# Phase 10 — Production-Readiness Audit (read-only)

> **Archived copy (Batch 10.1, 2026-11-25).** The audit body below is unchanged from the session report.
> Its decision IDs `P10-D1…D9` are the **audit's** numbering; the owner's approved decisions use a different
> numbering. See `phase-10-production-readiness.md` §2–§3 for the mapping and the current decision status.
>
> **Corrections found in Batch 10.1:**
> 1. The audit missed the existing skeleton `phase-10-final-acceptance-checklist.md` (Phase 7.10). Its rows A–E
>    are still valid and are carried into the Phase 10 plan.
> 2. The approved roadmap (P7-D1, `IMPLEMENTATION_PLAN.md` §6a) gives Phase 10 a wider scope than production
>    readiness: also the platform-operator role (P7-D2) and enterprise integrations. See the plan's open question OQ-1.

- Date: 2026-11-25
- Worktree: `D:\Campus_Ai-mock-interview`, branch `feature/ai-mock-interview`, HEAD `a336bb1`
- Method: read-only inspection of the repository and docs. Nothing was run against any hosted service, no `.env`
  values were read, `docker-compose.override.yml` was not opened, no file in the repo was changed.
- Status labels: **IMPL** = implemented in code, **MISSING** = not in repo, **RISK** = exists but has a known
  weakness, **UNVERIFIED** = may exist outside the repo / never tested live.

## Overall verdict

**NOT production-ready.** The code-level safety foundation is strong (fail-closed flags, startup secret/RLS gates,
circuit breaker, quotas, protected metrics, runbooks). The gaps are operational: the production hosting is
not decided, the deploy template is behind Phases 7–9, there is no live-LLM, load, real-device, backup-restore or
incident drill evidence, some AI features have no rate limit or quota, and nothing alerts a human automatically.

### Top 10 blockers (ranked)

| # | Finding | Evidence |
|---|---|---|
| B1 | Production host, domain, proxy topology and cookie policy are **not decided** | `deploy/README.md` lines 3–7 |
| B2 | Deploy template knows **no** Phase 7–9 settings: 0 mentions of `AI_MOCK_INTERVIEW_*`, `PLACEMENT_MANAGEMENT_*`, `METRICS_TOKEN`, worker `MOCK_INTERVIEW_*` | `deploy/compose.prod.yml`, `deploy/prod.env.example` (grep count 0) |
| B3 | Committed frontend image **cannot** enable the interview UI or voice: `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` arg and `microphone=(self)` exist only in your uncommitted Phase 5 edits; committed policy is `microphone=()` | `git show HEAD:frontend/Dockerfile` (no master arg), `HEAD:frontend/next.config.ts:84` |
| B4 | Interview engine never tested against a **live LLM** (Phases 3/4: "live LLM not run") | `PROJECT_STATE.md` status table |
| B5 | Mentor / resume / ATS AI routes have **no rate limit and no quota** → uncapped LLM cost per user | `grep limiter` in `backend/app/routers/mentor.py`, `resume.py`: no hits |
| B6 | Backend runs **one** uvicorn process with **sync** routes making blocking 60–180 s AI calls → the default 40-thread pool can be exhausted, stalling *all* routes on that instance | `backend/Dockerfile:43` (no `--workers`), `backend/app/routers/mock_interview.py` (`def` routes), `ai_client.py:58-67` |
| B7 | No load test tooling or capacity model; all perf numbers are SQLite/local | no locust/k6 in repo; Phase 9 caveat |
| B8 | Backups: scripts exist but **no schedule, no off-site copy, no RPO/RTO, no restore drill evidence**; Storage files not covered | `scripts/backup/*`, `.github/workflows/` has only `ci.yml` |
| B9 | Alerts are log lines + optional webhook; **no** alert receiver, uptime monitor, Sentry DSN or Prometheus scraper is configured anywhere; frontend has **no** error tracking | `alerts.py`, `compose.prod.yml` (`SENTRY_DSN:-`, `ALERT_WEBHOOK_URL:-` empty), no `sentry` in `frontend/src` |
| B10 | Real-device / browser matrix pending (Phase 5 voice, Phase 6 video 24 rows, Phase 9 screen readers) | `PROJECT_STATE.md` |

---

## 1. Hosting and deployment topology

| Item | Status | Evidence |
|---|---|---|
| Local dev stack (db, redis, qdrant, backend, RQ worker, ai-worker, frontend) | IMPL (dev only) | `docker-compose.yml` header: "LOCAL DEVELOPMENT ONLY", superuser DB |
| Hardened self-hosted template: read-only containers, `cap_drop: ALL`, `no-new-privileges`, digest images, owner role only in `migrate`, ai-worker unpublished, `${VAR:?}` required keys | IMPL, **never run** | `deploy/compose.prod.yml`, `deploy/README.md` "has not been run" |
| Template policy checker | IMPL | `deploy/check_compose_prod.py` |
| Free-tier public staging: Vercel + Render (backend, ai-worker) + Supabase + Qdrant Cloud, synthetic data | Exists outside repo; **UNVERIFIED which commit it runs** | `deploy/README.md` "Free-tier public staging" (checked 2026-11-16/17, at migration head `f0a1b2c3d4e5`, far behind current `e1a3c5b7d9f4`) |
| Staging: no Redis, no job worker; jobs lost on restart; rate limits per instance | RISK | `deploy/README.md`, `backend/jobs.py` |
| Staging ai-worker is a public URL protected only by a token (no private network) | RISK | `deploy/README.md` |
| Render free tier sleeps; mentor cold start 49 s (Render) / 170 s (local 0.1 CPU) vs 60 s backend timeout | RISK | `deploy/README.md` |
| Vercel auto-deploys every push to `main` | RISK (process) | `CLAUDE.md` hard rules |
| Production host / domain / TLS proxy / `TRUSTED_PROXY_HOPS` / cookie SameSite | **MISSING – owner decision** | `deploy/README.md`, `prod.env.example` "DECIDE WITH HOSTING" |
| Code runner (Phase 8.5) host | Not connected; G2 NOT passed — keep out of Phase 10 scope | `CLAUDE.md`, `runbooks/code-runner.md` |

## 2. Production vs local configuration

| Item | Status | Evidence |
|---|---|---|
| `ENVIRONMENT` accepts only development/test/production (typo fails) | IMPL | `backend/app/core/config.py:29-38`, `ai-worker/config.py` |
| Production refuses to boot with weak `SECRET_KEY` / empty `AI_WORKER_TOKEN` / short staging token | IMPL | `backend/main.py` (secrets gate), `config.py: insecure_secret_issues` |
| Production refuses a superuser / BYPASSRLS DB role | IMPL | `backend/main.py _lifespan`, `app/core/db.py runtime_role_issues` |
| Worker refuses to boot in production with token < 32 chars | IMPL | `ai-worker/config.py insecure_worker_issues` |
| `/docs`, `/openapi.json`, `/db-check` off in production; `/metrics` only with valid `METRICS_TOKEN` | IMPL | `backend/main.py` |
| All new features default OFF (flags) | IMPL | `config.py`; `CLAUDE.md` flag list |
| Template/env manifest missing every Phase 7–9 key (see B2) | **MISSING** | grep count 0 |
| Frontend build flags baked at build time → turning UI on/off needs a rebuild | RISK (by design; backend flag is authoritative) | `frontend/Dockerfile` ARGs, `next.config.ts` |
| Committed Dockerfile lacks master `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` arg; committed `microphone=()` (B3) | **MISSING – owner decision on your Phase 5 edits** | `PROJECT_STATE.md` Phase 5/6.1 notes |
| Settings cached per process (`lru_cache`) → any env change needs a restart | RISK (operational note) | `config.py get_settings` |

## 3. AI provider integrations, models and fallback

| Item | Status | Evidence |
|---|---|---|
| One provider at a time: Groq (`llama-3.3-70b-versatile`) or Gemini (`gemini-2.0-flash`) via `LLM_PROVIDER` | IMPL | `ai-worker/config.py`, `ai-worker/llm.py:21-55` |
| Backup provider / automatic fallback | **MISSING (deliberately deferred, P7-D6)** | `specs/phase-7-production-operations.md` |
| Default model names still served by the providers today | **UNVERIFIED** — must be checked in provider consoles before go-live | `ai-worker/config.py` |
| No explicit LLM SDK timeout / `max_retries` on `ChatGroq` / `ChatGoogleGenerativeAI` → SDK defaults; worker may keep running (and spending tokens) after the backend has given up at 60/120 s | RISK | `ai-worker/llm.py:34-51` |
| Chat model cached (`lru_cache`) → key rotation needs worker restart | RISK (note) | `ai-worker/llm.py:20` |
| Interview LLM calls excluded from Langfuse/LangSmith/Sentry AI integrations | IMPL + tests | `ai-worker/interview/no_tracing.py`, `tests/test_interview_no_langfuse.py`, `test_sentry_ai_integrations.py` |
| Mentor/resume/scoring calls **are** traced to Langfuse if keys are set (student profile data leaves to a third party) | RISK (privacy decision) | `ai-worker/tracing.py`, `compose.prod.yml` passes `LANGFUSE_*` |
| Live LLM run of the interview engine, report quality, Hindi/Hinglish behaviour | **UNVERIFIED** | `PROJECT_STATE.md` |
| Provider data-processing terms / data residency for student answers | **UNVERIFIED – owner/legal** | — |

## 4. Outages, retries, timeouts, graceful degradation

| Item | Status | Evidence |
|---|---|---|
| Interview calls: 60 s (turn/plan), 120 s (report), **one** retry only on connect errors (no retry after the LLM may have run) | IMPL | `backend/app/services/mock_interview_ai.py:57-59,126-133` |
| Circuit breaker per step, ON by default, conservative thresholds, alerts on transitions | IMPL | `backend/app/services/ai_circuit.py` |
| Breaker state is per process (several instances open independently) | RISK (documented) | `ai_circuit.py` docstring |
| Student sees "nothing you typed was lost" message on failure | IMPL | runbook `provider-outage-circuit-breaker.md` |
| Mentor/resume/scoring: 3 attempts incl. 5xx and read timeouts → mentor user may wait ~3×60 s; a retried 5xx can re-run a paid LLM call | RISK | `backend/ai_client.py:58-102` |
| Sync routes + single process + long blocking calls (B6) | RISK | `backend/Dockerfile:43` |
| Job queue: Redis/RQ with 3 retries; falls back to in-process (lost on restart) | IMPL / RISK without Redis | `backend/jobs.py`, `worker.py` |
| Readiness: DB required, Redis optional | IMPL | `backend/app/observability/health.py` |
| Live outage drill (kill worker, block provider, watch breaker + alerts) | **UNVERIFIED** (runbooks say "Phase 10") | `runbooks/README.md` |

## 5. Usage quotas, rate limits, cost controls

| Item | Status | Evidence |
|---|---|---|
| Interview: 10 sessions/student/day, AI step 20/min, report 5/min, coding/company per-caller limits | IMPL | `mock_interview_policy.py:48`, `routers/mock_interview*.py` |
| Institute monthly session/token quotas + soft-limit warnings (OPS flag) | IMPL | `mock_interview_policy.py`, runbook `institute-controls-quotas.md` |
| Platform ceilings `…_TENANT_MONTHLY_SESSION_MAX/TOKEN_MAX` | IMPL, **empty = no cap** | `config.py` |
| Cost estimates: `AI_PRICE_TABLE` empty → cost "not configured" | **MISSING – needs approved price table** | `runbooks/pricing.md` |
| Mentor / resume / ATS / proof scoring: no per-route limit, no quota (B5) | **MISSING** | grep |
| Rate-limit counters in memory unless `REDIS_URL` set (staging has none) | RISK | `app/core/rate_limit.py` |
| Login: per-IP only; no per-account lockout (campus NAT caveat) | RISK (documented) | `rate_limit.py` docstring |
| Provider-side spend cap / billing alert | **UNVERIFIED – check in Groq/Google consoles** | — |
| Provider RPM/TPM limits vs expected concurrency | **UNVERIFIED** | — |

## 6. Global and per-feature kill switches

| Item | Status | Evidence |
|---|---|---|
| Mock interview master + voice/video/coding/coding-AI/company/ops sub-flags, tenant allow-lists (fail closed) | IMPL | `config.py`, `runbooks/incident-investigation.md` "Kill switch" |
| Per-institute pause at runtime (no restart) via Ops page | IMPL (behind OPS flag) | `runbooks/institute-controls-quotas.md` |
| Worker-side flags (`MOCK_INTERVIEW_*`) | IMPL | `ai-worker/config.py` |
| Placement v2 flag + tenant list | IMPL | `config.py PLACEMENT_MANAGEMENT_*` |
| Circuit breaker can be disabled | IMPL | `AI_CIRCUIT_ENABLED` |
| Kill switch for mentor / resume / ATS / proof scoring | **MISSING** (only by removing the LLM key, which turns into errors) | `ai-worker/llm.py` |
| Global "all AI off" / maintenance mode | **MISSING** | — |
| Env flags need restart; frontend flags need rebuild | RISK (note) | §2 |
| Kill-switch drill timed on a real deployment | **UNVERIFIED** | — |

## 7. Monitoring, logs, metrics, alerting

| Item | Status | Evidence |
|---|---|---|
| JSON request logs with request id; correlation ids backend→worker | IMPL | `app/observability/observability.py`, `ai-worker/correlation.py` |
| Sentry (backend + worker) with privacy options; off without DSN | IMPL, **not configured** | `observability.py:100-190` |
| Prometheus metrics (HTTP + AI usage/latency/circuit) with token in production | IMPL, **no scraper** | `main.py`, `runbooks/metrics-access.md` |
| Alerts: structured `campus.alert` log line + optional webhook, throttled | IMPL, **no receiver configured** | `app/observability/alerts.py`, `ai_alerts.py` |
| AI usage ledger (content-free) + admin usage UI | IMPL | Phase 7.3/7.7/7.8 |
| Frontend error tracking (browser errors) | **MISSING** | no `sentry` in `frontend/src` |
| External uptime monitor, dashboards, SLOs, on-call routing | **MISSING** | — |
| Log retention / aggregation destination | **MISSING – hosting decision** | `compose.prod.yml` json-file 10m×5 only |

## 8. Database and Redis reliability

| Item | Status | Evidence |
|---|---|---|
| RLS with NOBYPASSRLS runtime role; PG suites pass (RLS 34/0, `tests_pg_api` 241/0) on disposable PG 16 | IMPL + tested locally | Phase 9 evidence |
| 8 tables without forced RLS (as of head `f0a1b2c3d4e5` on staging) incl. `users`, `refresh_tokens`, `tenant_invites` | RISK – re-check at current head | `deploy/README.md` "Tables without forced RLS" |
| Supabase Data API not disabled | RISK | `deploy/README.md` |
| Connection pool: SQLAlchemy defaults (5 + 10 overflow per process), `pool_pre_ping`; no `statement_timeout`, no pooler decision | RISK / UNVERIFIED vs Supabase limits | `app/core/db.py:14` |
| Migrations additive, owner role only in one-off job, 61-migration up/down/up verified on disposable PG | IMPL | `PROJECT_STATE.md`, `runbooks/deployment-migrations.md` |
| Migrations on a staging/prod-like DB with real data volume | **UNVERIFIED** | — |
| Redis: required by template, optional in code; circuit breaker and alert throttle are in-process | IMPL / RISK | `jobs.py`, `ai_circuit.py` |
| Redis persistence/eviction/auth/TLS choice | **MISSING – hosting decision** | `prod.env.example` asks for `rediss://` |
| Known: T-1 PG parameter limit for very large institutes; S-1 malformed ZIP → 500 | Accepted Phase 9 caveats | Phase 9 handoff |

## 9. Load testing and capacity

| Item | Status | Evidence |
|---|---|---|
| Load-test scripts | **MISSING** | no locust/k6/etc. in repo |
| Capacity model (institutes, students, concurrent interviews, peak placement days) | **MISSING** | — |
| Measured: backend ~120 MiB, ai-worker 85–305 MiB at 512 MB caps (local only) | Partial | `deploy/README.md` |
| Phase 9 perf on SQLite only | Accepted caveat | Phase 9 handoff |
| Threadpool / single-process limit (B6) | RISK – must be measured | §4 |

## 10. Real-device and browser compatibility

| Item | Status | Evidence |
|---|---|---|
| Unit + headless Chromium smoke (simulated mic/camera) | IMPL | Phase 5/6/9 evidence |
| Real microphone (Chrome/Edge desktop, Android Chrome), Hindi/Hinglish accuracy | **PENDING** | `PROJECT_STATE.md` Phase 5 |
| Phase 6 video real-device checklist (24 rows) | **PENDING** | `PROJECT_STATE.md` Phase 6 |
| Safari / iOS, Firefox, low-end Android, slow networks | **UNVERIFIED** (no evidence found) | — |
| Screen readers (NVDA/TalkBack/VoiceOver) | **PENDING** | Phase 9 caveat |
| Firefox/Brave: no usable STT → typed fallback | Known limitation | Phase 5 |

## 11. Secrets management and production security

| Item | Status | Evidence |
|---|---|---|
| Secrets from env only; templates hold key names only; gitleaks on full history in CI | IMPL | `prod.env.example`, `.github/workflows/ci.yml` |
| CSRF origin check, strict API CSP, frontend CSP, security headers, HSTS opt-in, constant-time token compares, hashed invite tokens | IMPL | `app/core/*`, `deploy/README.md` |
| Production secret store / rotation procedure / who has access | **MISSING – hosting decision** | — |
| "Old compromised project" referenced; past local secret leak noted | RISK — confirm all keys from that time were rotated | `deploy/README.md`, local memory note |
| Dependency vulnerability scanning (pip-audit / npm audit / Dependabot), container image scanning | **MISSING** | `.github/` has only `ci.yml` |
| CI does not run: ai-worker tests, frontend unit tests, `next build`, Docker image builds, PG/RLS suites | **MISSING** | `ci.yml` jobs: backend, frontend (tsc+lint), secrets, sandbox |
| External penetration test / security review of the deployed stack | **MISSING** | — |
| Data-protection obligations (student personal data; e.g. India DPDP Act), privacy notice, DSR process | **UNVERIFIED – owner/legal** | `runbooks/data-retention.md` covers ledger only |
| Code execution (Phase 8 G2) | Must stay OFF | `CLAUDE.md` |

## 12. Backup, recovery, incident response

| Item | Status | Evidence |
|---|---|---|
| `pg_backup.sh` (+sha256), `pg_restore.sh` (refuses without `--force`), `verify_restore.sh` (throwaway container), `qdrant_snapshot.sh` | IMPL | `scripts/backup/` |
| Scheduled backups, off-site/encrypted copy | **MISSING** | `pg_backup.sh` header leaves upload "to the caller" |
| `verify_restore.sh` uses unpinned `postgres:16` (rest of repo digest-pins) | RISK (minor) | `scripts/backup/verify_restore.sh` |
| Supabase Storage (uploaded files) backup | **MISSING** | — |
| Provider-managed backups / PITR on the chosen DB plan | **UNVERIFIED** | — |
| RPO / RTO targets | **MISSING – owner decision** | — |
| Restore drill on a real backup | **UNVERIFIED** | — |
| Runbooks (11) for outages, alerts, quotas, incidents, migrations, retention | IMPL, **never drilled** | `runbooks/README.md` "Nothing … executed against staging or production" |
| Severity levels, on-call contact, escalation, status page, breach-notification steps | **MISSING** | — |

---

## Decisions needed from the owner (before implementation)

| ID | Decision | Why it blocks |
|---|---|---|
| P10-D1 | Production hosting: stay on hosted platforms (Vercel/Render/Supabase/Qdrant Cloud, paid tiers) **or** self-host with `compose.prod.yml` | Drives Redis, private networking, scaling, backups, monitoring |
| P10-D2 | Domain layout (same-site vs cross-site) → cookie SameSite, CORS, `TRUSTED_PROXY_HOPS`, HSTS | Auth security |
| P10-D3 | Your Phase 5 edits (`frontend/Dockerfile`, `next.config.ts`): commit them (needed for interview UI + voice in a deployed build) or keep local | B3 |
| P10-D4 | Approved AI price table + platform monthly caps + provider spend caps | Cost control |
| P10-D5 | Limits/kill switch for mentor/resume/ATS (new flag, default OFF = current behaviour) | B5 |
| P10-D6 | Error/alert tooling (Sentry or alternative, webhook target, uptime monitor) and who receives alerts | B9 |
| P10-D7 | RPO/RTO, backup destination and retention | B8 |
| P10-D8 | Pilot size (institutes, students, concurrent interviews) for the capacity target | B7 |
| P10-D9 | Langfuse for non-interview features: on or off in production | Privacy |

## Proposed Phase 10 plan (nothing starts without approval)

Each batch: flags default OFF, additive only, full regression before commit, no push/deploy without approval.

### 10.1 — Decisions and production architecture document (docs only)
- Record P10-D1…D9, target topology diagram, capacity assumptions.
- **Accept:** owner signs off every decision; doc committed; no code change.

### 10.2 — Configuration parity
- Add all Phase 7–9 keys to `prod.env.example` / `compose.prod.yml` (defaults OFF); extend `check_compose_prod.py`
  to fail when a flag key is missing; resolve B3 per P10-D3; add a startup log line listing enabled flags (names only).
- **Accept:** policy check passes on a synthetic env; check fails when a key is removed; backend + worker suites green.

### 10.3 — AI resilience and cost guards
- Explicit LLM SDK timeout and `max_retries` aligned with backend timeouts; mentor/resume retry only on connect errors;
  per-user limits + daily quota + kill switch for mentor/resume/ATS behind a new default-OFF flag; uvicorn
  workers / threadpool sizing decision.
- **Accept:** new unit tests for timeouts, retries, limits and flag-off = unchanged behaviour; full backend 774+/0
  failed; worker tests green; PG suites green.

### 10.4 — CI hardening
- Add ai-worker tests, frontend `test:unit` + `next build`, Docker image build, dependency audit (pip-audit/npm audit,
  report-only first).
- **Accept:** all jobs green on a PR branch; documented baseline of audit findings.

### 10.5 — Observability wiring (after P10-D6)
- Frontend error tracking with the same privacy rules; alert webhook target; uptime checks on `/health` and
  `/health/ready`; minimal dashboard for AI failure rate, latency, circuit state, quota rejections.
- **Accept:** a synthetic alert reaches the chosen channel on staging; no student content in any event (privacy test).

### 10.6 — Backup and recovery
- Scheduled encrypted off-site backups; pin `verify_restore.sh` image; Storage + Qdrant coverage; RPO/RTO.
- **Accept:** a real backup restored into a scratch DB with row-count + head checks; restore time recorded vs RTO.

### 10.7 — Staging rebuild on the current commit + security re-check
- Deploy current branch to staging (approval required), run migrations, re-run RLS/privilege checks at head
  `e1a3c5b7d9f4`, decide on Supabase Data API, rotate any key from the compromised period.
- **Accept:** privilege report shows forced RLS on all tenant tables (or documented exceptions); 403/401/404 gates
  re-verified.

### 10.8 — Live AI and load tests (staging, synthetic data)
- Live LLM runs (text/voice/video/report, Hindi/Hinglish); load test to the P10-D8 target incl. AI latency and
  provider rate limits.
- **Accept:** p95 latency and error rate within agreed targets; no threadpool/DB-pool exhaustion; token usage and
  cost recorded.

### 10.9 — Drills
- Provider outage (breaker opens/closes + alert), worker down, kill switch timing, institute pause, restore drill,
  incident walkthrough by request id.
- **Accept:** every runbook step executed once with timestamps; runbooks corrected where reality differed.

### 10.10 — Real-device matrix and final acceptance
- Phase 5 voice rows, Phase 6 video 24 rows, Safari/iOS + Android + Firefox, screen readers, full regression.
- **Accept:** every row has a dated result (pass/fail/known-limitation); full regression green; owner go/no-go.

Out of scope for Phase 10: Phase 8 code execution (G2 not passed), multimodal AI, new product features.
