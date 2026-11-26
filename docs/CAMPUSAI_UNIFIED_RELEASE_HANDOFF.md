# CampusAI — Unified Product Release Handoff (source of truth)

**Last verified:** 2026-11-25 (repository state re-inspected while writing this file; no tests were run for it).
**Updated 2026-11-25 (later, documentation only):** backend code freeze; the **full backend suite passed once on the
frozen release candidate — 835 passed, 0 failed, 34 skipped, exit 0** (§4); B-1 resolved (§6.1); local integration
smoke recorded as **PARTIAL** (§4); root `README.md` rewritten from verified facts (roadmap step 6, §8).
**Purpose:** let a fresh session continue the release without reconstructing history. Where this file and older
documents disagree, this file reflects the later, re-inspected state; the detailed evidence lives in
`docs/mock-interview/specs/phase-10-production-readiness.md` (§10–§11, §10.9a).

**Current verdict: NOT PRODUCTION READY.** Not ready for a controlled pilot either (critical gates lack staging,
provider and owner evidence — §7).

---

## 1. Repository identity and Git state (verified 2026-11-25)

| Item | Value |
|---|---|
| Checkout | A dedicated Git **worktree** of this repository (repository-relative paths are used throughout) |
| Branch / HEAD | `feature/ai-mock-interview` / `12029d688505f134427c7333f683672913110dc8` |
| Other worktree | The original local checkout, on `feature/student-approval` @ `7f99e0b` — **never modify it**; `7f99e0b` is already an ancestor of this branch |
| Remote | `origin` = GitHub `Vishal-AI-ML-Dev/campus-ai`; `origin/main` = local `main` = `2d0d9ac` |
| Relationship | this branch is **26 commits ahead, 0 behind** `origin/main`; **never pushed** (no `origin/feature/ai-mock-interview`) |
| Staged | 0 |
| Modified (tracked) | 42 when first written; **47** after the B-8/mentor fixes, the README update and the `.gitignore` rule (re-counted 2026-11-25) |
| Untracked | 32 entries when first written; **34** entries now (re-counted 2026-11-25) |
| Code freeze | Application code and tests frozen 2026-11-25 at this HEAD + the working tree below; later changes were documentation only |
| Deleted | 0 |
| Alembic | 62 revision files, **single head `f3a5c7e9b1d2`** (uncommitted, Phase 10.3); last committed head `e1a3c5b7d9f4` (Phase 9.3) |

**Vercel deploys every push to `main`.** Nothing may be pushed to `main` without explicit owner approval.

### 1.1 Recent commits on this branch (all local, not pushed)

| Commit | Date | Content |
|---|---|---|
| `12029d6` | 2026-11-25 | Phase 5 D3 — interview build flag, flag-gated `microphone=(self)` (4 files) |
| `a336bb1` | 2026-11-25 | Phase 9 closure and hardening (14 files) |
| `9217d8d` | | Phase 9.2–9.5 placement hub, campaigns, analytics, acceptance |
| `2ccc8b9` | | Phase 9.1 placement management core |
| `7d4147c` | | Phase 8.5 hidden test pack + secure runner foundation (`code-runner/`) |
| `1d620e6`, `c62b067` | | Phase 8 Batch 2 / Batch 1 (coding review, company profiles, coding foundations) |
| `869d42b`, `863a8aa`, `ee48610` | | Phase 7 operations |
| `f69ee1d` … `1ad5628` | | Phase 6 video interview |
| `34fd3a9`, `18d8db0`, `926e59c`, `0fadaa8`, `f17d2bd` (+ docs commits) | | Phases 1–5 Mock Interview |
| `7f99e0b` | | Student self-registration requires admin approval (shared with the original local checkout) |

**Phase 10 work is entirely uncommitted** (see §1.2).

### 1.2 Uncommitted working tree (exact)

