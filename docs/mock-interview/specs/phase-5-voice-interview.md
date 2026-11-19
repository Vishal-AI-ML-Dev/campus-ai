# Phase 5 — Voice Interview (design spec)

Status: **implemented, uncommitted.** Automated and simulated tests pass; **the real-device manual verification is
pending** (§8). The design was approved "as written" on 2026-11-19, before any Phase 5 code. §5a records the
post-review changes, §5b the quality pass, §5c the current behaviour and §8 the verification record. Overall status:
`../PROJECT_STATE.md` → "Status at a glance".
Decisions (you, 2026-11-19): **browser Web Speech API** for STT and TTS (IMPLEMENTATION_PLAN.md §7 option a);
a **separate voice sub-flag**, default OFF.

## 1. Scope

**In scope:**
- A student can choose **Voice** instead of Text for an HR or technical interview.
- The interviewer **speaks** each question (browser `speechSynthesis`); the question text stays on screen.
- The student **answers by speaking** (browser `SpeechRecognition`). The live transcript fills an editable answer box;
  the student can correct it and then submits. Typing always works as a fallback.
- The answer is stored as text with `answer_source = "stt"`, and goes through the **same** Phase 3 engine and Phase 4
  report. No audio leaves the browser to our servers, and nothing audio is stored.

**Out of scope:** hosted STT/TTS, audio recording/storage, pronunciation or fluency scoring, video (Phase 6),
hands-free auto-submit, voice for coding/company rounds.

**Unchanged:** no migration (Phase 2 already allows `mode = voice` and `answer_source = stt`); no worker change
(scoring prompts already judge "substance, not grammar or accent"); no change to any existing field or status code.

## 2. Flags

| Layer | Flag | Default | Effect |
|---|---|---|---|
| Backend | `AI_MOCK_INTERVIEW_VOICE_ENABLED: bool` (new, optional) | `False` | Voice is offered only when this **and** the existing `AI_MOCK_INTERVIEW_ENABLED` (+ tenant allow-list) are on |

No frontend build flag: the UI follows `/options.modes`. Turning the flag off hides Voice for new interviews; voice
sessions already created can still be finished (their answers cost nothing extra server-side).

## 3. API changes (all additive, behind the existing gate)

| Route | Change |
|---|---|
| `GET /options` | + `modes: [{value, label}]` → `[text]`, or `[text, voice]` when the voice flag is on |
| `POST /sessions` | + optional `mode: "text" \| "voice"` (default `"text"`). `voice` while the voice flag is off → **422** "Voice interviews are not available" |
| `POST /sessions/{id}/answer` | + optional `source: "typed" \| "stt"` (default `"typed"`). `stt` on a text session → **422**. Stored in `answer_source` |
| Session detail `turns[]` | + `answer_source: "typed" \| "stt" \| null` |

Idempotency is unchanged: a retried answer with the same `turn_seq` and text is a no-op whatever its `source`.
The Phase 1 `/catalog` stays as is. History already returns `mode`.

## 4. Frontend

_The design as approved. Some details changed after review (e.g. "Hear question" moved into the answer box, `aria-pressed` removed); the current behaviour is in §5c._

- **`useSpeech.ts`** (new hook, `components/mock-interview/`): feature-detects `speechSynthesis` and
  `SpeechRecognition`/`webkitSpeechRecognition`; `speak(text, lang)`, `cancel()`; `listen()`/`stop()` with interim +
  final results; maps the session language: `en → en-IN`, `hi → hi-IN`, `hinglish → en-IN`. Cleans up on unmount
  (cancels speech, aborts recognition). Mic-permission denial and `no-speech`/`network` errors become friendly messages.
- **Setup form:** a Text / Voice choice, shown only when `/options.modes` includes voice. Under Voice: browser support
  note (Chrome/Edge recommended; typing fallback) and the privacy note (below).
- **`VoiceAnswerBox.tsx`** (new): mic toggle (Start / Stop speaking, `aria-pressed`), listening indicator, live
  transcript appended to an editable textarea (same draft persistence and char limit as `AnswerBox`), "Hear question
  again", mute toggle. Submits `source: "stt"` if any of the text was dictated, else `"typed"`. Recognition stops on
  submit, on question change and on End interview.
- **`InterviewRoom.tsx`:** for `mode === "voice"`, speak each newly shown question once (not replayed on refresh
  unless asked), render `VoiceAnswerBox` instead of `AnswerBox`, and cancel speech while "thinking" or after finish.
  Text sessions render exactly as today.
