# AI Mock Interview — API (Phases 3–7)

## Where and who

- **Base path:** `/v1/mock-interview`. The same routes are also mounted unversioned (`/mock-interview`), like every feature router.
- **Auth:** cookie or Bearer, the same as the rest of the API.
- **Errors:** the uniform JSON envelope (`detail`, `request_id`).

| Gate | Result |
|---|---|
| Not signed in | 401 |
| Feature off for the caller (`AI_MOCK_INTERVIEW_ENABLED`, tenant allow-list, recruiter) | **404** on every route except `/status` |
| Signed in, feature on, not a student (teacher/tpo/admin) | 403 on Phase 3 routes (`/status` and `/catalog` still work) |
| Session or resume not owned by the caller (other student or other institute) | 404 |

## Endpoints

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `/status` | – | `{enabled}` (Phase 1, unchanged) |
| GET | `/catalog` | – | static catalog (Phase 1, unchanged) |
| GET | `/options` | – | `Options` |
| POST | `/sessions` | `CreateSession` | 201 `Session` |
| GET | `/sessions` | – | `SessionSummary[]` (newest first, ≤50) |
| GET | `/sessions/active` | – | `Session` or `null` |
| GET | `/sessions/{id}` | – | `Session` |
| POST | `/sessions/{id}/start` | – | `Session` (opening question) |
| POST | `/sessions/{id}/answer` | `{turn_seq, answer, source?}` | `Session` (next question or ended) |
| POST | `/sessions/{id}/next` | – | `Session` |
| POST | `/sessions/{id}/finish` | – | `Session` |
| POST | `/sessions/{id}/abandon` | – | `Session` |
| GET | `/history` | – | `HistoryItem[]` (newest first, ≤50). Phase 4 |
| GET | `/sessions/{id}/report` | – | `Report`; **404 "Report not generated yet"** until generated. Phase 4 |
| POST | `/sessions/{id}/report` | – | `Report`: generates it once, or returns the stored one with no AI call. Phase 4 |

## Schemas

**Options**
```json
{
  "interview_types": [{"value": "hr", "label": "HR"}, {"value": "technical", "label": "Technical"}],
  "difficulties": [{"value": "easy", "label": "Beginner"}, {"value": "medium", "label": "Intermediate"}, {"value": "hard", "label": "Advanced"}],
  "durations_minutes": [15, 30, 45],
  "languages": [{"value": "en", "label": "English"}, {"value": "hi", "label": "Hindi"}, {"value": "hinglish", "label": "Hinglish"}],
  "resumes": [{"id": 3, "title": "Backend CV", "target_role": "Backend Developer", "is_primary": true, "created_at": "..."}],
  "job_description_max_chars": 10000,
  "answer_max_chars": 5000
}
```
`resumes` contains only the caller's own resumes in the caller's institute.

**CreateSession** (unknown fields → 422)

| Field | Type | Required | Rule |
|---|---|---|---|
| `interview_type` | `"hr"` \| `"technical"` | yes | |
| `target_role` | string | yes | 2–150 chars after trimming; control chars removed |
| `difficulty` | `"easy"` \| `"medium"` \| `"hard"` | no, `"medium"` | |
| `duration_minutes` | 15 \| 30 \| 45 | no, 30 | |
| `language` | `"en"` \| `"hi"` \| `"hinglish"` | no, `"en"` | |
| `company_name` | string | no | ≤150 |
| `resume_id` | int | no | must be the caller's resume in the same institute, else **404 "Resume not found"** |
| `job_description` | string | no | ≤10,000; blank → null |

**Session**
```json
{
  "id": 12, "interview_type": "technical", "mode": "text", "status": "in_progress",
  "target_role": "Backend Developer", "company_name": null, "difficulty": "medium",
  "language": "en", "duration_minutes": 30,
  "created_at": "...", "started_at": "...", "completed_at": null,
  "resume_id": 3, "has_job_description": true,
  "awaiting": "answer", "current_turn_seq": 2, "remaining_seconds": 1640,
  "progress": {"answered": 1, "question_budget": 12, "topics_total": 7, "topics_covered": 2},
  "turns": [
    {"seq": 1, "topic": "Introduction", "question": "Hi, I'm your AI interviewer...\n\nTell me about yourself.",
     "is_follow_up": false, "answer_text": "I am ...", "answered_at": "..."},
    {"seq": 2, "topic": "DBMS", "question": "What is normalization?", "is_follow_up": false,
     "answer_text": null, "answered_at": null}
  ],
  "closing_message": null, "end_reason": null
}
```

