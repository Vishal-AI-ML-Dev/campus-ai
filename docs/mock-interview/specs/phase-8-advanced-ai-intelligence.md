# Phase 8 — Advanced AI Interview Intelligence

Status (updated 2026-11-24, Batch 3): **the §14 decision register is the authoritative approval record.**
Approved so far: P8-D1…D11, D14…D17, B1-a/b/c, U1–U4, **O1 = option A** (§14.7) and the G2 sandbox decisions
**P8-S2, S3, S5, S6, S7** (§14.8, 2026-11-24). Still awaiting approval: P8-D12/D13, P8-S1, S1b, S4. Gates G2–G5 are
**not** passed (§15): G2's decisions are approved but its evidence (escape suite on gVisor, CI run) is missing.
Built: Batch 1 = 8.1 + 8.2 (`c62b067`, §16), Batch 2 = 8.3 + 8.4 (`1d620e6`, §17), Batch 3 = 8.5a + 8.5b
(implemented locally, uncommitted, §18). **No student code is executed anywhere: actual code execution stays
deferred until G2–G4 pass.** Originally written as
8.0 planning, revision 2 (base `869d42b`, Phase 7). Overall status: `../PROJECT_STATE.md`. Roadmap:
`../IMPLEMENTATION_PLAN.md` §6a.

Scope (from P7-D1): **coding interview round**, **secure code-execution sandbox**, **advanced coding evaluation**,
**company-specific interviews**, and a **full multi-round simulation** that combines them with the existing text,
voice and video modes.

Labels: **[exists]** = verified in the code at `869d42b`; **[new]** = proposed here.

**Revision history**
- r1 (2026-11-23): first planning draft.
- r2 (2026-11-23): refinement requested by the user. Hidden tests moved out of the repository into a runner-private
  test pack; the runner takes problem/test-set identifiers, never expected outputs; `coding-v1` weights changed
  (40/20/10/15/10/5) and the hint penalty removed; best graded submission instead of last; atomic quota reservation
  for simulations; expiry/cleanup policy; error-class handling; server-side limit enforcement; sandbox hardening
  detail (fd limits, process-tree kill, trusted/untrusted split, token rotation); a 20-threat model; milestones
  tagged by prerequisite; explicit approval gates. Changes per decision: §14.3.

---

## 1. What already exists (reuse, do not rebuild)

| Area | Exists [verified 2026-11-23] | Where | Phase 8 use |
|---|---|---|---|
| Session types | `INTERVIEW_TYPE_VALUES = (hr, technical, coding, company, full)`; the CHECK constraint already allows `coding`, `company`, `full` | `backend/app/models/mock_interview.py` | `coding` sessions need **no** change to the sessions table. `company`/`full` stay reserved (§6.7) |
| Create API | `MockInterviewSessionCreate.interview_type: Literal["hr","technical"]`; `/catalog` lists all five; `/options` offers hr/technical | `schemas/mock_interview.py`, `routers/mock_interview.py` | Add `coding` to the Literal and `/options` only behind the coding flag (additive) |
| Company + JD inputs | `company_name` (≤150) and `job_description` (≤10,000) on sessions; worker `jd_analyzer` grounds plan topics in JD text | `services/mock_interview.py` `_config`, `ai-worker/interview/graph.py` | Company profiles and JD skill mapping build on these; nothing new on the session row |
| Institute company data | `drives` (model `Drive`): `company_name`, `role_title`, `description`, `required_skills`, `is_open`, `deadline` (tenant-scoped) | `models/placement.py` | An approved source (§7.5) |
| Verified student data | Skills, projects, CGPA sent as a "verified only" profile | `services/mock_interview.py` `_profile` | Skill-gap mapping (§7.7) |
| Engine | Stateless LangGraph plan → turn → report; structured output + fallbacks; evidence-quote validation | `ai-worker/interview/*` | Explanation turns reuse the turn engine; coding AI steps are new endpoints |
| Reports | One final report per session; the backend re-validates and recomputes every aggregate; rubric mirrored in the backend with a contract test | `services/mock_interview_report.py`, `ai-worker/interview/rubric.py` | `coding-v1` follows the same pattern; `hr-v1`/`technical-v1` unchanged |
| Session reliability | Lease (CAS on `updated_at`), idempotent answers, `time_up` auto-close, one active session, 10/day, 30 turns; per-student create lock (`SELECT … FOR UPDATE` on the user row) | `services/mock_interview.py` | Reused by coding sessions and every simulation round |
| Voice (Phase 5) | Browser Web Speech; reviewed transcript only; `answer_source` | `useSpeech.ts`, `VoiceAnswerBox.tsx` | Spoken explanation turns only; never code |
| Video (Phase 6) | Browser-only preview; no capture/upload/analysis; static privacy guards; fail-closed allow-list | `VideoInterviewWorkspace.tsx` + guards | Coding/simulation rounds in video mode reuse the workspace; guards extended |
| Operations (Phase 7) | Request + invocation ids; `ai_usage_events` (free-string `step`/`outcome`); per-step circuit breaker; `ai_feature_controls` (`limits_json`); institute row lock `FOR NO KEY UPDATE` in quota checks; quotas only at create (R8); token-protected `/metrics`; R1/R2 | `ai_usage.py`, `ai_circuit.py`, `mock_interview_policy.py`, `observability.py` | Executions and new AI steps write ledger rows, use the breaker and quotas; R1/R2 extend to code |
| Jobs | RQ when `REDIS_URL`, else in-process `BackgroundTasks` | `backend/jobs.py` | Asynchronous execution dispatch |
| Deployment | Staging: Vercel + **Render free web services (512 MB)** + Supabase + Qdrant Cloud, no Redis; Compose template with `read_only`, `cap_drop: ALL`, `no-new-privileges`, internal network | `deploy/README.md`, `deploy/compose.prod.yml` | Render cannot host a sandbox (§4.9) |
| Frontend | Next.js 16.4.6, React 19.2.4, no editor library; `useApiResource`, `ui/states.tsx`; `node --test` unit tests | `frontend/package.json` | Editor = dependency decision (P8-D5) |
| Repository | Remote `origin` on GitHub; **visibility not verified** in this session | `git remote -v` | Treated as possibly public: no hidden tests or reference solutions in this repository (§3.2) |

**Not present [verified]:** any code execution path, editor component, problem bank, company-profile table or
multi-round orchestration.

## 2. Goals, non-goals, principles

**Goals.** (1) Timed coding rounds with an editor, visible and hidden tests, and objective results. (2) A sandbox that
is safe to run hostile code in. (3) Evaluation in which objective test results always win over AI opinion.
(4) Company- and role-specific practice built only from approved sources. (5) A full simulation (e.g. coding →
technical → HR) that reuses the existing rounds, report and history.

**Non-goals (Phase 8).** Proctoring, face monitoring or anti-cheat tracking (tab switches, paste tracking, gaze);
plagiarism detection; faculty-assigned coding campaigns and faculty access to student code (Phase 9); a platform-wide
problem-authoring role (Phase 10, needs the platform-operator role, P7-D2); speech-to-code; recording; any claim that a
company asks particular questions.

**Principles.**
1. **No code ever runs in the backend, the AI worker, the frontend server or a Render service.** Only the isolated
   runner (§4) executes code. Static tests enforce it (§11.6).
2. **Objective beats AI.** Test-derived categories are computed by the backend from verified runner results; the LLM
   can add narrative but can never change them, and never sees hidden tests.
3. **Hidden evaluation assets are private.** Hidden inputs, expected outputs and reference solutions live only in a
   runner-private test pack; they never enter this repository, the browser, the backend database, prompts, logs or
   reports.
4. **Additive only.** New tables, new routes, new optional response fields, new flags defaulting OFF. Existing tables,
   columns, responses and env vars unchanged.
5. **A round is a normal session.** Coding rounds and simulation rounds are `mock_interview_sessions` rows.
6. **Grounded content only.** Company material comes only from approved sources (§7.5).
7. **Degrade safely, never simulate.** No sandbox ⇒ review-only mode: editor and AI feedback, test categories
   `not_verified`, no overall coding score, **no simulated or AI-predicted execution result**.
8. **Server-side enforcement.** Every limit, quota and access rule is enforced by the backend (and the runner where it
   applies); the frontend only reflects it.

## 3. A — Coding interview round

### 3.1 Languages (P8-D1)

| Language | Toolchain (pinned image digest) | Phase 8 | Notes |
|---|---|---|---|
| Python | CPython 3.13 | yes | Most common in campus preparation |
| C++ | GCC, `-std=c++17` | yes | Standard for placement coding rounds |
| Java | OpenJDK 21 LTS | yes | Needs larger memory/time profiles (§4.4) |
| C | GCC, `-std=c11` | yes | Shares the GCC image |
| JavaScript | Node 22 LTS | later expansion | Not in Phase 8 |

Compile and run commands are **fixed per language by the runner**; the student cannot pass flags, and nothing can be
installed or downloaded at runtime (§4.4).

### 3.2 Problem bank: public and private assets (P8-D2)

Two artefacts, joined by `problem_key@version` and a test-set digest.

**Public bundle — this repository** (`backend/app/coding_bank/problems/<key>/problem.json`), safe to be public:

| Field | Notes |
|---|---|
| `key`, `version` | e.g. `pair-sum-sorted`, `3`. Sessions pin `key@version` |
| `title`, `difficulty`, `topics[]` | topics map to the technical topic library |
| `statement`, `input_format`, `output_format`, `constraints` | restricted Markdown (no HTML, images or links) |
| `examples[]` | `{input, output, explanation}` |
| `visible_tests[]` | shown in full; run by **Run** |
| `checker` | `exact` \| `tokens` (default) \| `float` (abs/rel tol) \| `unordered_lines`; no custom checker code |
| `limits` | `time_ms` (C++ baseline), `memory_profile` (`standard` / `large`) |
| `starter[lang]` | stdin/stdout skeleton |
| `hints[]` | ≤ 3 authored hints, never code |
| `editorial` | optional; shown **only after** the student's session report exists, and only when `editorial_public: true` |
| `expected_complexity` | rubric reference for the AI review |
| `hidden_test_set` | `{id, version, digest_sha256, groups: {basic: n, edge: n, performance: n}}` — counts and digest only |
| `source` | `original` + author + reviewer + review date; **no problems copied from LeetCode, HackerRank, GFG, past papers** |
| `status` | `draft` \| `published` \| `disabled` — only `published` problems are selectable. Publication validation rejects a problem unless its hidden test set has **≥ 1 `basic`, ≥ 1 `edge` and ≥ 1 `performance` test** (P8-D7 U2) |

**Private test pack — runner-private storage** (P8-S7): hidden inputs, hidden expected outputs, reference solutions in
every language, calibration data (reference timings) and private scoring metadata. Proposed home: a **separate
private Git repository** (access limited to named maintainers) whose CI builds a versioned, digest-addressed test-pack
archive; the archive is installed read-only on the runner VM by an operator (audited, §4.8). The runner verifies each
test set's digest against the public bundle before use and refuses mismatches.

**Hidden-test protection (mandatory):**
1. Hidden data is never bundled into the frontend, never sent to the browser, never stored in the backend database,
   never placed in AI prompts, logs, metrics, ledger rows, Sentry/Langfuse events or reports.
2. The backend sends the runner `problem_key`, `version` and `test_set` identifiers — never inputs or expected
   outputs for hidden tests (§4.6). The runner judges hidden tests inside its trusted layer.
3. The runner returns, for hidden tests, **only** per-group pass counts, the first failing group, its verdict class,
   and max time/memory (rounded). No inputs, expected outputs, actual outputs, stderr or per-test identifiers.
4. Oracle resistance (§3.9): 3 graded submissions per problem per session, a per-student per-problem cap across
   sessions, uniform summary granularity, rounded timings, rate limits.
5. Access: a student can only fetch or submit against problems **assigned to their own session** (§3.4); problem
   keys from the request are ignored except to match an assigned attempt.
6. CI in this (possibly public) repository uses only visible tests and synthetic fixtures; reference-solution
   validation runs in the private test-pack repository's CI (§11.5).

**Seed bank:** 24 original problems (8 easy, 8 medium, 8 hard) across arrays, strings, hashing, two pointers,
stacks/queues, sorting/searching, recursion, trees, graphs, greedy and DP. I can draft statements, visible tests,
hidden tests and reference solutions; **a human must review every statement and reference solution before a problem
is published** (status stays `draft` until then).

### 3.3 Standard input/output and error handling (P8-D3)

Programs read stdin and write stdout. Function-signature problems are a later extension (a `harness` field can be
added without breaking the format).

