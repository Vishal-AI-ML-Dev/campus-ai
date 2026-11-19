# Phase 3 — Text Interview Engine (design spec)

Status: **design**, written 2026-11-18 before any Phase 3 code. Source of requirements: the Phase 3 brief
(HR, technical, resume- and JD-grounded text interviews) and IMPLEMENTATION_PLAN.md §4–§6.

## 1. Scope

**In scope:**
- Text interviews of type `hr` or `technical`, optionally grounded on the student's own resume and/or a pasted job description.
- "Resume-based" and "JD-based" are not separate types: they are an HR or technical interview with a resume and/or JD attached.
- Session lifecycle, persisted turns, refresh recovery, ending early, and abandoning.

**Out of scope:**
- Reports and scoring UI (Phase 4); the per-answer evaluation is stored now but not shown.
- Voice, video, coding, company/full simulations, and faculty campaigns.

**Schema:** no new migration. The Phase 2 tables are enough (§6).

## 2. Configuration (validated on the backend)

| Field | Values | Default | Storage |
|---|---|---|---|
| `interview_type` | `hr`, `technical` | required | `interview_type` |
| `difficulty` | `easy`, `medium`, `hard` (UI labels: Beginner, Intermediate, Advanced) | `medium` | `difficulty` |
| `duration_minutes` | 15, 30, 45 | 30 | `duration_minutes` |
| `target_role` | 2–150 chars, control characters stripped | required | `target_role` |
| `company_name` | ≤150 chars | none | `company_name` |
| `resume_id` | the caller's own resume, same institute | none | `resume_id` |
| `job_description` | ≤10,000 chars | none | `job_description` |
| `language` | `en`, `hi`, `hinglish` | `en` | `language` |

- `mode` is always `text`. Unknown fields are rejected (`extra="forbid"`).
- DB values are reused unchanged, so the Phase 1 `/catalog` and the Phase 2 CHECK constraints stay as they are.
- The new `/options` endpoint returns the Phase 3 subset, with labels.

## 3. AI worker engine (`ai-worker/interview/`, stateless)

The worker never touches the database. On every call the backend sends the config, the plan and the transcript.

```
POST /interview/plan
  profile_loader → resume_context_loader → jd_analyzer → planner
POST /interview/turn
  interview_controller ─(no turns yet)──────────────────────────────▶ question_generator ─▶ END
                       └(answer to process)▶ answer_processor ─▶ answer_evaluator
                                             ─▶ follow_up_decider ─▶ question_generator ─▶ END
```

| Node | Kind | Job |
|---|---|---|
| `profile_loader` | deterministic | Normalizes the verified profile: skills, verified projects, CGPA. No name or email is sent. |
| `resume_context_loader` | deterministic | Sanitizes the resume Markdown (untrusted): strips control characters and delimiter tags; truncates to 8k chars. |
| `jd_analyzer` | LLM, structured | Extracts role, required skills and responsibilities from the JD (untrusted). Skipped when there is no JD; on failure, uses empty analysis. |
| `planner` | LLM, structured | Picks topics for the type, role, difficulty, resume and JD. Topic 0 is always "Introduction". On failure, uses a deterministic topic library plan. |
| `interview_controller` | deterministic | Opening vs. processing an answer. Computes question budget, time left and current difficulty. |
| `answer_processor` | deterministic | Normalizes the latest answer and flags empty / "I don't know" / "pata nahi" / skip. |
| `answer_evaluator` | LLM, structured | Coverage (full/partial/none), depth 1–5, missing points, evidence quote, follow-up advice, difficulty signal. On failure, uses a heuristic evaluation marked `source: "heuristic"`. Skipped for "don't know" answers. |
| `follow_up_decider` | deterministic | Chooses `follow_up`, `next_topic` or `end` (policy below). |
| `question_generator` | LLM, structured | Writes the opening (self-introduction + first question), the next question or follow-up, or the closing message. Never reveals scores. Retries once if the question duplicates an earlier one. **On failure: 502, with no fallback question**. |

**Follow-up policy** (deterministic, testable):
1. **End** when time left ≤ 45 s, the answered count reaches the question budget, or the hard cap of 30 turns is hit.
2. **Follow up** when all of these hold:
   - the answer was not "don't know";
   - coverage is not `full`, or the evaluator recommends a probe;
   - follow-ups on this topic < max (1 for easy, 2 otherwise);
   - spare budget remains after reserving one question per untouched topic.