Notes on the fields:
- **`awaiting`:**
  - `start`: not started, or the opening failed;
  - `answer`: answer question `current_turn_seq`;
  - `next_question`: the answer is saved but the next question is missing (call `/next`);
  - `none`: ended.
- **`remaining_seconds`:** only while `in_progress`, never below 0.
- **`end_reason`:** `time_up`, `question_budget_reached`, `topics_covered`, `ended_by_candidate` or `abandoned`.
- The job description text, the plan internals and per-answer evaluations are **not** returned in Phase 3.

## Behaviour and error codes

| Situation | Code |
|---|---|
| Create while a `setup`/`in_progress` session exists | 409 |
| More than 10 sessions created in 24 h | 429 |
| `start` on an ended session | 409 |
| `start` on a started session | 200, current state, no AI call |
| `answer` when not `in_progress` | 409 |
| `answer` same `turn_seq` + same text again | 200, current state, no AI call (retry-safe) |
| `answer` same `turn_seq` + different text | 409 |
| `answer` for a seq that is not the current question | 409 |
| Empty or >5,000-char answer, extra fields | 422 |
| AI worker unavailable (start / answer / next) | 503. **The answer is already saved**; retry `/start` or `/next` |
| Another request is already generating for this session | 409 "still preparing the next question" (no parallel LLM call) |
| More than 20 calls per minute to one of `start` / `answer` / `next` (per student, by access token, not IP) | 429 |
| Time ran out more than 5 minutes ago with nothing submitted | The session closes on the next read (`completed` if any answer, else `abandoned`, `end_reason: time_up`) |
| `next` with a question already pending, or after the end | 200, no AI call |
| `finish` | `completed` if ≥1 answer, else `abandoned`; idempotent |
| `abandon` | `abandoned`; idempotent on ended sessions (status unchanged) |

## Phase 5: voice (additive fields only, no new routes)

Voice uses the browser's Web Speech API (speech-to-text and text-to-speech). **The server never receives audio**, only
the text the student reviewed and submitted. Sub-flag `AI_MOCK_INTERVIEW_VOICE_ENABLED` (default off; only read
behind the master flag and tenant allow-list).

| Where | Field | Notes |
|---|---|---|
| `GET /options` | `modes: [{value, label}]` | `[text]`, or `[text, voice]` when the voice flag is on |
| `POST /sessions` | `mode?: "text" \| "voice"` (default `text`) | `voice` while the voice flag is off → **422** "Voice interviews are not available"; no row created |
| `POST /sessions/{id}/answer` | `source?: "typed" \| "stt"` (default `typed`) | `stt` on a text session → **422**; a session not in progress → **409** first (as in Phase 3). Any other value → 422. A label only: never sent to the worker, never affects scoring |
| Session `turns[]` | `answer_source: "typed" \| "stt" \| null` | `null` until answered |

- Idempotency is unchanged: re-sending the same `turn_seq` + text is a no-op whatever its `source` (the first one is kept).
- Turning the voice flag off blocks **new** voice sessions only; voice sessions already created can still be answered and finished.
- The worker contract is unchanged (the transcript keys stay `{seq, topic, question, is_follow_up, answer}`).

## Phase 6.1: video (additive value only, no new routes)

Video = a camera self-view that never leaves the browser, plus the Phase 5 speech path. **The server never receives
video or audio**, only reviewed answer text. Design: `specs/phase-6-video-interview.md`.

Gate (all required; `mock_interview_video_enabled_for`): master switch, master tenant allow-list,
`AI_MOCK_INTERVIEW_VIDEO_ENABLED`, the institute in `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS` (**empty or invalid = no
institute**), and the student role. Independent of the voice flag.

| Where | Change | Notes |
|---|---|---|
| `GET /options` | `modes` may also contain `{value:"video", label:"Video"}`, after `text`/`voice` | Only when the gate passes for the caller |
| `POST /sessions` | `mode?: "text" \| "voice" \| "video"` | `video` while the gate fails → **422** "Video interviews are not available"; no row created. Staff 403 / master-off 404 as before |
| `POST /sessions/{id}/answer` | `source: "stt"` accepted for `voice` **and** `video` sessions | `stt` on a text session → 422 with the unchanged Phase 5 message |