| Class | Detected by | Student sees (visible / custom run) | Student sees (graded, hidden) | Counts toward limits? |
|---|---|---|---|---|
| `compile_error` | non-zero compiler exit | compiler message, ≤ 8 KB, paths scrubbed | same message (it is their code, not test data) | yes |
| `runtime_error` | non-zero exit / signal (not a limit) | exit code or signal name, stderr ≤ 4 KB | "Runtime error in group *edge*" | yes |
| `time_limit` | CPU or wall limit hit, process tree killed | "Time limit exceeded (2.0 s)" | "Time limit exceeded in group *performance*" | yes |
| `memory_limit` | cgroup OOM | "Memory limit exceeded (256 MB)" | group + class only | yes |
| `output_limit` | captured stream > 64 KB, tree killed | first 4 KB + "output limit exceeded" | group + class only | yes |
| `wrong_answer` | checker mismatch | expected vs actual (visible only) | group counts only | yes |
| `accepted` | checker match | ✓ | group counts | yes |
| `sandbox_error` | runner-side failure (not the student's fault) | "We couldn't run your code. It is saved; try again." | same | **no** |
| `rejected` | quota, rate, breaker open, queue full | reason + retry hint | same | **no** |

Overall verdict = the first non-accepted result in group order `basic → edge → performance`. A compile error stops
all tests.

### 3.4 Difficulty, selection and access

| Setting | easy | medium | hard |
|---|---|---|---|
| Problems per round | 1–2 | 2 | 2 (or 1 hard + 1 medium) |
| Round duration (min) | 30 | 45 | 60 |
| Execution runs per problem (visible + custom) | 30 | 30 | 30 |
| Graded submissions per problem | 3 | 3 | 3 |
| Hints per problem | 3 | 3 | 2 |

**Selection:** the backend assigns problems at session start from `published` problems matching difficulty and
topics, avoiding the student's last 10 coding sessions (deterministic, seeded by session id). Practice mode lets the
student pick a published problem, subject to the per-problem cross-session cap (§3.9). Simulation rounds never let
the student pick. **Access check on every coding route:** the session belongs to the caller and tenant, and the
attempt `seq` exists in that session; anything else → 404. Durations use an additive coding Literal (`30|45|60`); the
DB CHECK already allows 5–120.

### 3.5 Editor (P8-D5)

| Option | Size / deps | Accessibility | Mobile | CSP impact | Verdict |
|---|---|---|---|---|---|
| `<textarea>` (tab handling, line numbers) | none | native, good | good | none | Always-available fallback ("Simple editor") |
| **CodeMirror 6** (`@codemirror/state`, `view`, `commands`, `language`, `lang-python`, `lang-cpp`, `lang-java`; C uses `lang-cpp`) | ≈ 150–250 KB gzip, MIT | screen-reader support; Escape-then-Tab focus exit | usable | none (no eval, no workers) | **Recommended** |
| Monaco | ≈ 2–5 MB, web workers | weaker on mobile | poor | needs `worker-src blob:` | Rejected |

Requirements: exact version pins (no `^`), only the packages listed (no theme or extras packages), dynamic import
only inside the coding room, wrapped in `CodeEditor.tsx` so it can be swapped; keyboard interaction documented on
screen; the textarea fallback is selectable at any time and is used automatically if the editor chunk fails to load.
`npm audit --omit=dev` must stay at 0 after the addition.

### 3.6 Student lifecycle

```
setup ─▶ instructions (languages, rules, rubric, limits, review-only notice if applicable) ─▶ start (timer)
  for each assigned problem:
    statement ─▶ code (draft autosave) ─▶ Run (visible / custom) … ─▶ Submit (graded, hidden) ≤ 3
                       ▲                                         │
                       └─ hint (optional, no score penalty) ◀───┘   ─▶ "Done with this problem"
    explanation turn(s): approach, complexity, edge cases, how you tested (typed; spoken in voice/video mode)
  finish, or time up (each unfinished attempt's latest saved draft becomes an automatic graded submission)
─▶ evaluating ─▶ report (objective results from the best graded submission + AI estimates)
```

### 3.7 State machines

**Session** (existing, unchanged): `setup → in_progress → evaluating → completed | abandoned | failed`.

**Attempt** (`mock_interview_code_attempts.status`):
```
pending ─start─▶ working ─"done"──────────▶ explaining ─explanation ends─▶ closed
                   │  ▲ run / submit
                   │  └────────┘
                   ├─time up──────────────▶ auto_submitted ─auto submission settled─▶ closed
                   └─session abandoned────▶ abandoned
```
`closed` and `abandoned` are terminal; no run or submission is accepted in them (409).

**Submission** (`mock_interview_code_submissions.status`):
```
queued ─dispatch─▶ running ─runner result─▶ completed (verdict set)
  │                  └─runner error/timeout/unreachable─▶ error   (retryable, auto ≤ 2; NOT counted)
  ├─review-only mode (graded/auto)──────────▶ not_executed (terminal; counted as a graded submission)
  └─quota / rate / breaker / queue full────▶ rejected     (terminal; NOT counted)
```
In review-only mode **Run** is disabled (no simulated output); **Submit** stores the code as `not_executed` for the AI
review.

### 3.8 Server-side limit enforcement (P8-D6)

| Control | Enforcement |
|---|---|
| Runs / graded submissions per attempt (30 / 3) | atomic conditional update in the transaction that inserts the submission: `UPDATE …attempts SET runs_used = runs_used + 1, in_flight_submission_id = :new WHERE id = :a AND tenant_id = :t AND status = 'working' AND runs_used < 30 AND in_flight_submission_id IS NULL` (graded: `submissions_used < 3`). 0 rows ⇒ 409 with the reason. The backend restores a count only when a submission ends `error` or `rejected` |
| One in-flight submission per attempt | the `in_flight_submission_id` compare-and-set above (SQLite and Postgres); cleared at the submission's terminal status |
| Idempotent retries | unique `(attempt_id, idempotency_key)`; a repeat returns the original submission, no new count |
| Per session | ≤ 90 executions per session; ≤ 1 queued-or-running submission per session across attempts |
| Per student | ≤ 150 executions per rolling 24 h; per-problem graded cap across sessions (§3.9) |
| Per institute | monthly execution cap (env ceiling ∩ institute row); concurrency share in dispatch (≤ max(2, 25 % of runner slots)); excess waits `queued` |
| Rate limits (slowapi, per caller, Redis-shared when `REDIS_URL`) | draft 30/min, run 10/min, submit 3/min, hint 5/min, poll 60/min |
| Payload bounds | source ≤ 64 KB, custom input ≤ 16 KB, language ∈ the session's allowed list; validated before any count |
| Compile/execution resources | fixed by the runner per language profile (§4.4); the backend cannot raise them |
| Hints | `hints_used < max_hints` conditional update; authored hint returned deterministically |

**Exhausted limits:** 409 (attempt limits) or 429 (rate/quota) with a stable `code` and a plain message ("You have
used all 3 graded submissions for this problem. Your best result is kept."); the UI disables the control and shows
remaining counts from `GET …/coding`. Time-up auto-submission is **not** blocked by exhausted run limits; it is skipped
only if 3 graded submissions already exist (the best one then stands).

### 3.9 Oracle resistance

Hidden feedback is summary-only (§3.2 rule 3). In addition: graded submissions ≤ 3 per problem per session and ≤ 6 per
student per problem per rolling 7 days across sessions (practice mode included); identical source (`source_sha256`)
resubmitted against the same test set returns the earlier result without execution and without a new count; timings
rounded to 10 ms; per-group counts only (never which test inside a group failed); the same response shape whatever the
failure.

### 3.10 Drafts and persistence

Drafts autosave after 10 s of inactivity and on Run/Submit (`PUT …/draft` with `draft_rev`; stale → 409, client
reloads). Refresh resumes exactly (server is the source of truth). No code in `localStorage`.

### 3.11 Hints

Hint level n returns the authored hint n first (deterministic, free). If the coding-AI flag is on and authored hints
run out, the worker produces a tailored hint from the statement, the student's current code and **visible** results
only. Guards: no code blocks over 3 lines; no complete solution (worker filter; the backend holds no reference
solution); never hidden data. **Hints do not reduce the score** (P8-D7); `hints_used` is recorded as an analytics
signal and shown in the report as information.

## 4. B — Secure code-execution sandbox

**Nothing in this section is built until its gate (§15) is passed.** Until then the coding round runs in review-only
mode.

### 4.1 Isolation options compared

| Option | Isolation | Fits our infra? | Ops burden | Verdict |
|---|---|---|---|---|
| Run in backend / AI worker (`subprocess`) | none | — | — | **Forbidden** |
| Plain Docker (`runc`) | namespaces + cgroups, shared kernel | needs Docker daemon | low | **Insufficient alone** |
| Judge0 CE self-hosted | `isolate` inside a **privileged** container; historically cgroup v1 | dedicated VM only | medium | Fallback only, ≥ 1.13.1 (2024 sandbox-escape advisories, e.g. CVE-2024-28185, fixed there) |
| Piston self-hosted | `isolate`, privileged container | dedicated VM | medium | Similar to Judge0 |
| **gVisor (`runsc`) + thin runner** | user-space kernel between the code and the host kernel; plus cgroups, seccomp, no network, read-only rootfs | any Linux VM (systrap platform; no nested virtualization) | medium; ≈ 500 lines of runner code | **Recommended** |
| Firecracker / Kata microVMs | hardware virtualization | needs KVM | high | Revisit in Phase 10 |
| nsjail on a VM | namespaces + seccomp-bpf, shared kernel | yes | medium | Weaker than gVisor against kernel bugs |
| Hosted API (Judge0 Cloud, Sphere Engine) | provider's | yes | low | Sends student code to a third party: separate privacy decision; the hidden pack would also have to be uploaded |
| Browser WASM | browser | yes | low | Cannot be trusted for grading |

### 4.2 Architecture: trusted orchestration vs untrusted code

```
Backend (Render or Compose)                                  Dedicated runner VM (Ubuntu 24.04, nothing else)
┌──────────────────────────────┐  HTTPS, bearer token        ┌───────────────────────────────────────────────────┐
│ coding service               │  (+ IP allow-list; private  │ TRUSTED LAYER (runner user; no Campus AI secrets  │
│  store source → submission   │   network or mTLS where     │  except its own token list)                       │
│  job: dispatch (RQ/BG task)  ├────practical)──────────────▶│  auth (constant-time) → validate → bounded queue  │
│  score from verified results │  X-Request-ID,              │  test-pack store (read-only, digest-verified)     │
│  ledger, breaker, quotas     │  X-Execution-ID             │  executor: create container, feed stdin, capture  │
└──────────────────────────────┘◀────────────────────────────┤  capped output, enforce wall clock, kill tree,     │
   receives: verdicts, group counts, visible-test details     │  judge with checker, reduce hidden results,       │
   never: hidden inputs / expected / actual outputs           │  remove container, reap orphans                   │
                                                              │ ─────────────── boundary ──────────────────────── │
                                                              │ UNTRUSTED (one fresh gVisor container per         │
                                                              │  submission; no socket, no network, no secrets)   │
                                                              │  runsc · uid 65534 · cap-drop ALL ·               │
                                                              │  no-new-privileges · read-only rootfs ·           │
                                                              │  tmpfs /workspace 64m · cgroup cpu/mem/swap/pids · │
                                                              │  rlimits nofile/fsize/core · image@sha256         │
                                                              └───────────────────────────────────────────────────┘
```

**Separation rules.** Only the trusted layer talks to the Docker daemon; the Docker socket is never mounted into an
execution container and the execution user is not in the `docker` group. Checkers and test data live only in the
trusted layer; the untrusted container receives the source, then one test's stdin at a time, and nothing else. The
trusted layer runs as a dedicated system user. Because Docker daemon access is root-equivalent, the VM runs nothing
else and holds no Campus AI secret other than the runner token list; rootless Docker with `runsc` is evaluated in 8.5b
and adopted if cgroup limits still work under it.

### 4.3 Untrusted container hardening (P8-S2)

| Control | Setting |
|---|---|
| Runtime | `--runtime=runsc`, the only runtime the executor accepts; a startup self-check refuses to serve if `runsc` is unavailable |
| Privileges | never `--privileged`; `--cap-drop ALL`; `--security-opt no-new-privileges`; `--user 65534:65534` |
| Syscalls | gVisor's syscall interception + Docker's default seccomp profile |
| Filesystem | `--read-only` rootfs; `--tmpfs /workspace:size=64m,nosuid,nodev`; the build-output directory is made read-only before tests run and per-test scratch is wiped between tests; no host bind mounts; no host paths with secrets |
| Network | `--network none` (no inbound or outbound); the host firewall also drops traffic from container bridges |
| Resources | `--cpus 1`; `--memory` = profile; `--memory-swap` = the same value (no swap); `--pids-limit 64` (Java 128); `--ulimit nofile=64:64` (Java 256); `--ulimit fsize=16m`; `--ulimit core=0` |
| Environment | cleared: only `PATH`, `LANG=C.UTF-8`, `HOME=/workspace`; no proxy variables, no tokens |
| Init | `--init` so orphans are reaped; the container's whole cgroup is killed on any limit |
| Lifetime | fresh container per submission, `--rm`; a reaper removes any execution container older than 120 s and any orphan workspace; if leftovers persist the runner reports `degraded` and refuses new work |

### 4.4 Execution limits (P8-S3)

Per-test limits. **Total wall clock per submission: 30 s** including compilation; at 30 s the tree is killed and
the remaining tests are reported `time_limit` (not run).

| Language | Compile wall / memory | Run CPU per test | Run wall per test | Memory `standard` / `large` | pids | nofile |
|---|---|---|---|---|---|---|
| C11 (`gcc -O2 -std=c11 -lm`) | 10 s / 512 MB | 2 s × problem factor | 2.5 × CPU | 256 / 512 MB | 64 | 64 |
| C++17 (`g++ -O2 -std=c++17`) | 10 s / 512 MB | 2 s × factor | 2.5 × CPU | 256 / 512 MB | 64 | 64 |
| Java 21 (`javac`; `java -Xss64m -XX:+UseSerialGC -Xmx<profile − 64 MB>`) | 15 s / 768 MB | 2 s × 2 × factor | 2.5 × CPU | 512 / 768 MB | 128 | 256 |
| Python 3.13 (syntax check; `python -I -S`) | 5 s / 256 MB | 2 s × 3 × factor | 2.5 × CPU | 256 / 512 MB | 64 | 64 |

`factor` comes from the private pack's calibration (reference time × safety margin), capped so no test exceeds 6 s
CPU. Output: 64 KB per stream per test; ≤ 40 tests per submission. Compiler options are fixed by the runner; the
images contain only the toolchain and standard library, so source-level includes or imports cannot load anything
outside them, and there is no network for downloads. **On any limit the trusted layer kills the entire process tree**
(container cgroup kill), records the limit, and continues with the next test only if wall budget remains.

### 4.5 Execution flow

1. Trusted layer verifies the token, schema, sizes and language, and that `problem_key@version` and `test_set` exist
   in the installed pack with a matching digest.
2. Creates the container, writes the source to `/workspace/src`, runs the compile step under compile limits.
3. Makes `/workspace/build` read-only for the run user; clears scratch.
4. For each test in order: runs the program with that test's stdin streamed from trusted memory (never a file the
   code could list in advance); captures stdout/stderr up to the cap; enforces CPU/wall; records exit/signal; wipes
   scratch.
5. Judges each output with the problem's checker (trusted layer).
6. Removes the container; discards captured output; reduces hidden results to the summary.
7. Returns the response; logs one content-free line.

### 4.6 Runner API (internal, `v1`) (P8-S4)

`POST /v1/executions` — idempotent on `execution_id` (a replay within 10 minutes returns the cached content-free
result; the source is never cached).
```json
{"execution_id": "uuid-v4", "kind": "visible|custom|graded",
 "problem_key": "pair-sum-sorted", "problem_version": 3,
 "test_set": "visible|hidden", "language": "cpp17",
 "source": "…≤64 KB…", "custom_input": "…≤16 KB, kind=custom only…"}
```
The request contains **no limits and no expected outputs**: limits come from the pack/profile and cannot be raised by
the caller. Unknown fields → 422. `custom` executes the student's input only and returns its output (no checker).

Response (`200`):
```json
{"execution_id": "…", "runner_version": "1.0.0", "pack_digest": "sha256:…",
 "compile": {"ok": true, "message_trunc": null, "time_ms": 820},
 "verdict": "wrong_answer",
 "visible": [{"id": "v1", "verdict": "accepted", "stdout_trunc": "…", "stderr_trunc": "", "time_ms": 10, "memory_kb": 3100}],
 "hidden_summary": {"groups": {"basic": [6, 6], "edge": [3, 5], "performance": [0, 0]},
                    "first_failure": {"group": "edge", "class": "wrong_answer"},
                    "max_time_ms": 40, "max_memory_kb": 5120},
 "sandbox_error": null}
```
`visible` appears only for `test_set=visible` or `custom`; `hidden_summary` only for `hidden`. Errors: `401` (generic
body), `413`, `422`, `409` (pack digest mismatch), `503` (queue full or not accepting, `Retry-After`). Other
endpoints: `GET /healthz` (no auth; only `ok|degraded`) and `GET /metrics` (bearer `METRICS_TOKEN`, separate from the
runner token).

**Transport and credentials.** HTTPS only (TLS 1.2+); runner firewall accepts 443 only from the backend's egress IPs
(or a private network/tunnel when self-hosted); mTLS where practical (self-hosted backend: yes; Render free: not
available, so token + IP allow-list). Token ≥ 32 random characters, compared in constant time, never logged, never
echoed in errors.
**Rotation:** the runner accepts a token list `RUNNER_TOKENS` (current + next). Procedure: add the new token to the
runner → deploy the backend with the new `CODE_RUNNER_TOKEN` → verify traffic → remove the old token. On suspected
compromise, remove the old token at once (in-flight jobs fail as `error` and are retried). Every rotation is recorded
in the runner admin log (§4.8).

### 4.7 Capacity (pilot proposal, not proven)

Pilot profile: 2 vCPU / 4 GB, **2 concurrent executions**, queue depth 20, one pilot institute. Targets to verify in
8.5c: p95 end-to-end ≤ 8 s (C++/Python) and ≤ 15 s (Java); no host OOM at 2× load for 10 minutes (graceful 503s).
**Capacity review triggers:** queue-wait p95 > 5 s on 3 consecutive days, `rejected` > 2 % of submissions, host CPU
steal > 10 %, or a second institute onboarded. Production capacity is established only by the 8.5c load test and
the pilot.

### 4.8 Runner privacy and administration (P8-S5)

- Logs: one JSON line per execution: `execution_id`, request id, language, kind, verdict, compile/run timings, limit
  hit, pack digest, outcome class. **Never** source, stdin, stdout, stderr, hidden data or tokens.
- Error classes are sanitized enums (`toolchain_missing`, `container_start_failed`, `reaper_timeout`, …).
- No Sentry or other third-party telemetry on the runner. `/metrics` is token-protected; labels `language`, `kind`,
  `verdict`, `outcome` only.
- Source and outputs exist only in tmpfs and process memory for the life of one execution.
- Administrative operations (test-pack install, image update, gVisor update, token rotation, `RUNNER_ACCEPTING`
  toggle, reboot) are done over SSH by named operators and appended to an admin log on the VM (who, when, what,
  digest/version), mirrored to the deployment change record. There is no remote admin API.
- Backend retention (P8-D17): graded and automatic submissions keep their source for the session's lifetime (deleted
  with the session); run and custom submissions keep metadata only once the session report exists (`source` and
  `custom_input` set to null).

### 4.9 Infrastructure, cost and provisioning (P8-S1)

- **Why a new host:** Render free web services cannot run gVisor, privileged or nested runtimes, and would expose the
  runner publicly.
- **Pilot profile (proposal):** one VM, Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, about 40 GB disk, static IP or private
  network to the backend, region near the backend. Docker Engine + pinned gVisor; firewall inbound 443 from backend
  IPs only and SSH from operator IPs only (key-only, no root login); outbound deny-all except a scheduled patch
  window; unattended security updates; disk and memory monitoring.
- **Backend requirement:** a **static egress IP** for the backend (Render free does not guarantee one) or a private
  network/tunnel. Without either, the IP allow-list cannot be applied; the risk must be explicitly accepted or the
  backend moved.
- **Cost:** **not estimated.** No reliable current price was verified in this session, so none is quoted (same rule
  as P7-D9). On request, I can collect current published prices for 2–3 providers before procurement; you choose and
  approve any spend.
- **Nothing is provisioned and no cost is incurred without your explicit approval** (gate G3).
- **Alternatives:** a hosted execution API (student code and the hidden pack go to a third party — separate privacy
  decision P8-S1b), or stay in review-only mode.

### 4.10 Kill switches

`AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED=false` or empty `CODE_RUNNER_URL` (instant review-only); runner
`RUNNER_ACCEPTING=false` (503 for new work); stop the VM (the backend breaker opens). Rollback never deletes student
data.

## 5. Threat model (20 threats)

Actors: A1 student submitting hostile code; A2 attacker with a stolen student account; A3 attacker reaching the runner
endpoint; A4 prompt injection through code or JD text; A5 compromised image or dependency; A6 insider with repository
access. Assets: runner VM and kernel; other students' code and results; Campus AI credentials and data; the hidden
test pack and reference solutions; availability; money.

| # | Threat | Attack path | Preventive control | Detection | Verification | Residual risk |
|---|---|---|---|---|---|---|
| 1 | Container escape | exploit a Docker/containerd/`runsc` bug from inside the container | gVisor user-space kernel; no privileged containers; caps dropped; `no-new-privileges`; non-root; dedicated secret-free VM | host integrity baseline (unexpected processes/files), reaper anomalies | escape suite (§11.5) on every runner release | Low-medium: a gVisor + host bug chain; blast radius limited to the VM |
| 2 | Kernel/runtime vulnerabilities | known CVE in the host kernel, gVisor or Docker | pinned, patched versions; monthly patch window + emergency patch runbook; minimal host packages | CVE review of pinned versions | CI asserts pinned versions; patch checklist | Medium between disclosure and patch |
| 3 | Fork bomb | `while (1) fork()` | `pids-limit`, cgroup kill of the tree, `--init` | `limit_hit=pids` metric | suite: contained, host load normal afterwards | Low |
| 4 | Memory exhaustion | huge allocations, leaks | cgroup memory, swap = memory; JVM `-Xmx` below the cgroup | OOM counter | suite: allocation loop → `memory_limit` | Low |
| 5 | CPU exhaustion | infinite loop, busy threads | `--cpus 1`, per-test CPU + wall, 30 s total, 2 concurrent | total-wall hits; host CPU alert | suite: loop → `time_limit` within limit + 1 s | Low |
| 6 | Disk exhaustion | write until full | read-only rootfs; tmpfs 64 MB; `fsize` 16 MB; host log rotation | host disk alert at 80 % | suite: write loop fails, host disk unchanged | Low |
| 7 | Output flooding | print forever | 64 KB capture per stream, tree killed; backend truncates again for display | `output_limit` metric | suite | Low |
| 8 | Network access / SSRF | connect to the backend, metadata IP, internet, DNS | `--network none`; host firewall drop for bridges; runner has no route to DB/Redis; backend never fetches URLs from code or JD | firewall drop counters | suite: TCP/UDP/DNS/raw socket/metadata all fail | Low |
| 9 | Filesystem traversal | read host files, `/proc`, symlinks out of the workspace | gVisor's own filesystem view; no host bind mounts; read-only rootfs | — | suite: `/etc/shadow`, `/proc/1/*`, symlink tricks | Low |
| 10 | Secret discovery | env, `/proc/self/environ`, cloud metadata, token files | empty env; nothing secret visible in the container; metadata unreachable; runner tokens only in the trusted layer | — | suite: env/metadata probes find nothing | Low |
| 11 | Hidden-test extraction | print inputs, encode them in exit codes or timing, read pack files | pack only in the trusted layer; stdin streamed per test; hidden stdout/stderr/exit codes never returned; summary only; rounded timings | per-student/problem submission-pattern metric | sentinel tests: hidden values never in any response, DB row, log or prompt | Low-medium: a few bits per graded submission via group counts |
| 12 | Test-oracle attack | many submissions varying code to learn hidden cases | 3 per session and 6 per 7 days per problem; duplicate-source short-circuit; group-level counts only | per-student submission metrics | cap and duplicate tests | Low (bounded bits) |
| 13 | Cross-tenant access | read another institute's session/submission/profile by id | tenant + owner filters, forced RLS, composite FKs; runner is tenant-unaware and stateless | 404 counters | SQLite + Postgres cross-tenant tests | Low |
| 14 | Runner API abuse | direct calls, replay, oversized or malformed requests | HTTPS, token, IP allow-list, strict schema, size limits, idempotent `execution_id`, bounded queue | 401/413/422 rates, firewall logs | API tests | Low-medium where only a token protects it (no static backend IP) |
| 15 | Credential theft | token leaked from backend env, logs or CI | token only in host secret stores; never logged; separate tokens for runner, metrics and worker; rotation; no production credentials in CI | unexpected source IPs in runner logs | log-scan test for token patterns; rotation drill (Phase 10) | Low-medium |
| 16 | Resource starvation / noisy neighbour | one student or institute floods executions | per-student rate and daily caps; per-institute monthly cap and concurrency share; bounded queue → `rejected` | queue-wait p95; per-tenant queued counts (backend) | load test with two synthetic tenants | Medium at pilot capacity; capacity review triggers |
| 17 | Malicious compiler flags or dependencies | inject flags, include remote code, install packages, poisoned image | fixed commands per language; no flags in the request schema; no network; images from official bases, digest-pinned, SBOM recorded; no package manager at runtime | image digest check at runner start | CI: extra request fields → 422; digest assertion | Low-medium (upstream base-image compromise) |
| 18 | Persistence across submissions | leave files or processes for the next submission | fresh container per submission; tmpfs destroyed; `--rm`; reaper; no shared volumes | reaper metrics | suite: marker not visible to the next execution | Low |
| 19 | Cleanup failure | container or tmpfs left after a crash | `--rm`; reaper every 30 s; refuse work while leftovers persist; restart re-scan | `runner_orphans` gauge; health `degraded` | fault injection: kill the runner mid-execution | Low |
| 20 | Denial of service against backend or runner | flood run/poll endpoints; a slow runner ties up backend threads | slowapi limits; dispatch only in background jobs; polling backoff; bounded runner queue with `Retry-After`; breaker step `code_execute` | breaker state, 429/503 rates, Phase 7 alerts | rate-limit and breaker tests; load test | Medium on free-tier backend capacity |

AI-specific threats (tested in §11.3): prompt injection through code comments or JD text (objective categories are
never AI-produced; AI scores are clamped and evidence-checked); fabricated company claims (approved sources, claim
filter, disclaimer).

## 6. Data model (three additive migrations)

All new tables: `tenant_id` FK (CASCADE, indexed), `ENABLE + FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy
(copy `a1b2c3d4e5f7_…`), composite FK `(session_id, tenant_id) → mock_interview_sessions(id, tenant_id)` wherever a
session is referenced, dialect-neutral types (SQLite + Postgres), allowed values as CHECK constraints on strings.
**No existing table or column changes. No hidden test data in any table.**

### 6.1 Migration A (8.1): coding

**`mock_interview_code_attempts`**

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id`, `session_id` | int | composite FK to sessions |
| `seq` | int | unique `(session_id, seq)` |
| `problem_key`, `problem_version` | str(80), int | pinned |
| `status` | str(20) | `pending, working, explaining, auto_submitted, closed, abandoned` |
| `language` | str(20) nullable | last used |
| `draft_source` | Text nullable | ≤ 64 KB (app-enforced) |
| `draft_rev` | int default 0 | optimistic concurrency |
| `hints_used`, `runs_used`, `submissions_used` | int default 0 | atomic conditional updates (§3.8) |
| `in_flight_submission_id` | int nullable | one in flight; compare-and-set |
| `best_submission_id` | int nullable | set by the backend (§6.4); app-validated (no FK: circular) |
| `started_at`, `closed_at`, `created_at`, `updated_at` | timestamptz | |

**`mock_interview_code_submissions`** (the table named in the original plan, extended)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `tenant_id`, `session_id`, `attempt_id` | int | composite FKs to sessions and attempts |
| `kind` | str(10) | `visible`, `custom`, `graded`, `auto` |
| `language` | str(20) | CHECK `python313, cpp17, java21, c11` |
| `source` | Text nullable | ≤ 64 KB; nulled for `visible`/`custom` after the report (§4.8) |
| `source_sha256` | str(64) | duplicate detection (§3.9) |
| `custom_input` | Text nullable | ≤ 16 KB; nulled after the report |
| `execution_id` | str(36) unique | = runner idempotency key = ledger `event_id` |
| `idempotency_key` | str(64) nullable | unique `(attempt_id, idempotency_key)` |
| `status` | str(20) | `queued, running, completed, error, not_executed, rejected` |
| `verdict` | str(20) nullable | §3.3 classes |
| `group_results_json` | JSON nullable | `{basic: [p, t], edge: [p, t], performance: [p, t]}` — counts only |
| `first_failure_json` | JSON nullable | `{group, class}` |
| `visible_results_json` | JSON nullable | visible/custom only, truncated |
| `compile_message` | Text nullable | ≤ 8 KB, path-scrubbed |
| `max_time_ms`, `max_memory_kb` | int nullable | rounded |
| `pack_digest`, `runner_version`, `error_class` | str nullable | content-free |
| `retry_count` | int default 0 | automatic retries of `error` |
| `created_at`, `started_at`, `completed_at` | timestamptz | indexes `(tenant_id, created_at)`, `(attempt_id, created_at)`, `(status, created_at)` |

~~Coding report data uses the existing `mock_interview_reports` row.~~ **Corrected 2026-11-23 (O1 = option A,
§14.7):** coding feedback does **not** use `mock_interview_reports` — its `overall_score` is `Float NOT NULL` (CHECK
0–100), and review-only feedback must never carry a score (P8-D7 rules 6–8). Coding feedback is stored in its own
additive table `mock_interview_code_reviews` (§6.1a, migration `a5c7e9b1d3f2`) and served by
`GET/POST /sessions/{id}/coding/feedback`; the existing report routes keep answering coding rounds with their
documented 409. The same table carries a nullable `problem_score` reserved for verified `coding-v1` (8.6).
Explanation Q&A uses the existing `mock_interview_turns` (topic `coding:<seq>:explain`).

### 6.1a Migration a5c7e9b1d3f2 (8.3): coding feedback (O1 = option A)

**`mock_interview_code_reviews`** — one row per attempt (unique `attempt_id`): `tenant_id`; composite FKs
`(session_id, tenant_id)` → sessions and `(attempt_id, tenant_id)` → attempts (CASCADE); `submission_id` (reviewed
graded/auto submission, app-validated); `rubric_version` (`coding-v1-unverified` | `coding-v1`); `status`
(`ai_estimated` | `not_submitted`); `verification` (`not_verified` | `verified`); `language`; `code_quality_score`,
`reasoning_score`, `testing_score` (0–5, NULL = not assessed); `problem_score` (Float, NULL); `estimates_json`
(evidence + rationale per AI category); `feedback_json` (complexity estimate, observations, cases to consider,
testing notes, hints used, explanation answers); `provider`; `created_at`. CHECKs: allowed values; scores 0–5;
**`rubric_version = 'coding-v1-unverified'` ⇒ `verification = 'not_verified'` AND `problem_score IS NULL`**;
`problem_score` only when verified and 0–100. `ENABLE + FORCE ROW LEVEL SECURITY` + `tenant_isolation`.

### 6.2 Migration B (8.4): company profiles

**`mock_interview_company_profiles`**: `id`, `tenant_id`; `name` str(150); `role_title` str(150);
`role_template_key` str(60); `drive_id` int nullable (FK `drives`, SET NULL; same tenant checked in the app);
`summary` Text ≤ 2,000; `job_description` Text ≤ 10,000; `focus_skills_json` (≤ 20 `{name, weight 1–3}`);
`competencies_json` (≤ 8 labels); `rounds_json` (1–4 `{type: coding|technical|hr, difficulty, duration_minutes,
weight}`); `sources_json` (≥ 1 to publish: `{kind: drive|jd|institute_note|public_url, label, url?,
recorded_by, recorded_at}`); `status` (`draft, published, archived`); `created_by_id`, `updated_by_id` (SET NULL);
`created_at`, `updated_at`.

### 6.3 Migration C (8.7): simulations and reservations

**`mock_interview_simulations`**

| Column | Type | Notes |
|---|---|---|
| `id`, `tenant_id`, `student_id` | | composite FK `(student_id, tenant_id) → users` |
| `source_kind` | str(20) | `company_profile, drive, role_template` |
| `company_profile_id`, `drive_id` | int nullable | SET NULL |
| `snapshot_json` | JSON | frozen profile/template (later edits never change it) |
| `mode`, `language` | str | session values |
| `status` | str(20) | `ready, in_progress, between_rounds, completed, abandoned, expired` |
| `current_round` | int | |
| `reserved_units`, `consumed_units`, `released_units` | int | quota reservation (§8.3); CHECK `consumed + released ≤ reserved` |
| `reserved_at`, `reservation_month` | timestamptz, str(7) | `YYYY-MM` (UTC) |
| `status_history_json` | JSON | ≤ 20 entries `{status, at, by: student|system}`, content-free |
| `summary_json` | JSON nullable | aggregate report |
| `expires_at`, `started_at`, `completed_at`, `ended_at`, `created_at`, `updated_at` | timestamptz | index `(tenant_id, status, expires_at)` |

**`mock_interview_simulation_rounds`**: `id`, `tenant_id`, `simulation_id` (composite FK, CASCADE); `seq` (unique per
simulation); `round_type` (`coding, technical, hr`); `difficulty`, `duration_minutes`, `weight`; `session_id` nullable,
**unique** (composite FK to sessions, set at round start; a round never deletes a session); `status` (`pending, active,
completed, skipped, failed, cancelled`); `retry_used` bool.

### 6.4 Best graded submission (P8-D14)

Among an attempt's `graded`/`auto` submissions with status `completed`, the best is the first by this ordering key
(frozen per rubric version, `coding-v1`):
1. basic passed (desc) → 2. edge passed (desc) → 3. performance passed (desc) → 4. total passed (desc) → 5. without
`compile_error` before with → 6. earliest `created_at` → 7. lowest `id`.
AI feedback never takes part in the ordering. All submissions stay in the attempt history; the report states "Score
uses graded submission #2 of 3". If no graded submission completed (all `error` or `not_executed`), the objective
categories are `not_verified`.

### 6.5 Phase 7 tables reused (no schema change)

- `ai_usage_events`: steps `code_hint`, `code_review`, `code_followup`, `jd_skills`, `sim_summary` (LLM) and
  `code_run`, `code_custom`, `code_graded` (execution; tokens 0; `model` = runner version); outcomes add `rejected`,
  `sandbox_error`.
- `ai_feature_controls`: a row with `feature = "mock_interview_coding"` (`paused`; `limits_json` keys
  `student_code_executions_per_day`, `tenant_code_executions_per_month`); `validate_limits` accepts them (narrow only,
  R6/R7).

### 6.6 Retention

Source, custom input, outputs, JDs and profile text are student or institute content: deleted with their session,
student or tenant (CASCADE). Run/custom source and input are nulled after the report (§4.8). Ledger rows hold ids and
counts only (400 days, P7-D5). Expired simulations keep their rows (§8.4).

### 6.7 Reserved values

Session `interview_type` values `company` and `full` stay unused: company rounds are `hr`/`technical` sessions with
company context, and a full simulation is the parent of ordinary sessions.

## 7. C — Coding evaluation and D — company-specific interviews

### 7.1 Coding rubric `coding-v1` (P8-D7 — **APPROVED 2026-11-23**; details §14.5)

| Category | Weight | Kind | Score 0–5 |
|---|---:|---|---|
| `correctness` — objective correctness | 40 % | objective | `5 × basic_passed / basic_total` (best graded submission) |
| `robustness` — robustness / edge cases | 20 % | objective | `5 × edge_passed / edge_total` |
| `efficiency` — time and space | 10 % | objective | `5 × perf_passed / perf_total`; 0 when `correctness < 2.5` |
| `code_quality` — code quality | 15 % | AI estimate | each non-zero score needs ≥ 1 verbatim code quote with its line number |
| `reasoning` — reasoning | 10 % | AI estimate | evidence quotes from the explanation answers (Phase 4 rules) |
| `testing` — testing approach | 5 % | AI estimate | evidence from the explanation or the student's own custom inputs (never hidden data) |
| **Total** | **100 %** | | |

**Rubric versions** (stored in `mock_interview_reports.rubric_version`, `String(20)`): `coding-v1` (objectively
verified) and `coding-v1-unverified` (review-only; exactly 20 characters).

**Deterministic calculation (backend, `coding-v1`):**
1. Objective scores are computed by the backend from `group_results_json` of the best graded submission (§6.4). The
   worker never receives or returns them (schema-enforced); AI output containing objective keys is rejected. AI
   assessments can never override, raise or lower objective results (approved rule 3).
2. AI scores are integers 0–5, clamped; each non-zero score needs valid evidence from the submitted code or the
   candidate's explanation (re-validated as in Phase 4); missing evidence → 0 with "not assessed" (approved rule 2).
