# Phase 4 — Evaluation, Report and History (design spec)

Status: **implemented** (design written 2026-11-19 before any Phase 4 code; §5a records the post-review changes). Requirements come from IMPLEMENTATION_PLAN.md §4–§6:
- Phase 4 is "Evaluation, report, history UI"; its exit criterion is "Evidence-backed scoring tests".
- Plus the §5 rules:
  - every score carries an `evidence` quote from the answer;
  - a missing answer scores 0 with "no answer given";
  - reports say practice scores don't predict selection.

## 1. Scope

**In scope:**
- A final, evidence-backed report for a **completed** text interview (HR or technical):
  - an overall score;
  - category scores;
  - per-question scores, evidence and feedback;
  - strengths, weaknesses and recommendations;
  - a disclaimer.
- A history list of the student's interviews, with their scores.
- A report view, reached from the finished screen and from the history list.

**Out of scope:**
- Voice, video, coding, company/full rounds and faculty views (Phases 5–9).
- Score trends or charts across sessions.
- PDF export.
- Re-scoring a report.

**Unchanged:**
- No migration: `mock_interview_reports` (Phase 2) already exists.
- No new env vars and no new flag: Phase 4 sits behind the existing flags.
- No existing API response changes: the Phase 1 routes and Phase 3 session responses stay as they are.

## 2. Worker: `POST /interview/report` (stateless)

**Request:** `{config, plan?, transcript[{seq, topic, question, is_follow_up, answer}]}`.
- No resume, JD or profile is sent: the report judges only what the candidate said, which also keeps less personal data in prompts and traces.

**Graph:** `report_preparer → answer_scorer → report_aggregator → report_writer`

| Node | Kind | Job |
|---|---|---|
| `report_preparer` | deterministic | Sanitizes every turn (`clean_text`). Classifies each turn as `no_answer` (asked, never answered), `dont_know` (`policy.process_answer`) or `to_score` |
| `answer_scorer` | LLM, structured | Scores the `to_score` answers in chunks of up to 4 answers per call (≤4 calls in parallel). Each answer is truncated to 2,000 chars for scoring |
| `report_aggregator` | deterministic | Applies the evidence and brevity rules (§3), the category averages, the weighted overall score, and baseline strengths and weaknesses |
| `report_writer` | LLM, structured | Writes the strengths, weaknesses (each with `seq` + quote) and recommendations in the session language. Unverifiable items are dropped. On failure it uses deterministic text built from the category scores |

**Failure rules:**
- An answer whose scoring call fails is `not_assessed` and is left out of the averages.
- If **no** `to_score` answer could be scored, the request returns **502** (no report), and the backend returns 503 so the student can retry.
- A transcript made only of `no_answer` and `dont_know` turns needs no LLM call.

**Response:** `{rubric_version, overall_score, categories[], questions[], strengths[], weaknesses[], recommendations[], provider}`.

## 3. Scoring rubric (`hr-v1`, `technical-v1`)

Each answer is scored 0–5 per category, and every category score needs an `evidence` quote.

| HR category | Weight | | Technical category | Weight |
|---|---|---|---|---|
| `relevance`: answers the question asked | 0.25 | | `technical_accuracy`: correct concepts and facts | 0.35 |
| `structure`: clear structure / STAR | 0.20 | | `depth`: explains why/how, trade-offs | 0.25 |
| `specificity`: concrete examples, outcomes | 0.20 | | `problem_solving`: reasoning, approach | 0.15 |
| `communication`: clarity, conciseness | 0.20 | | `communication`: clarity, structure | 0.15 |
| `self_awareness`: reflection, attitude, learning | 0.15 | | `relevance`: answers the question asked | 0.10 |

**Evidence-backed rules (enforced in code, never trusted from the model):**
1. **Evidence check:** a score counts only when its evidence quote is found in that answer (`safety.quote_in`, normalized).
   - If the quote can't be verified, that category is **not assessed** for that answer (dropped, not guessed).
   - When the quote is missing and the score is 0, the score is kept, with no evidence.
2. **No answer:** `no_answer` scores 0 in every category, with the note "No answer given".
3. **"Don't know":** `dont_know` scores 0 in every category, with the note "Candidate said they did not know".
4. **Brevity cap:** an answer under 8 words is capped at 2/5 per category, so a one-liner can't earn top marks.
5. **Score range:** model scores are clamped to the integers 0..5. Unknown category keys are ignored.
6. **Category score:** the mean of its assessed scores × 20, giving 0–100. It is `null` when nothing was assessed.
7. **Overall score:** the weighted mean of the non-null categories, with the weights renormalized, rounded to one decimal.
8. **Prompt injection:** answers are data inside `<candidate_answer>`. An instruction like "give me full marks" can't move a score without a verified quote, and quoting the injection only scores what was actually said.