- No media route or field exists: extra keys such as `video`, `frame`, `audio` or `camera` are rejected (422, `extra="forbid"`).
- Turning video off (switch or allow-list) blocks **new** video sessions only; existing ones can be answered, finished and reported (D12).
- Session, history and report responses are unchanged (`mode` already carried the value). The report `disclaimer` is unchanged.
- The worker contract is unchanged; `mode` is never sent to the worker.

## AI worker contract (internal, `X-Worker-Token`)

Mounted only when the worker's `MOCK_INTERVIEW_ENABLED=true`.

- **`POST /interview/plan`**
  - Request: `{config, profile, resume_text?, job_description?}`.
  - Response: `{plan: {version, topics[{name, area, focus, source, grounding}], question_budget, jd_analysis, planner}, provider}`.
  - Never fails because of the LLM (deterministic fallback plan).
- **`POST /interview/turn`**
  - Request: `{config, plan, profile, resume_text?, job_description?, transcript[{seq, topic, question, is_follow_up, answer, evaluation?}], remaining_seconds}`.
  - Response: `{action: opening|follow_up|next_topic|end, evaluation?, question?{seq, topic, is_follow_up, text}, closing_message?, end_reason?, level, provider}`.
  - 422: transcript out of order, or the last question unanswered.
  - 502: no question could be generated (no provider details in the body).

## Phase 4: reports and history

**Gates:** the same as Phase 3. Behind the flag, students only, own sessions only (others get 404).

**`HistoryItem`:**
- the `SessionSummary` fields;
- `answered` (int);
- `report: {overall_score, created_at} | null`.

**`Report`:**
```json
{
  "session_id": 12, "interview_type": "hr", "target_role": "Backend Developer", "company_name": null,
  "difficulty": "medium", "language": "en", "duration_minutes": 30,
  "started_at": "...", "completed_at": "...", "created_at": "...",
  "overall_score": 63.5, "rubric_version": "hr-v1",
  "categories": [{"key": "relevance", "label": "Relevance", "weight": 0.25,
                  "description": "Answers the question that was asked", "score": 70.0, "assessed": 4}],
  "questions": [{"seq": 1, "topic": "Introduction", "question": "...", "is_follow_up": false,
                 "answer_text": "...", "status": "scored",
                 "scores": [{"key": "relevance", "label": "Relevance", "score": 4,
                             "evidence": "verbatim words from the answer"}],
                 "feedback": "..."}],
  "strengths": [{"text": "...", "seq": 1, "evidence": "..."}],
  "weaknesses": [{"text": "...", "seq": null, "evidence": ""}],
  "recommendations": ["..."],
  "disclaimer": "This is AI-generated practice feedback ..."
}
```

**Field rules:**
- `status` is one of `scored`, `no_answer`, `dont_know` or `not_assessed`.
- `no_answer` and `dont_know` questions score 0 in every category.
- Every non-zero score has an `evidence` quote taken from the student's own answer.
- `disclaimer` is a fixed backend string, built at read time and not stored. Since Phase 5 it is modality-independent:
  "This is AI-generated practice feedback based on the answers you submitted during this practice session, including
  typed or speech-to-text answers. It can make mistakes, and practice scores do not predict selection in a real
  interview." The response shape and scoring are unchanged.
- `category.score` is `null` when nothing could be assessed.
- The question text and answers come from the turns. The plan, the resume and the JD are never returned.

| Situation (POST `/report`) | Code |
|---|---|
| Report already exists | 200, the stored report, no AI call (reports are final) |
| Session `setup` / `in_progress` | 409 "Finish the interview…" |
| Session `abandoned` | 409 "Cancelled interviews don't have a feedback report." |
| Another request is generating | 409 "already being prepared" |
| Worker down, failed or timed out, or its output is invalid | 503; nothing stored, retry. After a **timeout**, retries get 409 until the 180 s lease expires |
| 3 × "no answer could be assessed with evidence" | 409 "We couldn't assess the answers in this interview…" (final) |
| More than 5 POSTs per minute (per student) | 429 |