3. Arithmetic uses exact decimals: contribution = weight × (score / 5) × 100; raw problem score = Σ contributions;
   problem score = raw rounded half-up to 0.1. Objective scores are not rounded before summing. Session score = mean
   of the assigned problems' rounded scores, rounded half-up to 0.1 — produced **only when no assigned problem is
   `not_verified`** (U3). An unattempted problem (no code saved or submitted) scores 0 "Not attempted" and counts in
   the mean; it is not `not_verified`, because there is nothing to verify. If any assigned problem is `not_verified`,
   **no session score is calculated or displayed** (no partial mean); each verified problem shows its own score and
   the others are marked `not_verified`. Every published problem has all three test groups (U2), so no weight is
   ever redistributed.
4. **Basic-failure cap (approved rule 4, U1 approved 2026-11-23):** when the best graded submission passes **zero**
   tests in the `basic` group, the problem score is `min(problem score, 12.0)`. This is the approved meaning of the
   cap; it is **not** "passes only basic tests". The cap applies to the **total** (after rounding), not only to the AI
   categories (capping only the AI categories at 2/5 would not guarantee ≤ 12, because `robustness` can still score).
   With `basic_passed = 0`, `efficiency` is already 0 by the gate in the table.
5. **Complexity-claim rule:** if the student's stated complexity is contradicted by `time_limit` failures in the
   `performance` group, `reasoning` is capped at 3/5 and the report says why.
6. **Hints:** no score effect; `hints_used` is recorded for analytics and shown for information (approved rule 1).
7. **Versioning:** weights, caps, gates, ordering key and rounding are frozen per version string; a stored report is
   never recomputed under a newer version; the backend mirrors the worker rubric with a contract test (approved
   rule 5).
8. **`not_verified` (approved rules 6–8, P8-D8):** an objective category is `not_verified` (stored as `null`) unless
   the best graded submission was executed by the approved runner and judged against the private hidden test set.
   Review-only rounds use `coding-v1-unverified`: all three objective categories are `not_verified`, **no problem or
   session score is produced** (no overall, no AI-only subtotal), and the three AI categories are shown individually as
   0–5 "AI estimate" with their evidence and an uncertainty note. Nothing is simulated, predicted or inferred from code
   reading: no test result, execution result, correctness or score is ever fabricated (approved rule 7).
9. **Timeouts vs runner faults (U4, approved 2026-11-23):** if the candidate's code reaches the overall execution
   time limit (§4.4), the tests that did not run are counted as `time_limit` failures; the submission is `completed`
   with an objective result. If an infrastructure or runner fault interrupts evaluation, the submission is
   **incomplete** (status `error`, verdict class `sandbox_error`, error class recorded) and **cannot be selected as the
   best graded submission** (§6.4 considers only `completed` submissions). The two are kept distinct in stored status
   and in every report and UI text: a timeout is the candidate's result; a runner fault is never counted against the
   candidate and never turned into a score.

### 7.2 AI review inputs and guards

**Review-only mode (8.3, corrected 2026-11-23):** no code is executed, so **no runner results of any kind exist and
none are sent or invented**. The worker receives only public problem material (title, statement, formats,
constraints, examples, `expected_complexity` as the rubric reference), the latest graded/automatic submission's code
and language, the typed explanation answers and the hint count. Output: AI estimates for `code_quality`,
`reasoning`, `testing` only (evidence re-validated by the backend; unknown keys such as objective categories or
totals reject the whole answer), a complexity estimate quoting a code line, ≤ 3 observations, ≤ 3 "Consider …"
cases (never "missed"), ≤ 3 testing notes; sentences claiming test results or correctness are removed.

**Verified mode (8.6, after G4 — not built):** the worker additionally receives the best graded submission's
**visible** test results, hidden **group counts and first-failure class**, and the student's custom inputs. It returns a complexity estimate with a justification quoting the code, ≤ 3 quality observations
with line numbers, ≤ 3 "consider these cases" notes (phrased as "missed" only when the `edge` group has failures),
testing observations and suggestions. Code and JD text are passed as delimited untrusted data; injection fixtures
(§11.3) must not raise scores above a no-comment baseline.

### 7.3 Explanation turns

After "Done" (for a problem with a graded submission), 1–2 questions (approach, complexity, edge cases, how the
student tested) are generated by `/interview/coding/followup` and answered through the existing `/answer` path.
**Corrected 2026-11-23: explanation turns are typed only — decision B1-a stays in force** (coding rounds are
text-only; `stt` → 422). Spoken explanation turns in voice/video mode need a separate explicit approval. A fixed
question is used when the worker is down; a failed second question closes the problem. Problems closed by time-up
get no explanation turns (their automatic submission is still reviewed).

### 7.4 Role templates (code data, no table)

`backend/app/services/role_templates.py` (8.4): `sde_fresher`, `frontend_developer`, `backend_developer`,
`data_analyst`, `qa_sdet`, `business_analyst` — topic weights, default round mix, coding topics, HR competencies.
Generic and written by us; no company content.

### 7.5 Approved company sources (P8-D10 — **APPROVED 2026-11-23**)

Only: (1) the institute's **open placement drives** (`is_open`; closed drives are not eligible) — this is the only
"institute data" source; (2) **admin/TPO-authored company profiles with source records** (`sources_json`, ≥ 1 to
publish; kinds `drive`, `jd`, `institute_note`, `public_url`); (3) a **JD the student supplies**; (4) generic role
knowledge (role templates). The earlier separate "verified institute profile data" category is **removed** (no such
data exists in Campus AI; qualifying institute data is drive data). **Excluded:** recruiter-owned company `about`
text (`recruiters.about`) and anything a recruiter writes. **Never:** "Company X asks …", leaked or
remembered question lists, interview-experience sites, or invented facts about culture, salary, process or selection
rates.

**Public URLs are never fetched or scraped.** A `public_url` source is a citation the author read; it is shown to
students as **plain text (label and domain only), never as a clickable link**.

**Attribution:** institute-authored content (profile summary, competencies, notes) is shown with the explicit label
**"From your institute"** and is never presented as independently verified company fact. The claim filter applies to
AI-generated text; attributed institute text is displayed as written.