**Modified (42):**
`.github/workflows/ci.yml`, `CLAUDE.md`,
`ai-worker/config.py`, `ai-worker/llm.py`, `ai-worker/mentor.py`, `ai-worker/resume.py`, `ai-worker/scoring.py`,
`ai-worker/interview/llm_io.py`, `ai-worker/interview/router.py`, `ai-worker/interview/coding/router.py`,
`ai-worker/interview/company.py`, `ai-worker/interview/schemas.py`, `ai-worker/interview/usage.py`,
`ai-worker/tests/test_worker_metrics_token.py`, `ai-worker/tests/test_worker_security.py`,
`backend/ai_client.py`, `backend/main.py`, `backend/app/core/config.py`, `backend/app/core/rate_limit.py`,
`backend/app/models/__init__.py`, `backend/app/models/ai_operations.py`, `backend/app/observability/ai_metrics.py`,
`backend/app/observability/health.py`, `backend/app/routers/mentor.py`, `backend/app/routers/resume.py`,
`backend/app/services/ai_circuit.py`, `backend/app/services/mock_interview_ai.py`,
`backend/tests/test_config_hardening.py`, `backend/tests/test_metrics_token.py`,
`deploy/check_compose_prod.py`, `deploy/compose.prod.yml`, `deploy/prod.env.example`,
`docs/mock-interview/IMPLEMENTATION_PLAN.md`, `docs/mock-interview/PROJECT_STATE.md`,
`docs/mock-interview/runbooks/README.md`, `docs/mock-interview/runbooks/alerts.md`,
`docs/mock-interview/specs/phase-10-final-acceptance-checklist.md`,
`frontend/Dockerfile`, `frontend/src/app/layout.tsx`, `frontend/src/app/error.tsx`,
`frontend/src/app/dashboard/error.tsx`, `scripts/backup/verify_restore.sh`.
Added since (B-8 `FACE_ENABLED` fix and mentor breaker precheck, then the documentation update): `ai-worker/face.py`,
`backend/tests/test_ai_circuit.py`, `README.md`, `.gitignore` (C10: ignore `ai-worker/qdrant_data/`); untracked
`ai-worker/tests/test_face_config.py`.

**Untracked — Phase 10 (to commit after approval):**
`ai-worker/provider_errors.py`, `ai-worker/tests/test_provider_budget.py`,
`backend/alembic/versions/f3a5c7e9b1d2_ai_platform_usage_daily.py`, `backend/app/routers/client_errors.py`,
`backend/app/services/ai_governance.py`, `backend/tests/test_ai_governance.py`, `backend/tests/test_deploy_policy.py`,
`backend/tests/test_monitoring_phase10.py`, `backend/tests_pg_api/test_ai_governance_pg.py`,
`deploy/make_synthetic_env.py`, `docs/mock-interview/runbooks/ai-governance.md`,
`docs/mock-interview/runbooks/backup-restore.md`, `docs/mock-interview/runbooks/monitoring-email-alerts.md`,
`docs/mock-interview/specs/phase-10-production-readiness.md`, `docs/mock-interview/specs/phase-10-readiness-audit.md`,
`frontend/src/components/observability/` (`ClientErrorReporter.tsx`, `clientErrors.ts`),
`frontend/tests/unit/clientErrors.test.mjs`, `scripts/backup/backup_all.sh`, `scripts/backup/restore_drill.sh`,
`scripts/backup/retention.py`, `scripts/backup/storage_backup.py`, `scripts/backup/test_backup_tools.py`,
`scripts/ci/pg_suite_setup.sh`, `scripts/load/ai_load_test.py`, `scripts/load/stub_ai_worker.py`,
`scripts/ops/staging_verify.py`, and this file `docs/CAMPUSAI_UNIFIED_RELEASE_HANDOFF.md`.

**Untracked — owner/local, NEVER commit, never modify:**
`.claude/`, `frontend/AGENTS.md`, `docker-compose.override.yml` (never open), `ai-worker/qdrant_data/` (never touch,
see §6.4), `docs/mock-interview/PHASE_7_MASTER_HANDOFF.md`, `PHASE_8_MASTER_HANDOFF.md`, `PHASE_9_MASTER_HANDOFF.md`.