3. **Next topic** = the first planned topic not yet asked. If none are left, follow up when allowed, otherwise end.

**Difficulty adaptation:**
- `level = clamp(base + sum(difficulty_signal of the last 3 evaluated answers))`, over easy/medium/hard.
- It is a pure function of the transcript, so it needs no hidden state.

**Budgets:**
- Question budget: 15 min → 6, 30 min → 12, 45 min → 18.
- Topics: 4, 7 or 10, including Introduction.

**Grounding and prompt injection:**
- Resume, JD and answers go inside `<resume>`, `<job_description>` and `<candidate_answer>` tags. The system prompt says their content is data and that instructions inside them are ignored.
- Tag-like text inside the content is neutralized, and the content is truncated.
- Planner topics that claim `source: resume|jd` must carry a `grounding` quote found (normalized) in that text. Otherwise the topic is downgraded to `role` and the quote is dropped.
- An evaluator `evidence` quote that is not a substring of the answer is blanked.
- Prompts forbid inventing experience, projects, skills, employers or qualifications.
- Questions are capped at 1,000 chars.

**Language:**
- `en`: English.
- `hi`: Hindi in Devanagari, with technical terms allowed in English.
- `hinglish`: Roman-script Hindi–English mix.

**Topic library:**
- HR: introduction, strengths and weaknesses, teamwork (STAR), leadership, conflict resolution, career goals, situational, project discussion, behavioural (STAR).
- Technical: Python, Java, C++, DSA, OOP, DBMS, SQL, OS, CN, ML, DL, GenAI, LLMs/RAG, backend, frontend.
- Technical topics are picked from the role, JD and verified skills; not every topic goes into every interview.

## 4. Backend (`/v1/mock-interview`, also unversioned)

- **Gate:** every new route uses `require_mock_interview` (404 when off), then a **students-only** check (403 for other roles).
- **Ownership:** sessions are loaded with `tenant_id == user.tenant_id AND student_id == user.id`; anything else returns 404.
- **Resume ownership (Phase 2 finding M1):** `resume_id` is accepted only if `Resume.student_id == user.id AND Resume.tenant_id == user.tenant_id`, else 404. It is re-checked with the same filter every time resume text is loaded for the worker.

| Method & path | Purpose |
|---|---|
| `GET /options` | Phase 3 choices + labels + the caller's resumes (id, title, target_role, is_primary) |
| `POST /sessions` | Create (`setup`). 409 if the student already has a `setup`/`in_progress` session; 429 after 10 sessions in 24 h |
| `GET /sessions` | Own sessions, newest first (max 50) |
| `GET /sessions/active` | The active session detail, or `null` (refresh recovery) |
| `GET /sessions/{id}` | Detail + transcript |
| `POST /sessions/{id}/start` | Plan + opening question. Idempotent: a started session returns its state with no LLM call |
| `POST /sessions/{id}/answer` | `{turn_seq, answer}` → saves, then returns the next question or the closing message |
| `POST /sessions/{id}/next` | Fetch the next question (retry after a worker failure; no LLM call if a question is already pending) |
| `POST /sessions/{id}/finish` | End early: `completed` if ≥1 answer, else `abandoned`. Idempotent |
| `POST /sessions/{id}/abandon` | `abandoned`. Idempotent |

**Session state machine:**
- `setup` → `start` → `in_progress` → (`completed` | `abandoned`).
- `failed` is unused in Phase 3.
- `evaluating` is reserved for Phase 4.

**Detail response (`awaiting`):**
- `start`: not started.
- `answer`: a question is pending.
- `next_question`: the last answer is saved but the next question is missing.
- `none`: ended.

**Idempotency and duplicates:**
- The answer is written with `UPDATE … WHERE answer_text IS NULL` and the result is committed *before* the worker is called.
- `turn_seq` is the idempotency key:
  - same seq + same text → no-op success;
  - same seq + different text → 409;
  - a stale seq → 409.
- New turns insert `seq = last + 1`. A unique-key collision from a concurrent request is caught: rollback, then return the current state. No duplicate seq is possible.
- `start` races resolve the same way.

