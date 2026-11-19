# AI Mock Interview — API (Phase 3)

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
| POST | `/sessions/{id}/answer` | `{turn_seq, answer}` | `Session` (next question or ended) |
| POST | `/sessions/{id}/next` | – | `Session` |
| POST | `/sessions/{id}/finish` | – | `Session` |
| POST | `/sessions/{id}/abandon` | – | `Session` |

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
