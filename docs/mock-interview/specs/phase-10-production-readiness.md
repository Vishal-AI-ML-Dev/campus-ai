# Phase 10 — Production readiness: decision record and plan

Status (updated 2026-11-25, end of the Phase 10 execution session): **Batches 10.1–10.10 executed as far as is
possible without external accounts, deployment or live-provider authorization** (owner instruction "complete the whole
Phase 10"). All work is **local and uncommitted** (no commit, push, PR, merge or deployment). Results per batch: §10;
gate-by-gate acceptance and the final verdict: §11 — **NOT PRODUCTION READY** (critical gates still need staging,
provider and owner evidence). **D3 (Phase 5 frontend configuration) is complete and committed locally as `12029d6`**
(separate owner-approved commit; see §2 D3 and §6).

_Historical status line (Batch 10.1 draft):_ "Batch 10.1 (documentation only) — drafted 2026-11-25, awaiting owner
review. Batches 10.2–10.10 have not started and each needs separate owner approval." Superseded by the owner's
instruction of 2026-11-25 to execute all of Phase 10.

- Branch `feature/ai-mock-interview`, base commit `a336bb1` (Phase 9 closure; verified 2026-11-25).
- Inputs: `phase-10-readiness-audit.md` (the read-only audit, archived), `phase-10-final-acceptance-checklist.md`
  (Phase 7.10 skeleton), `PROJECT_STATE.md`, `IMPLEMENTATION_PLAN.md` §6a, `deploy/README.md`, the runbooks, and
  the source files cited below.
- Labels: **VERIFIED** (checked in the repo), **OWNER** (owner decision), **ASSUMPTION** (believed, not checked),
  **UNVERIFIED** (must be checked on the provider or the live service before anything relies on it).

**Primary principle (owner):** CampusAI must stay secure and financially safe even when free-tier limits prevent a
production launch. A delayed launch is better than an insecure or unexpectedly paid deployment.

---

## 1. Verified starting state (2026-11-25)

| Item | Value | How checked |
|---|---|---|
| Branch / HEAD | `feature/ai-mock-interview` / `a336bb1fda78bb5cf17ac76d2778adf0a2c95b40` | `git branch --show-current`, `git rev-parse HEAD` |
| Phase 9 closure commit | `a336bb1` "fix(placement): complete Phase 9 closure and hardening", 14 files | `git show --stat a336bb1` |
| Staged changes | none | `git diff --cached --name-only` |
| Owner's Phase 5 edits (unstaged) | `frontend/Dockerfile`, `frontend/next.config.ts` | `git status` |
| Untracked, preserved | `.claude/`, `ai-worker/qdrant_data/`, `docker-compose.override.yml` (never opened), Phase 7/8/9 handoffs, `frontend/AGENTS.md` | `git status` |
| Phase 8 G2 | NOT passed: no student code is executed anywhere | `PROJECT_STATE.md`, `CLAUDE.md` |
| Push / merge / deploy of this branch | none | `PROJECT_STATE.md` |

_Historical snapshot taken before D3._ Since then (2026-11-25): HEAD is `12029d688505f134427c7333f683672913110dc8`
(parent `a336bb1`), and the owner's Phase 5 edits are no longer unstaged; they were corrected and committed in D3 (§6).

## 2. Owner decisions D1–D6 (approved 2026-11-25, recorded verbatim in substance)

Owner IDs are `D1…D6`. The audit used `P10-D1…D9` for its own list; §3 maps the two.

### D1 — Hosting: free tier only (OWNER)
- Free-tier hosting only. No paid plans, no automatic paid upgrade, no unexpected billing.
- Existing architecture and providers must be evaluated against free-tier limitations.
- If free hosting cannot meet a necessary security or reliability requirement, the limitation is reported and
  **production acceptance is blocked**; safeguards are never weakened to fit.

### D2 — Domain: `https://campus-ai-staging.vercel.app` (OWNER)
- The existing free Vercel subdomain. No custom domain purchase.
- Cookie, CORS, origin and proxy compatibility must be verified before relying on it for production-like testing.

### D3 — Phase 5 frontend configuration (OWNER)
- Approved: a **separate review** and a **separate local commit** of `frontend/Dockerfile` and
  `frontend/next.config.ts`, kept apart from the Phase 9 commit.
- Deployment readiness is not assumed; no deployment is started.
- ~~**Status: review drafted (§6), commit NOT made.**~~ **Status (2026-11-25): COMPLETE — committed locally.** The
  review findings were resolved (R2 removed, R3 option 2b approved and implemented), the targeted tests and builds
  passed (§6.2), and the owner approved the commit:
  `12029d688505f134427c7333f683672913110dc8` "fix(frontend): finalize Phase 5 interview permissions" (parent
  `a336bb1`, separate from the Phase 9 commit and from all Phase 10 docs). Not pushed or deployed.

### D4 — AI spending: ₹0 per month (OWNER)
- Free-tier AI usage only. No paid provider fallback, no automatic billing, no accidental paid models.
- Application-side usage limits must be enforced; new AI calls are stopped or rejected when free quotas are
  exhausted.
- Non-AI platform functionality must keep working when AI is unavailable.
- A provider's advertised free tier is never assumed to be unlimited or guaranteed.

### D5 — Monitoring alerts: email (OWNER)
- Preferred channel: **email**.
- Required alerts: critical service outage; database connectivity; AI quota exhaustion; repeated backend errors;
  backup failure; recovery notifications where useful.
- No email address or provider credentials are authorised yet. None will be invented, stored in the repo or
  configured without owner input.

### D6 — Backup and recovery objectives (OWNER)
- **RPO 24 hours**, **RTO 8 hours**, **daily** backups, storage within the free-tier-only constraint.
- These are targets, not proof. Backup readiness is claimed only after a real, isolated restore drill succeeds.

## 3. Mapping to the audit, and the pending decisions D7–D9

### 3.1 Audit decision list vs owner decisions

| Audit ID (exact text) | Covered by | Status |
|---|---|---|
| P10-D1 "Production hosting: hosted platforms (paid tiers) or self-host with `compose.prod.yml`" | **D1** | Decided: free-tier hosted platforms. Self-hosting needs a server, so it is out unless free. |
| P10-D2 "Domain layout (same-site vs cross-site) → cookie SameSite, CORS, `TRUSTED_PROXY_HOPS`, HSTS" | **D2** | Decided: Vercel subdomain. Compatibility still to verify (§5.2). |
| P10-D3 "Your Phase 5 edits: commit them or keep local" | **D3** | Decided: separate review + separate commit. |
| P10-D4 "Approved AI price table + platform monthly caps + provider spend caps" | **D4** | Decided: ₹0. Price table not needed (§5.3); caps are now mandatory. |
| P10-D5 "Limits/kill switch for mentor/resume/ATS (new flag, default OFF = current behaviour)" | **D4** (limits required) | **Partly open**: see OQ-2 (default ON vs OFF). |
| P10-D6 "Error/alert tooling and who receives alerts" | **D5** (channel = email) | **Partly open**: tool choice, see OQ-4. |
| P10-D7 "RPO/RTO, backup destination and retention" | **D6** (RPO/RTO/frequency) | **Partly open**: destination, retention, key custody → **D7** below. |
| P10-D8 "Pilot size (institutes, students, concurrent interviews) for the capacity target" | — | **Open → D8** |
| P10-D9 "Langfuse for non-interview features: on or off in production" | — | **Open → D9** |

The owner's D7–D9 are the audit's P10-D7…D9, taken exactly as written. Because owner D6 already answers the RPO/RTO
part of P10-D7, D7 is narrowed to what is still unanswered. **Nothing below is decided until the owner says so.**

### 3.2 D7 — Backup destination, retention, encryption key (OPEN — owner chose "decide later", 2026-11-25)

**Owner decision (2026-11-25): decide later.** The encrypted backup/restore tooling is built and drilled locally
(§10.6); no external destination is configured and no key is created for production. **G9 stays blocked** until D7.

**Question:** Where are daily backups stored, how long are they kept, and who holds the decryption key?

**Constraints:** free-tier only, never public, encrypted before leaving the source, Postgres **and** uploaded files
(Supabase Storage) covered, daily.

| Option | Pros | Cons / UNVERIFIED |
|---|---|---|
| A. Owner's Google Drive (personal free storage) | Already owned, private, no new account | Upload automation needs a Drive credential in CI (a new secret); quota shared with personal files |
| B. Backblaze B2 / similar free object-storage tier | Built for backups | New account; free limits and whether a card is required are UNVERIFIED (a card on file conflicts with D1/D4) |
| C. GitHub Actions artifacts | No new account | Retention capped (UNVERIFIED limit); on a **public** repo, artifacts may be downloadable by others — unacceptable unless encrypted; scheduled workflows run only from the default branch (`main`), and a push to `main` triggers a Vercel deploy (see OQ-6) |
| D. Owner's PC (scheduled task) | Fully private, free | PC must be on daily; RPO 24 h at risk; single copy |

**Recommendation:** encrypt every dump with a public key kept in the repo and a private key kept **offline by the
owner**; store in **A** (primary) with **D** as a second copy; retention **14 daily + 8 weekly**
(`pg_backup.sh` already defaults to 14 days). Final choice after 10.6 verifies the free limits.

### 3.3 D8 — Pilot size (**APPROVED 2026-11-25**: 1 institute, up to 100 students, at most 3 simultaneous AI interviews)

**Question:** How many institutes, students and simultaneous AI interviews must the first release support?

**Why it matters:** the load target (10.8), quotas (10.3) and the verdict (§7) depend on it. Free AI tiers have
per-minute and per-day limits (UNVERIFIED values), and the backend runs one process with a ~40-thread pool (audit
B6).

**Recommendation (provisional, to be confirmed by 10.8 measurements):** 1 institute, up to 100 students, at most
3 simultaneous AI interviews, and a daily platform AI budget set below the measured free-tier limit.

**Owner decision (2026-11-25): approved as recommended.** Applied as `AI_MAX_CONCURRENT_CALLS=3` in the templates and as
the 10.8 load-test target (§10.8).

### 3.4 D9 — Langfuse for non-interview features (OPEN)

**Question:** Should mentor/resume/ATS/scoring LLM calls be traced to Langfuse in production?

**Facts (VERIFIED):** interview calls are already excluded (`ai-worker/interview/no_tracing.py`, tests). Mentor,
resume and scoring calls are traced only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set
(`ai-worker/tracing.py`); those prompts contain student profile data.

**Recommendation:** **OFF** (leave both keys empty in production). It keeps student data with fewer third parties and
avoids another free-tier dependency.

### 3.5 Open questions raised by Batch 10.1

Decided by the owner on 2026-11-25 (answers to the questions asked at the start of the execution session):
- **OQ-2 → "Off in code, required in production."** `AI_GOVERNANCE_ENABLED` defaults to `false` (dev/tests unchanged,
  CLAUDE.md respected); a **production** backend refuses to start unless it is `true` and both platform caps are
  positive integers (`config.ai_governance_issues`). The worker likewise refuses to start in production without
  `LLM_TIMEOUT_SECONDS` (1–45) and `LLM_MAX_RETRIES` (0–1). The kill switches (`AI_KILL_SWITCH`,
  `AI_DISABLED_FEATURES`) act even with governance off; their defaults kill nothing.
- **OQ-4 → "External free monitors."** Uptime monitor (`/health`, `/health/ready`), heartbeat monitor (backup job),
  Sentry free plan (errors and `campus.alert`). No SMTP secret in the repo (runbook 14).

Still open (not decided; nothing below was chosen silently): **OQ-1** (scope — the platform-operator role and
enterprise integrations remain **in Phase 10 scope** and are **not implemented**; the owner instruction of
2026-11-25 says to preserve them until explicitly deferred), **OQ-3** (Gemini/Groq free-tier data terms), **OQ-5**
(hosting terms), **OQ-6** (`main` coupling), **D7**, **D9**.

| ID | Question | Recommendation |
|---|---|---|
| OQ-1 | **Scope.** The approved roadmap (P7-D1) defines Phase 10 as "enterprise integrations, platform-level security and final acceptance: platform-operator role and cross-institute ops view (P7-D2), integrations, security review, deployment doc, full regression and all real-device testing". The 10.1–10.10 plan covers production readiness only. | Keep 10.1–10.10 as approved. Move the **platform-operator role** and **enterprise integrations** to a later phase (e.g. Phase 11) by explicit owner decision. Nothing is dropped silently. |
| OQ-2 (**DECIDED 2026-11-25: off in code, required in production**) | **Default for new AI cost guards.** `CLAUDE.md` says new features default OFF and existing API responses must not change. D4 requires limits on mentor/resume/ATS/scoring, which today have none; an OFF-by-default guard leaves them uncapped. | Approve a **documented exception** like P7-D4 (circuit breaker): the global AI budget guard and the mentor/resume/ATS per-user limits default **ON**, returning a clear 429/503 when a limit is hit. Each guard can still be switched off. |
| OQ-3 | **Gemini free-tier data use.** Google's terms for unpaid Gemini API use may allow prompts and responses to be used to improve its products (UNVERIFIED; must be read on the provider's current terms). Student answers would be such data. | Until verified, use **Groq only** for student data (`LLM_PROVIDER=groq`) and do not configure a Gemini key. Groq's free-tier data terms are also UNVERIFIED and must be read in 10.3. |
| OQ-4 (**DECIDED 2026-11-25: external free monitors**) | **Email alert tooling** (D5 sets email only). | Use free tiers that send email without SMTP secrets in our code: an external uptime monitor for `/health` and `/health/ready` (outage, DB, recovery emails), a heartbeat monitor for the backup job (missed or failed backup), and Sentry (free plan) for repeated errors and for `campus.alert` events (AI quota, circuit). Free limits UNVERIFIED. The owner enters the alert email address in each dashboard; it never goes in the repo. |
| OQ-5 | **Hosting terms.** Free plans may restrict commercial use (e.g. Vercel's Hobby plan — UNVERIFIED wording). Serving paying institutes could breach them. | Read the current terms of Vercel, Render, Supabase, Qdrant Cloud and the AI provider in 10.2; if a term forbids the use, block production acceptance (D1). |
| OQ-6 | **`main` coupling.** Vercel deploys every push to `main`; GitHub scheduled workflows (daily backup) run only from `main`. | Do not plan any `main` change inside Phase 10 without a separate owner decision. Prefer a backup trigger that does not need `main` (D7 option D, or a provider-side schedule) until that decision. |

## 4. Free-tier operating model

What D1 + D4 mean in practice. Provider limits are **UNVERIFIED** until checked on the provider in the batch named.

| Component | Free-tier reality (from `deploy/README.md`, VERIFIED as documented on 2026-11-16/17) | Consequence | Checked in |
|---|---|---|---|
| Frontend (Vercel) | Public; `/v1` same-origin proxy adds the staging-gate header (`frontend/src/proxy.ts`) | Auth cookies are first-party on the Vercel host | 10.7 |
| Backend + ai-worker (Render free web services) | Sleep after 15 min idle; mentor cold start ~49 s; no background workers; no private network; ai-worker public, token-protected | No uptime guarantee; first request after sleep may fail; no RQ worker, so jobs run in-process and are lost on restart | 10.2, 10.7 |
| Redis | Not used on staging | Rate limits are per instance; no durable queue. A free managed Redis is possible but UNVERIFIED | 10.2 |
| Postgres + Storage (Supabase free) | RLS with NOBYPASSRLS `app_user`; Data API not disabled (point-in-time observation) | Provider backups/PITR on free plan UNVERIFIED; project pausing after inactivity UNVERIFIED | 10.6, 10.7 |
| Vector store (Qdrant Cloud free) | Holds `career_kb` only; face disabled | Snapshot or rebuild path needed | 10.6 |
| AI (Groq default; Gemini optional) | Single provider, no fallback (P7-D6) | Free per-minute/per-day limits UNVERIFIED; no billing method may be added to provider accounts | 10.3, 10.8 |
| Monitoring / alerts | Code exists; nothing configured | Needs free external tools (OQ-4) | 10.5 |

**Expected ceiling (ASSUMPTION, to be confirmed by evidence):** a free-tier stack has no uptime guarantee, sleeps
when idle, and has no private networking. Even if every gate passes, the realistic best verdict is likely
**READY FOR CONTROLLED PILOT**, not PRODUCTION READY. The final verdict (§7) will say so if the evidence shows it.

## 5. Constraints and targets per area

### 5.1 RPO / RTO (D6)

| Target | What it needs | Current gap (VERIFIED) | Risk |
|---|---|---|---|
| RPO 24 h, daily | A scheduled, monitored daily dump of Postgres **and** Storage files | Scripts exist (`scripts/backup/`); no schedule, no off-site copy, no Storage backup | If the job silently fails, RPO is lost: needs a backup-failure alert (D5) |
| RTO 8 h | A written, drilled restore: new free DB project → roles (`app_role.template.sql`, an unverified draft) → restore → migrate → repoint backend env → smoke test | Never drilled; `verify_restore.sh` uses an unpinned `postgres:16` image | Free-tier project limits and restore speed UNVERIFIED |

Backup readiness is **not** claimed until a real backup is restored into an isolated database with checks
(row counts, Alembic head, a sample tenant read as `app_user`) and the time is recorded against the 8 h RTO.

### 5.2 Domain compatibility checks (D2) — to run in 10.2 (static) and 10.7 (live)

1. `CORS_ORIGINS` = exactly `https://campus-ai-staging.vercel.app`. It is also the CSRF allow-list (`backend/main.py`).
2. `COOKIE_DOMAIN` stays **empty** (host-only). `vercel.app` is a public suffix, so a parent-domain cookie is
   impossible anyway (ASSUMPTION, standard Public Suffix List behaviour).
3. Cookie flags in production: Secure + SameSite=None by default (`config.py cookie_samesite`). With the same-origin
   proxy, `Lax` may be sufficient and stricter — decide after the live check.
4. **Client IP for rate limits:** requests reach Render via Vercel's proxy, so the backend may see Vercel's
   addresses, putting all users into one rate-limit bucket. `TRUSTED_PROXY_HOPS` must be set from a measured
   `X-Forwarded-For` chain, never guessed (`app/core/rate_limit.py`).
5. HSTS: `vercel.app` is already HTTPS-only. Our own `HSTS_MAX_AGE` stays 0 until checked; `includeSubDomains`
   must never be set on a shared platform domain.
6. CSP `connect-src` must include the Storage origin for signed uploads (`deploy/README.md`).

### 5.3 ₹0 AI budget (D4)

- `AI_PRICE_TABLE` stays **empty** (prices are never invented, P7-D9). Cost control is by **counts**: requests and
  provider-reported tokens per user, per institute and platform-wide, set **below** the verified free limits.
- When a provider returns a rate-limit or quota error, the call must be classified as **quota exhausted** (not a
  generic failure), new AI calls must be refused with a clear message, and an alert must be raised.
- No fallback provider, no paid model name, no billing method on provider accounts (owner checks this in the
  dashboards; nothing is read from them by us).

## 6. D3 review — `frontend/Dockerfile`, `frontend/next.config.ts` (static review, 2026-11-25)

Diff reviewed: `git diff -- frontend/Dockerfile frontend/next.config.ts` (3 changed lines).

| # | Change | Finding | Severity | Proposed action (needs approval in the D3 commit step) |
|---|---|---|---|---|
| R1 | Builder stage: `ARG NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=false` | Correct. Same pattern as the committed video/ops/coding build flags; build args are visible to `npm run build`. Default off. | OK | Keep |
| R2 | Runner stage: `ENV NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=$NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` | The `ARG` is declared only in the **builder** stage; build args are stage-scoped, so in the runner stage this evaluates to an **empty string**. `NEXT_PUBLIC_*` values are inlined at build time, so the line most likely has no effect, but it misleads. | Low (functional clarity) | Remove the line, or re-declare the `ARG` in the runner stage. Confirm with a local image build (needs approval if a base image must be pulled). |
| R3 | `Permissions-Policy` `microphone=()` → `microphone=(self)` on **every** route | Needed for browser voice (Phase 5). Allows only first-party pages to *ask*; the browser still prompts the user; no third-party frame gets the mic. Broader than needed: it applies even when the interview build flag is off. A route-scoped rule has the same reload caveat as the camera rule (`next.config.ts` comment). | Low (least privilege) | Option 1: keep as is (simplest, matches Phase 5 testing). Option 2: set `microphone=(self)` only when `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=true`, else keep `microphone=()`. **Recommendation: option 2.** |

_Historical (static review as written on 2026-11-25, before implementation):_ "Existing unit tests already accept
either microphone value (`mockInterviewVideoFoundations.test.mjs:137-149`, `cameraMedia.test.mjs:685`). Not yet run
for D3: `npm run test:unit`, `tsc`, lint, `next build`, live header check, Docker build. D3 is not complete."
**Correction:** that statement held for the global `microphone=(self)` edit, but not for option 2: both tests
asserted that a build with the master flag on has the same Permissions-Policy as the default build, so they had to be
rewritten (the owner approved adding `cameraMedia.test.mjs` to the scope for this).

### 6.1 Resolution (implemented and committed in `12029d6`)

| # | Outcome |
|---|---|
| R1 | **Kept.** Builder-stage `ARG NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=false` (default off), with a comment that it also decides the microphone policy. |
| R2 | **Implemented.** The ineffective runner-stage `ENV NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=$NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` was removed; the other runner ENV values are unchanged. |
| R3 | **Implemented (option 2b, owner-approved).** `mockInterviewMicrophoneEnabled(env)` in `next.config.ts`: the **global** Permissions-Policy has `microphone=(self)` only when `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` is exactly `"true"`, otherwise `microphone=()` (the committed default before D3). The video flag never changes the microphone. No route-scoped microphone rule (that would add the client-side navigation/reload caveat for voice). Camera route rule, CSP and every other header unchanged; backend `security_headers.py` unchanged (`microphone=()`). |
| Tests | `mockInterviewVideoFoundations.test.mjs`: "the video flag alone never changes the global microphone policy" (replaces the old test) plus three new tests — master off (unset, `false`, `TRUE`, `1`, empty, video-only) keeps `microphone=()`; master on (alone and with video) gives `microphone=(self)` with the camera still blocked globally; the flag changes only the Permissions-Policy header. `cameraMedia.test.mjs` (lines 685–701 only): the camera stays route-scoped, and the route's microphone equals the global microphone for the same flags. |

**Commit scope (exactly 4 files):** `frontend/Dockerfile`, `frontend/next.config.ts`,
`frontend/tests/unit/mockInterviewVideoFoundations.test.mjs`, `frontend/tests/unit/cameraMedia.test.mjs`.

### 6.2 Evidence (2026-11-25, working tree = `a336bb1` + the 4 files, Node v24.22.1, Windows)

| Check | Result |
|---|---|
| `node --test` on the two changed test files | 67 passed / 0 failed / 0 skipped |
| `npm run test:unit` | 210 passed / 0 failed / 0 skipped (207 before: 3 new tests) |
| `tsc --noEmit` (TypeScript 5.9.3) | exit 0 |
| `npm run lint` | 0 errors; 2 warnings, both in `e2e/smoke.spec.ts` (not touched by D3) |
| `next build` (16.4.6, Turbopack) × 4 in an isolated scratch copy (no `.env*`, flags set explicitly) | all exit 0, 67/67 pages |
| Compiled `routes-manifest.json`, master off / video off | 1 rule: `camera=(), microphone=(), geolocation=()` |
| Master off / video on | 1 rule: `camera=(), microphone=(), geolocation=()` |
| Master on / video off | 1 rule: `camera=(), microphone=(self), geolocation=()` |
| Master on / video on | global `camera=(), microphone=(self), geolocation=()`; both `/dashboard/mock-interview` rules `camera=(self), microphone=(self), geolocation=()` |
| Other global headers with master on | identical to the master-off build |
| `git diff --check` | clean |

**Limitations (still open):** no Docker image build and no live-response header check from a running server (the
compiled manifest was checked instead); real-microphone and browser-prompt behaviour not tested on a device (checklist
A1, gate G14); the builds downloaded the Geist font files from Google Fonts (`next/font/google`, public CDN, no
account). The policy is decided at build time, so each deployment scope (e.g. Vercel Preview vs Production) gets the
value of its own build-time environment.

## 7. Production-readiness acceptance gates

Each gate needs dated evidence (command/output, environment, who ran it) in this spec or the final checklist.
A gate marked **critical** blocks both "PRODUCTION READY" and "READY FOR CONTROLLED PILOT".

| Gate | Category | Pass criteria | Critical | Batch | Existing checklist rows |
|---|---|---|---|---|---|
| G1 | Configuration parity | Templates list every Phase 7–9 key with safe defaults (flags OFF); policy check fails if a key is missing; no secret values in any template | yes | 10.2 | — |
| G2 | Secrets & startup checks | Production refuses weak secrets, BYPASSRLS role, short worker token — re-verified on the deployed staging | yes | 10.2, 10.7 | D2 |
| G3 | Tenant isolation / RLS | PG suites green on the release commit; live privilege report at the current head: forced RLS on every tenant table or a documented, approved exception; Supabase Data API decision applied | yes | 10.4, 10.7 | B1, B2 |
| G4 | Auth, cookies, origin | §5.2 checks pass on `campus-ai-staging.vercel.app`; CSRF rejects a foreign origin; rate-limit IP is correct | yes | 10.2, 10.7 | — |
| G5 | AI cost control (₹0) | Per-user, per-institute and global limits plus kill switches on **every** AI path; quota exhaustion is refused with a clear message and an alert; no paid fallback; provider timeouts below backend timeouts; no duplicate paid retries — all proven by tests | yes | 10.3 | C3, C9 (replaced by count limits) |
| G6 | AI failure behaviour | Provider timeout/outage/quota → clear message, answers kept, non-AI features keep working; circuit breaker opens and closes | yes | 10.3, 10.8, 10.9 | C1, C2, C6 |
| G7 | CI | Backend, AI-worker, frontend unit, tsc, lint, `next build`, image build, policy check, secret scan and dependency audit run on every PR without secrets | no (pilot) / yes (production) | 10.4 | B1–B4 |
| G8 | Monitoring & email alerts | Each D5 alert type delivered by email in a drill, plus a recovery email; no secrets or student content in any event | yes | 10.5, 10.9 | C4, C5, D1 |
| G9 | Backup & recovery | Daily encrypted private backup of DB + Storage; failure alert; isolated restore drill meets RPO 24 h / RTO 8 h | yes | 10.6, 10.9 | C7 |
| G10 | Staging = current code | Frontend, backend, worker versions and Alembic head match the approved commit; flags at approved values | yes | 10.7 | — |
| G11 | Queue / background jobs | Behaviour on restart measured; job loss without Redis documented and accepted, or Redis added within free tier | no (pilot, if accepted) | 10.7, 10.9 | C8 |
| G12 | Load & capacity | D8 target met: measured p95 latency and error rate; non-AI pages stay responsive during long AI calls | yes for pilot size | 10.8 | C8 |
| G13 | Live AI | Live runs of text/voice/video interview and report (synthetic data, strict request budget) | yes | 10.8 | B5 |
| G14 | Browser, device, accessibility | Phase 5 voice rows, Phase 6 RD-01…RD-24, Safari/iOS, Android, Firefox, screen reader — each row dated | no (pilot, with listed limitations) / yes (production) | 10.10 | A1, A2 |
| G15 | Incident response | Runbooks drilled once with timings; severity levels and owner contact path written | no (pilot) / yes (production) | 10.9 | C1–C7 |
| G16 | Hosting terms | Free-plan terms permit the intended use (OQ-5) | yes | 10.2 | — |
| G17 | Documentation | Deployment doc, decision record, runbooks and `PROJECT_STATE.md` match reality | no | every batch | — |

**Verdict rules (10.10):**
- **PRODUCTION READY** — every gate passes.
- **READY FOR CONTROLLED PILOT** — every critical gate passes; the non-critical gaps are listed and accepted by the
  owner in writing.
- **NOT PRODUCTION READY** — any critical gate fails or is unverified.

## 8. Implementation plan and checklist (10.2–10.10)

Rules for every batch: separate owner approval to start; additive changes; flags default OFF unless an exception is
approved; no secrets in the repo; full relevant regression before any commit; staging by explicit file name; no
push/merge/PR/deploy without approval; the batch report covers the 10 points of the master prompt §6.

### 10.2 — Configuration templates (repo only)
- [ ] Add every Phase 7–9 backend key (`AI_MOCK_INTERVIEW_*`, `AI_CIRCUIT_*`, `METRICS_TOKEN`, `PLACEMENT_MANAGEMENT_*`,
      `STAGING_GATE_TOKEN`) and worker key (`MOCK_INTERVIEW_*`) to `deploy/compose.prod.yml` / `prod.env.example`,
      flags OFF.
- [ ] Extend `deploy/check_compose_prod.py` to fail on a missing key or an enabled flag without approval.
- [ ] Document the Vercel + Render free-tier topology next to the compose template (which one is real).
- [ ] D2 static checks (§5.2 items 1, 2, 5, 6); read free-plan terms (OQ-5).
- **Accept:** policy check passes on a synthetic env and fails on each removed key; backend + worker suites green.

### 10.3 — AI reliability and ₹0 protection
- [ ] Inventory every AI call path (interview, coding, company, mentor, mentor stream, resume, ATS, proof scoring,
      face — face stays disabled).
- [ ] Explicit provider timeout + `max_retries` in `ai-worker/llm.py`, lower than the backend timeouts.
- [ ] Retry only on connection errors for paid calls (`backend/ai_client.py`); idempotency for duplicate submits.
- [ ] Per-user, per-institute, platform-daily limits; concurrency cap; global AI kill switch + per-feature switches.
- [ ] Classify provider 429/quota errors as `quota_exhausted` → refuse + alert.
- [ ] Decide the backend process/threadpool sizing (audit B6) within the free memory limit.
- **Accept:** tests for each item; flag-off paths unchanged except approved exceptions (OQ-2); full backend, worker
  and PG suites green.

### 10.4 — CI
- [ ] Add AI-worker tests, frontend `test:unit` + `next build`, image builds (no push), compose policy check,
      dependency audit (report-only first), PG/RLS suite in a service container if free CI minutes allow.
- **Accept:** green run on a PR branch (pushing a branch needs approval); actions pinned by SHA; no secrets.

### 10.5 — Monitoring and email alerts (after OQ-4)
- [ ] Frontend error tracking with privacy filters; `campus.alert` → email path; uptime + heartbeat monitors.
- **Accept:** a synthetic alert of each D5 type arrives by email; recovery email arrives; privacy sentinel scan clean.

### 10.6 — Backup and recovery (after D7)
- [ ] Encrypted daily DB + Storage (+ Qdrant or rebuild) backup; pin `verify_restore.sh` image; failure alert;
      restore runbook for a fresh free project.
- **Accept:** isolated restore drill with checks and timing ≤ 8 h; no backup in a public location.

### 10.7 — Staging current-code verification (needs deploy approval)
- [ ] Versions, Alembic head, RLS/privilege report, Data API, cookies/origin/IP, flags, startup checks.
- **Accept:** G2, G3, G4, G10 evidence recorded; no live service changed beyond the approved deploy.

### 10.8 — Live AI and load tests (after D8, strict budget)
- [ ] Verify free availability and no billing method; synthetic users; normal / timeout / outage / quota / retry /
      concurrency / restart cases.
- **Accept:** measured latency and error rates; zero billing events; stop immediately on any paid signal.

### 10.9 — Operational drills (isolated or approved staging)
- [ ] Provider outage, quota exhaustion, Redis down, worker/backend restart, restore, Storage recovery, alert
      delivery, kill switch.
- **Accept:** expected vs actual, recovery time, data loss and evidence for each drill.

### 10.10 — Final acceptance
- [ ] Real-device matrix, full regression, gate review, verdict (§7), owner sign-off
      (`phase-10-final-acceptance-checklist.md` §E).

**Out of scope for Phase 10:** Phase 8 code execution (G2 not passed), multimodal AI, new product features, and —
unless OQ-1 decides otherwise — the platform-operator role and enterprise integrations.

## 9. Batch 10.1 record

- Changed (documentation only): this file (new), `phase-10-readiness-audit.md` (new, archived audit + corrections),
  `phase-10-final-acceptance-checklist.md` (status line + section F), `PROJECT_STATE.md` (header, Phase 9 commit,
  Phase 10 row), `IMPLEMENTATION_PLAN.md` §6a (Phase 10 status cell).
- Not changed: application code, tests, configuration, templates, CI, infrastructure, hosted services.
- D3 (separate from the 10.1 docs): done 2026-11-25, commit `12029d6` (4 files, §6.1); the D3 status was then
  recorded in this file (§2, §6) and in `PROJECT_STATE.md`.
- Awaiting owner: review of this record; D7, D8, D9; OQ-1…OQ-6; approval to commit the 10.1 docs; approval to start
  10.2. ~~the D3 commit step~~ (done, `12029d6`).
- **Update 2026-11-25:** the owner instructed execution of the whole of Phase 10 and decided OQ-2, OQ-4, D7 (later)
  and D8 (§3). The 10.1 documentation was completed in the same session (decision record, stale D3/Phase 9 wording
  corrected, D7–D9 and OQ-1/3/5/6 kept open); 10.2–10.10 results are in §10, acceptance in §11. Still awaiting the
  owner: D7, D9, OQ-1, OQ-3, OQ-5, OQ-6, and approval to commit.

## 10. Execution record, Batches 10.2–10.10 (2026-11-25, local, uncommitted)

Environment for every result below: Windows 11 workstation, branch `feature/ai-mock-interview` at `12029d6` plus the
uncommitted Phase 10 working tree; Python 3.13 (backend/worker venvs), Node v24.22.1; disposable Docker containers
(`postgres:16@sha256:63e83b5a…` = 16.16, `redis:7.4.12-alpine3.22@sha256:7f7d30b7…`, `qdrant/qdrant:v1.18.3@sha256:0bd98fa7…`)
on `127.0.0.1` only, tmpfs data, removed afterwards (Docker volume count 31 before and after). Synthetic data only.
No hosted service, staging, provider or `D:\Campus_Ai` was touched. `docker-compose.override.yml` was never opened.
Pulled public images (no account): `qdrant/qdrant:v1.18.3`, `redis:7.4.12-alpine3.22`.

### 10.2 Configuration (gate G1) — DONE (repo level)

- `deploy/prod.env.example`, `deploy/compose.prod.yml`: every Phase 7–9 key and the new Phase 10 keys, flags default
  **off**, AI governance **on and mandatory** (compose refuses to resolve without both platform caps and the worker
  LLM budget), `AI_PRICE_TABLE` empty (D4), `BUILD_COMMIT`, worker route flags, `LLM_TIMEOUT_SECONDS`/`LLM_MAX_RETRIES`.
- `deploy/check_compose_prod.py`: refactored into `check(cfg, approved)`; new rules: every backend/worker/ai-worker key
  present, product flags off unless `--approve FLAG`, `AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS` never true,
  governance true, caps positive, price table empty, LLM timeout 1–45 s and retries 0–1, provider groq|gemini.
- `deploy/make_synthetic_env.py`: synthetic env for the policy check (CI + local).
- `backend/tests/test_deploy_policy.py` (8 tests): every backend `Settings` field is in the compose template and in the
  policy list; every template variable is in the manifest; manifest flags off, governance on, no secret values;
  each missing key fails; unapproved flag fails; governance/worker-budget violations fail.
- Evidence: `docker compose … config | check_compose_prod.py` on the synthetic env → `OK: 0 failed`; with
  `AI_MOCK_INTERVIEW_ENABLED=true` → 2 failures, passes with `--approve`; with an empty token cap compose refuses
  (`required variable AI_PLATFORM_DAILY_TOKEN_MAX is missing a value`).
- Not done (owner/live): §5.2 live checks (10.7), free-plan terms (OQ-5, G16).

### 10.3 AI governance (gate G5, G6) — DONE in code and tests; live caps need owner values

- New `backend/app/services/ai_governance.py`; migration `f3a5c7e9b1d2` (additive, non-tenant table
  `ai_platform_usage_daily`, FORCE RLS + `ai_platform_counter` policy behind a transaction GUC); governed calls in
  `backend/ai_client.py` (mentor chat/stream, resume, ATS, proof scoring) and `mock_interview_ai._post` (all interview,
  coding and JD steps); `AIRefused` → 429/503 envelope with `error.type = "ai_unavailable"`, reason and `Retry-After`
  (`main.py`); precheck before anything is written in the mentor/resume/ATS routes; `Idempotency-Key` replay;
  circuit breaker extended to the governed steps; metrics `ai_governance_refusals_total`, `ai_feature_calls_total`.
- Worker: explicit `LLM_TIMEOUT_SECONDS`/`LLM_MAX_RETRIES` passed to Groq (`timeout`, `max_retries`) and Gemini
  (`request_timeout`, `retries`; the SDK default was 6 retries); production refuses to start without them;
  `provider_errors.is_quota_error` → worker 429 (mentor/resume/ATS/scoring), SSE `code: quota_exhausted`,
  interview `usage.quota_exhausted`; on quota the ATS and interview JSON fallbacks no longer spend a second call.
- Reused: AI usage ledger (`ai_usage`), circuit breaker, `ai_alerts`, `_worker_request`, tenant GUC, error envelope.
  No second provider factory, client, queue or quota system was added.
- **Defect found by the 10.9 outage drill and fixed:** circuit-breaker refusals consumed a platform reservation
  although no provider call was made; now refunded (`refund_platform_request`; test
  `test_circuit_refusal_does_not_consume_platform_budget`; re-verified live: 3 refusals → +0).
- **Security defect found in self-review and fixed:** the governed mentor stream detected provider quota exhaustion
  by a raw substring match, so a student could make the model print `quota_exhausted` and trigger a platform-wide
  15-minute AI cooldown (denial of service). It now parses SSE frames and acts only on a worker `error` event with
  `code: quota_exhausted` (test `test_answer_text_cannot_trigger_quota_cooldown`).
- Tests: `backend/tests/test_ai_governance.py` 29 (flag-off legacy behaviour unchanged, kill switch, per-feature
  switch, ledger + counter, no repeat on 5xx/502/read timeout, one retry on connect error, quota cooldown, user /
  institute / platform request caps, platform token cap, fail closed, busy, user-busy, idempotency replay and
  release, stream precheck/ledger/quota, resume/ATS, proof scoring, interview kill switch + platform cap + quota
  signal, production gate, content-free messages); `ai-worker/tests/test_provider_budget.py` 9;
  `backend/tests_pg_api/test_ai_governance_pg.py` 4 on Postgres as `app_user` (FORCE RLS on the counter; invisible
  and unwritable without the GUC; **20 parallel admissions against a cap of 7 reserve exactly 7**; institute cap
  counts only the own institute).
- Production gate verified locally: `ENVIRONMENT=production` with `AI_GOVERNANCE_ENABLED=false` → "Unsafe AI
  configuration: AI_GOVERNANCE_ENABLED must be true in production … Refusing to start"; with an empty token cap →
  refused likewise.
- Remaining: the owner must read the provider's current free limits, confirm no billing method, and set the caps
  (runbook 12). Mentor streaming reports no tokens (counts as requests only). Per-process state (cooldown,
  concurrency, idempotency) documented. The breaker's alert text still says "interview step" for mentor steps
  (cosmetic).

### 10.4 CI (gate G7) — WORKFLOW WRITTEN, NOT RUN ON GITHUB

`.github/workflows/ci.yml` (existing jobs unchanged, read-only token, no secrets, actions pinned by SHA, service images
pinned by digest): frontend `test:unit` + `next build`; new jobs `ai-worker` (full suite + real Qdrant service),
`backend-postgres` (fresh Postgres 16 → `scripts/ci/pg_suite_setup.sh` → migration round trip → RLS suites →
`tests_pg_api` incl. Redis/RQ), `compose-policy`, `images` (build, never push), `dependency-audit` (report-only:
pip-audit 2.9.0, `npm audit --omit=dev --audit-level=high`); backend job also runs the backup-tooling tests.
`scripts/ci/pg_suite_setup.sh` applies the canonical `app_role.template.sql` (only its interactive `\password` step is
replaced) and verifies the role (fails closed). Locally: YAML parses (10 jobs); the same steps ran green (below).
**Blocked:** a GitHub run needs pushing a branch (owner approval). Browser e2e is not in CI (needs a full seeded stack).

Dependency scan, run locally 2026-11-25 (report-only; **not fixed** — lockfile upgrades need their own approved,
regression-tested change): backend **19 known vulnerabilities in 8 packages** (urllib3, dnspython, ecdsa, pyasn1,
python-jose, cryptography, certifi, anyio; `ecdsa` and `python-jose` have **no fixed version** — python-jose is the JWT
library); ai-worker **84 in 10 packages** (urllib3, langgraph-sdk, h2, opentelemetry-api, pillow, pyasn1,
cryptography, filelock, certifi, anyio); frontend `npm audit --omit=dev --audit-level=high`: **0**.

### 10.5 Monitoring and e-mail alerts (gate G8) — APP SIDE DONE; e-mail delivery BLOCKED on owner accounts

- `/health/ready` now also reports `queue` (`in_process` without Redis, depth/failed with RQ) and `ai`
  (`degraded` + reason codes when new AI calls are refused); neither ever fails readiness (additive keys).
- Frontend error tracking without a new dependency: `POST /client-errors` (backend flag
  `CLIENT_ERROR_REPORTING_ENABLED`, default off → 404) + `ClientErrorReporter` / error boundaries (build flag
  `NEXT_PUBLIC_CLIENT_ERROR_REPORTING_ENABLED`, default off); only kind, error class name, normalised route, release,
  digest; messages/stacks rejected (422 on unknown fields); rate-limited 30/min; `frontend_errors_total`.
- New alerts `ai_quota_exhausted`, `ai_platform_quota_reached`, `ai_tenant_quota_reached`,
  `ai_governance_unavailable`, `backup_failed`; runbooks 12–14 (`monitoring-email-alerts.md` lists the monitors to
  create). Tests: `backend/tests/test_monitoring_phase10.py` 7 (every D5 alert type emitted in isolation and
  content-free; client-error privacy; readiness fields; limiter fallback) + `frontend/tests/unit/clientErrors.test.mjs` 5.
- Existing and reused: correlation ids, JSON logs, Prometheus `/metrics` with `METRICS_TOKEN`, Sentry with privacy
  filters. **Owner action:** create the uptime, heartbeat and Sentry monitors and enter the e-mail address.

### 10.6 Backup and recovery (gate G9) — TOOLING DONE AND DRILLED LOCALLY; G9 BLOCKED on D7

- `scripts/backup/backup_all.sh` (Postgres dump + Storage files + manifest with exact row counts and checksums → one
  tar **encrypted to a GPG public key**, `.sha256`, retention 14 daily + 8 weekly, heartbeat success/fail,
  `backup_failed` line, fail closed, partial file removed), `storage_backup.py` (read-only backup, verify, restore with
  `--apply` and never overwriting, path-traversal guard), `retention.py`, `restore_drill.sh` (isolated restore with
  checks), `verify_restore.sh` pinned image + random password; `GPG_DOCKER_IMAGE` mode because `gpg-agent` cannot run
  in this Windows shell. Tests `scripts/backup/test_backup_tools.py` 9.
- **Drill 2026-11-25 (local):** source = disposable Postgres at head `f3a5c7e9b1d2` with synthetic data (3
  institutes, 60 users, 25 ledger rows), fake loopback Storage with 15 synthetic files (76 500 bytes), throwaway drill
  key. Backup: exit 0 in **5 s**, bundle 40 340 bytes, OpenPGP public-key packet, no plaintext markers. Restore drill:
  **PASS** — checksum, decrypt, inner checksums, Storage verify 15/15, restore into a fresh cluster, runtime role from
  the template, Alembic head equal, **exact row counts equal for all 55 tables (89 rows), 0 mismatches**, as
  `app_user`: 0 rows without a tenant and 20 own-institute users with the GUC; **elapsed 11 s vs RTO 28 800 s**;
  container removed. Negative checks: a 1-bit-tampered bundle is rejected at step 1; three real failures during
  bring-up (script path, manifest parse, key path) each produced `backup_failed`, exit ≠ 0 and no bundle (the
  key-path failure left an empty `.partial` file; the script now removes it on failure).
- Not done: daily schedule, off-site destination and key custody (**D7**); a restore from a real Supabase backup into
  a new project. RPO 24 h is therefore **not** yet met in practice.

### 10.7 Staging verification (gates G2, G3, G4, G10) — PREPARED; live run BLOCKED (needs authorization)

- `scripts/ops/staging_verify.py` (read-only: health, readiness, version + build commit, production surface,
  staging gate, security headers, HSTS, CORS, CSRF). `BUILD_COMMIT` (or Render's `RENDER_GIT_COMMIT`) is shown by
  `/version` only when set (default response unchanged).
- Local production-mode run (backend `ENVIRONMENT=production`, NOBYPASSRLS `app_user`, staging gate on, governance
  on): **17/17 PASS**; login cookies `HttpOnly; Secure; SameSite=None`, host-only, refresh cookie path `/v1/auth`.
- Postgres at the new head as `app_user`: RLS suites **34 passed / 0 failed** (104 s); `tests_pg_api` **245 passed /
  0 failed / 0 skipped** (20 min 2 s, incl. both Redis queue tests); governance + ops PG tests re-run after the refund
  fix: 10 passed. Migration chain from empty: 62 revisions; newest revision down/up round trip OK.
- **Owner authorization needed** for: deploying the current code to staging, running the migration there, then
  `staging_verify.py` against `campus-ai-staging.vercel.app`'s backend, the live RLS/privilege report, the Supabase
  Data API decision and measuring `TRUSTED_PROXY_HOPS`.

### 10.8 Load and live AI (gates G12, G13) — LOCAL LOAD MEASURED; live AI BLOCKED

Harness `scripts/load/ai_load_test.py` (loopback only unless `--allow-remote`), stub worker
`scripts/load/stub_ai_worker.py` (no provider, no cost). Local production-mode backend, one process, Postgres as
`app_user`, D8 pilot institute with 100 synthetic students (bearer tokens minted with the local throwaway key).

| Scenario | AI requests | AI result | AI p50 / p95 / max | Other requests | Other p50 / p95 / max | Errors |
|---|---|---|---|---|---|---|
| A: D8 pilot, 3 AI users (2 s LLM) + 30 users, 120 s | 130 | 130 × 200 | 2 792 / 3 160 / 3 366 ms | 18 407 (150 rps) | 191 / 273 / 482 ms | 0 |
| B: overload, 10 AI users vs cap 3 + 30 users, 60 s | 84 | 84 × 200 (queued ≤ 10 s) | 7 637 / 8 711 / 9 562 ms | 8 651 | 197 / 349 / 676 ms | 0 |
| C: long AI calls (20 s LLM), 6 AI users + 30 users, 90 s | 35 | 15 × 200, 20 × 503 `busy` | 10 933 / 29 862 / 30 999 ms | 13 752 | 192 / 297 / 593 ms | 0 |

Reading: the concurrency cap protects the provider and the backend; ordinary pages stay responsive (p95 < 350 ms)
while AI calls are long or queued. Limits: one workstation, stub LLM, no network latency, not the Render free tier
(0.1 CPU, cold starts). **Blocked:** live AI runs (G13) need OQ-3 (provider data terms), confirmation that the provider
account is free with no billing method, and an owner-approved request budget; load on the free hosting needs
deployment authorization.

### 10.9 Operational drills (gates G6, G11, G15) — LOCAL DRILLS DONE

| Drill | Expected | Actual (2026-11-25, local) | Recovery | Data loss |
|---|---|---|---|---|
| AI provider outage (stub 502) | fast failures, breaker opens, non-AI fine | 5 × 502 then 503 `circuit_open`; 0 worker calls while open; non-AI 200/200; alerts `ai_circuit_open`/`ai_circuit_closed` | 10.2 s after the provider recovered (open period 10 s) | none |
| Provider quota exhausted (stub 429) | refuse new calls, alert, recover | 3 requests → 1 provider call, all 503 `provider_quota`; readiness `ai: degraded`; non-AI 200; alert `ai_quota_exhausted` | 20.8 s (cooldown 20 s) | none |
| Kill switch (`AI_KILL_SWITCH=true`) | AI refused, rest works | 503 `kill_switch` with the fixed message; non-AI 200; readiness `degraded` | restart | none |
| AI worker down + restart | AI fails fast, rest works | 502 in ~4.7 s (one connect retry); non-AI 200 | first call after restart succeeded | none |
| Backend hard stop (+ restart) | acknowledged writes kept | 5/5 acknowledged answers persisted; an AI call in flight at the stop: client saw a dropped connection, its question was stored without an answer | healthy 19–22 s after the stop | 0 acknowledged writes lost; 1 unanswered question |
| Redis down (Redis-enabled backend) | rest keeps working | **Defect found:** login/register/refresh returned **500** (limiter storage). **Fixed:** in-memory limiter fallback; re-run: login 200, limit still enforced (429s), queue recovers when Redis returns | immediate | none |
| Backup restore / Storage recovery | §10.6 | PASS (11 s; 15/15 objects) | — | none |
| E-mail alert delivery | e-mail received | **BLOCKED** — no monitor accounts yet (owner) | — | — |

Also found: a backend that **starts** while Redis is down keeps its read-cache disabled until restarted (readiness
`redis: disabled`) — harmless, documented, not fixed. Not drilled: staging/hosted services (authorization).

### 10.9a Review fixes (2026-11-25, after the read-only change review)

| Finding | Outcome |
|---|---|
| #1 Interview quota missed when a step fails outright | **Fixed.** Worker `provider_errors.step_failure`: a failed interview, coding-hint, coding-review or JD-skills step answers **429** (`AI provider quota exhausted`) only when `llm_io` flagged quota from the provider exception class/status; otherwise its usual 502. Backend classifies a worker 429 as `provider_error` / `ProviderQuotaExhausted` and the existing shared pause starts. Answer, question or error-body text is never inspected (tests). Exception chaining for Sentry kept. |
| #2 Idempotency key not bound to the body | **Fixed.** `request_fingerprint` = SHA-256 of the canonical validated payload (sorted-key JSON); only the hash is stored. Same key + same body → replay; different body → 409; keys stay per user and feature. |
| #4 Browser errors never reached Sentry | **Fixed.** One explicit, content-free Sentry event per (kind, error class, route) per `AI_ALERT_THROTTLE_SECONDS`, max 20 per window, only when Sentry is configured; passes the shared `scrub_event`; log line and Prometheus counter unchanged. |
| #7 Breaker alert said "interview step" for mentor steps | **Fixed** ("AI step"). |
| #3 Unanswered mentor question after a breaker refusal | Open (recommendation in the review report; behaviour unchanged). |
| #5 Readiness shows AI reason codes | Open, accepted: codes only (`kill_switch`, `provider_quota`); on staging `/health/ready` is behind the staging gate. |
| #6 Per-process state | Open, documented single-process pilot limitation (runbook 12). |
| #8 Cache stays off after a Redis-down start | Open, documented. |
| #9 CI never run | Open (no push without approval). |
| New: `test_worker_security.py` opened the developer's `ai-worker/qdrant_data` | **Fixed** (test now uses a throwaway `QDRANT_LOCAL_PATH`). That embedded open rewrote `qdrant_data/meta.json` twice (14:33 and 17:22 on 2026-11-25; `storage.sqlite` untouched). |

### 10.10 Full regression on the final working tree

| Suite | Result |
|---|---|
| Backend SQLite (full) | Final run alone on the final tree (3 615 s): **808 passed, 34 skipped (opt-in Postgres), 10 failed**. All 10 were subprocess tests that boot the app with `ENVIRONMENT=production` and now hit the new mandatory governance gate (OQ-2) — the gate working as designed. Fixed in test setup only (synthetic `AI_GOVERNANCE_ENABLED`/caps added to the env builders in `test_config_hardening.py` and `test_metrics_token.py`) and re-run: those 4 files **38/38 passed**. A further complete clean run was not repeated (≈1 h on this machine). |
| Backend Postgres: RLS suites / `tests_pg_api` | 34 / 0 failed; 245 / 0 failed / 0 skipped (governance + ops re-run after the last fix: 10 passed) |
| AI worker (full) | 131 passed + the 5 `test_face_qdrant_server.py` tests passed against a real Qdrant = 136/136 (without a Qdrant server those 5 fail on Windows: embedded-mode file lock; they are server-gated by design) |
| Backup tooling | 9 passed |
| Frontend | `tsc` exit 0; lint 0 errors (2 pre-existing warnings in `e2e/smoke.spec.ts`); unit 215/215; `next build` exit 0 (default flags and with the error-reporting flag) |
| Compose policy | OK, 0 failed (synthetic env) |
| Real devices, screen readers, browser e2e | **not run** (A1, A2, G14) |

Process note: one earlier full backend run was **voided** — a targeted pytest was started while it was running on the
shared SQLite file (CLAUDE.md forbids this) and the code changed afterwards; it was stopped and the full suite was
re-run alone on the final tree (the result above).

## 11. Final acceptance (Batch 10.10)

| Gate | Critical | Status | Evidence / what is missing |
|---|---|---|---|
| G1 Configuration parity | yes | **PASS (repo)** | §10.2 |
| G2 Secrets & startup checks | yes | PARTIAL | Local production gates verified (§10.3, §10.7); not re-verified on deployed staging |
| G3 Tenant isolation / RLS | yes | PARTIAL | PG suites green at head `f3a5c7e9b1d2`; live privilege report + Supabase Data API decision not done |
| G4 Auth, cookies, origin | yes | PARTIAL | Local 17/17 + cookie flags; not checked on `campus-ai-staging.vercel.app`; `TRUSTED_PROXY_HOPS` not measured |
| G5 AI cost control (Rs 0) | yes | PARTIAL | Every AI path governed and tested; real caps from verified free limits + "no billing method" confirmation outstanding |
| G6 AI failure behaviour | yes | PARTIAL | Local drills pass; no live-provider run |
| G7 CI | pilot: no | NOT RUN | Workflow written; needs a GitHub run (push approval); dependency findings open |
| G8 Monitoring & e-mail alerts | yes | BLOCKED | Alert generation tested; monitors/e-mail not configured (owner) |
| G9 Backup & recovery | yes | BLOCKED | Local drill PASS; D7, schedule and real-provider restore outstanding |
| G10 Staging = current code | yes | BLOCKED | Tooling ready; deployment authorization needed |
| G11 Queue | pilot: no | PARTIAL | Behaviour measured locally; no-Redis job loss needs owner acceptance |
| G12 Load & capacity | yes (pilot size) | PARTIAL | D8 met locally with a stub LLM; not measured on free hosting |
| G13 Live AI | yes | BLOCKED | OQ-3, billing confirmation, request budget |
| G14 Browser / device / a11y | pilot: no | NOT RUN | Real-device matrix pending |
| G15 Incident response | pilot: no | PARTIAL | Drills timed locally; owner contact path and severity levels not written |
| G16 Hosting terms | yes | BLOCKED | OQ-5: owner must read the current free-plan terms |
| G17 Documentation | no | PASS | This record, runbooks 12–14, PROJECT_STATE |

**Verdict: NOT PRODUCTION READY.** Not "ready for controlled pilot" either: critical gates G2–G6, G8–G10, G12, G13 and
G16 lack staging, provider or owner evidence. Nothing in this record claims otherwise. The platform-operator role and
enterprise integrations (original Phase 10 scope, OQ-1) are **not implemented** and remain open.

### 11.1 Owner actions to unblock (in order)

1. Review the working tree and approve commits (suggested split: 10.3 governance; 10.2/10.4 config + CI; 10.5
   monitoring; 10.6 backup tooling; 10.7–10.9 scripts; docs).
2. Approve pushing a branch for one CI run (G7); review dependency findings and approve an upgrade batch.
3. Decide D7 (destination, retention, key custody) and create the offline GPG key; schedule the daily backup (G9).
4. Create the monitors and enter the alert e-mail (runbook 14, G8); decide whether monitors may send the staging
   gate header.
5. Read the provider's free limits, confirm no billing method, set `AI_PLATFORM_DAILY_*`/`AI_*_DAILY_REQUEST_MAX`
   (G5); decide OQ-3; approve a small live-AI request budget (G13).
6. Read Vercel/Render/Supabase/Qdrant/provider free-plan terms (OQ-5, G16); decide OQ-6, D9, OQ-1.
7. **Before** any redeploy of this code: add `AI_GOVERNANCE_ENABLED=true`, `AI_PLATFORM_DAILY_REQUEST_MAX`,
   `AI_PLATFORM_DAILY_TOKEN_MAX` (backend) and `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` (worker) to the staging env —
   otherwise both services refuse to start (by design, OQ-2). Then authorize deploying this code + migration
   `f3a5c7e9b1d2` to staging, then run `staging_verify.py`, the privilege
   report, `TRUSTED_PROXY_HOPS` measurement and the staging drills (G2–G4, G10–G12).
8. Real-device and screen-reader matrix (G14).