**Changed by the findings-fix task (after the Phase 10 run):** `backend/app/services/ai_governance.py`,
`backend/app/routers/{mentor,resume,client_errors}.py`, `backend/app/services/{mock_interview_ai,ai_circuit}.py`,
`ai-worker/provider_errors.py`, `ai-worker/interview/{router,company}.py`, `ai-worker/interview/coding/router.py`,
`backend/tests/{test_ai_governance,test_monitoring_phase10}.py`, `ai-worker/tests/{test_provider_budget,test_worker_security}.py`,
spec §10.9a.

---

## 2. Unified architecture (from source)

CampusAI is **one codebase and one product**: a FastAPI backend, a separate FastAPI AI worker, and a Next.js 16
frontend, sharing one Postgres database, one auth system and one tenant model. AI Mock Interview was built **into**
this platform (same users, roles, tenants, ledger, worker), not as a separate service.

| Layer | Implementation | Notes |
|---|---|---|
| Frontend | `frontend/` Next.js 16 (read `frontend/AGENTS.md`); pages `src/app/dashboard/<feature>/page.tsx`; same-origin `/v1` proxy adds the staging-gate header (`src/proxy.ts`) | Feature build flags `NEXT_PUBLIC_*`, all default off |
| Backend | `backend/` FastAPI, SQLAlchemy 2, Alembic; routers in `backend/app/routers/`, mounted at `/v1` and legacy unversioned | 37 router modules (`backend/app/routers/*.py` excluding `__init__.py`; re-counted 2026-11-25 — an earlier draft said 39) |
| AI worker | `ai-worker/` FastAPI + LangChain/LangGraph; one provider factory `ai-worker/llm.py` (Groq default, Gemini optional; no paid fallback); Qdrant for RAG (`career_kb`) and face embeddings | Stateless; only the backend calls it (`X-Worker-Token`) via `backend/ai_client.py` |
| Database | Postgres with **RLS**: tenant tables use `tenant_id` + `ENABLE/FORCE RLS` + `tenant_isolation` policy on GUC `app.current_tenant_id`; runtime role `app_user` NOBYPASSRLS (production refuses a bypassing role) | `apply_tenant_guc` dependency on tenant routers |
| Auth | JWT access + rotating refresh tokens, httpOnly cookies (production: Secure, SameSite=None, host-only) or Bearer; CSRF origin check on all unsafe methods; staging gate token | Roles: `student`, `teacher`, `tpo`, `admin`, `recruiter` (`backend/app/models/enums.py`) |
| Jobs | `backend/jobs.py`: RQ on Redis when `REDIS_URL` is set (`backend/worker.py`), else in-process `BackgroundTasks` (lost on restart) | Staging has no Redis |
| Storage | Supabase Storage, signed URLs (`backend/storage.py`, `uploads.py`) | |
| Observability | JSON logs with request/invocation ids, Prometheus `/metrics` (token-protected in production), Sentry with privacy scrubbing, `campus.alert` alerts, `/health` + `/health/ready` | `observability.py`/`errors.py`/`metrics.py` are CI-synced between backend and worker — do not edit one copy only |

### 2.1 Existing platform vs Mock Interview vs shared

| Area | Origin | Status in code |
|---|---|---|
| Academics, attendance, assignments, materials, timetable, calendar, announcements, doubts, ECA, leave/OD, internships, notifications, people, institute, tenants, audit, analytics | Existing CampusAI | Active |
| Skills/projects with AI proof scoring, AI mentor (RAG, streaming), AI resume builder + ATS | Existing CampusAI (AI) | Active; now governed by Phase 10.3 |
| Face attendance (`face`, `face-attendance`, `face-enroll`) | Existing CampusAI | Worker `FACE_ENABLED` defaults **false** (fail-closed, B-8 fixed 2026-11-25); disabled for pilot/production |
| Placement (legacy `/drives`, `placements`, recruiters, offers) | Existing CampusAI | Active |
| Placement management v2 (placement hub, campaigns, analytics, import) | Phase 9 | Behind `PLACEMENT_MANAGEMENT_ENABLED` (off; empty tenant list = none) |
| AI Mock Interview: text, voice, video, reports, coding (review-only), company profiles, ops console | Phases 1–8 | All behind flags, **all off** by default |
| `code-runner/` gVisor sandbox | Phase 8.5 | **Standalone, not connected to the backend**; no student code is executed anywhere |
| AI governance, usage ledger, circuit breaker, alerts, quotas | Phase 7 + 10.3 | **Shared by every AI path** (mock interview, mentor, stream, resume, ATS, proof scoring) |