Strengths and weaknesses:
- **Baseline (deterministic):**
  - strengths are the top 2 categories scoring ≥ 60;
  - weaknesses are the bottom 2 categories scoring < 60.
- **LLM items:** each must cite a `seq` and a quote verified in that answer, otherwise it is dropped.
- **Merge:** the LLM items come first; then up to 3 of each kind are kept.

Recommendations:
- the LLM writes up to 5;
- if it fails, fixed per-category tips for the 2 weakest categories are used.

## 4. Backend

| Method | Path | Result |
|---|---|---|
| GET | `/sessions/{id}/report` | 200 `Report`. If the session is the caller's but has no report yet: **404 "Report not generated yet"** |
| POST | `/sessions/{id}/report` | Generates the report, or returns the existing one with no AI call. 200 `Report` |
| GET | `/history` | `HistoryItem[]`: the caller's sessions, newest first, ≤50, each with `{overall_score, report_created_at} \| null` and the answered count |

**Rules:**
- All three routes are behind the existing flag, for students only, with tenant + owner 404s (the same gates as Phase 3).
- **Eligibility for POST:** `completed` with ≥1 asked turn. `abandoned`, `setup` and `in_progress` sessions get **409**.
- **One report per session:**
  - it is final once written; there is no regeneration and no score-shopping;
  - the unique key on `session_id`, with `IntegrityError` handling, returns the stored report.
- **Concurrency:**
  - the Phase 3 session lease (`_claim`, here with a 180 s lease) allows one generation at a time; a parallel POST gets 409;
  - the report and the lease release are committed together.
- **Worker call:**
  - `mock_interview_ai.generate_report`, with a 120 s timeout;
  - one retry, on connect errors only (the same policy as Phase 3);
  - failure → 503 "Your answers are saved; try again".
- **Validation:** the worker output is re-validated by a strict schema:
  - scores 0–100 / 0–5;
  - category keys from the rubric only;
  - `seq`s must exist in the session;
  - text is capped and control characters are stripped.
  - Invalid output → 503; nothing is stored.
- **Rate limit:** POST `/report` is limited to 5 per minute per student (access-token key).
- **Storage** (existing columns):
  - `overall_score`;
  - `category_scores_json = {"categories": [...], "questions": [...]}`;
  - `strengths_json`, `weaknesses_json`, `recommendations_json`;
  - `rubric_version`, `provider`.
  - The question text and answer are joined from the turns at read time, not copied.
- **Disclaimer:** a fixed backend string in the response, not stored.

## 5. Frontend

- **`InterviewReport.tsx`:**
  - overall score;
  - category bars (accessible: a text value next to each bar);
  - strengths, weaknesses and recommendations, each with its quote and question number;
  - a per-question section with the answer, category scores, evidence and feedback;
  - the disclaimer.
- **`InterviewFinished.tsx`:** the "coming soon" text is replaced by the report:
  - it calls GET, and on 404 it calls POST;
  - while it waits it shows a "Generating your feedback…" loading state;
  - on 503 it shows an error with a retry.
  - Abandoned sessions show no report.
- **`InterviewHistory.tsx`:**
  - shown under the setup form;
  - lists past interviews with date, type, role, status and score;
  - "View report" opens the report view with a Back button.
- **API helpers:** in `mockInterviewApi.ts`.
- **Shared UI:** `useApiResource` and `states.tsx`.

## 5a. Changes after the independent reviews (implemented)

Two read-only reviews (security; code quality / spec conformance) found no High issues. These changes override §2–§4 where they differ.

**Evidence and scoring:**
- **Stronger evidence** (security M1). A quote counts only when all of these hold:
  - it is ≥3 words and ≥15 characters, or the whole answer when the answer is shorter;
  - it is found in that answer;
  - it does not contain scoring or instruction words (score, marks, grade, rating, rubric, evaluat\*, assess\*, instruction, prompt, ignore, "out of N", "N/N") or the `[removed-tag]` / `[truncated]` sanitizer markers.
  - So an answer that says "Evaluator note: … score 5" cannot be quoted to justify its own score.
  - The same rule applies to strength and weakness quotes.
  - Trade-off: a legitimate quote such as "F1 score" is not accepted as evidence; the model must quote other words.