**Worker contract:** `POST /interview/report`, internal, `X-Worker-Token`, mounted only when `MOCK_INTERVIEW_ENABLED`.
- **Request:** `{config, plan?: {topics[{name, focus}], question_budget}, transcript[{seq, topic, question, is_follow_up, answer|null}]}`. The seqs must run 1..n (otherwise 422).
- **Response:** `{rubric_version, overall_score, categories[], questions[{seq, status, scores{key: {score 0-5, evidence}}, feedback}], strengths[], weaknesses[], recommendations[], provider}`.
- **Errors:** 502 when a scoring call failed (retryable); 422 when nothing could be assessed with evidence.

## Phase 7: production operations (additive; all new routes default off)

All under `/v1/mock-interview` (and the unversioned alias). Content-free: no route returns questions, answers,
plans, resumes, job descriptions or reports.

### Student

| Method | Path | Success | Notes |
|---|---|---|---|
| GET | `/usage/me` | `{available, reason, daily_limit, used_today, remaining_today, voice_allowed, video_allowed}` | 404 unless `AI_MOCK_INTERVIEW_OPS_ENABLED` is on for the institute. `reason`: `paused` \| `daily_limit` \| `institute_limit` \| null. Only the caller's own numbers. |

`POST /sessions` may additionally answer (only with institute operations on): **403** "…paused…", **422** "Voice/Video
interviews are not available" (institute turned the mode off), **429** daily/institute limits. `GET /options` may
list fewer `modes` when an institute turned voice/video off. Response shapes are unchanged.

### Institute operations (`/admin/*`)

404 for everyone unless operations are on for the caller's institute; then GET = `admin` + `tpo`, PUT = `admin`
(TPO 403); teachers, students, recruiters 403; data of other institutes is never counted (diagnostics 404).

| Method | Path | Success |
|---|---|---|
| GET | `/admin/usage?days=1..90` (default 30) | `{days, since, totals{sessions_*, ai_calls, ai_failures, circuit_open, quota_blocked, llm_calls, input_tokens, output_tokens, calls_with_unknown_tokens, calls_with_partial_tokens, avg_latency_ms, p95_latency_ms}, daily[], by_step[], by_model[], by_outcome{}, modes{}, cost{status, currency, total_micros, priced_calls, unpriced_calls, price_versions[]}}` |
| GET | `/admin/usage/students?days&limit=1..100&offset` | `[{student_id, full_name, sessions_created, sessions_completed, ai_calls, ai_failures, input_tokens, output_tokens, last_activity}]`, `X-Total-Count` |
| GET | `/admin/controls` | `{feature, configured, paused, voice_allowed, video_allowed, limits{}, effective_limits{}, platform_limits{}, platform_voice_enabled, platform_video_enabled, updated_at, can_edit}` |
| PUT | `/admin/controls` | body `{paused, voice_allowed, video_allowed, limits{student_sessions_per_day, tenant_sessions_per_month, tenant_tokens_per_month}}` (unknown fields 422; a limit above the platform limit 422) → same as GET; writes audit `mock_interview.controls_update` |
| GET | `/admin/sessions/{id}/diagnostics` | `{session_id, student_id, status, mode, interview_type, duration_minutes, created_at, started_at, completed_at, turns, answered, report_generated, events[{event_id, created_at, step, outcome, http_status, error_class, latency_ms, llm_latency_ms, llm_calls, input_tokens, output_tokens, fallback_used, provider, model, request_id}]}` |
| GET | `/admin/health` | `{circuit_enabled, steps[{step, state, consecutive_failures, retry_in_seconds, last_hour_calls, last_hour_failures, last_hour_error_rate}], note}` |

`cost.status`: `pricing_not_configured` (no approved table: `total_micros` null), `estimated`, `partial`,
`no_priced_calls`, `mixed_currency`. Token counts never include unknown values.

Rate limits: 60/min per caller on usage/diagnostics, 20/min on PUT.

### Audit (additive filters)

`GET /v1/audit` (admin only, unchanged response) gains `target_type`, `since`, `until` (ISO-8601; naive = UTC;
`since > until` → 422).

### Worker contract additions

`/interview/plan|turn|report` successful responses carry an optional `usage` block: `{provider, model, llm_calls,
failed_calls, fallback_used, input_tokens|null, output_tokens|null, tokens_reported, tokens_unreported,
llm_latency_ms}`. The backend removes it before any other processing and stores it in `ai_usage_events`. Requests
carry `X-Request-ID` and `X-AI-Invocation-ID` (Phase 7.1).

### Metrics