Enforcement (unchanged): source records on every profile; prompt rules; job descriptions and profile text passed to
the AI as delimited untrusted data (prompt-injection protection); a mandatory worker claim filter that removes
company-claim sentences; a mandatory disclaimer on every company round and report ("Questions are generated in the
style of the role and the information your institute provided. They are not actual questions from <company>."); no
JD or profile text in logs, metrics, the usage ledger, Sentry or Langfuse (R1/R2/R5); sentinel tests.

### 7.6 Profile management (P8-D9 — **APPROVED 2026-11-23**)

Company profiles are **institute-owned**. Admin and TPO of the same institute create, edit, publish and archive them
(either role may publish; no second-person review). Teachers and recruiters have **no profile access** in Phase 8
(403, read and write); another institute → 404. Admin and TPO may **create a profile from an open placement drive of
their own institute** ("Create from drive" pre-fills role, JD and skills; a closed drive or another institute's drive
→ 404). Every write is audited (`mock_interview.company_profile_{create,update,publish,archive}`) with a **content-free
summary**. Profiles are archived, never deleted. Students see **only published profiles of their own institute**
(drafts and archived profiles → 404) and the open drives of their own institute.

### 7.7 JD ingestion and skill mapping

Worker `/interview/jd/skills`: JD (≤ 10,000 chars) → ≤ 25 `{skill, category, importance 1–3, evidence}`; any item
without a verbatim JD quote is dropped. The backend maps skills to the student's verified skills through a static alias
table → `matched`, `missing`, `focus_order`, passed to the existing plan request as an additive optional
`focus_skills` field and used for coding-topic choice. The setup page shows the mapping before start.

### 7.8 Question strategy, difficulty and transparency (P8-D11 — **APPROVED 2026-11-23**)

**Company context influences question generation only.** Technical: topic weights from focus skills ∪ role template,
with the existing adaptive level. HR: profile competencies drive behavioural questions. Coding: problems whose topics
intersect the focus skills.

**Evaluation is unchanged:** the evaluator receives the same payload as a generic interview (transcript and the
existing config) and uses the existing `hr-v1` / `technical-v1` rubrics unchanged. There is **no company-fit score
and no competency score**. The Phase 6.5 report-parity tests are extended to prove the evaluation payload and stored
report are identical with and without a company profile.

**Storage:** the profile reference (`company_profile_id`) and a **frozen copy** of the profile used are stored in the
session's `plan_json`; no session column is added. Later profile edits never change a past session.

**Transparency:** before start the student sees the rounds, durations, rubric categories and weights, which parts are
objective and which are AI estimates, the profile's sources (plain text) with "From your institute" attribution, and
the disclaimer; the report repeats them.

## 8. E — Full interview simulation

### 8.1 Flow

```
choose: company profile | open drive | role template → preview (rounds, rubric, sources, quota needed)
  → create (atomic reservation of all rounds; status ready)
  → round k start → creates an ordinary session (type = round type, mode = simulation mode) → existing room
  → round report → between_rounds (resume any time before expires_at)
  → … last round report → summary report → completed
```

### 8.2 Rules

Rounds run in order. A round may be skipped (score 0, "Skipped"). One active session per student (unchanged). One mode
for the whole simulation; video gates are checked at creation, and if video access is withdrawn mid-simulation the
remaining rounds continue (Phase 6 D12). A `failed` round may be retried once, using a **spare unit** reserved at
creation if the quota allowed one; otherwise no retry is offered.

### 8.3 Atomic quota reservation (P8-D12)

- **Reserve at creation:** in one transaction, take the existing per-student lock and the Phase 7 institute row lock
  (`FOR NO KEY UPDATE`; SQLite's single-writer lock in tests), compute usage, and insert the simulation with
  `reserved_units = rounds` (+1 spare if available). If any limit would be exceeded, nothing is inserted — no partial
  reservation — and the student sees the limit message.
- **Usage counting:** student daily and institute monthly usage = standalone sessions (sessions not linked to a
  simulation round) + Σ(`reserved_units − released_units`) of simulations whose `reserved_at` / `reservation_month` is
  in the window. Round sessions are never counted twice.
- **Concurrency:** concurrent creations serialize on the institute row lock; a Postgres concurrency test (as in Phase
  7.6) proves the institute limit is never exceeded.
- **Later rounds:** exempt from quota and pause checks (extends R8); each started round increments `consumed_units`.
- **Release policy:** abandon → `released_units = reserved − consumed` immediately; expiry → the same at expiry;
  completion → unused spare released; skip → that round's unit released. Released units return capacity only to the
  window they were reserved in (the same 24-hour start or `reservation_month`); nothing is credited to later windows.
- **Institute pause:** blocks new simulations only; running simulations continue.

### 8.4 Expiry, cleanup and history (P8-D13)

A simulation not completed within **7 days** of creation becomes `expired`. Expiry is applied lazily on any read or
action (like `expire_if_overdue`) and by an optional manual sweep script (dry-run by default, `--apply`, prints
per-tenant counts; never scheduled automatically in Phase 8). On expiry: an active round session is ended through the
existing `time_up` path so it can still be reported; pending rounds → `cancelled`; reservations released; completed
rounds and their reports are kept; `status_history_json` gains `{expired, at, by: system}`. The student sees
"Expired — completed rounds and their reports are kept" and a "Start a new simulation" action. Nothing with content is
written to audit logs; staff-visible operational counts come from the Phase 7 usage queries.

### 8.5 Compatibility and summary report

Each round's session, turns, report and history row are exactly as today; history items gain an additive optional
`simulation` field. The summary is computed by the backend: weighted mean of round scores (skipped = 0; review-only
coding rounds excluded from the mean and flagged), per-round links, merged strengths and weaknesses, and an optional
short AI narrative generated only from the round reports (labelled AI-generated). Disclaimer: practice scores do not
predict selection, and company style is not the actual company process.

## 9. Engineering design

### 9.1 Feature flags (P8-D4) — all default OFF; the environment is the upper bound

| Layer | Flag | Off ⇒ |
|---|---|---|
| Backend | `AI_MOCK_INTERVIEW_CODING_ENABLED` | coding hidden; coding routes 404; create `coding` → 422 |
| Backend | `AI_MOCK_INTERVIEW_CODING_TENANT_IDS` | **fail-closed**: empty = no institute |
| Backend | `AI_MOCK_INTERVIEW_CODING_AI_ENABLED` (added 8.3) | no AI hints, no explanation turns, coding feedback POST 409 (Batch 1 behaviour) |
| Backend | `AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED` | review-only (no runner call) |
| Backend | `CODE_RUNNER_URL`, `CODE_RUNNER_TOKEN` | empty URL = review-only; production refuses a URL without a ≥ 32-char token |
| Backend | `AI_MOCK_INTERVIEW_COMPANY_ENABLED` | company routes 404 |
| Backend | `AI_MOCK_INTERVIEW_SIMULATION_ENABLED` | simulation routes 404 |
| Backend | `AI_MOCK_INTERVIEW_TENANT_MONTHLY_CODE_EXECUTION_MAX`, `AI_MOCK_INTERVIEW_STUDENT_DAILY_CODE_EXECUTION_MAX` | empty = built-in default (150/day), no monthly cap |
| Worker | `MOCK_INTERVIEW_CODING_ENABLED`, `MOCK_INTERVIEW_COMPANY_ENABLED` | routes not mounted |
| Runner | `RUNNER_TOKENS`, `RUNNER_ACCEPTING`, `RUNNER_MAX_CONCURRENCY`, `RUNNER_QUEUE_MAX`, `METRICS_TOKEN` | separate service |
| Frontend | `NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED`, `NEXT_PUBLIC_MOCK_INTERVIEW_SIMULATION_ENABLED` | UI hidden (Dockerfile `ARG`s only with your approval) |

New backend flags are read only behind `AI_MOCK_INTERVIEW_ENABLED` and the tenant allow-list. Institute controls only
narrow (R6/R7): an institute row can never enable a feature the environment disables, or add itself to an allow-list.

### 9.2 Backend APIs (all new, `/v1/mock-interview`, also unversioned)

Student (owner + tenant; 404 otherwise):

| Method + path | Purpose | Limit |
|---|---|---|
| `GET /coding/catalog` | languages, difficulties, topics, limits (no problems) | — |
| `POST /sessions` (existing) | `interview_type: "coding"` + optional `coding: {languages[], topics[], problem_key?}` | existing |
| `GET /sessions/{id}/coding` | assigned problems (public fields only), attempts, counters, remaining limits, review-only flag | — |
| `PUT /sessions/{id}/coding/attempts/{seq}/draft` | `{language, source, draft_rev}` | 30/min |
| `POST /sessions/{id}/coding/attempts/{seq}/runs` | `{language, source, custom_input?, idempotency_key}` → 202 | 10/min |
| `POST /sessions/{id}/coding/attempts/{seq}/submissions` | graded → 202 | 3/min |
| `GET /sessions/{id}/coding/submissions/{sid}` | status + student-safe result | 60/min |
| `GET /sessions/{id}/coding/attempts/{seq}/submissions` | attempt history (metadata, verdicts, which one is best) | — |
| `POST /sessions/{id}/coding/attempts/{seq}/hints` | next hint | 5/min |
| `POST /sessions/{id}/coding/attempts/{seq}/done` | start explanation turns | 20/min |
| `GET /company/role-templates`, `GET /company/profiles`, `GET /company/profiles/{id}` | published, own institute | — |
| `POST /company/jd-skills` | `{job_description}` \| `{drive_id}` \| `{profile_id}` → skill map | 5/min |
| `POST /simulations`, `GET /simulations`, `GET /simulations/{id}` | create (atomic reservation) / list / detail | create = existing create limits |
| `POST /simulations/{id}/rounds/{seq}/start` \| `/skip` \| `/retry` | round actions | 10/min |
| `POST /simulations/{id}/abandon`, `GET /simulations/{id}/report` | | existing report limit |

Staff (admin + TPO, own institute, audited): `GET/POST /company/profiles/admin`, `PUT /company/profiles/{id}`,
`POST /company/profiles/{id}/publish|archive`, `POST /company/profiles/from-drive/{drive_id}`.

Admin ops (Phase 7 router, additive): the usage summary gains optional `code_executions`, `coding_sessions` and
`simulations` counts; controls accept the coding feature row.

**Changes to existing responses (P8-D16, additive optional fields only):** `/options.interview_types` may include
`coding` (flag on only); session detail gains optional `coding_summary` and `simulation`; the report gains optional
`coding`; history items gain optional `simulation`. Nothing is removed, renamed or retyped; an OpenAPI snapshot test
proves existing shapes are unchanged with the flags off.

### 9.3 Backend modules

New: `services/mock_interview_coding.py`, `services/code_runner.py` (client, breaker step `code_execute`, ledger),
`services/coding_bank.py` (public-bundle loader/validator that rejects any hidden-test field),
`services/mock_interview_coding_report.py`, `services/mock_interview_company.py`, `services/role_templates.py`,
`services/mock_interview_simulation.py`; routers `mock_interview_coding.py`, `mock_interview_company.py`,
`mock_interview_simulation.py` (added once to `FEATURE_ROUTERS`, `apply_tenant_guc`); models and schemas per area;
`scripts/expire_simulations.py`. Modified (additive): `config.py`, `schemas/mock_interview.py`,
`services/mock_interview.py` (coding branch; hr/technical unchanged), `services/mock_interview_report.py` (dispatch),
`services/mock_interview_policy.py` (coding limits, reservation-aware counts), `jobs.py`, `models/__init__.py`,
`main.py`. The backend has **no checker for any test**: the runner judges visible and hidden tests, so verdicts have a
single source.

### 9.4 AI worker (stateless; never executes code)

New `ai-worker/interview/coding/` (`router.py` with `/interview/coding/hint`, `/review`, `/followup`; `prompts.py`,
`schemas.py`, `filters.py`) and `interview/company.py` (`/interview/jd/skills`, claim filter). Every call uses
`tracing_disabled()`, `untraced_config()` and `usage_scope()` (R1 and the 7.2 usage block). Additive optional
`focus_skills` / `competencies` / `company_context` on the plan/turn config (`extra="ignore"`).

### 9.5 Frontend structure

```
src/app/dashboard/mock-interview/page.tsx                 (existing; routes coding sessions to the coding room)
src/app/dashboard/mock-interview/company/page.tsx
src/app/dashboard/mock-interview/simulations/[id]/page.tsx
src/app/dashboard/interview-company-profiles/page.tsx     (admin/TPO; outside /mock-interview so the camera policy never applies)
src/components/mock-interview/coding/     CodingRoom  ProblemPanel  CodeEditor (lazy CodeMirror | textarea)  LanguageSelect
                                          RunResults  SubmissionHistory  HintPanel  ReviewOnlyNotice  codingApi.ts  codingFlow.ts
src/components/mock-interview/company/    ProfilePicker  SkillMap  CriteriaPreview  companyApi.ts
src/components/mock-interview/simulation/ SimulationOverview  RoundStepper  SimulationReport  simulationApi.ts
src/components/interview-company-profiles/ ProfileForm  ProfileList  SourcesEditor
```
Video mode wraps the coding room in the existing `VideoInterviewWorkspace`; static privacy guards cover the new
folders. Polling: 1 s backing off to 3 s; stop at a terminal status or after 60 s.

### 9.6 Authorization and isolation

Every query filters `tenant_id` and ownership; RLS backs it up; composite FKs block cross-tenant rows; another
institute's drive or profile → 404; problem access is limited to the session's assigned attempts; the runner receives
no tenant or user identifiers; staff access to student code is not in Phase 8.

### 9.7 Logging, privacy, observability

Source, custom input, outputs, hidden data, JDs and profile text never appear in logs, metrics, ledger, alerts, Sentry
or Langfuse (R1, R2, R5 extended). Correlation: `X-Request-ID` + `X-Execution-ID` to the runner. Metrics (no tenant
labels): backend `code_executions_total{language,kind,outcome}`, `code_execution_seconds`, `code_queue_wait_seconds`;
runner `runner_executions_total{language,verdict}`, `runner_busy`, `runner_rejections_total`,
`runner_sandbox_errors_total`, `runner_orphans`. Alerts (existing throttled path): runner unreachable, `sandbox_error`
> 5 %, queue saturation, orphan growth. Runbooks (8.8): runner down, suspected compromise (stop the VM, rotate the
token, rebuild from image), queue saturation, problem defect (disable a key), abuse/cost.

### 9.8 Performance and failure handling

| Failure | Behaviour |
|---|---|
| Runner unreachable / 5xx / timeout | submission `error`; breaker `code_execute`; auto-retry ≤ 2; count restored; "Your code is saved; we couldn't run it" |
| Breaker open / queue full | `rejected`, not counted, retry hint; review-only banner after repeated rejections |
| Runner crash mid-execution | backend sweep re-dispatches `queued/running` older than 2 min (idempotent `execution_id`) |
| Time up with the runner down | automatic submissions stored; the report waits up to 10 min (re-dispatch), then objective categories are `not_verified` and one later re-grade is offered |
| Worker (LLM) down | hints: authored only; review: the Phase 4 retry path; explanation: existing fallbacks |
| Public bundle invalid at startup | coding disabled with a startup log error; the rest is unaffected |
| Pack digest mismatch | runner 409 → `sandbox_error` + alert; the problem is disabled for execution until fixed |

Targets: editor chunk ≤ 300 KB gzip, lazy; draft save p95 ≤ 300 ms; no extra query on hr/technical paths.

### 9.9 Accessibility and responsive UI

WCAG 2.2 AA target: visible focus; documented focus exit (Escape then Tab); "Simple editor" option; font-size control;
app-token themes; Ctrl/Cmd+Enter run and Ctrl/Cmd+Shift+Enter submit, each also a button; results in a semantic table
with an `aria-live="polite"` summary; timer `role="timer"` with milestone-only announcements; targets ≥ 44 px.
≥ 1024 px: split view; < 1024 px: tabs (Problem / Code / Results); no Mock Interview overflow at 320/390/768/1366 px;
a desktop is recommended for coding, nothing is blocked on mobile.

## 10. Sub-phases, prerequisites and sequence

Tags: **[pre-VM]** can be built and tested before any sandbox host exists · **[needs-VM]** requires the provisioned
runner VM · **[decision]** requires the listed approvals · **[cost]** requires procurement and cost approval.

| # | Scope | Migration | Prerequisites | Tags |
|---|---|---|---|---|
| **8.0** | Spec r1 + r2, plan and state | — | — | done (docs) |
| **8.1** | Coding foundations: flags; public-bundle format, loader and validator (rejects hidden fields); drafted problems (public parts, status `draft`); migration A; coding create; attempts, drafts, server-side limits; submissions as `not_executed`; `/coding/catalog`, `/sessions/{id}/coding`; a fake runner implementing the §4.6 contract for tests | A | G1 | [pre-VM] [decision] |
| **8.2** | Coding UI in review-only mode: room, editor, Submit (Run disabled with an explanation), timer, history, a11y, responsive, privacy guards | — | 8.1; P8-D5 | [pre-VM] [decision] |
| **8.3** | AI coding evaluation, unverified: worker hint/review/followup, filters, `coding-v1-unverified`, explanation turns, report and history rendering | — | 8.2 | [pre-VM] [decision] |
| **8.4** | Company: role templates, migration B, profile CRUD + audit, drive-derived profiles, JD skills + alias map, `focus_skills`, claim filter, authoring UI, student picker | B | G1 | [pre-VM] [decision] |
| **8.5a** | Private test-pack repository and format; hidden tests and reference solutions drafted; **human review** of statements and references | — | P8-S7; a reviewer you name | [pre-VM] [decision] |
| **8.5b** | Runner service code (`code-runner/`), executor, limits, reaper, metrics, escape/conformance suite, language images, Linux CI job (synthetic fixtures only); developed against a local Linux/gVisor environment, **not connected to the backend** | — | G2 | [decision] |
| **8.5c** | Provision the pilot VM, harden it, install the runner and pack, run the escape suite and load test on it | — | G3 | [needs-VM] [cost] |
| **8.6** | Execution integration: `code_runner.py`, async dispatch, re-dispatch sweep, breaker, ledger, quotas, tenant concurrency share, verified `coding-v1`, best-submission selection, auto-submit at time-up | — | G4 | [needs-VM] |
| **8.7** | Full simulation: migration C, atomic reservation, orchestrator, expiry script, summary report, UI, history field | C | 8.3, 8.4 (8.6 optional: review-only coding rounds are flagged) | [pre-VM] [decision] |
| **8.8** | Hardening: full backend suite, worker suite, Postgres RLS, runner load test, sentinel scans, a11y/responsive pass, runbooks, docs, acceptance record | — | all built parts | mixed |

**Can start before the VM exists (after G1):** 8.1, 8.2, 8.3, 8.4, 8.5a, 8.5b (local/CI only, after G2), 8.7.
**Needs the VM:** 8.5c, 8.6. **No untrusted student code is executed anywhere before G4.**

## 11. Test plan

### 11.1 Backend (SQLite offline; Postgres opt-in)

- Flags off ⇒ 404s, and unchanged OpenAPI for existing routes (snapshot).
- Coding: create/validation, ownership/tenant 404s, problem access limited to assigned attempts, attempt state
  machine, draft 409, atomic counters under concurrent requests (threaded on SQLite, real concurrency on Postgres),
  one in flight, idempotency, `error`/`rejected` not counted, duplicate-source short-circuit, cross-session
  per-problem cap, time-up auto-submit, R8.
- Scoring: objective categories derived only from group counts (property test); worker output containing objective
  keys rejected (mutation test); basic-failure cap; complexity-claim cap; hints do not change the score;
  best-submission ordering and tie-breaks; `not_verified` gives no overall score; rubric version frozen.
- Hidden-data guards: the bundle loader rejects hidden fields; a sentinel planted in a fake runner's hidden data never
  appears in any API response, DB row, log line or worker payload.
- Company: RBAC matrix, audit rows, publish requires a source, same-tenant drives only, JD evidence validation.
- Simulation: atomic reservation (concurrent creates on Postgres never exceed the institute limit; no partial rows),
  no double counting, release on abandon/expiry/skip, expiry ending an active round, retry rules, history field.
- Migrations A/B/C up/down/up on SQLite; Postgres RLS for all new tables as `app_user`.
- One full SQLite run at 8.1, 8.6, 8.7 and 8.8 (never two at once).

### 11.2 AI worker (stubbed LLM)

Hint filter; no hidden data in any captured prompt; JD grounding; claim filter; R1 (no callbacks); usage block;
contracts with the backend.

### 11.3 Adversarial AI fixtures

Injection in code comments and JD text; full-solution requests in custom input; company-claim bait. Structural
assertions (scores within baseline, no solution, no claim sentence).

### 11.4 Frontend

`node --test` for `codingFlow.ts`, simulation state and formatting; review-only mode shows no score and no run output;
privacy guards extended; tsc and lint; simulated-browser smoke with the fake runner in text, voice and video modes;
keyboard-only and screen-reader rows added to the Phase 10 checklist.

### 11.5 Sandbox (P8-S6)

**Main-repository Linux CI job** (no secrets, synthetic data only): installs the pinned gVisor release; asserts the
executor refuses to run without `runsc`; inspects container configuration (not privileged, caps dropped, read-only,
`no-new-privileges`, no network, limits present); runs the escape/abuse suite: infinite loop, busy threads, fork bomb,
memory bomb, huge stdout/stderr, write loops, root and `/tmp` writes, `/etc/shadow`, `/proc/self/environ`, `/proc/1/*`,
env dump, TCP to a host IP, public IP, DNS, UDP, raw socket, metadata IP, `ptrace`, `mount`, `unshare`, setuid
attempts, sleep past wall, orphans and zombies, symlink tricks, marker persistence across executions, 41 tests,
oversized body, extra request fields, bad token, replay, killing the runner mid-execution (cleanup). Hidden-test
privacy: a synthetic pack with sentinel values; no sentinel may appear in any response or log line.
**Private test-pack repository CI:** every reference solution passes every test in every language within 50 % of its
limit; slow references fail `performance`; the digest manifest is generated. Neither CI holds production credentials
or real user data.
**On the VM (8.5c):** the same suite plus a 10-minute 2× load test; results recorded in this spec before G4.

### 11.6 Static guards

No `subprocess`, `os.system`, `exec`, `eval`, `compile` or `pty` in backend or worker modules that handle student
code; no hidden-test fields in the public bundle (loader + CI check); no code written to `localStorage`; Phase 6 media
guards on the new frontend folders.

## 12. Acceptance criteria

1. With all new flags off, existing behaviour, responses and the full backend suite are unchanged.
2. A coding round completes in text, voice (spoken explanation) and video mode; a refresh resumes code and timer.
3. No code runs anywhere except the runner (static guard and review).
4. The escape/abuse suite passes 100 % on the deployed runner; any failure blocks release.
5. Hidden inputs, expected outputs and hidden execution output never reach the browser, the backend database, the LLM,
   logs, ledger or reports (sentinel tests); this repository contains no hidden tests or reference solutions.
6. Objective categories come only from verified runner results and the AI cannot change them; `basic_passed = 0` ⇒
   problem score ≤ 12/100; hints never change the score.
7. Review-only mode: the editor works, AI estimates are labelled, objective categories are `not_verified`, there is
   no overall score and no simulated execution output.
8. Limits hold under concurrent requests; exhausted limits return stable 409/429 codes; runner failures never consume
   limits or lose code.
9. The best graded submission is selected deterministically and shown; all submissions stay in the history.
10. Company rounds use only approved sources; publishing requires a source; claim filter and disclaimer are present;
    the RBAC and audit matrix passes.
11. Simulation reservation is atomic, never exceeds institute limits under concurrency, and is released per policy;
    expiry keeps completed rounds and shows the documented status.
12. Every AI step and execution writes exactly one content-free ledger row with the request id.
13. RLS cross-tenant tests pass on Postgres for all new tables.
14. Accessibility: a keyboard-only coding round; screen-reader announcements; no overflow at 320/390/768/1366 px
    (real devices in Phase 10).

## 13. Risks and dependencies

| Risk | Impact | Mitigation |
|---|---|---|
| No host, or no static backend egress IP | no execution, or weaker runner access control | review-only first; private network/tunnel; explicit risk acceptance, or the hosted API (privacy decision) |
| gVisor compatibility (JVM; cgroups under rootless Docker) | Java slow or failing | conformance suite; JVM flags; multipliers; drop Java only with your approval |
| Sandbox 0-day | VM compromise | secret-free isolated VM; rebuild runbook; token rotation |
| Test-pack quality | unfair scores | reference solutions in every language pass in private CI; human review; per-problem disable |
| Test-pack leak (private repo or VM) | hidden tests known | access limited to named maintainers; pack only on the VM; digest-versioned so a leaked pack can be replaced |
| Copyrighted problems | legal | `source: original` + reviewer record |
| Token budgets too small for new AI steps | institutes hit caps | per-step ledger visibility; review ceilings before a pilot |
| Free-tier backend (no Redis, sleeping instances) | lost dispatches, slow polling | background dispatch + re-dispatch sweep; Redis recommended for any execution pilot |
| Company content mistaken for official | reputational/legal | approved sources, claim filter, disclaimers, audit |
| New frontend dependency | supply chain, bundle size | exact pins, lazy load, audit |
| Real-device backlog (Phases 5, 6, 8) | late surprises | Phase 8 rows added to the Phase 10 checklist |

**External dependencies:** your approvals (§14, §15); a reviewer for problems and reference solutions; a private
repository for the test pack; the runner VM, its network path and cost approval; Redis for an execution pilot
(recommended).

## 14. Decision register

Statuses: **Recommended — awaiting user approval** · **Accepted** (only after your explicit approval) · **Rejected**
(only after your explicit rejection).

**G1 approval (2026-11-23, user):** P8-D1, D2, D3, D4, D5, D6, D8, D14, D15, D16 and D17 are **Accepted** for Batch 1
(8.1 + 8.2), together with the Batch 1 decisions in §14.4 and the narrower G1 scope (§15). P8-D16 was approved as
"controlled coding request-schema changes, including the coding interview type and duration requirements; existing
clients must remain compatible; avoid unnecessary changes to existing response contracts". P8-D5 includes permission
to install the minimal exact-pinned CodeMirror 6 packages; the coding build `ARG` in `frontend/Dockerfile` is
authorized (staged separately from the user-owned Phase 5 lines). P8-D7, D9–D13 and every sandbox decision (P8-S1…S7,
S1b) remain **Recommended — awaiting user approval**. Nothing has been rejected.

### 14.1 Product and engineering

| # | Decision | Recommendation (r2) | Status |
|---|---|---|---|
| P8-D1 | Languages | Python 3.13, C++17, Java 21, C11; JavaScript as a later expansion | **Accepted (G1, 2026-11-23)** |
| P8-D2 | Problem bank | 24 original problems; public bundle (statements, constraints, examples, visible tests, editorials when appropriate) in this repository; hidden inputs/outputs, reference solutions, calibration and private scoring metadata only in the runner-private pack (P8-S7); protection rules §3.2; human review before publication; access limited to assigned attempts | **Accepted (G1, 2026-11-23)** |
| P8-D3 | I/O style and errors | stdin/stdout; function style later; per-class handling §3.3 | **Accepted (G1, 2026-11-23)** |
| P8-D4 | Flags | §9.1, all default OFF; the environment is the upper bound; institutes only narrow; coding allow-list fail-closed | **Accepted (G1, 2026-11-23)** |
| P8-D5 | Editor | CodeMirror 6, exact pins, minimal packages, lazy-loaded on coding pages only, keyboard-accessible, textarea fallback | **Accepted (G1, 2026-11-23)** |
| P8-D6 | Limits | 30 runs, 3 graded submissions, 2–3 hints per problem; 30/45/60-min rounds; atomic server enforcement, rate limits, per-session/student/institute safeguards, race protection, bounded resources, exhausted-limit behaviour (§3.8) | **Accepted (G1, 2026-11-23)** |
| P8-D7 | Rubric `coding-v1` | 40 / 20 / 10 objective + 15 / 10 / 5 AI estimates; deterministic and versioned (`coding-v1`, `coding-v1-unverified`); 12/100 cap; no hint penalty; evidence for AI scores; AI never overrides objective results; `not_verified` when unexecuted (§7.1, §14.5) | **APPROVED (2026-11-23)**; semantics U1–U4 approved 2026-11-23 (§14.5) |
| P8-D8 | Review-only mode | editor available; "AI estimate" labels; no overall score; test categories "not verified"; no simulated execution | **Accepted (G1, 2026-11-23)** |
| P8-D9 | Profile authoring | institute-owned profiles; admin + TPO create/edit/publish/archive, incl. from their own institute's open drives; every write audited with content-free summaries; teachers and recruiters no access; students see only their institute's published profiles (§7.6) | **APPROVED (2026-11-23)** |
| P8-D10 | Approved company sources | open placement drives (the only institute-data source; separate "verified institute profile data" category removed), admin/TPO profiles with source records, student JDs, generic role templates; recruiter `about` text excluded; URLs never fetched, shown as plain-text label + domain; "From your institute" attribution; claim filter, disclaimer, prompt-injection and telemetry-privacy rules mandatory (§7.5) | **APPROVED (2026-11-23)** |
| P8-D11 | Company context | question generation only; evaluation payload and `hr-v1` / `technical-v1` unchanged; no company-fit or competency score; profile reference + frozen copy in `plan_json`, no session column (§7.8) | **APPROVED (2026-11-23)** |
| P8-D12 | Simulation quotas | one unit per round; atomic all-round reservation at creation; no partial reservation; concurrency-safe; later rounds exempt; release policy §8.3 | Recommended — awaiting user approval |
| P8-D13 | Simulation expiry | 7 days; lazy expiry + manual sweep script; completed rounds kept; documented student status; content-free status history | Recommended — awaiting user approval |
| P8-D14 | Which graded submission counts | the **best** objectively graded submission, deterministic ordering and tie-breaks (§6.4); all attempts kept; the chosen one shown | **Accepted (G1, 2026-11-23)** |
| P8-D15 | Proctoring | none in Phase 8 (no face monitoring, no tab/paste tracking) | **Accepted (G1, 2026-11-23)** |
| P8-D16 | API compatibility | only the additive optional fields in §9.2 | **Accepted (G1, 2026-11-23)** |
| P8-D17 | Source retention (new in r2) | graded/auto source kept for the session lifetime; run/custom source and input nulled once the report exists | **Accepted (G1, 2026-11-23)** |

### 14.2 Sandbox security

| # | Decision | Recommendation (r2) | Status |
|---|---|---|---|
| P8-S1 | Infrastructure | dedicated Ubuntu 24.04 VM, 2 vCPU / 4 GB as a **pilot profile** (not proven capacity), 2 concurrent executions, load test and capacity review triggers (§4.7); no provisioning or spend without approval; no cost quoted until reliable current prices are collected | Recommended — awaiting user approval |
| P8-S1b | Hosted execution API alternative | only with a separate privacy decision (student code and the hidden pack go to a third party) | Not recommended unless S1 is impossible — awaiting user decision |
| P8-S2 | Runtime isolation | Docker + gVisor `runsc` only; hardening §4.3; trusted/untrusted separation §4.2; Judge0 ≥ 1.13.1 only as a fallback on the dedicated VM | **APPROVED 2026-11-24 with Judge0 removed** (§14.8) |
| P8-S3 | Limits | §4.4 per language (2 s CPU baseline, 256/512 MB profiles, 64 pids, 64 KB output, 30 s per submission, no network); process-tree kill | **APPROVED 2026-11-24** as ceilings; calibration may only tighten (§14.8) |
| P8-S4 | Backend ↔ runner | HTTPS; ≥ 32-char token; constant-time compare; IP allow-list; private network or mTLS where practical; rotation procedure; no credentials in logs or errors; strict schema; identifiers instead of expected outputs | Recommended — awaiting user approval |
| P8-S5 | Runner privacy and observability | content-free logs; no Sentry; protected `/metrics`; sanitized error classes; audited admin operations; no source retention on the runner | **APPROVED 2026-11-24** (§14.8) |
| P8-S6 | Linux security CI | main-repository job (gVisor, isolation configuration, network/filesystem/limits/cleanup/truncation/hidden-data sentinels, escape attempts; synthetic only) + private-pack CI (reference solutions); no production credentials or user data; changes `.github/workflows/ci.yml` | **APPROVED 2026-11-24** (§14.8); job written, **not yet executed** (needs a push/PR approval) |
| P8-S7 | Private test-pack storage (new in r2) | a separate private Git repository (named maintainers) building digest-addressed packs, installed read-only on the runner VM by an operator; never in this repository | **APPROVED 2026-11-24** (§14.8); local-only repository created, no remote |

### 14.4 Batch 1 decisions (accepted with G1, 2026-11-23)

| # | Decision | Status |
|---|---|---|
| B1-a | Coding sessions are text-only in Batch 1; `mode` voice/video with `interview_type: coding` → 422 (voice/video explanation turns arrive with 8.3) | **Accepted (G1, 2026-11-23)** |
| B1-b | Development-only setting `AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS` (default off) lets `draft` problems be selected; ignored (and logged) when `ENVIRONMENT=production`, so production only ever serves `published` problems | **Accepted (G1, 2026-11-23)** |
| B1-c | No Run endpoint, runner client or fake runner in 8.1; execution (and the run limits) move to 8.6 | **Accepted (G1, 2026-11-23)** |

### 14.5 P8-D7 approval record (2026-11-23)

**Status: APPROVED** by the user, 2026-11-23 ("approved proceeding with the proposed P8-D7 scoring rubric").

Approved weights: objective correctness 40 %, robustness / edge cases 20 %, efficiency 10 %, code quality 15 %,
reasoning 10 %, testing approach 5 % (total 100 %).

Approved rules (as given):
1. No automatic score penalty for using hints; hint usage may be recorded for analytics only.
2. AI-generated subjective assessments must include supporting evidence from the submitted solution or candidate
   explanation.
3. AI assessments must never override objective test results.
4. "A solution that passes only basic tests must have a maximum score of 12/100." (see U1)
5. Evaluation must be deterministic and versioned; the rubric version is recorded.
6. When code has not been executed and objectively verified, status must be `not_verified`.
7. Do not fabricate test results, execution results, correctness or scores.
8. Before the secure runner and private hidden-test infrastructure are approved and available, no verified
   correctness and no overall verified score are claimed; qualitative AI feedback may be given with appropriate
   uncertainty.

Also part of the approved proposal (§7.1): the efficiency gate (0 when `correctness < 2.5`), the complexity-claim cap
(`reasoning` ≤ 3/5), session score = mean over assigned problems (unattempted = 0), missing AI evidence → 0 "not
assessed", best-graded-submission selection (P8-D14), exact-decimal arithmetic and half-up rounding to 0.1.

**Effect on Batch 2:** 8.3 implements only `coding-v1-unverified` (no score, AI estimates per category). The
`coding-v1` total, the cap and the verification rules are implemented in 8.6; U2's publication check lands with the
private test pack (8.5a).

**Scoring semantics U1–U4 — RESOLVED and APPROVED by the user, 2026-11-23** (applied in §7.1 rules 3, 4, 9 and §3.2):

| # | Approved decision |
|---|---|
| U1 | If **zero** basic tests pass, the problem score is capped at **12.0/100** (cap on the total). This is the approved interpretation of rule 4; it is not "passes only basic tests". |
| U2 | Every published problem must contain at least one `basic`, one `edge` and one `performance` test. Publication validation rejects a problem missing any group. Weights are never redistributed. |
| U3 | If any assigned problem in a session is unverified, no overall session score is produced. Verified problems show their individual scores; unverified problems are marked `not_verified`. No misleading partial session score is calculated or displayed. |
| U4 | Candidate code reaching the overall execution time limit: tests that did not execute count as timeouts. An infrastructure or runner fault: the submission is marked incomplete and cannot be selected as the best graded submission. Candidate timeouts and infrastructure faults stay distinct in stored status and reporting. |

Question history (as raised before approval, kept for the record):

| # | Question | Why it matters | Recommendation made |
|---|---|---|---|
| U1 | Rule 4 says "passes **only** basic tests" → max 12/100. With the approved 40 % correctness weight, passing all basic tests alone already earns 40, so the literal reading contradicts the weights (it would also make passing basic tests score lower than the cap allows). The proposal the user approved said "passes **no** basic test" → max 12/100 | The cap's trigger changes who gets capped | Confirm the proposal's meaning: **no basic test passed ⇒ problem score ≤ 12.0**, applied to the total (§7.1 rule 4, working definition). If "only basic tests" was intended, a different cap value or weighting is needed, which would change the approved rubric |
| U2 | A test group with 0 tests (e.g. a problem without `performance` tests) makes its criterion undefined | No weight redistribution was approved | Require ≥ 1 test in each of `basic`, `edge`, `performance` for every **published** problem (bank validator, 8.5a) so the case cannot occur; no renormalisation |
| U3 | Partial verification inside a session: some problems verified, others `not_verified` (e.g. runner down) | A session mean would mix verified and unverified problems | No session score unless every assigned problem is verified or unattempted; show each verified problem's score and mark the rest `not_verified` |
| U4 | Partial execution inside a submission (total wall-clock limit reached, remaining tests not run) | §4.4 counts them as `time_limit` failures | Keep §4.4: they are objective failures of that submission (the code exceeded the round's limits), not `not_verified`; a runner fault (`sandbox_error`) instead leaves the submission uncompleted, so it cannot be the best submission |

### 14.6 P8-D9, P8-D10, P8-D11 approval record and Batch 2 scope (2026-11-23)

**Status: APPROVED** by the user, 2026-11-23 (the recommended defaults of the decision brief, including sub-decisions
9a option A, 9b no teacher access, 9c admin may create from drive, 10a source category removed, 10b recruiter text
excluded, 10c plain-text URLs, 10d open drives only, 11a option A, 11b frozen copy in `plan_json`). Detailed rules:
§7.5, §7.6, §7.8.

**Batch 2 scope (approved):** **8.3** AI coding review (review-only, `coding-v1-unverified`) and **8.4** company
profiles and company-context question generation (migration B). Unchanged gates and flags: all new flags default OFF
(P8-D4), including backend `AI_MOCK_INTERVIEW_COMPANY_ENABLED` and worker `MOCK_INTERVIEW_CODING_ENABLED` /
`MOCK_INTERVIEW_COMPANY_ENABLED`; nothing executes code (P8-D8); no sandbox work before G2–G4. **Batch 2
implementation still needs its own explicit approval.**

### 14.7 O1 approval record: review-only coding feedback storage (2026-11-23)

**Status: APPROVED** by the user, 2026-11-23: **O1 = option A** — a separate additive coding-feedback table with its
own dedicated API. Implemented in 8.3 as `mock_interview_code_reviews` (migration `a5c7e9b1d3f2`, §6.1a) and
`GET/POST /mock-interview/sessions/{id}/coding/feedback`. `mock_interview_reports` (and its NOT NULL
`overall_score`) is unchanged and never used for review-only feedback; no fabricated zero or other overall score is
stored; a DB CHECK forbids a score or a "verified" status on `coding-v1-unverified` rows. The nullable
`problem_score` column is the future path for verified `coding-v1` (8.6). Option B (feedback inside `plan_json`) was
not chosen. **Actual code execution remains deferred until the sandbox gates G2–G4 pass.**

### 14.8 G2 sandbox decisions approval record (2026-11-24)

**Status: APPROVED** by the owner on 2026-11-24, in the Batch 3 implementation authorization ("use these defaults"):

| # | Approved as |
|---|---|
| P8-S2 | Docker + gVisor `runsc`, **systrap** platform; plain Docker is never the security boundary; **Judge0 removed** from the design (never introduced silently); Docker, gVisor, runtime configuration and images pinned (images by digest); the runner refuses to execute when `runsc` or the approved runtime is missing, the runtime version or platform is wrong, any mandatory hardening setting is missing, or an image is not digest-pinned |
| P8-S3 | §4.4 values are **starting ceilings**; calibration may tighten them and may never raise one without the owner's explicit approval; Java factors are measured, not invented |
| P8-S5 | one content-free JSON log line per execution; sanitized error codes; no source, output, hidden data or token in logs or metrics; no Sentry or AI tracing; token-protected `/metrics`; source only in tmpfs/memory; admin actions over SSH with an audit record |
| P8-S6 | dedicated job in the existing `ci.yml`, path-filtered, pinned gVisor with checksum, synthetic data only, no Actions secrets; plus private-repository CI for the reference solutions. **CI acceptance is claimed only after a real run**; no push, PR or merge is authorized yet |
| P8-S7 | separate private repository; local-only at `D:\campus-ai-testpack` (no remote) until the owner creates the private GitHub repository (suggested `campus-ai-interview-testpack`: named maintainers, 2FA required, protected default branch, forks disabled, no unnecessary Actions secrets) |

Batch 3 scope (approved the same day): **8.5a + 8.5b** exactly as recorded in §18. Batch 4 = 8.5c + 8.6. Not
approved: P8-S1/S1b/S4 (G3), any provisioning, spend, push, PR, merge, deploy, publication of problems, or execution
of student code.

### 14.3 Changes from r1

| # | r1 | r2 |
|---|---|---|
| D2 | 24 problems; hidden tests in this repository | public/private split; hidden tests, references and calibration in a private pack; protection rules; access validation; human review |
| D3 | stdin/stdout | + per-class error-handling table |
| D4 | flags off | + environment upper bound and narrow-only institutes stated |
| D5 | CodeMirror 6 | + minimal packages, fallback on load failure, audit requirement |
| D6 | limits | + atomic server enforcement, race protection, per-session/student/institute caps, exhausted-limit codes |
| D7 | 35/10/15 objective, 15/15 AI, 10 independence (hint penalty); cap 30/100 | 40/20/10 objective, 15/10/5 AI; **no hint penalty**; cap ≤ 12/100 when no basic test passes; deterministic and versioned |
| D8 | review-only | + no simulated execution; Run disabled |
| D9–D11 | as r1 | source list aligned (added verified institute profile data — later removed on approval, §14.6) |
| D12 | check all rounds at creation | atomic reservation, no partial, concurrency-safe, release policy |
| D13 | 7 days | + cleanup, student status, content-free history |
| D14 | last submission | **best** submission, deterministic ordering |
| D15, D16 | as r1 | unchanged |
| D17 | — | new: source retention |
| S1 | VM | pilot profile only; capacity review; no spend without approval; static egress requirement |
| S2 | gVisor | + full hardening list, trusted/untrusted split, fd limits, `--init`, reaper |
| S3 | limits | + per-language compile/run table; process-tree kill |
| S4 | HTTPS + token + allow-list | + mTLS where practical, rotation, constant-time compare, identifiers instead of expected outputs, no credentials in errors |
| S5 | content-free logs | + sanitized classes, audited admin operations, retention |
| S6 | Linux CI | split main/private CI; explicit check list; no credentials |
| S7 | — | new: private test-pack storage |

## 15. Approval gates

| Gate | Unlocks | Requires (all explicit, from you) |
|---|---|---|
| **G0** | nothing (this document) | — |
| **G1** | 8.1–8.4, 8.5a, 8.7 (no execution) | P8-D1…D4 and D6…D17 accepted (D5 before 8.2's editor work) |
| **G1 (Batch 1, narrower scope — passed 2026-11-23)** | 8.1 + 8.2 only | P8-D1…D6, D8, D14…D17 and B1-a/b/c accepted. D7 and D9…D13 are still required before 8.3, 8.4 and 8.7 |
| **Batch 2 decisions (recorded 2026-11-23)** | 8.3 + 8.4 | P8-D7 (+ U1–U4), P8-D9…D11 and O1 = A APPROVED; implementation authorized 2026-11-23 and done (§17; uncommitted). P8-D12/D13 still required before 8.7 |
| **G2** | 8.5b runner code + Linux CI job (synthetic data, no production) | P8-S2, S3, S5, S6, S7 accepted — **decisions APPROVED 2026-11-24 (§14.8); gate NOT passed: escape suite on gVisor and CI run outstanding (§18.6)** |
| **G3** | provisioning the VM and any spend | P8-S1 and P8-S4 accepted; provider, region and cost approved; backend egress/private-network approach chosen |
| **G4** | 8.6 — **first execution of untrusted student code** | 8.5c evidence recorded: escape suite 100 % on the VM, load test within targets, firewall/egress verified, token rotation tested; your sign-off of sandbox security acceptance |
| **G5** | any pilot with real students | 8.8 acceptance, runbooks, pilot institute and flags chosen by you |

Nothing in Phase 8 is implemented before G1. **No untrusted code runs anywhere before G4.**

## 16. Batch 1 record: 8.1 coding foundations + 8.2 coding UI (2026-11-23; implementation complete, review-only)

Approval: G1 narrower scope (§14.1, §14.4, §15), 2026-11-23. This document records implementation status and
evidence; Git history tracks commit status.

### 16.1 What was built

**8.1 backend.**
- Flags (default off): `AI_MOCK_INTERVIEW_CODING_ENABLED`, `AI_MOCK_INTERVIEW_CODING_TENANT_IDS` (fail-closed: empty =
  no institute), development-only `AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS` (ignored with a logged warning when
  `ENVIRONMENT=production`, B1-b). Gate = master gate + student + coding switch + coding allow-list; coding is offered
  only when at least one problem is servable.
- Public problem bank `backend/app/coding_bank/` (24 problems, 8/8/8, status `draft`, author "draft by Claude Code,
  pending human review"; 4 language templates). Allow-list validator `services/coding_bank.py`: any unknown key or
  extra file invalidates the whole bank (coding then disabled); restricted text (no HTML/links/URLs); `published`
  requires reviewer, review date and the private hidden-test digest. **No hidden tests, expected outputs or reference
  solutions were written to the repository**; visible-test outputs were computed with reference code kept in the
  session scratchpad only (8.5a will move references to the private pack).
- Migration A `e7a1c4d9b2f6` (revises `d6f0b4c8e3a5`): `mock_interview_code_attempts`,
  `mock_interview_code_submissions` exactly as §6.1, composite tenant FKs, CHECKs, indexes, RLS forced.
- Service `services/mock_interview_coding.py`: create validation, deterministic problem assignment at start (slots
  by difficulty §3.4, topic preference, avoid last 10 rounds, practice `problem_key`), drafts with optimistic
  `draft_rev`, graded submissions stored `not_executed` (idempotency key, identical-code short-circuit, atomic
  `submissions_used < 3` with in-flight guard, 6 per problem per student per 7 days), authored hints (atomic cap,
  no score effect), done, finish/abandon/time-up in one transaction (saved drafts → `auto` submissions), summary.
- Router `routers/mock_interview_coding.py` (added once to `FEATURE_ROUTERS`; routes in API.md "Phase 8.1").
  Existing router: `/options` offers `coding` behind the gate; create checks (422 unavailable / text-only, B1-a);
  start/finish/abandon dispatch; **answer/next → 409 "Coding rounds don't use interview questions yet."; report
  GET/POST → 409 "Feedback for coding rounds is not available yet."** Session responses use
  `response_model_exclude_unset` so `coding_summary` appears only on coding sessions.
- No run endpoint, runner client or fake runner (B1-c). No AI call, no ledger row, no worker change.

**8.2 frontend.**
- CodeMirror 6 exact pins (P8-D5): `@codemirror/state` 6.7.6, `view` 6.43.15, `commands` 6.11.1, `language` 6.13.1,
  `lang-python` 6.2.1, `lang-cpp` 6.0.3, `lang-java` 6.0.2 (all MIT; newest releases, each ≥ 2 weeks old;
  12 transitive MIT packages incl. `@codemirror/autocomplete`, `@codemirror/streamparser`, `@lezer/*`). Lockfile:
  additions only. `npm audit --omit=dev`: 0 vulnerabilities.
- `components/mock-interview/coding/`: `CodingWorkspace`, `CodingInstructions`, `CodingRoom`, `CodeEditor`
  (dynamic `import()` only, textarea fallback), `ProblemPanel`, `SubmissionHistory`, `ReviewOnlyNotice`,
  `CodingSetupFields`, `CodingFinished`, `codingApi.ts`, `codingFlow.ts`. Existing files (additive):
  `page.tsx` (coding routing), `InterviewSetupForm.tsx`, `InterviewHistory.tsx`, `mockInterviewApi.ts`,
  `mockInterviewFlags.ts`. Build flag `NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED` (Dockerfile builder `ARG`, default
  false). No CSP or Permissions-Policy change (CodeMirror needs neither eval nor workers).

### 16.2 Deviations from the r2 text (all narrower, none wider)

- **Institute coding pause** (`ai_feature_controls` row `mock_interview_coding`, §6.5) is **not** built: it needs a
  change to the Phase 7 admin controls API/UI. The existing institute pause already blocks new coding rounds (tested).
  Deferred to 8.6 with the execution quotas.
- **Execution counters** (`runs_used`, 90 per session, 150 per student per day, institute monthly execution cap) have
  columns but no enforcement: there are no executions in Batch 1 (B1-c). They land with 8.6.
- **Rate limits are per caller and per request path** (slowapi), i.e. per problem on attempt routes — the same model as
  the existing per-session AI-step limits.
- `explaining` status and spoken explanation turns arrive with 8.3; Batch 1 closes a problem directly.
- Public bundle serves the current version of a problem; older pinned versions are not kept (all are v1 today).

### 16.3 Test evidence (2026-11-23)

| Check | Result |
|---|---|
| New SQLite tests (`test_mock_interview_coding_{sessions,flags,bank,limits,privacy,models}.py`) | sessions 34, flags 16, bank 29, limits 8, privacy 4, models 13 = **104 passed** (+ 6 Postgres-only tests that skip on SQLite) |
| Existing tests updated (approved, additive) | `test_mock_interview_privacy.py` create-body field set gains `coding` (P8-D16); `test_mock_interview_flags.py` interview-table list gains the 2 coding tables |
| Migration A on SQLite (pre-8.1 schema from the ORM, `stamp d6f0b4c8e3a5` → `upgrade head` → `downgrade -1` → `upgrade head`) | all exit 0; 2 tables + 6 indexes created/dropped/recreated. Full chain cannot run on SQLite (pre-existing `1d61da8d89e7`) |
| Throwaway Postgres 16 (pinned digest, tmpfs, `--rm`, 127.0.0.1:15434): full chain from empty → `e7a1c4d9b2f6`, `downgrade -1`, `upgrade head` | passed; `app_user` NOSUPERUSER/NOBYPASSRLS; container removed afterwards |
| Postgres RLS files as `app_user` (`test_mock_interview_coding_rls_postgres.py` new 6 + existing 12) | **18 passed** |
| Postgres API as `app_user` (`tests_pg_api`: new coding 3 + existing mock-interview pg, ops pg, reports pg) | **18 passed** (incl. 8 truly parallel graded submits → exactly 3 accepted) |
| Full backend SQLite regression | one run, 32 min 26 s: **579 passed, 1 failed, 18 skipped**. The failure was the guard test `test_mock_interview_flags.py::test_only_planned_interview_tables_exist`, which pins the interview table list; it was updated to include the two planned Phase 8.1 tables (test-only change) and that file re-run: **17 passed**. **Final full re-run after that change (16:43–17:15): 580 passed, 0 failed, 18 skipped, exit 0 (32 min 3 s).** Skips = 18 Postgres-only RLS tests (12 existing + 6 new), run separately on Postgres above |
| ruff (all new/changed Python) | clean |
| Frontend `npm run test:unit` | **166/166** (150 + 16 new in `codingRound.test.mjs`) |
| `npx tsc --noEmit` / `npm run lint` | exit 0 / 0 errors, 2 pre-existing warnings (`e2e/smoke.spec.ts`) |
| `npm run build` (coding flags on) | exit 0; production check: CodeMirror (5 chunks, ≈ 124 KB gzip) downloaded only when the coding room opens, none on the setup page |
| Real Chromium smoke (scratch backend on its own SQLite + `next dev`, synthetic student) | **35/35**: gate + build flag, setup fields, review-only notice, timer, Run disabled, CodeMirror lazy load, accessible name, Esc→Tab exit, server autosave, no code in browser storage, refresh resume, hint, submit (no score), simple-editor fallback, no overflow at 320/390/768/1366 px, tabs + arrow keys, done, end, finished focus, history label, past summary, 0 console errors |
| Build flag off (server gate on), real Chromium | only HR/Technical offered, HR fields and durations unchanged, no coding text, no page errors. (Turbopack **dev** prefetches a CodeMirror chunk; the production check above shows none) |
| Real devices, screen readers | **not run** — Phase 10 rows |

### 16.4 Known limitations

- All 24 problems are drafts: **production serves no coding problem** until a human reviews and publishes them and the
  private hidden-test digests exist (8.5a). Development uses the B1-b setting.
- Review-only: no execution, no correctness feedback, no coding score or report (8.3/8.6).
- The dashboard shell's pre-existing horizontal overflow below ~490 px is outside this feature (the coding room itself
  does not overflow).

### 16.5 Remaining before Batch 2

P8-D7 approved 2026-11-23 with semantics U1–U4 approved the same day (§14.5); P8-D9…D11 approved 2026-11-23 (§14.6); Batch 2 = 8.3 + 8.4; P8-D12/D13 before 8.7; P8-S2/S3/S5/S6/S7 (G2) before runner code;
human review of the 24 drafts.

## 17. Batch 2 record: 8.3 AI coding review + 8.4 company profiles (2026-11-23; implemented, uncommitted)

Approval: Batch 2 scope (§14.6) and implementation authorized 2026-11-23, with **O1 = option A** (§14.7). This
document records implementation status and evidence; Git history tracks commit status. **No code is executed
anywhere; no Run endpoint, fake runner, simulated result or verified score exists.**

### 17.1 What was built

**8.3 backend** (flag `AI_MOCK_INTERVIEW_CODING_AI_ENABLED`, default off, behind the coding gate):
- Migration `a5c7e9b1d3f2` + model `MockInterviewCodeReview` (§6.1a): one row per problem, RLS forced, CHECKs that
  forbid any score or "verified" status on `coding-v1-unverified` rows.
- `services/mock_interview_coding_ai.py`: AI hints (only past the authored hints and within the P8-D6 cap; slot
  reserved atomically and given back on failure; text re-filtered and **not stored**); explanation turns ("Done" with
  a graded submission → `explaining` by compare-and-set; 1–2 typed questions in `mock_interview_turns`, topic
  `coding:<seq>:explain`; fixed fallback question; answers via the existing `/answer`; `stt` → 422).
- `services/mock_interview_coding_report.py`: feedback generated once under the session lease, all problems or
  nothing; strict worker-output schema (unknown keys such as objective categories or totals → 503, nothing stored);
  evidence re-validated (code quote within the 3 lines starting at the cited line; explanation quotes by the Phase 4
  rule; quotes about
  scores/instructions never count); correctness/test-result sentences removed; no score of any kind.
- `services/mock_interview_coding_guards.py`: the backend copy of the text guards (≤ 3 code lines, claim filter,
  evidence rules).
- Routes `GET/POST /sessions/{id}/coding/feedback`; `/answer` for coding sessions (AI help on) → explanation answers;
  `/next` and the report routes keep their documented 409. Ledger steps `code_hint`, `code_followup`, `code_review`
  (new optional `step=` on the worker client; default unchanged).

**8.3 worker** (flag `MOCK_INTERVIEW_CODING_ENABLED` + `MOCK_INTERVIEW_ENABLED`): `interview/coding/` (`router.py`
`/hint` `/followup` `/review`, `schemas.py` strict model output, `prompts.py` numbered code as data, `filters.py`,
`review.py`); every route `tracing_disabled()` + `usage_scope()`. New data tags in `safety.DATA_TAGS`.

**8.3 frontend:** `ExplanationPanel` (typed only), `CodingFeedback` ("AI estimate" per category, "Not verified"
objective categories, evidence with its source, uncertainty note, disclaimer, no score), AI hints shown once in
`ProblemPanel`, `CodingRoom`/`CodingFinished` wiring, helpers in `codingFlow.ts`.

**8.4 backend** (flag `AI_MOCK_INTERVIEW_COMPANY_ENABLED`, default off, behind the master switch and allow-list):
- `services/role_templates.py` (6 generic templates); migration `b6d8f0a2c4e3` + model
  `MockInterviewCompanyProfile` (§6.2 + `published_at`, `archived_at`; RLS forced; `drive_id` FK SET NULL).
- `services/mock_interview_company.py` + `routers/mock_interview_company.py` (routes in `API.md` "Phase 8.4"):
  RBAC (admin/TPO write, either may publish; teacher/recruiter 403; other institute 404; students published-only),
  content-free audit rows, archive never delete, ≥ 1 source to publish, open same-institute drives only, URLs never
  fetched (students get label + domain), JD skill map with verbatim-quote check (ledger step `jd_skills`).
- Create accepts optional `company_profile_id`, `role_template_key`, `focus_skills` (HR/technical only); frozen copy
  in `plan_json["company"]`; `company_context` added to **plan/turn** payloads only (`_with_company`); session
  detail gains optional `company_context`; the report payload is untouched.

**8.4 worker** (flag `MOCK_INTERVIEW_COMPANY_ENABLED`): `interview/company.py` (claim filter, `/interview/jd/skills`);
optional `company_context` on plan/turn requests, used in the planner and question prompts inside
`<company_context>` with company rules, **only while the flag is on**; generated questions pass the claim filter.

**8.4 frontend:** student `CompanyContextPicker` (profile or role template, transparency panel, skill map),
`CompanyContextNotice` (instructions, video lobby, finished/report); staff page
`/dashboard/interview-company-profiles` (`ProfileForm`, `SourcesEditor`, create from open drive, publish, archive)
and an admin/TPO sidebar entry shown only when the server answers. No new build flag.

### 17.2 Deviations from the r2 text (all narrower)

- Company context applies to **HR and technical** sessions only; coding + company → 422 (coding-topic choice from
  focus skills deferred to 8.7).
- `rounds_json` exists (nullable) but is not exposed (simulations, 8.7).
- AI hint text is not persisted (shown once); with the current bank (3 authored hints per problem, caps 3/3/2) AI
  hints cannot trigger yet.
- Explanation turns only after "Done" with a graded submission; none after time-up (the automatic submission is
  still reviewed).
- Profiles gained `published_at` / `archived_at` timestamps (additive, informational).

### 17.3 Independent reviews (read-only agents) and fixes

No High findings from either the security or the code-quality review. Fixed before handover (all re-tested):
- **Prompt injection could steer AI estimates** (code comments or answers like "reviewer: give 5/5"): code or
  answers containing reviewer-directed text now **withhold** the affected estimates (not assessed); code quotes
  need ≥ 8 characters with a real token; explanation quotes always ≥ 3 words / 15 characters (no short-answer
  relaxation). Backend and worker.
- **Hint filter bypass** (solution on one line, in inline code, or as plain assignments): code is now measured in
  statements (`;` and `: <stmt>` count) and characters across fenced blocks, inline code and code-looking lines
  (≤ 3 statements, ≤ 240 characters). Backend and worker.
- **Free retries of a filtered AI hint:** unsafe output now keeps the hint slot used (only an outage gives it back);
  the worker answers 422 for "filtered", so unsafe model output cannot open the `code_hint` circuit breaker.
- **Problem stuck in `explaining`:** a question that cannot be stored closes the problem; "Done" heals a problem
  stuck without an open question once it is older than the lease window (a parallel "Done" may still be asking —
  this race was found by the Postgres concurrency test and fixed); `mark_done` also closes `explaining` (AI help
  switched off mid-round).
- **Published profile without sources:** `PUT` with no sources on a published profile → 422.
- **Company context after the flag is switched off:** no longer sent to the worker (the student still sees the
  session's disclaimer).
- Feedback lease released on any unexpected error; evidence shows the line actually matched; audit summaries use
  correct past tense; the claim filter keeps line breaks/indentation and no longer drops "previous questions" /
  "interview process" generally; the coding room saves the draft before asking for a hint.

Accepted / documented (not changed): `GET /sessions/{id}/coding` now always carries `ai_enabled` and per-attempt
`explanation` (8.1 is unreleased; additive); `jd-skills` with `profile_id` returns verbatim quotes of a **published**
profile's JD (student-facing practice material by design); a drive source kept unchanged on a drive that closed
later stays valid; the admin/TPO nav probe calls `/company/role-templates` (404 while off) in builds with the
mock-interview flag; a step-by-step algorithm written in prose cannot be fully detected by a filter (residual risk;
prompts forbid it).

### 17.4 Test evidence (2026-11-23, final code)

| Check | Command | Result |
|---|---|---|
| New 8.3 backend tests | `cd backend; .venv/Scripts/python -m pytest -p no:cacheprovider -o addopts="" -q tests/test_mock_interview_coding_ai.py` | **34 passed** |
| New 8.4 backend tests | `… tests/test_mock_interview_company.py` | **16 passed** |
| Updated guard tests | `… tests/test_mock_interview_privacy.py tests/test_mock_interview_flags.py` | passed (table list + create-body fields + integer-id exemption for `company_profile_id` / `profile_id`) |
| **Full backend SQLite regression** (run alone, 21:27–22:03) | `cd backend; .venv/Scripts/python -m pytest -p no:cacheprovider -o addopts="" -q` | **630 passed, 0 failed, 24 skipped**, exit 0 (35 min). The 24 skips are the Postgres-only RLS files (12 earlier + 6 coding + 6 Batch 2) — **skipped on SQLite, not passed there**; run on Postgres below |
| Postgres 16 throwaway container (pinned digest, tmpfs, `--rm`, 127.0.0.1:15434; removed) — migrations | owner: `alembic upgrade head` (empty → `b6d8f0a2c4e3`), `downgrade -2`, `upgrade head`; `heads` | passed; single head; `app_user` NOSUPERUSER / NOBYPASSRLS; both new tables RLS enabled + forced |
| Postgres RLS as `app_user` | `RLS_TEST_DATABASE_URL=… pytest tests/test_mock_interview_batch2_rls_postgres.py tests/test_mock_interview_coding_rls_postgres.py tests/test_mock_interview_rls_postgres.py tests/test_rls_postgres.py` | **24 passed** (6 new) |
| Postgres API as `app_user` (mock-interview files) | `pytest --confcutdir=tests_pg_api tests_pg_api/test_mock_interview_batch2_pg.py …coding_pg …pg …ops_pg …reports_pg` | **21 passed** (3 new: parallel feedback → 1 review call + 1 row set; parallel "Done" → 1 question; company RBAC/audit/RLS/frozen copy) |
| **Full `tests_pg_api` folder** as `app_user` | `pytest --confcutdir=tests_pg_api tests_pg_api` | **237 passed, 2 skipped**, exit 0 (skips: the known Redis queue tests, `RQ_TEST_REDIS_URL` not set) |
| SQLite migrations | pre-Batch-2 schema from the ORM, `stamp e7a1c4d9b2f6` → `upgrade head` → `downgrade -2` → `upgrade head` | passed; columns of both tables match the ORM |
| AI worker interview suites | `cd ai-worker; .venv/Scripts/python -m pytest -p no:cacheprovider -q tests/test_interview_{company,coding,engine,report,flag,no_langfuse,usage}.py tests/test_worker_security.py tests/test_sentry_{ai_integrations,privacy}.py tests/test_worker_{correlation,metrics_token}.py` | **116 passed** (25 new). `ai-worker/qdrant_data/` unchanged. Qdrant/face server tests and the whole folder not run |
| ruff | backend `app/`, migrations, tests; worker `interview/`, tests | clean |
| Frontend unit | `npm run test:unit` | **187/187** (166 + 21 new) |
| TypeScript / lint | `npx tsc --noEmit` / `npm run lint` | exit 0 / 0 errors (2 pre-existing warnings in `e2e/smoke.spec.ts`) |
| Production build | `npm run build` with mock-interview + coding build flags on, and with defaults (off) | exit 0 / exit 0 |
| Real Chromium smoke, flags on (scratch backend on its own SQLite + scripted AI, `next dev`) | session scratch script | **38/38**: admin authoring (save, publish, source as plain text, no link, create from drive), teacher no access, student picker (From your institute, domain text, disclaimer, evaluation note, no links, skill map with quotes), instructions notice, picker hidden for coding, typed explanation turns (2 questions, closed), feedback (AI estimate labels, 3× Not verified, Not assessed, uncertainty note, no claim, no overall/percentage), no overflow at 320/390/768/1366 px (profiles list, setup form, feedback), 0 unexpected console errors |
| Real Chromium smoke, Batch 2 flags off | same, backend `--flags-off` | **7/7**: no nav entry, page "not enabled", no picker, Done closes directly, no feedback button, not-available note, 0 console errors |
| `git diff --check` | | clean (CRLF notices only; `core.autocrlf=true` normalizes) |

**Not run / future acceptance (not reported as passed):** live LLM; real devices and screen readers (Phase 10);
Redis queue path (2 skips); load tests; anything involving code execution (gates G2–G4 not passed).

### 17.5 Pre-staging review (2026-11-24)

**Scoring compliance — individual AI estimates are not scores.** Review-only feedback may carry an **individual
0–5 AI estimate** for each of code quality, reasoning and testing approach. Each assessed estimate needs verified
evidence and now carries its **own `uncertainty` text** (added in this review; additive field, rendered in the UI);
a not-assessed category has `score: null` and an uncertainty text saying why. Correctness, robustness and efficiency
are always `not_verified`. No overall, problem, session or AI-only score exists in the schemas, the service, the
worker response or the UI. Database: the 0–5 CHECKs on the three estimate columns accept every approved value; the
`unverified_unscored` CHECK forbids `problem_score` and `verification = 'verified'` on `coding-v1-unverified` rows;
`problem_score` is allowed only with `verification = 'verified'` (future `coding-v1`). A `coding-v1` row that is
`not_verified` without a score remains valid on purpose (U3). Submissions are only ever `not_executed`; nothing can
store a test result.

**AI hint fallback is dormant for the current bank.** All 24 problems have 3 authored hints and the caps are 3/3/2,
so authored hints always cover the cap and the AI hint worker is never called (test). With a deterministic fixture
(one authored hint per problem): the authored hint comes first with no AI call; hints 2–3 use the AI fallback; the cap
stops further calls; unsafe AI output uses up its slot (no endless retries); without AI help the authored count caps
hints. The hint payload carries public problem text, the student's code and the authored hints only (no hidden
data, no rubric reference). Code-limit bypass cases (one-line solutions, inline code, plain assignment lines, an
unterminated fence, mixed fenced + inline code, an over-long single statement) are rejected; exactly 3 statements
are allowed.

## 18. Batch 3 record: 8.5a hidden test pack + 8.5b secure runner foundation (2026-11-24; implemented locally, uncommitted)

Approval: owner's Batch 3 implementation authorization, 2026-11-24 (G2 decisions §14.8; local implementation only:
no staging, commit, push, PR, merge, deploy, provisioning, spend, system-software installation or student code).
During the session the owner additionally approved, for this batch only: starting the already-installed Docker
Desktop and pulling the three digest-pinned toolchain images to compile and run **our own reference solutions**
(never student code, never hostile fixtures); and declined installing Ubuntu WSL2 + Docker Engine + gVisor locally
("not now"), so the real escape suite stays blocked until a verified gVisor host exists.

### 18.1 8.5a — private test pack (`D:\campus-ai-testpack`, local Git, no remote, nothing committed)

- Format v1 (`tools/packformat.py`, byte-identical to `code-runner/runner/packformat.py`; shared golden vector):
  `manifest.json`, `problems/<key>/problem.lock.json`, `tests/{visible,basic,edge,performance}/NN.{in,out}`; plus
  non-runtime `generators/gen.py`, `reference/{python,cpp,java,c}/`, `slow/main.cpp`, `calibration.json`,
  `labels.json`, `review-log/<key>.md`, `public/<key>.json` (snapshot of the bound public fields).
- Digest process (one definition): file = SHA-256 of raw bytes (no line-ending conversion); group = SHA-256 over
  ordered `NN, sha256(in), len(in), sha256(out), len(out)`; hidden test-set = SHA-256 over the canonical lock fields
  (key, versions, checker, time_ms, memory profile) and the three hidden group digests (**the value for the public
  `hidden_test_set.digest_sha256`**); visible-set and pack digests likewise; runtime archive = uncompressed ustar,
  sorted entries, mtime 0, uid/gid 0, fixed modes, named by the pack digest, containing only runtime files.
- Content: 24 problems; **320 hidden tests** (easy 6/4/1, medium 6/5/3, hard 6/5/4 per problem — exactly the
  counts already declared in the public bundle; every group >= 1, U2); **96 reference solutions** (4 languages x 24);
  **15 deliberately slow solutions** (C++, must fail `performance`; the other 9 problems have no slower common
  approach to separate, recorded per problem). Seeded generators: fixed integer seeds, `random.Random` only, explicit
  `check_input` schema, bounded sizes; expected outputs derived only by the generator oracle, cross-checked against an
  independent brute force on small inputs, against the public visible outputs, and against all four references.
- Ambiguities chosen and recorded for the reviewer (generator notes): reverse-word-order allows leading/trailing
  spaces (the public visible tests already contain them); connect-all-hostels and shortest-delivery-route include
  parallel edges, no self-loops; course-schedule-order uses no repeated rule and no `a == b`; tree-level-sums roots
  may be any member, parents may be numbered after children.
- Built pack (2026-11-24): pack digest `c13fded0` (first 8 hex characters; the full value is kept only in the private repository); runtime
  archive 116,480,000 bytes, SHA-256 `618ce669` (first 8 hex characters); two clean
  rebuilds reproduce the manifest and the archive byte for byte; the runner's own verifier loads the archive alone
  (24 problems, same digest; no references, generators, labels or calibration inside).
- Human review: **no reviewer named; all 24 records `Decision: PENDING`**; checklist covers originality, clarity,
  ambiguity, constraints, coverage, performance separation, checker, hint safety and each reference. No digest has
  been written to the public bundle; all 24 problems stay `draft`.
- Private CI written (`.github/workflows/validate.yml`: build, verify, tests, validate all references) — **not run**
  (no remote exists).

### 18.2 8.5a — U2 in the public validator

`services/coding_bank.py`: a `published` problem additionally needs >= 1 `basic`, `edge` and `performance` hidden
test (rejected otherwise, weights never redistributed); drafts unchanged. Tests: one per missing group, a valid
1/1/1 problem, drafts exempt, a bad published problem invalidates the whole bank, and the shipped bank still has no
digest, no reviewer and every group >= 1.

### 18.3 8.5b — `code-runner/` (standalone; not connected to the backend; nothing executed)

Modules: `config` (fail-closed env), `auth` (constant-time, 1-3 rotating tokens), `schemas` (strict, extra=forbid,
96 KB body, 64 KB source, 16 KB custom input), `pins` (verified pins), `languages` (fixed argv; `javac -proc:none`),
`limits` (§4.4 ceilings, tighten-only factor), `sandbox` (allow-listed `docker run` arguments checked by
`assert_hardened`, Docker CLI wrapper with capped streams and container-kill on any limit, fail-closed `self_check`),
`executor` (compile container -> opaque build tar -> run container(s); per-limit container kill and recreation; U4;
cleanup proof or discard; judging; reduction), `pack` (startup verification, read-only required), `service` (bounded
queue, idempotent replays, reaper, degraded state), `observability` (one content-free JSON line per execution,
token-protected Prometheus text), `app` (HTTP surface, generic errors, no docs/OpenAPI). Contract and operations:
`code-runner/README.md`, `runbooks/code-runner.md`.

Pins (verified 2026-11-24 at the publishers): gVisor `release-20261102.0` (archive SHA-512, `runsc` SHA-256
`c5a5a7ba…5098`), runtime args `--platform=systrap --network=none`; Docker Engine 29.9.3 / containerd.io 2.4.2-3 for
Ubuntu 24.04 (package SHA-256 from Docker's apt index; apt key fingerprint `9DC8 5822 9FC7 DD38 854A E2D8 8D81
803C 0EBF CD88`); images `gcc:14.4.0` `sha256:a6f425a3…0765`, `python:3.13.17-slim-bookworm`
`sha256:e32be0e5…8530`, `eclipse-temurin:21.0.12.1_1-jdk-noble` `sha256:57424b1d…7029` (Docker Hub tag API and
registry `Docker-Content-Digest` agree).

CI (P8-S6): `ci.yml` gains `sandbox-scope` (path detection) and `sandbox` (installs the pinned Docker with key and
package checksums, the pinned gVisor with checksums, registers `runsc`, pulls pinned images, runs unit tests and the
escape suite with `RUNNER_GVISOR_E2E=1`; no secrets). **Not executed** (needs a push/PR the owner has not approved).

### 18.4 Deviations from the r2 text (all narrower or explicitly recorded)

1. Graded requests carry `hidden_digest`; a mismatch with the installed pack is 409 (binds the run to the reviewed
   test set).
2. Compilation runs in its own container with compile limits; the build is moved to the run container as an opaque
   tar (keeps compile memory separate from run memory, and lets a killed run container be replaced mid-submission).
3. Containers' own processes run as uid 65533 (builder); programs run as uid 65534, which cannot signal them.
4. Per-test CPU uses RLIMIT_CPU (whole seconds, rounded up, never above the ceiling); the runner's wall clock
   (2.5 x CPU) is the backstop. Per-test memory is not measured in v1 (`memory_kb: null`).
5. Problem `time_ms` is the problem factor (C/C++ x1, Java x2, Python x3, capped at 6 s).
6. Performance test files are git-ignored build outputs pinned by the manifest (size), reproduced by `verify.py`.
7. Judge0 removed from the design (§14.8).

### 18.5 Test evidence (2026-11-24, this machine; raw outputs were in the session scratchpad)

| Check | Result |
|---|---|
| Runner suite (`code-runner/tests`, Windows, scripted Docker, no execution) | **183 passed, 2 skipped** (symlink test is POSIX-only; escape suite skipped) |
| Runner self-check against this machine's real Docker (read-only queries) | **refuses** (`runsc_runtime_missing`, `runsc_binary_missing`) |
| Escape suite forced (`RUNNER_GVISOR_E2E=1`) on this non-gVisor machine | **refused to run** (fixture error), container count unchanged |
| ruff (`code-runner`, private tools/tests/generators, changed backend files) | clean |
| Private pack tests (`tests/`: determinism, schema, golden vector, pack vs public, negative control) | **52 passed** |
| `tools/verify.py --archive` | manifest, public binding, two clean rebuilds, byte-identical archive: **pass** |
| `tools/validate_refs.py` (all 24 x 4 languages; pinned images on Docker Desktop **runc**, no network, read-only, non-root) | **96/96 accepted** (all outputs correct; max CPU <= 50 % of limit: worst coin-combinations Python 1380/3000 ms = 46 %); **15/15 slow solutions fail performance** |
| Backend `tests/test_mock_interview_coding_bank.py` | **36 passed** (7 new U2/publication tests) |
| **Full backend SQLite suite** (run alone, `pytest -p no:cacheprovider -o addopts="" -q`) | **655 passed, 0 failed, 24 skipped** (Postgres-only RLS tests), exit 0, 38 min 35 s |
| Worker interview suites (8 files: coding, company, engine, flag, no_langfuse, report, usage, metrics token) | **91 passed** (no worker change) |
| Frontend unit / `tsc` / lint / production build (default flags) | **187/187**, exit 0, 0 errors (2 pre-existing warnings), exit 0 (no frontend change) |

**Not run / blocked (not reported as passed):** the escape/abuse/conformance suite on gVisor (no verified gVisor
host: local install declined; CI needs push approval); both CI workflows; gVisor timing calibration (all timings are
**provisional runc** figures); Java factors under gVisor; load test (8.5c); Postgres suites (no schema, RLS or API
change in Batch 3).

### 18.6 G2 status: NOT PASSED

Decisions approved (§14.8). Evidence present: pins verified, hardening enforced and tested, fail-closed self-check
proven against a real non-gVisor daemon, content-free logs/metrics and sentinel tests, strict API, U2, reproducible
digest-addressed pack, all references valid. **Missing:** a run of the full escape suite (§11.5) inside the pinned
gVisor (100 % required), and an executed CI run. G2 passes only when the `sandbox` CI job (or the same suite on a
verified gVisor host) has run green and the result is recorded here.

### 18.7 Next: Batch 4 (8.5c + 8.6) prerequisites

Push/PR approval so the `sandbox` CI job can run (completes G2); a named human reviewer and the review of all 24
problems; the private GitHub repository (owner-created); G3 decisions (P8-S1 VM, P8-S4 transport, provider/region/
cost, backend egress or tunnel); then 8.5c on the VM (hardening, pack install, escape suite 100 %, gVisor calibration
incl. Java, 2x load test, firewall/egress, token rotation drill) and G4 sign-off before 8.6 connects the backend
(`code_runner.py`, dispatch, sweep, breaker, ledger, quotas, verified `coding-v1`, best submission) behind
`AI_MOCK_INTERVIEW_CODE_EXECUTION_ENABLED` (default off). Publication of any problem needs its approved review and a
separate owner approval.