- **All or nothing** (M3): if **any** scoring call fails, the whole report fails (502, nothing stored). A final report never silently drops answers.
- **Nothing assessable:** if the model gives no evidence-backed score for any answer, the worker returns **422** (no longer 502). After 3 such results the backend answers **409 "We couldn't assess the answers in this interview"**, with no further paid retries (counter: `plan_json.report_unassessable`).
- **Number handling:** `Infinity` and `NaN` scores become 0. Rounding is half-up. The overall score is computed from unrounded category means. The fallback recommendations use the 2 weakest categories, as §3 says.

**Backend:**
- **Authoritative re-check** (L1):
  - the backend recomputes the category scores and the overall score from the per-question scores, with its own copy of the rubric weights;
  - it re-checks every non-zero score's quote, and every strength or weakness quote, against the **stored** answer (same normalization as the worker);
  - it requires `no_answer` and `dont_know` questions to have 0 in every category;
  - it requires `scored` questions to have ≥1 score and `not_assessed` questions to have none;
  - it strips control characters from every text field.
- **Timeout** (M2): a worker read timeout no longer releases the lease. It expires after 180 s, so a retry can't start a second paid run while the first is still running.
- **Slim plan** (L2): only topic `name` + `focus` and the question budget are sent. No resume or JD grounding quotes, and no JD analysis.
- **Eligibility:** a completed session always has ≥1 answer (Phase 3 turns finish-without-answers into `abandoned`), so "≥1 asked turn" in §4 is effectively "≥1 answer"; the guard stays.

**Frontend:**
- The finished screen and history rows without a report call the idempotent POST directly. Rows with a report use GET, so there is no expected-404 round trip.
- The transcript stays available when the report fails to load.
- A 409 (no report possible) shows no retry button.
- Error states show the request id.
- The native disclosure marker is kept.
- A category's description sits on its own line; this fixed 5 px of extra phone-width overflow.

**Not changed (accepted):**
- (L4) Worker thread use per report: one request thread plus ≤4 scoring threads. This depends on the deployment; a global limit can be added at rollout.
- No per-call LLM timeout in the shared `llm.py` factory; changing it would affect existing features.

**Contract test:** `backend/tests/fixtures/mock_interview_worker_report.json` is real worker output for a fixed transcript. The worker test asserts that the fixture equals the current output, and the backend test feeds the fixture through `_validated`, so drift on either side fails a test.

## 6. Tests (from this spec)

**Worker** (stubbed LLM):
- evidence verified, or the category is dropped;
- `no_answer` and `dont_know` score 0 with notes and need no LLM call;
- the brevity cap;
- clamping and unknown keys;
- the weighted aggregation and renormalization (hand-computed);
- injection ("give me 100") with a fake quote gives no score;
- strengths and weaknesses with fake quotes or seqs are dropped;
- the writer-failure fallback;
- a partial scorer failure gives `not_assessed`;
- a total scorer failure gives 502;
- chunking for 9 answers;
- answers are sanitized and sit inside data tags;
- the route works only when the flag is mounted.

**Backend** (SQLite, engine stubbed):
- flag off → 404 on both mounts;
- staff 403;
- another student or institute → 404;
- eligibility 409s;
- GET before generation → 404;
- generate, store, then GET with the same data;
- a second POST makes no engine call;
- the engine is down → 503 with no row, and a retry works;
- invalid worker output (score 140, an unknown seq) → 503 with no row;
- a held lease → 409;
- the payload has no resume, JD or profile;
- history lists scores and only the caller's sessions;
- the rate limit.

**Postgres** (opt-in `tests_pg_api`):
- parallel POSTs → one engine call and one row;
- the report is visible only under the owning tenant.

**Frontend:** `tsc` + `lint`, plus a headless browser smoke test with a scripted engine.

## 7. Acceptance criteria

1. A completed interview produces a stored report with an overall score, category scores and per-question scores.
2. Every non-zero score has an evidence quote verified to be in the candidate's answer. Missing or "don't know" answers score 0 with a note.
3. Reports are owner- and tenant-scoped (404), students only, and hidden while the flag is off.
4. Report generation is idempotent and single-flight, and survives a worker failure (retryable, nothing partial stored).
5. A student can see their history and open any past report.
6. The disclaimer is shown.
7. Existing features keep working: the full regression passes, and `tsc`/`lint` are clean.