Production `/metrics` (backend and worker) only with a valid `METRICS_TOKEN`; see
`runbooks/metrics-access.md`.

## Phase 8.1: coding round, review-only (additive; default off)

Gate: `AI_MOCK_INTERVIEW_ENABLED` + master allow-list + `AI_MOCK_INTERVIEW_CODING_ENABLED` + the institute in
`AI_MOCK_INTERVIEW_CODING_TENANT_IDS` (**empty = no institute**) + student role + at least one servable problem
(`published`; `draft` only with the development-only `AI_MOCK_INTERVIEW_CODING_ALLOW_DRAFT_PROBLEMS`, ignored when
`ENVIRONMENT=production`). Every coding route 404s when the gate is closed. **No code is executed** (review-only,
P8-D8); there is no run endpoint (B1-c).

### Changes to existing routes (approved P8-D16)

- `POST /sessions`: `interview_type` also accepts `"coding"` (422 "Coding interviews are not available" when the gate
  is closed); `duration_minutes` also accepts `60` — coding rounds must use 30/45/60, HR/technical keep 15/30/45
  (422 otherwise); optional `coding: {languages[1..4] of python313|cpp17|java21|c11, topics[≤5], problem_key?}`
  (only with `"coding"`). Coding rounds are text-only in Batch 1: `mode` voice/video → 422 "Coding rounds are
  text-only for now".
- `/options.interview_types` lists `{value: "coding", label: "Coding"}` only while the gate admits the caller.
- Session detail (`GET /sessions/{id}`, `/sessions/active`, create/start/finish/abandon responses) gains
  `coding_summary: {problems, problems_closed, graded_submissions, review_only}` **only for coding sessions**; every
  other session's JSON is byte-identical (the key is omitted).
- `POST /sessions/{id}/start` on a coding round assigns problems (no AI call); `finish` turns saved drafts into
  automatic graded submissions (completed when anything was submitted, else abandoned); `abandon` as before.
- Coding rounds on the HR/technical routes (documented 409s until 8.3): `POST …/answer`, `POST …/next` →
  `409 "Coding rounds don't use interview questions yet."`; `GET|POST …/report` →
  `409 "Feedback for coding rounds is not available yet."`
- `/history` lists coding rounds unchanged in shape (`interview_type: "coding"`, `report: null`).

### New routes (students only; another student's or institute's session → 404)

| Route | Notes | Limit |
|---|---|---|
| `GET /coding/catalog` | languages, topics, difficulties, durations `[30,45,60]`, `graded_submissions_per_problem` (3), `hints_per_problem`, `source_max_bytes` (65536), `review_only: true`, `problems[{key,title,difficulty,topics}]` (practice choice) | — |
| `GET /sessions/{id}/coding` | `{session_id, status, languages, review_only, remaining_seconds, graded_submissions_per_problem, attempts[{seq, status, language, draft_source, draft_rev, hints_used, hints_available, submissions_used, submissions_remaining, problem}]}`; `problem` = public fields only (statement, formats, constraints, examples, visible tests, starter per language, time/memory limits, `hints_revealed`) | — |
| `PUT /sessions/{id}/coding/attempts/{seq}/draft` | `{language, source ≤ 64 KB UTF-8, draft_rev}` → `{seq, draft_rev, saved_at}`; stale `draft_rev` → 409 | 30/min |
| `POST /sessions/{id}/coding/attempts/{seq}/submissions` | `{language, source, idempotency_key [A-Za-z0-9_-]{8,64}}` → **202** `{id, attempt_seq, kind, language, status: "not_executed", verdict: null, verification: "not_verified", is_best, created_at}`; same key → same row; same key + other code → 409; identical code → earlier row, no count; 4th → 409; > 6 graded per problem per student in 7 days → 429 | 3/min per problem |
| `GET /sessions/{id}/coding/attempts/{seq}/submissions` | attempt history (metadata, no code) | — |
| `GET /sessions/{id}/coding/submissions/{sid}` | one submission (no code) | 60/min |
| `POST /sessions/{id}/coding/attempts/{seq}/hints` | next authored hint `{seq, hint_number, hint, hints_used, hints_available}`; none left → 409. Hints never lower a score | 5/min |
| `POST /sessions/{id}/coding/attempts/{seq}/done` | closes one problem (idempotent) → coding state | 20/min |