- Transcript/report: stt answers get a small "spoken" marker; history shows "Voice".

**Privacy note shown before a voice interview:** "Speech is converted to text by your browser. Some browsers (e.g.
Chrome) send audio to their speech service to do this. Campus AI stores only the text you submit, never audio."

## 5. Security

- The server never receives audio; `source` is a label, not trusted for scoring (the transcript is validated, cleaned,
  length-limited and injection-handled exactly like typed answers).
- `mode`/`source` are closed `Literal`s; `extra="forbid"` stays.
- Same tenant/owner 404 rules, student gate and rate limits; no new routes.
- Mic access is requested only on the student's click, never on page load.

## 5a. Changes after the independent reviews (security + code quality)

No High findings. Fixed:
- "Hear question" moved into the answer box and disabled while listening, so the interviewer's voice can't be dictated
  into the answer (reported by both reviews).
- The mic stops when the answer is sending or the interview is ending (it stayed open during End interview), and
  the Stop button stays usable while listening.
- Android-style re-delivered or growing final results are de-duplicated **by text**. An index-only rule was tried
  first; the browser test showed it drops new speech when an engine restarts its result list at 0.
- Dictation past 5,000 chars stops the mic with "Answer limit reached" instead of silently cutting text.
- The `stt` label resets when the student clears the box and types instead.
- The `speaking` state now follows the latest utterance only.
- The status line is an always-present live region, so screen readers announce changes to it.
- `aria-pressed` was removed from buttons whose label already changes.
- Voice matching normalises `en_IN` to `en-in`.
- Backend: the "not in progress" 409 now comes before the "stt on a text session" 422, matching Phase 3 behaviour
  (pinned by a test). `/options.modes` defaults to `[text]`.
- The stale `:stt` draft key is cleared on resync, and the spoken-question mark is cleared on finish.
- The privacy note now also says unsent text stays in the browser tab.
- Test: "flag off rejects voice" now asserts that no session row is created.

Accepted: `source` is self-reported by the browser. It is an informational label with no effect on scoring, and a
repeated identical phrase within one dictation can be merged by the de-duplication.

## 5b. Voice quality pass (after real-device testing, 2026-11-19)