**Integration verdict (source evidence):** one integrated platform. Components that are present but deliberately
not live: Mock Interview and placement v2 (flags off), face attendance (env-disabled on staging), `code-runner/`
(unconnected by design). Overlapping user experiences that exist side by side and need a product decision before
release: legacy `drives`/`placements` pages vs the Phase 9 `placement-hub` (legacy is what runs while the v2 flag is
off — decision P9-D1/D6), and two face pages (`face`, `face-attendance`) whose relationship was not re-verified here.

---

## 3. Completed work and status

Status words: **Committed locally** / **Implemented locally (uncommitted)** / **Tested with recorded evidence** /
**Partially tested** / **Not tested** / **Blocked**.

| Item | Status |
|---|---|
| Phases 1–7 Mock Interview + operations | Committed locally; real-device tests (Phase 5 voice A1, Phase 6 RD-01…RD-24) **not done** |
| Phase 8 Batches 1–3 (review-only coding, company profiles, hidden test pack, `code-runner/`) | Committed locally; **G2 not passed**, G3–G5 open; all 24 coding problems still drafts |
| Phase 9 (placement management) | Committed locally (`2ccc8b9`, `9217d8d`, `a336bb1`); tested with recorded evidence in its spec §16–§20 |
| Phase 5 D3 | Committed locally `12029d6`; tested (unit/tsc/lint/build, spec §6.2); no device test |
| 10.1 decision record | Implemented locally (docs) |
| 10.2 configuration templates + policy check | Implemented locally; tested (parity + policy tests, synthetic compose check) |
| 10.3 AI governance on every AI path | Implemented locally; tested (unit, Postgres, local drills); **caps not set from real provider limits** |
| 10.4 CI workflow | Implemented locally; **not tested (never ran on GitHub)** |
| 10.5 monitoring (readiness queue/AI, client error reports → log, metric, Sentry) | Implemented locally; tested with mocks; **email delivery blocked** |
| 10.6 encrypted backup/restore | Implemented locally; local simulated drill passed; **blocked on D7** |
| 10.7 staging verification script | Implemented locally; passed locally against a production-mode backend; **staging run blocked** |
| 10.8 load harness + stub worker | Implemented locally; local measurements only (stand-in AI) |
| 10.9 drills | Partially tested (local only); email delivery blocked |
| 10.10 acceptance | Recorded (spec §11): **NOT PRODUCTION READY** |
| Findings fixes #1, #2, #4, #7 + Qdrant test isolation | Implemented locally; tested (focused suites, then the full backend suite) |
| B-8 `FACE_ENABLED` fail-closed + mentor breaker precheck | Implemented locally; tested (focused suites, then the full backend suite) |
| Backend code freeze + one full backend run | **Done 2026-11-25: 835 passed, 0 failed, 34 skipped** (§4) |
| Combined local integration smoke | **PARTIAL** — test suites passed; live local infrastructure and browser-to-backend checks unavailable (§4) |
| Root `README.md` | Rewritten from verified facts (documentation only) |

---

## 4. Test evidence and confidence

Evidence classes: **[R]** recorded in a repository document; **[L]** saved log in the previous session's temporary
scratchpad (outside the repo, may already be gone); **[S]** reported in the session only, no saved log;
**[X]** blocked/unverified. **No test logs are committed in the repository.**