Stable messages: 409 "This coding round is not in progress", "This problem is closed. Your saved code is kept.",
"Your code was changed in another tab or window. Reload to continue.", "You have used all 3 graded submissions for
this problem. Your best result is kept.", "There are no more hints for this problem.", "This request key was
already used for different code."; 422 "Choose one of the languages selected for this round.", "Code is limited to
64 KB.", "Write some code before submitting."; 429 "You have submitted this problem 6 times in the last 7 days. Try a
different problem."; 503 "Coding problems are unavailable right now. Please try again later." (invalid bank).
Rate limits are per caller and per request path (slowapi), i.e. per problem for the attempt routes.

Time: a round whose time is up ends on the next request (saved drafts become automatic submissions,
`end_reason: "time_up"`). No AI call, no ledger row and no log line carries source code.

## Phase 8.3: AI coding review, review-only (additive; default off)

Needs the coding gate (Phase 8.1) **and** `AI_MOCK_INTERVIEW_CODING_AI_ENABLED` (backend) **and** the worker's
`MOCK_INTERVIEW_CODING_ENABLED`. Nothing is ever executed. With the AI flag off every route below behaves exactly as
in Batch 1.

### Changes to existing routes (additive)

- `GET /sessions/{id}/coding`: optional `ai_enabled` (bool) and, per attempt, `explanation`
  (`[{turn_seq, question, answer_text, answered_at}]`).
- `POST /sessions/{id}/coding/attempts/{seq}/hints`: optional `source` (`"authored"` | `"ai"`). An AI hint is served
  only after the authored hints are used and only within the per-problem cap (easy/medium 3, hard 2); it is filtered
  (≤ 3 code lines, no correctness claims) and **not stored** (shown once). AI unavailable or unsafe → **503**, the
  hint count is given back.
- `POST /sessions/{id}/coding/attempts/{seq}/done`: with AI help, a problem **with a graded submission** moves to
  `explaining` and gets a typed explanation question (worker `/interview/coding/followup`; fixed fallback question
  when the worker is down). Without a submission it closes as before. Idempotent; concurrent calls ask once.
- `POST /sessions/{id}/answer` for a **coding** session (AI help on): answers an explanation question by `turn_seq`
  (typed only — `source: "stt"` → **422**, decision B1-a). After the answer the server asks one more question
  (max 2) or closes the problem. Same answer again = no-op; different answer → 409; not the current/explanation
  question → 409; ended round → 409. With AI help off: the documented 409 "Coding rounds don't use interview
  questions yet." is unchanged. `/next` and the report routes keep their documented 409 for coding rounds.

### New routes (students; owner + institute; another student or institute → 404)

| Method + path | Purpose | Limit |
|---|---|---|
| `GET /sessions/{id}/coding/feedback` | stored feedback, 404 until generated | — |
| `POST /sessions/{id}/coding/feedback` | generate once (idempotent, final) | 5/min |

POST errors: **409** AI help off ("Feedback for coding rounds is not available yet."), round not finished, cancelled
round, nothing submitted, or another request is preparing it; **503** worker unavailable or unusable output (nothing
stored; retry). One AI review per problem with a submission (ledger step `code_review`); all problems or nothing.

`CodingFeedbackOut`:
```json
{"session_id": 12, "rubric_version": "coding-v1-unverified", "review_only": true, "verification": "not_verified",
 "uncertainty_note": "These are AI estimates …", "disclaimer": "This is AI-generated practice feedback …",
 "provider": "groq", "created_at": "…",
 "problems": [{"attempt_seq": 1, "problem_title": "…", "language": "python313", "status": "ai_estimated",
   "verification": "not_verified",
   "objective": [{"key": "correctness", "label": "Correctness", "weight_percent": 40, "status": "not_verified"}, …],
   "estimates": [{"key": "code_quality", "label": "Code quality", "weight_percent": 15, "kind": "ai_estimate",
                  "score": 4, "assessed": true, "evidence": [{"line": 4, "quote": "total = sum(nums[1:])"}],
                  "rationale": "…", "uncertainty": "AI estimate from reading your code. The code was not run, …"}, …],
   "complexity": {"time": "O(n)", "space": "O(n)", "justification": "…", "quote": {"line": 4, "quote": "…"}},
   "observations": [{"line": 4, "text": "…"}], "consider_cases": ["Consider …"], "testing_notes": ["…"],
   "hints_used": 1, "explanation_answers": 2}]}
```
**Individual AI estimates vs scores:** each of the three AI categories may carry its own **individual 0–5 AI
estimate** (`estimates[].score`), always with verified `evidence` and its own `uncertainty` text. These are never
combined: there is **no overall, problem, session or AI-only score** in any response, and `weight_percent` is shown
for rubric transparency only (P8-D7). Correctness, robustness and efficiency are always `not_verified`. `score: null`
with `assessed: false` = not assessed (no verified evidence).