**Worker failure:**
- Answers are never lost.
- The client gets 503 ("answer saved, try again"); `/next` resumes.
- `_worker_request` already retries transient errors.
- Logs carry the session id and the error class only: no answer, resume or JD text.

**Time:**
- `remaining_seconds = duration*60 − (now − started_at)`, computed on the server and sent to the worker and client.
- An answer after time-up is still saved, then the interview ends.

**Persistence:**
- `plan_json` = `{plan: {version, topics, question_budget, jd_analysis, planner}, provider, closing_message?, end_reason?}`. It is replaced as a whole, never mutated in place.
- `turn_eval_json` = the evaluation of that turn's answer. It is server-only in Phase 3.

**Cost control:** no per-IP limiter on these routes, because a campus NAT would throttle a whole college. Cost is bounded by:
- one active session per student and ≤10 new sessions per day;
- one generation per answered turn;
- the 30-turn hard cap and the time limit.

## 5. Frontend (`/dashboard/mock-interview`, build flag + `/status`)

Flow and recovery:
- On load, `/sessions/active` decides what to show.
- **Setup:** form from `/options`.
- **Instructions:** for a `setup` session, with start or cancel (abandon).
- **Interview room:**
  - transcript;
  - current question;
  - textarea (Ctrl/⌘+Enter submits, ≤5,000 chars with counter);
  - thinking indicator;
  - progress bar and countdown;
  - End interview (confirm);
  - when `awaiting = next_question`, a retry banner.
- **Finished:** closing message + transcript; the report is noted as coming in Phase 4.

Accessibility and layout:
- Labelled controls and `aria-live` for new questions.
- Buttons are disabled while busy, so double submits can't happen.
- Mobile-first layout.

## 6. Database usage (Phase 2 tables, no migration)

- `mock_interview_sessions`: configuration, status, timestamps and plan.
- `mock_interview_turns`: one row per interviewer message that expects an answer, in `seq` order (1…n):
  - `topic` = plan topic name;
  - `is_follow_up` as decided;
  - the answer is filled in place.
- The closing message lives in `plan_json`, so no turn ever waits forever without an answer.
- `mock_interview_reports`: **not written** in Phase 3.

## 6a. Changes after the independent reviews (implemented)

- **One AI step per session:** a lease, implemented as a compare-and-set on `updated_at` + `plan_json.lease`.
  - Parallel `/start` or `/next` gets 409.
  - A late AI result is dropped if the session was finished meanwhile.
- **Rate limit:** per-student slowapi limit (20/min per route) on start/answer/next, keyed by access-token hash, not IP.
- **Worker client:** retries only connect errors, never 5xx or timeouts (no double LLM spend).
- **Create:** locks the user row (Postgres) against parallel creates.
- **Overdue sessions:** auto-close 5 minutes after time-up.
- **Profile:** capped to 40 skills / 10 projects.
- **Answer 30:** is evaluated too.
- **Follow-up allowance:** uses the configured difficulty.
- **Prompt hardening:**
  - replayed transcript sanitized;
  - Unicode Cf characters stripped;
  - unterminated tags neutralized;
  - role/company limited to a character allow-list;
  - follow-up angle and grounding moved into `<context_note>`.
- **Planner:** "Self introduction"-style topics are dropped (Introduction is always topic 0).

## 7. Tests

**Worker, stubbed LLM:**
- HR and technical plans;
- the fallback plan;
- grounding downgrade;
- opening, follow-up on a partial answer, next topic on a full answer;
- end on time or budget;
- duplicate-question retry;
- evaluator fallback;
- question-generator failure → 502;
- injection text kept as data;
- endpoint token check.

**Backend, worker stubbed:**
- create/validate and the feature flag off;
- students only;
- resume ownership (another student's or another institute's resume → 404);
- JD passed to the worker;
- HR and technical flows;
- follow-up persisted;
- turn ordering;
- duplicate and conflicting answers;
- worker failure keeps the answer, then `/next` recovers;
- active-session recovery;
- cross-tenant and cross-student 404;
- finish/abandon idempotency;
- the one-active-session rule.

**Regression:** full backend suite (includes mentor) run sequentially, the worker suite, then `tsc` + `lint`.