| Claim | Result | Evidence |
|---|---|---|
| **Full backend suite on the frozen release candidate (2026-11-25, HEAD `12029d6` + working tree; run alone, 3 734 s)** | **835 passed, 0 failed, 34 skipped, exit 0.** The 34 skips are the opt-in Postgres RLS tests (`RLS_TEST_DATABASE_URL` not set). Command: `cd backend && .venv/Scripts/python -m pytest -rfEs -p no:cacheprovider`. `git status` identical before and after | [L] log kept by the owner outside the repository (SHA-256 `d736950f…4a17`); not committed |
| *Historical:* full backend suite before the findings fixes | 808 passed, 34 skipped, 10 failed (production-boot tests needing governance env; fixed in test setup, 38/38 in their 4 files) | [L][R spec §10.10] — superseded by the row above |
| Combined local integration smoke (2026-11-25, after code freeze) | 411 focused backend tests passed; 77 AI-worker integration tests passed; frontend `tsc` passed; no source file changed. **Verdict PARTIAL**: local Postgres, Redis, Qdrant and application servers were unavailable, so no live-infrastructure or browser-to-backend check ran | [S] reported by that session; no log in the repo. **These counts are separate runs — never add them to the full-suite numbers** |
| Backend focused governance/monitoring/circuit/ledger/mentor/health | 95 passed | [S] |
| Backend Mock Interview + resume suites | 194 passed | [S] |
| AI worker budget tests | 14 passed | [S] |
| AI worker related suites (excluding both face/Qdrant files) | 87 passed after the chain fix (an earlier run: 129 passed, 1 failed — fixed) | [S] |
| Worker security tests, isolated from `qdrant_data` | 6 passed | [S] |
| AI worker full suite | 131 passed + 5 Qdrant-server tests failed without a server; those 5 passed against a disposable Qdrant | [L] + [S]; **never one clean complete run**; predates the findings fixes |
| Postgres API suite (`tests_pg_api`, as `app_user`) | 245 passed, 0 skipped | [L][R] **historical** (Phase 10 run; predates the findings fixes and the code freeze; **not rerun on the final tree**) |
| RLS suites | 34 passed | [L][R] **historical** (same run; **not rerun on the final tree** — final Postgres/RLS validation pending) |
| Frontend | unit 215/215; tsc clean; lint 0 errors (2 old warnings); build OK | [S][R] (frontend unchanged by the findings fixes) |
| Backup/restore drill | passed locally, synthetic data, 11 s | [L][R] local simulation |
| Load tests | measured with a **stand-in AI worker** | [L][R] local simulation |
| GitHub CI | never ran | [X] |
| Staging, live AI, real email delivery, real Supabase restore, real devices / screen readers | not done | [X] |

Rule: **never present targeted results as "the whole suite passed".**

---

## 5. Owner decisions

**Approved:** ₹0/month AI (no paid fallback, no automatic billing) — D4; free-tier hosting only — D1; staging
`https://campus-ai-staging.vercel.app` — D2; email alerts — D5 (via free external monitors — OQ-4); RPO 24 h /
RTO 8 h daily backups — D6; pilot = 1 institute, ≤ 100 students, ≤ 3 concurrent AI interviews — D8; AI governance
off in code but **required in production** — OQ-2; Phase 5 D3 committed — D3.

**Pending (do not treat as approved):** D7 backup destination/retention/key custody; D9 Langfuse tracing for
non-interview AI; OQ-1 platform-operator role + enterprise integrations scope (in Phase 10 scope, **not
implemented**); OQ-3 provider free-tier data-use terms; OQ-5 free-plan commercial-use terms; OQ-6 `main` push / Vercel
auto-deploy; monitoring accounts + alert email; staging deployment + migration authorization; provider free-limit and
"no billing method" verification; product decision on overlapping legacy/v2 pages; `FACE_ENABLED` production value.

---

## 6. Known issues, risks and classification

### 6.1 Release blockers (must close before release)
- **B-1** ~~Full backend suite not green on the final tree~~ **resolved 2026-11-25**: one full run on the frozen
  release candidate, 835 passed / 0 failed / 34 skipped (§4). Still pending (not part of B-1): the Postgres/RLS
  suites and one complete AI-worker run with a disposable Qdrant on the final tree.
- **B-2** AI caps not derived from verified provider free limits; "no billing method" not confirmed (G5).
- **B-3** Dependency advisories: backend 19 advisories in 8 packages, AI worker 84 in 10 (pip-audit 2.9.0, severity
  **not verified**); `python-jose` and its `ecdsa` dependency have **no fixed version** — review/migration pending.