Root causes in our code (not the browser's acoustic model):
- **Brittle de-duplication.** Raw `startsWith` was checked against the last phrase only. Re-deliveries differing only
  in case or punctuation, and whole-session re-deliveries, were appended as duplicates.
- **A lost-phrase race.** An effect copied React state back into the append buffer, so a phrase arriving between a
  render and its effect could be overwritten.
- **Unfinished interim speech was dropped** when the engine ended on its own.
- **Late events** from an old recognition instance could change the current one.
- **Weak error handling.** "network" (typical on Brave, which has no speech service) wasn't explained, and permanent
  errors left the mic retryable.

Changes:
- New framework-free `speechDictation.ts`:
  - word-based merger that ignores case and edge punctuation but appends the student's tokens unchanged;
  - recognition controller with per-session guards;
  - leftover interim kept on an unexpected end;
  - no auto-restart.
- `useSpeech.ts` is now a thin hook over it. It exposes `blocked` (permission/language errors) and `stoppedUnexpectedly`.
- `VoiceAnswerBox.tsx`:
  - the race fix;
  - "Listening stopped…" notice;
  - mic disabled after a permanent error;
  - shows the recognition language, e.g. "Hindi (hi-IN)".
- The report disclaimer is modality-independent ("…including typed or speech-to-text answers…"). It is built at read
  time, so existing reports show it too; scoring is unchanged.
- Unit tests: `frontend/tests/unit/speechDictation.test.mjs` (`npm run test:unit`, built-in `node --test`, no new dependency).

Limits: recognition accuracy (misheard words, fillers, Hindi/Hinglish quality) is decided by the browser's speech service
and the microphone; the app never rewrites words, only removes exact or growing re-deliveries.

## 5c. Implemented behaviour reference (current code)

Files: `frontend/src/components/mock-interview/speechDictation.ts` (framework-free logic), `useSpeech.ts` (React
hooks), `VoiceAnswerBox.tsx` (answer UI), `InterviewRoom.tsx` (question playback), `mockInterviewApi.ts`
(`source` field).

**Languages.** `speechLang()`: `en → en-IN`, `hi → hi-IN`, `hinglish → en-IN`; unknown values fall back to `en-IN`.
The same tag is used for recognition (`rec.lang`) and speech output. The answer box shows it, e.g.
"Recognition language: Hindi (hi-IN)".

**Recognition session** (`createDictation`):
- One session per click on Start/Speak more. Settings: `continuous = true`, `interimResults = true`, `maxAlternatives = 1`.
- **Never restarts itself.** After the engine ends, the student must click again.
- **Results.** For each event, results from `resultIndex` onward are read:
  - final results go through the merger (below);
  - non-final ones form the live interim preview, which is not part of the answer until finalised.
- **The merger (`FinalTranscriptMerger`, one per session)** compares words ignoring case and edge punctuation, but
  appends the recognised tokens unchanged. It:
  - skips the same words at the same index;
  - skips an identical repeat of the previous final phrase;
  - when a final starts with all words already delivered in this session, or with the previous phrase, appends only
    the extra words;
  - otherwise appends the final whole. A possible visible duplicate is preferred over losing speech.
  - It never uses confidence scores and never invents or corrects words.
- **End.** When the engine ends (`onend`), any interim text that was never finalised is passed through the merger and
  kept. The end counts as *unexpected* unless the student pressed Stop or an error was reported; an unexpected end
  shows "Listening stopped. Press Speak more to continue, or edit and submit."
- **Errors.** `aborted` is ignored. Other codes are reported once:

  | Code | Message (summary) | Retry |
  |---|---|---|
  | `no-speech` | No speech was heard; check the mic and try again | allowed |
  | `network` | Speech service unreachable; some browsers (e.g. Brave) don't provide speech recognition; use Chrome/Edge or type | allowed |
  | `audio-capture` | No microphone found, or it is in use | allowed |
  | `not-allowed`, `service-not-allowed` | Microphone or speech access blocked; allow it and reload, or type | **mic button disabled** for the current question |
  | `language-not-supported` | This browser can't recognise the language; type | **mic button disabled** for the current question |
  | start failure, other | Stopped unexpectedly; try again or type | allowed |

- **Stale events.** Each hook callback checks that it belongs to the live session, so a late `onresult`/`onend` from an
  earlier session can't change the current state. After `abort()` the engine's handlers are detached.
- **Mic release.** The mic is released:
  - when the student presses Stop;
  - when an answer is being submitted or the interview is ending (`busy`);
  - at the 5,000-character limit;
  - when the answer box unmounts (`abort()`): question change, finish, leaving the page.

**Transcript editing** (`VoiceAnswerBox`):
- Dictated phrases are appended to an editable textarea. The append buffer is updated synchronously, never re-synced
  from React state, so no phrase can be lost to a render race.
- Submit is disabled while listening, so the student reviews the text first.
- The text is kept as a per-question draft in `sessionStorage` (`mock-interview:draft:<id>:<seq>`, with `:stt`
  marking that it contains dictation).
- It is submitted with `source: "stt"` if any of the current text was dictated, else `"typed"`. Clearing the box
  resets the label.

**Question playback** (`InterviewRoom`):
- Each new question is read once. It is marked read in `sessionStorage` (`mock-interview:spoken:<id>`) only when speech
  actually starts, so a refresh doesn't replay it, and a blocked or cancelled attempt doesn't mark it.
- Reading is skipped when muted or before any user click (browser autoplay rules). "Hear question" replays on demand
  and is disabled while listening, so the interviewer's voice can't be dictated.
- Speech output is cancelled on submit, on finish, before the mic opens and on unmount.

**Backend.** It receives only text:
- `POST /answer {turn_seq, answer, source?}`;
- `stt` is accepted only for `mode = voice` sessions (otherwise 422; a session not in progress gives 409 first).
- The text gets the same validation, cleaning, injection handling, engine and report as typed answers.
- `source` is never sent to the AI worker and never affects scoring.
- No audio route, field or storage exists.

**Evaluation report.**
- Unchanged Phase 4 pipeline (worker `POST /interview/report` → backend re-validation and recomputation → one stored
  report). **Scoring logic was not changed in Phase 5.**
- The disclaimer, built at read time so it applies to existing reports too, now reads: "This is AI-generated practice
  feedback based on the answers you submitted during this practice session, including typed or speech-to-text
  answers. It can make mistakes, and practice scores do not predict selection in a real interview."

## 6. Tests (implemented)

- **Backend** `backend/tests/test_mock_interview_voice.py` (27 tests, SQLite, stubbed worker):
  - `/options` modes with the flag off and on;
  - voice is rejected while the flag is off (no row created), and the master flag and allow-list still apply;
  - `mode` and `source` validation;
  - `stt` is stored and returned; the default is `typed`; `stt` on a text session → 422; 409 before 422;
  - idempotent retry; other student or institute → 404;
  - turning the flag off mid-session still allows finishing;
  - the report payload to the worker is unchanged; no audio field or route;
  - an edited spoken transcript is stored verbatim and drives the follow-up;
  - the disclaimer for text-only, voice-only and mixed sessions.
- **Frontend unit tests** `frontend/tests/unit/speechDictation.test.mjs` (26 tests, `npm run test:unit`, built-in
  `node --test`, fake recognition engine):
  - the language mapping;
  - duplicate, growing and whole-session final results;
  - case and punctuation; Hindi and Hinglish;
  - interim then final;
  - stop and no restart; an unexpected end keeping interim speech;
  - network, no-speech, permission and language errors;
  - `abort()` cleanup; late events ignored; start failure.
- **Browser smoke test** (scratch, not in the repo): headless Chromium, scripted engine and reporter, with a fake
  `SpeechRecognition`/`speechSynthesis` injected. It covers the end-to-end UI paths listed in §8.
- **Not verifiable automatically:** real microphone recognition quality. That needs the manual tests in §8.

## 7. Acceptance criteria

1. With the voice flag off, nothing about voice is visible or accepted (`/options` text only, `mode=voice` → 422).
2. With it on, a student completes a voice HR/technical interview and gets the normal Phase 4 report.
3. Answers are stored as text with `answer_source = stt`; no audio is sent to or stored by the backend.
4. Typing fallback works where STT is unsupported or denied.
5. Text interviews and all existing features are unchanged (full regression green).

Status:
- Criteria 1, 3 and 4 are met by automated and simulated tests.
- Criterion 2 is met with simulated speech and a scripted engine; real speech and a live LLM are pending.
- Criterion 5 was met by the full regressions on the pre-quality-pass code. A full regression of the latest code is
  pending (§8).

## 8. Verification record

**Automated and simulated (2026-11-19; none of these uses a real microphone, browser speech service or LLM):**

| Test | Result | When |
|---|---|---|
| Frontend unit tests (`npm run test:unit`) | 26/26 passed | after the quality pass |
| Backend mock-interview tests (`test_mock_interview_{flags,models,sessions,reports,voice}.py`) | 132 passed, 0 failed | after the quality pass |
| Browser smoke test, simulated speech API (ports 3101/8101) | 51/51 passed | after the quality pass |
| Browser smoke test, voice flag off | 3/3 passed | initial Phase 5 run |
| TypeScript `tsc --noEmit` | passed | after the quality pass |
| ESLint on the touched files | 0 problems | after the quality pass |
| ruff on the touched Python | clean | after the quality pass |
| Full backend regression (SQLite) | 298 passed, 0 failed | **before** the quality pass |
| Postgres `tests_pg_api` / RLS suites (NOBYPASSRLS `app_user`) | 225 passed / 12 passed, 0 failed | **before** the quality pass |
| AI worker tests | 48/48 (worker unchanged) | initial Phase 5 run |

**Manual tests: PENDING, none performed on the latest code.** Rebuild the local frontend first; the mic needs
`localhost` or HTTPS, and `Permissions-Policy` must allow `microphone=(self)`.

| # | Check | Status |
|---|---|---|
| M1 | Chrome/Edge desktop, English: the hint shows `en-IN`; 2–3 sentences with pauses → no repeated words; edit, then submit → "spoken" marker | pending |
| M2 | ~10 s of silence → "No speech was heard"; block the mic → the button is disabled and the message is shown | pending |
| M3 | Hindi session: the hint shows `hi-IN`; Devanagari text; check accuracy | pending |
| M4 | Hinglish session: the hint shows `en-IN`; a mixed sentence; check accuracy | pending |
| M5 | Android Chrome: long answers with pauses → no repeated phrases; lock the screen mid-answer → the stop notice, unfinished text kept | pending |
| M6 | Brave: a `network` error appeared in your earlier local testing; check that the message names the cause and that typing works | pending (the error itself is expected: Brave has no speech service) |
| M7 | Finish → the report disclaimer mentions typed or speech-to-text answers | pending (automated: passed) |
| M8 | Live LLM: voice interview follow-ups and report quality | pending (no development key used so far) |
| M9 | Full SQLite and Postgres regression on the latest code | pending |

Browser-specific recognition accuracy is outside the application's control. A natural or neural TTS voice is
**deferred and not implemented**.