**AI hint fallback is dormant for the current problem bank:** every one of the 24 problems has 3 authored hints and
the caps are easy/medium 3, hard 2, so authored hints always cover the cap and the AI hint worker is never called.
The fallback activates only for a problem with fewer authored hints than its cap (verified with a deterministic test
fixture). `status: "not_submitted"` = nothing was submitted for that
problem (not reviewed).

### Worker routes (internal; `MOCK_INTERVIEW_ENABLED` + `MOCK_INTERVIEW_CODING_ENABLED`)

`POST /interview/coding/hint` (`{problem, language, code, previous_hints, hint_number}` → `{hint}`; 502 when no safe
hint), `POST /interview/coding/followup` (`{problem, language, code, explanation, max_questions}` →
`{action: "ask"|"end", question, source}`; never fails on LLM errors), `POST /interview/coding/review`
(`{problem, language, code, explanation, hints_used}` → AI estimates; 502 on failure). Requests carry public
problem material only; model output with extra keys is rejected. Every response has the Phase 7.2 `usage` block.

## Phase 8.4: company profiles and company-context questions (additive; default off)

Needs the master switch + master allow-list + `AI_MOCK_INTERVIEW_COMPANY_ENABLED` (backend; else every route 404)
and, for skill maps and the claim filter, the worker's `MOCK_INTERVIEW_COMPANY_ENABLED`.

| Method + path | Who | Purpose |
|---|---|---|
| `GET /company/role-templates` | student, admin, TPO | generic role templates (no company content) |
| `GET /company/profiles` | student | published profiles of my institute (brief) |
| `GET /company/profiles/{id}` | student | one published profile; draft/archived/other institute → 404 |
| `POST /company/jd-skills` | student (5/min) | `{job_description}` \| `{drive_id}` (open, own) \| `{profile_id}` (published, own) → skills, each with a verbatim JD quote; `matched`/`missing` vs verified skills; `focus_order`. 503 when the AI is down. Nothing stored |
| `GET /company/profiles/admin` | admin, TPO | all my institute's profiles |
| `POST /company/profiles/admin` | admin, TPO (20/min) | create a draft (audited) |
| `PUT /company/profiles/{id}` | admin, TPO | replace; archived → 409 (audited) |
| `POST /company/profiles/{id}/publish` | admin, TPO | needs ≥ 1 source (422 otherwise); idempotent (audited once) |
| `POST /company/profiles/{id}/archive` | admin, TPO | archive (never delete); idempotent (audited once) |
| `POST /company/profiles/from-drive/{drive_id}` | admin, TPO | draft pre-filled from an **open** drive of my institute; else 404 (audited) |

Teachers and recruiters: **403** on every route. Another institute's profile or drive: **404**. Sources:
`{kind: drive|jd|institute_note|public_url, label, url? (public_url only, http(s)), drive_id? (drive only)}`; URLs are
**never fetched**; students get `{kind, label, domain}` only. Audit actions
`mock_interview.company_profile_{create,update,publish,archive}`, summaries content-free (id, status, source count).

### Changes to existing routes (additive, P8-D11)

- `POST /sessions`: optional `company_profile_id` (published, own institute; else 422 "That company profile is not
  available"), `role_template_key`, `focus_skills` (≤ 20 strings). HR/technical only (coding → 422). Company
  feature off → 422 "Company profiles are not available". A frozen copy is stored in `plan_json["company"]`.
- Session detail: optional `company_context` `{profile_id, name, role_title, role_template_label, sources,
  attribution: "From your institute", disclaimer, evaluation: "standard_rubric"}` — only on such sessions.
- Worker `/interview/plan` and `/interview/turn`: optional `company_context` (generation only). `/interview/report`
  is unchanged: the evaluation payload is identical with and without a profile; no company-fit or competency score.
- Worker `POST /interview/jd/skills` (`{job_description}` → `{skills: [{skill, category, importance, evidence}]}`).