- **B-4** GitHub CI never ran.
- **B-5** Staging not running this code; new mandatory env (`AI_GOVERNANCE_ENABLED` + platform caps on the backend,
  `LLM_TIMEOUT_SECONDS` + `LLM_MAX_RETRIES` on the worker) must be set **before** any redeploy or both services
  refuse to start; migration `f3a5c7e9b1d2` not applied anywhere real.
- **B-6** Email alert delivery, real backup destination/schedule (D7), live-AI runs, hosting/provider terms (OQ-3,
  OQ-5) unverified.
- **B-7** Phase 8 security gates G2–G5 and human security acceptance incomplete; **student code execution must stay
  disabled**.
- **B-8** ~~`FACE_ENABLED` defaults to true~~ **fixed 2026-11-25**: owner decision = face recognition disabled for
  the pilot and production. Worker default is now `false` (fail-closed); `FACE_ENABLED=false` is in
  `deploy/prod.env.example` and `compose.prod.yml`; `check_compose_prod.py` fails on `true` without a separate
  `--approve FACE_ENABLED`.

### 6.2 Pilot limitations (acceptable only if the owner accepts them in writing)
- Process-local concurrency limit, provider-quota pause and idempotency replay cache (single backend process).
- No Redis on the free tier: in-process jobs are lost on restart; rate limits per instance; a backend that starts
  while Redis is down keeps its read cache disabled until restart.
- Mentor circuit-breaker refusal: **fixed 2026-11-25** — both mentor routes run a read-only breaker check
  (`ai_circuit.would_refuse`, never `allow()`) before saving the question and return the usual 503
  `circuit_open`. Remaining race: a breaker that opens between that check and the governed call's `allow()` still
  refuses the call as before, leaving that one saved question unanswered (window = one request's DB work).
- Readiness exposes AI reason codes (`kill_switch`, `provider_quota`) — behind the staging gate on staging.
- Free hosting sleeps when idle; cold starts near the 60 s worker timeout.
- Real-device, screen-reader and accessibility matrix not run (G14).

### 6.3 Non-blocking improvements
- Mentor streaming reports no tokens (counts as requests only).
- Ledger-based institute/user caps can overshoot by up to the concurrency limit.
- True multimodal AI is **not implemented** (out of scope).
- Platform-operator role and enterprise integrations not implemented (OQ-1).

### 6.4 Local data incident — `ai-worker/qdrant_data/`
`qdrant_data/meta.json` was rewritten twice by test runs on 2026-11-25 (14:33 and 17:22; now 513 bytes;
`collection/student_faces/storage.sqlite` untouched, dated 2026-11-20). Cause: `test_worker_security.py` opened the
embedded store at the default path; **fixed** (the test now uses a throwaway `QDRANT_LOCAL_PATH`). The original
content is unknown. **Do not inspect, restore, reset or delete anything in that folder without owner direction.**
When running worker tests, exclude `tests/test_face_qdrant_server.py` and `tests/test_face_tenant_filter.py` unless a
disposable Qdrant server is configured (`QDRANT_TEST_URL` = `QDRANT_URL`).

---

## 7. Gate summary (spec §11)
G1 config parity **PASS (repo)**; G17 docs **PASS**; G2–G6, G11, G12, G15 **PARTIAL**; G7, G14 **NOT RUN**;
G8, G9, G10, G13, G16 **BLOCKED**. Production-ready requires every gate; a controlled pilot requires every critical
gate plus written owner acceptance of the non-critical gaps.

---

## 8. Final release roadmap (finite, dependency-ordered)

**Progress (2026-11-25):** steps 1–4 done; step 3 verdict **PARTIAL** (no live local infrastructure); step 5 done
for the SQLite backend suite only (835/0/34) — the AI-worker complete run with a disposable Qdrant and the Postgres
suites on the final tree are **still pending**; step 6 done (README, then corrected for upload limits, public
sign-up and governance wording); step 7 changed-file review done (no unexpected changes; no dependency files
changed — the dependency review B-3 is still open); step 8 plan C1–C10 approved in principle. Next: owner approval
to stage C1.

1. **Close confirmed release-blocking code defects:** decide/implement the mentor breaker precheck (§6.2) if the owner
   wants it before the pilot; add `FACE_ENABLED` to the templates with the owner's value (B-8). No other new code.
2. **Targeted local tests** for exactly the modules changed in step 1.
3. **One combined local application smoke round** (local production-mode stack, synthetic data, stub AI).
4. **Freeze application code.**
5. **Run the full backend suite once** on the release candidate (alone — it shares `%TEMP%\campus_ai_test.db`); plus
   one complete AI-worker run with a disposable Qdrant, the Postgres suites and the frontend checks.
6. **Update the root `README.md`** and module docs with verified results only (§10).
7. **Final changed-file and dependency/security review**; owner decides the dependency batch (B-3).
8. **Prepare logical commits** and obtain owner approval. Plan C1–C10, approved in principle 2026-11-25
   (supersedes the earlier unrecorded "C1–C7"; spec §11.1 has only a suggested split). C1 backend AI governance,
   readiness and client-error endpoint + tests → C2 AI worker provider budget + fail-closed face + tests → C3 frontend
   error reporting → C4 deploy templates + policy test → C5 backup tooling → C6 staging-verify + load harness → C7 CI
   → C8 Phase 10 specs + runbooks → C9 README, `CLAUDE.md`, project state, plan, this handoff → C10 ignore local
   Qdrant data. Explicit per-file `git add --` lists only; owner-local files (§1.2) never staged.
9. **Push the feature branch and open a PR** — only after explicit authorization (never `main`).
10. **Run GitHub CI**; fix only genuine CI blockers.
11. **Staging**: set the new env first, then deploy + migration + `scripts/ops/staging_verify.py`, privilege
    report, `TRUSTED_PROXY_HOPS` measurement, staging drills — each step authorized.
12. **Phase 8 security gates** G2–G5 and human security acceptance.
13. **Phase 10 final acceptance** (all gates, real devices, email drills, real restore) and controlled pilot.
14. **Production launch** only after every required gate passes.

---

## 9. Rules to prevent repeat work
- No full backend suite for small changes; focused tests after focused fixes; related regressions only when relevant.
- Exactly one full backend run after the backend code freeze (step 5). Never two backend pytest runs at once.
- Keep valid existing evidence; do not rerun suites without a reason.
- No new features unless mandatory for the approved release.
- No repeated audits unless code changed or a real blocker requires one.
- Never weaken, skip or delete a failing test to look green.

## 10. README ownership and timing
The root `README.md` is updated **after local validation and code freeze (step 5), before any GitHub push (step 9)**.
It must document only verified facts: unified architecture; features and roles (with disabled features marked);
installation and environment configuration (key names only); database migrations (head and count as verified at
that time); local test commands with their actual results; deployment steps; known limitations and disabled
features. Never invent test counts or migration counts, never call unverified features complete.

---

## 11. Strict safety rules for the next session
- Work only in the release worktree on `feature/ai-mock-interview`. Never modify the original local checkout
  (`feature/student-approval`).
- Never open, read, print, copy or modify `docker-compose.override.yml`. Never print `.env` values or secrets (list
  key names only).
- Never touch `ai-worker/qdrant_data/` (§6.4).
- No staging, commit, push, PR, merge or deployment without explicit owner approval; never push `main`.
- No connection to staging/production databases; Alembic only against a throwaway local database; migrations additive.
- No paid AI calls; no live provider calls without confirmed free, authorized, ₹0 conditions.
- No dependency installs or upgrades without approval; no Docker global cleanup.
- Stage files explicitly; never `git add .`/`-A`; no destructive Git commands.
- Every AI path must go through `ai_governance`; new features behind default-off flags.
- Edit text files with LF line endings (earlier scripted edits on Windows produced CRLF once).

## 12. Exact next task
~~Close the remaining confirmed release-blocking code defects (steps 1–2)~~ — done; steps 3–7 done (see §8 progress).
**Next: with explicit owner approval, stage and commit C1–C10 one at a time (§8 step 8), each with an explicit
`git add --` path list and a `git diff --cached --name-only` check before committing.** No push, PR or deployment
in that task; the owner still decides the dependency batch (B-3); the application code stays frozen.
