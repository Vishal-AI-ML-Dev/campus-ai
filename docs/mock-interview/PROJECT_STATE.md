# AI Mock Interview — Project State

_Last updated: 2026-11-19 (Phase 6.3 video lobby, room and lifecycle implemented; Phase 6.4 not started)._

**This file is the single source of truth for status.** The "Status at a glance" block below is current. Everything
after it is the dated, detailed history of each phase; where a historical section disagrees with this block, this
block wins.

## Status at a glance

| Phase | Scope | Status | Commit |
|---|---|---|---|
| 0 | Audit, baseline tests, plan | Done | docs in `docs/mock-interview/` (`BASELINE_TEST_RESULTS.md`, `SAFETY_AUDIT.md`, `ARCHITECTURE_AUDIT.md`, `IMPLEMENTATION_PLAN.md`) |
| 1 | Feature flags, `/status`, `/catalog`, worker ping, frontend notice | Done, committed | `f17d2bd` (docs `3bce105`) |
| 2 | Models + additive RLS migration (sessions, turns, reports) | Done, committed | `0fadaa8` (docs `0ac3efc`) |
| 3 | Text HR + technical interview engine | Implemented, committed; live LLM not run | `926e59c` (docs `f8e3485`) |
| 4 | Evaluation report + history | Implemented, committed; live LLM not run | `18d8db0` |
| **5** | **Voice interview (browser speech)** | **Implemented, committed; automated/simulated tests pass; real-device tests pending** | `34fd3a9` |
| **6** | **AI video interview** | **Design approved (D1–D12). 6.1 foundations committed. 6.2 camera infrastructure implemented. 6.3 video lobby, room and lifecycle implemented, `VIDEO_UI_READY = true`, automated + simulated-browser tests pass. Real-device tests pending; 6.4–6.6 not started** | 6.1: `1ad5628` |
| 7–10 | Coding, company/full simulation, faculty campaigns, final review | Not started | — |

- **Branch:** `feature/ai-mock-interview` in the worktree `D:/Campus_Ai-mock-interview`. Phase 5 is committed as `34fd3a9`; a docs-only commit recording it follows. Phase 6.1
  is committed as `1ad5628`. Nothing is pushed, merged or deployed.
- **Flags:** every flag defaults to OFF: `AI_MOCK_INTERVIEW_ENABLED`, `AI_MOCK_INTERVIEW_TENANT_IDS`,
  `AI_MOCK_INTERVIEW_VOICE_ENABLED` (Phase 5), `AI_MOCK_INTERVIEW_VIDEO_ENABLED` + `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS`
  (Phase 6; empty list = no institute), the worker's `MOCK_INTERVIEW_ENABLED`, and the frontend build flags
  `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` and `NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED` (Phase 6).
- **Production readiness: NOT established.** The live LLM, real-device speech, `next build` and multi-worker load
  testing have not been verified, and no production rollout has been done.

### Phase 5 — current status

**Implemented (committed `34fd3a9`):**
- **Text-to-speech:** the browser reads questions aloud (`speechSynthesis`).
- **Speech-to-text:** the browser's `SpeechRecognition` / `webkitSpeechRecognition`. Languages: English `en-IN`,
  Hindi `hi-IN`, Hinglish `en-IN`.
- **The student edits the transcript before submitting.** Only that reviewed text goes to the backend, with
  `answer_source = stt` or `typed`.
- **Audio:** the application backend never receives or stores audio. The browser's speech provider may process
  microphone audio (Chrome uses Google's service), and the UI says so.
- **Reliability:** done in the quality pass; spec §5b.
- **Report disclaimer:** no longer modality-specific. **Scoring logic is unchanged.**
- **Spec and design:** `specs/phase-5-voice-interview.md` (§5a review fixes, §5b quality pass, §8 verification record).
  Architecture: `AI_INTERVIEW_ARCHITECTURE.md` → "Phase 5". API: `API.md` → "Phase 5".

**Automated / simulated tests passed:**

| Test (2026-11-19) | Result | Kind |
|---|---|---|
| Frontend unit tests `npm run test:unit` (`frontend/tests/unit/speechDictation.test.mjs`) | **26/26 passed** | automated, fake recognition engine |
| Backend mock-interview tests (`test_mock_interview_{flags,models,sessions,reports,voice}.py`, SQLite) | **132 passed, 0 failed** | automated, stubbed AI worker |
| Browser smoke test (headless Chromium, scripted engine and reporter) | **51/51 passed** | **simulated**: fake `SpeechRecognition`/`speechSynthesis`, no real microphone |
| Browser smoke test, voice flag off | 3/3 passed (initial Phase 5 run; that code path is unchanged by the quality pass) | simulated |
| TypeScript `npx tsc --noEmit` | passed | automated |
| ESLint on the touched files | 0 problems | automated |
| ruff on the touched Python | clean | automated |
| Full backend regression (SQLite) | 298 passed, 0 failed (**before** the quality pass) | automated |
| Postgres: full `tests_pg_api` / RLS suites, as NOBYPASSRLS `app_user` | 225 passed, 0 failed / 12 passed (**before** the quality pass) | automated, throwaway Postgres 16 |
| AI worker tests | 48/48 (worker code unchanged in Phase 5) | automated |

**Manual tests pending (none of these has been done; none is marked passed):**
- A real-microphone test of the **latest** speech fixes; the final Chrome/Edge desktop microphone regression.
- Hindi (`hi-IN`) and Hinglish (`en-IN`) recognition accuracy.
- The final Android Chrome regression (re-delivered results, screen lock, silence timeout).
- Brave: in your local testing, speech recognition showed a `network` error. Brave exposes the API but has no
  working speech service, so the app shows a message and students type instead. This is expected, not a fix target.
- The live LLM, plus a full integration regression on the latest code (the full SQLite and Postgres suites ran
  **before** the quality pass, whose only backend change is the disclaimer string).

**Known limitations:**
- Recognition accuracy (misheard words, fillers, Hindi script, accents) is set by the browser's speech service and
  the microphone, not by the app. The app never rewrites words.
- Two identical phrases spoken back to back are merged.
- The `stt` label is reported by the browser and is informational only.
- Firefox and Brave have no usable speech recognition, so students type there.

**Deferred (not implemented):**
- A natural or neural TTS voice upgrade.
- Hosted or server-side STT, e.g. cloud transcription. Changing where audio goes needs separate privacy approval.

**Phase 5 commit:** `34fd3a9` "feat(mock-interview): add browser-based voice interview" (23 files, staged by name, 2026-11-19).
It covers the backend (config, router, schemas, services, report disclaimer, `test_mock_interview_voice.py`), the
frontend mock-interview components, `speechDictation.ts`, `useSpeech.ts`, `VoiceAnswerBox.tsx`, `package.json`
(`test:unit`) and `frontend/tests/unit/speechDictation.test.mjs`, and the docs (including both specs).

- **Not committed; your local Docker test setup.** Whether it belongs in a commit is your decision:
  - `frontend/Dockerfile`: passes `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED`;
  - `frontend/next.config.ts`: `Permissions-Policy` `microphone=(self)`;
  - untracked `docker-compose.override.yml`.
  - Voice in a deployed build needs a `microphone=(self)` policy.
- Never commit: `.claude/` and `frontend/AGENTS.md`.

### Phase 6.1 — video foundations (2026-11-19; committed `1ad5628`)

Design and approved decisions D1–D12: `specs/phase-6-video-interview.md`. **D1: Phase 5 real-device tests are
deferred, not waived as passed; Phase 5 stays incomplete.** The architecture audit and the design are done.

**What 6.1 does:** no camera code yet. It adds the server-side gate and the browser configuration that later
milestones build on.
- **Backend:**
  - `AI_MOCK_INTERVIEW_VIDEO_ENABLED` and `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS` flags.
  - `mock_interview_video_enabled_for(user)` = master gate + video switch + institute in the video list
    (**empty/invalid = none**) + student.
  - `/options` appends `video` only when that passes; `POST /sessions {mode:"video"}` → 422 otherwise.
  - `answer_source="stt"` is accepted for video sessions (`SPOKEN_MODES`).
  - Existing video sessions can finish after access is withdrawn (D12).
- **Frontend:**
  - Build flag `NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED` (Dockerfile `ARG`, default `false`).
  - `next.config.ts` `buildHeaderRules()` adds `camera=(self)` only for `/dashboard/mock-interview(/…)` and only
    when both build flags are on; the microphone/geolocation directives follow the global header.
  - The setup form never offers `video` yet (`VIDEO_UI_READY = false` until 6.3); `MODE_LABELS` knows "Video".
- **Database / worker / scoring:** no migration (Phase 2 already allows `mode='video'`; Alembic head unchanged), no AI
  worker change, no scoring or disclaimer change.
- **API:** additive only. `mode` accepts `"video"`, and `/options` `modes` may contain it. Response shapes are unchanged.

**Files.**
- Modified (6.1):
  - `backend/app/core/config.py`, `backend/app/routers/mock_interview.py`, `backend/app/schemas/mock_interview.py`,
    `backend/app/services/mock_interview.py`;
  - `frontend/next.config.ts`, `frontend/Dockerfile`;
  - `frontend/src/components/mock-interview/{InterviewSetupForm.tsx,mockInterviewApi.ts,mockInterviewFlags.ts}`;
  - `CLAUDE.md` (flag line), `docs/mock-interview/{API.md,AI_INTERVIEW_ARCHITECTURE.md,IMPLEMENTATION_PLAN.md,PROJECT_STATE.md}`;
  - `docs/mock-interview/specs/phase-6-video-interview-handoff.md` (marked superseded).
- New: `backend/tests/test_mock_interview_video.py`, `frontend/src/components/mock-interview/mockInterviewModes.ts`,
  `frontend/tests/unit/mockInterviewVideoFoundations.test.mjs`, `docs/mock-interview/specs/phase-6-video-interview.md`.
- `frontend/next.config.ts` and `frontend/Dockerfile` **also carry your uncommitted Phase 5 local edits**
  (`microphone=(self)`; the `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED` arg/env). Only the 6.1 hunks were committed; those
  Phase 5 edits remain uncommitted in the working tree. `docker-compose.override.yml` is untouched (it does not set
  the new video flags).

**Commit `1ad5628`** "feat(mock-interview): add video interview foundations (phase 6.1)" (19 files, 901 insertions,
26 deletions; nothing pushed).
- Files were staged by name. For `frontend/next.config.ts` and `frontend/Dockerfile` the index was built from the
  working file with the Phase 5 lines removed, so the commit holds only the 6.1 hunks. The three Phase 5 lines
  (Dockerfile `ARG NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=false`, the runner-stage `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED`
  env line, and `microphone=(self)`) remain unstaged.
- **Change made while preparing the commit:** one unit test in `mockInterviewVideoFoundations.test.mjs` asserted the
  global `microphone=(self)`, which exists only in the uncommitted Phase 5 edit. It was rewritten to assert that the
  video flags never change the global microphone directive, whatever it is. Re-run after the change: `npm run
  test:unit` 44/44 on the working tree, and 44/44 on an export of the staged snapshot (global `microphone=()`);
  ESLint clean on that file.
- With only the committed files, the Docker video build arg cannot take effect: the video build flag also needs
  `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED`, whose Dockerfile arg is part of the uncommitted Phase 5 edit. Video then
  stays off (fail-safe).

**Tests (2026-11-19, all automated; no real camera exists in 6.1):**

| Test | Result |
|---|---|
| `backend/tests/test_mock_interview_video.py` (new) | 30 passed |
| All mock-interview backend suites (flags, models, sessions, reports, voice, video), SQLite | **162 passed, 0 failed** (132 existing + 30 new) |
| Full backend regression, SQLite (`pytest`, whole suite) | **332 passed, 12 skipped, 0 failed** (15 min 19 s). The 12 skips are the opt-in Postgres RLS tests (`test_rls_postgres.py`, `test_mock_interview_rls_postgres.py`), which need a throwaway Postgres |
| Frontend `npm run test:unit` | **44/44 passed** (26 Phase 5 + 18 new) |
| `npx tsc --noEmit` | passed |
| `npm run lint` | 0 errors (2 pre-existing warnings in `e2e/smoke.spec.ts`, untouched) |
| ruff on the touched Python | clean |
| Live headers, `next dev` on a scratch port, both build flags on | `/dashboard/mock-interview` (and `/…/x`): `camera=(self), microphone=(self), geolocation=()`; `/dashboard`, `/login`: `camera=(), microphone=(self), geolocation=()`; one header per response |
| Live headers, video build flag off | `camera=(), microphone=(self), geolocation=()` on every route checked |
| Note on the two live-header rows | Run against the **working tree**, which includes the uncommitted Phase 5 `microphone=(self)` edit. The committed tree's global header has `microphone=()`; no live-header check was run on the committed tree alone |
| Postgres `tests_pg_api` / RLS suites | **not run** (no schema, RLS or query change in 6.1) |

**Self-review findings (fixed before handover):** the stt-rejection message for text sessions was briefly reworded;
restored to the exact Phase 5 text so text behaviour is unchanged. No other issues found.

**Known limitations of 6.1:**
- A student who passes every server gate can create a video session through the API directly (the UI does not offer
  it yet). The current room renders such a session like a text interview; that is safe (text in, text out) but not
  the video experience. 6.3 adds the video lobby and room.
- Client-side navigation keeps the previous document's `camera=()`; the 6.2 lobby must detect it and offer a reload.

### Phase 6.2 — camera infrastructure (2026-11-19; implemented)

Details: `specs/phase-6-video-interview.md` §5 (behaviour) and §8 (verification).

**What 6.2 does:** reusable camera building blocks for the video lobby and room. **No page renders them yet**
(`VIDEO_UI_READY` is still `false`), so students see no change. No backend, API, schema, worker or configuration
change; `API.md` is unchanged.
- `cameraMedia.ts`:
  - React-free `CameraController`: start only on request, one request at a time, stale-request guard, old stream
    stopped before a new one, `pagehide`/unmount/off stop every track;
  - track `ended`/`mute` and `devicechange` handling, saved-camera fallback;
  - error mapping, secure-context and Permissions-Policy detection; video only (`audio: false`).
- `useCamera.ts`: React binding; the mount effect only attaches listeners.
- `CameraPreview.tsx`, `CameraDeviceSelect.tsx`, `VideoPrivacyNotice.tsx`, `VideoLobby.tsx`: preview with
  "Camera on" indicator, camera picker, privacy notice with per-session acknowledgement, and lobby. Continue needs
  a live camera AND the acknowledgement.
- Reload fallback: "Reload to enable camera" (student-initiated full reload, at most once per tab).

**Privacy:**
- The stream stays in browser memory and is shown only in a local `<video>` element.
- Nothing records, captures, encodes, uploads or analyses it. No `MediaRecorder`, canvas, `captureStream` or
  `toDataURL`; the 6.1 static guard covers the new files.
- No face, emotion, gaze or identity features, and no imports from the face features.
- Only the chosen camera id is stored, in `localStorage` on the device.

**Files (6.2):**
- New:
  - `frontend/src/components/mock-interview/{cameraMedia.ts,useCamera.ts,CameraPreview.tsx,CameraDeviceSelect.tsx,VideoPrivacyNotice.tsx,VideoLobby.tsx}`;
  - `frontend/tests/unit/cameraMedia.test.mjs`.
- Docs updated: `specs/phase-6-video-interview.md`, `AI_INTERVIEW_ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, this
  file.
- Phase 6.1 commit `1ad5628` is recorded in the project documentation (this file, the Phase 6 spec and the plan).

**Tests (2026-11-19):**

| Test | Result |
|---|---|
| `frontend/tests/unit/cameraMedia.test.mjs` (new, fake media APIs) | **46/46 passed** |
| All frontend unit tests (`npm run test:unit`) | **90/90 passed** (44 before + 46 new) |
| Mutation check on a scratch copy (3 deliberate bugs) | each caught (2 / 2 / 3 failing tests) |
| `npx tsc --noEmit` | passed |
| ESLint, touched files / full `npm run lint` | 0 problems / 0 errors, 2 pre-existing warnings (`e2e/smoke.spec.ts`) |
| Scratch browser check, real Chromium with its **fake** camera device | **15/15 passed** (spec §8) |
| Backend suites | not run: no backend change in 6.2 (6.1 results stand: 162 mock-interview, 332 full) |
| **Real camera / real devices** | **not done; pending** |

**Known limitations / open points:**
- The components have been type-checked, linted and their logic unit-tested, but not rendered in a browser (no page
  uses them until 6.3).
- On browsers without a Permissions-Policy API (Firefox, Safari), a policy block looks like a normal denial
  (`not-allowed`) unless the browser throws `SecurityError`; the reload button is then not offered.
- The camera button does not wait for the privacy acknowledgement (Continue does). If you want the camera to be
  usable only after the acknowledgement, that is a small 6.3 change.
- The 6.1 limitations still apply (a video session created through the API directly renders like a text session).

### Phase 6.3 — video lobby, room and session lifecycle (2026-11-19; implemented)

Details: `specs/phase-6-video-interview.md` §5a (design) and §9 (verification, remaining scope).

**What 6.3 does:** the student-facing video interview, end to end, behind the existing gates.
- `VideoInterviewWorkspace` is the single camera owner. It shows the lobby, then the room, then the finished view,
  with the same stream across lobby and room, and stops the camera on any terminal status.
- `VideoInterviewRoom` contains:
  - the interviewer tile, with a speaking animation from the existing TTS state;
  - the candidate camera;
  - the toolbar: Mute mic, Stop/Start camera, Mute voice, timer, End, and camera/mic/offline status;
  - the Phase 5 `VoiceAnswerBox` and the retry path.
- Session API, idempotency, lease, timer and recovery are reused unchanged.
- The setup form can create video sessions. `VIDEO_UI_READY = true`, so video appears only when the build flag,
  server flags, video allow-list and student role all allow it. Defaults stay off.
- D9 note on finished video interviews; history label "· Video".
- **No backend, API, schema, migration, worker, scoring, Docker or configuration change.**

**Camera / microphone:**
- The camera is requested only from clicks, video-only; after a refresh it stays off until a click.
- A camera failure after the start leaves the interview running (D7).
- Finish, Cancel, any terminal status and unmount stop every track.
- "Mute mic" stops dictation through new optional `VoiceAnswerBox` props (unused by the voice room).
- No second microphone stream.

**Privacy:** unchanged from 6.2. Nothing records, captures, uploads or analyses video; only reviewed answer text is
sent. The static guards cover the new files.

**Files (6.3):**
- New:
  - `frontend/src/components/mock-interview/{VideoInterviewWorkspace.tsx,VideoInterviewRoom.tsx,videoInterviewFlow.ts}`;
  - `frontend/tests/unit/videoInterviewFlow.test.mjs` and `frontend/tests/unit/helpers/fakeMedia.mjs`.
- Modified:
  - `frontend/src/app/dashboard/mock-interview/page.tsx` (video routing);
  - `InterviewSetupForm.tsx` (video choice), `VoiceAnswerBox.tsx` (optional `micMuted`/`onMicStateChange`),
    `InterviewRoom.tsx` (`export` on `Countdown` only), `mockInterviewApi.ts` (create `mode` type),
    `mockInterviewModes.ts` (`VIDEO_UI_READY = true`), `InterviewFinished.tsx` (D9 note) and
    `InterviewHistory.tsx` (label), all in `frontend/src/components/mock-interview/`;
  - `frontend/tests/unit/{cameraMedia,mockInterviewVideoFoundations}.test.mjs`: the readiness assertions were
    updated to the 6.3 state.
- Docs: the Phase 6 spec, the architecture, the plan and this file.

**Tests (2026-11-19):**

| Test | Result |
|---|---|
| `frontend/tests/unit/videoInterviewFlow.test.mjs` (new) | **24/24 passed** |
| All frontend unit tests (`npm run test:unit`) | **114/114 passed** (90 before + 24 new) |
| `npx tsc --noEmit` | passed |
| ESLint, touched files | 0 problems |
| Browser smoke test, final code: Chromium with a **fake** camera and fake permission UI, fake speech APIs, scripted engine (spec §9) | **34/34 passed** |
| Backend suites | not run: no backend change in 6.3 |
| **Real camera / microphone / devices** | **not done; pending** |

**Known limitations:**
- Real devices are untested; the browser check used Chromium's fake camera and injected speech fakes.
- Pre-existing, outside Mock Interview: at 390 px the dashboard shell already scrolls horizontally (about 489 px,
  text room too). The video room adds nothing beyond that.
- Firefox/Safari have no Permissions-Policy API, so a policy block there looks like a denial and the reload button
  is not offered.
- `VideoInterviewRoom` duplicates the room actions of `InterviewRoom` (kept separate so text/voice are untouched).

### Next recommended action (approved sequence)
1. Phase 6.1 is committed (`1ad5628`) and recorded in the project documentation. Phases 6.2 and 6.3 are
   implemented.
2. **Phase 6.4 — NOT started; needs your go-ahead:** real-device verification of camera + browser speech together
   and the remaining items in `specs/phase-6-video-interview.md` §9 "Phase 6.4 remaining scope".
3. Phase 6.6 as in `specs/phase-6-video-interview.md` §6.

Phase 5's real-device checks (`specs/phase-5-voice-interview.md` §8) remain open and tracked above (D1: deferred).

## Where the work lives
| | |
|---|---|
| Worktree | `D:/Campus_Ai-mock-interview` |
| Branch | `feature/ai-mock-interview`, based on `7f99e0b` |
| Base commit `7f99e0b` | "Require admin approval for student self-registration" on `feature/student-approval` (local only; `main` is its parent `2d0d9ac`) |
| Alembic head | **`b4d8f2a6c9e1`** (Phase 2, single head) → `a7c3e9d2b4f1`. Phases 3–5 and 6.1 added no migration |
| Original folder | `D:/Campus_Ai`, on `feature/student-approval`. Phases ≤5 used its `backend/.venv`; Phase 6.1 tests ran with the worktree's own `backend/.venv` (the worktree has no `.env`) |

## Phase 5 — detailed record: initial implementation and verification (2026-11-19; committed in `34fd3a9`)

_Historical record of the first Phase 5 delivery. The current status is under "Status at a glance"; the later quality pass follows below._

Spec: `specs/phase-5-voice-interview.md`. §1–§7 are the approved design; §5a lists the post-review changes.
Your decisions (2026-11-19): **browser Web Speech API** for speech-to-text and text-to-speech, and a **separate voice
sub-flag**, default OFF. Design approved "as written".

**What it does:**
- A student can choose **Voice** instead of Text for an HR or technical interview (only when the voice flag is on).
- Each question is read aloud by the browser. The student dictates, reviews and edits the text, then submits; typing
  always works too.
- Answers are stored as text with `answer_source = "stt"` and go through the same engine and Phase 4 report.
- **No audio reaches or is stored by our servers.** The UI says that some browsers (e.g. Chrome) send audio to their
  own speech service.

**Files**
- **Created:**
  - `frontend/src/components/mock-interview/useSpeech.ts`
  - `frontend/src/components/mock-interview/VoiceAnswerBox.tsx`
  - `backend/tests/test_mock_interview_voice.py` (23 tests)
  - `docs/mock-interview/specs/phase-5-voice-interview.md`
- **Modified (additive):**
  - Backend:
    - `app/core/config.py` (+`AI_MOCK_INTERVIEW_VOICE_ENABLED`)
    - `app/routers/mock_interview.py` (`/options.modes`, the voice gate on create, `source` passed through)
    - `app/schemas/mock_interview.py` (+`modes`, `mode`, `source`, `answer_source`)
    - `app/services/mock_interview.py` (`mode` from the payload; `submit_answer(source=...)`; `answer_source` in the detail)
  - Frontend:
    - `InterviewRoom.tsx`: reads questions aloud, mute, the voice answer box
    - `InterviewSetupForm.tsx`: "Answer by" Text/Voice choice and the privacy note
    - `InterviewInstructions.tsx`: Mode row and voice tips
    - `InterviewFinished.tsx`: "spoken" marker
    - `InterviewHistory.tsx`: "· Voice" label
    - `mockInterviewApi.ts`: types, and `source` sent only for voice
  - Docs: `API.md`, `AI_INTERVIEW_ARCHITECTURE.md`, this file
- **Not changed:** the AI worker (its prompts already judge "substance, not grammar or accent"), migrations, and existing routes.

**Database / API / env:**
- **No migration.** Phase 2 already allows `mode = voice` and `answer_source = stt`. The Alembic head is still `b4d8f2a6c9e1`.
- **API (additive, no new routes):**
  - `/options` + `modes`
  - `POST /sessions` + optional `mode` (`voice` while the voice flag is off → 422, no row)
  - `POST /answer` + optional `source` (`stt` on a text session → 422; not in progress → 409 first)
  - `turns[]` + `answer_source`
- Text sessions send exactly the Phase 3 payload (verified in the browser).
- **New env var:** `AI_MOCK_INTERVIEW_VOICE_ENABLED` (optional, default False). It is read only behind the master flag and the tenant allow-list. No other flag or env var changed.

### Tests (initial Phase 5 verification, before the quality pass)
| Suite | Result |
|---|---|
| New `tests/test_mock_interview_voice.py` (SQLite, alone) | **Exit 0: 23 passed** (flag off/on, master flag and allow-list still apply, `mode`/`source` validation, `stt` stored, the typing fallback, idempotent retry, 409 before 422, other student/institute 404, the flag turned off mid-session, the same report path and worker payload, no audio field or route) |
| **Full backend regression (SQLite)**, final code, run alone | **PASS. Exit 0: 298 passed, 12 skipped, 0 failed**, 1 pre-existing warning, 976 s (12:31:32–12:47:52). 298 = Phase 4's 275 + 23 new. The 12 skips are the opt-in Postgres tests, which pass below. An earlier full run on the pre-review code was stopped at 46% by me so the final code could be tested; it is not counted |
| AI worker (`test_interview_engine`, `test_interview_report`, `test_interview_flag`, `test_worker_security`) | **Exit 0: 48 passed, 0 failed** (worker unchanged) |
| Frontend `npx tsc --noEmit` / `npm run lint` | **Exit 0** / **0 errors** (the 2 pre-existing warnings in `e2e/smoke.spec.ts`) |
| `ruff check` on the changed and new Python | Clean |
| App import with the flags off/on, `SAWarning` as an error | **Pass.** 26 mock-interview paths in both cases (no new routes); `mode`/`source` appear in the OpenAPI schemas |
| Throwaway Postgres 16 (pinned digest `63e83b5a…`, tmpfs, `--rm`, `--memory 1g`, 127.0.0.1:15433): full chain `upgrade head` | **PASS.** Single head `b4d8f2a6c9e1`; `app_user` NOSUPERUSER/NOBYPASSRLS/NOCREATEROLE/NOCREATEDB; RLS enabled and forced on the 3 interview tables |
| Postgres `tests/test_rls_postgres.py` + `tests/test_mock_interview_rls_postgres.py` as `app_user` | **PASS. Exit 0: 12 passed** |
| Postgres full `tests_pg_api` as `app_user` (`--confcutdir=tests_pg_api`) | **PASS. Exit 0: 225 passed, 2 skipped, 0 failed, 0 errors**, 12:50:39–13:09:10 (the same as Phase 4: Phase 5 adds no Postgres-specific test because it has no schema or RLS change). The 2 skips are the known Redis queue tests (`RQ_TEST_REDIS_URL` not set) |
| **Browser smoke test** (headless Chromium; scratch backend on its own SQLite file with the master and voice flags on and a scripted engine and reporter; `next dev`; **fake** `SpeechRecognition`/`speechSynthesis` injected) | **41/41 PASS** on the code at that time (details below). Superseded by **51/51** after the quality pass |
| Browser, voice flag **off** | **3/3 PASS**: no "Answer by" choice and no Voice option |
| Real microphone / real speech recognition (en-IN, hi-IN, Hinglish), real devices, screen readers | **NOT RUN.** Headless Chromium has no real speech engine; this needs a manual test on Chrome/Edge (desktop + Android) |
| Live LLM | **NOT RUN** (no development key; the same accepted limitation as Phases 3–4) |

**Browser checks that passed in the initial run (41; the quality-pass run extended this to 51):**
- Setup and instructions:
  - Voice choice and privacy note;
  - instructions show Mode: Voice and the mic tips.
- Reading questions aloud:
  - question 1 read aloud in `en-IN` after Start;
  - each next question read aloud automatically;
  - Hindi uses `hi-IN`;
  - mute stops auto-reading;
  - "Hear question" works on demand;
  - after a refresh the question is **not** replayed, but "Hear question" works.
- Dictation:
  - the mic shows Stop while listening; Submit and "Hear question" are disabled while listening;
  - the status live region says "Listening…";
  - interim preview;
  - final phrases appended, then editable;
  - the POST carries `source: "stt"` and the bubble is marked "spoken".
- Edge cases:
  - Android-style re-delivered and growing finals are not duplicated;
  - clearing the dictated text and typing sends `typed`;
  - at 5,000 chars the mic stops with "Answer limit reached";
  - mic denied → friendly message, then typing works;
  - ending the interview while dictating closes the mic.
- Results and other browsers:
  - a voice interview gets the normal report, and its transcript marks spoken answers;
  - history shows "HR · Voice";
  - a browser with no speech recognition shows a notice and no mic, and typing works.
- Text sessions unchanged:
  - Mode: Text, no voice tips, no voice controls;
  - the answer payload is exactly `{turn_seq, answer}`.
- Phone width: the voice room adds no overflow compared with the text room (168 px vs 169 px; both are the pre-existing AppShell overflow).
- No console errors.

Screenshots: session scratchpad `ui/shots/p5-*.png`.

### Security and code-quality reviews (two independent read-only agents)
No High findings. Security: no flag bypass (the voice flag is read only behind the master flag, the role check and the tenant allow-list), no IDOR, `source` never reaches prompts or scoring, no audio path, no XSS, the mic opens only on a click, and the API changes are additive only.
**Fixed** (details in spec §5a):
- "Hear question" could be dictated into the answer (both reviews).
- The mic stayed open during End interview.
- Android duplicate finals. The first, index-based fix dropped new speech in the browser test; the final fix is text-based.
- Silent truncation at the limit.
- The sticky `stt` label.
- A stale `speaking` state.
- The live region, `aria-pressed` and the voice-language normalisation.
- 409 before 422 for answers to ended sessions.
- The default for `modes`.
- Stale sessionStorage keys.
- The privacy wording.
- A weak test assertion.

**Open / accepted:**
- `source` is self-reported (an informational label only).
- Two identical phrases spoken in a row within one dictation can be merged by the de-duplication.
- Real speech-recognition quality (Indian English, Hindi, Hinglish) is unverified.
- Hindi recognition returns Devanagari, which the engine accepts as-is (not live-tested).

### Phase 5 voice quality pass (2026-11-19, after your real-device testing; committed in `34fd3a9`)
Details are in spec §5b; the verification record is in spec §8.

**Files:**
- **Created:**
  - `frontend/src/components/mock-interview/speechDictation.ts`
  - `frontend/tests/unit/speechDictation.test.mjs`
- **Modified:**
  - `useSpeech.ts`
  - `VoiceAnswerBox.tsx`
  - `backend/app/services/mock_interview_report.py` (the `DISCLAIMER` text only)
  - `backend/tests/test_mock_interview_voice.py` (+4 tests)
  - `frontend/package.json` (+`test:unit` script; no dependency change)
- Your local changes (`frontend/Dockerfile`, `frontend/next.config.ts` `microphone=(self)`, `docker-compose.override.yml`) were left untouched.

**Results:**
- Frontend unit tests: **26/26 pass**.
- Backend mock-interview tests (flags, models, sessions, reports, voice): **132 passed, 0 failed**.
- Voice + report suites alone: 59 passed.
- `tsc`: exit 0. ESLint on the touched files: 0 problems. ruff: clean.
- Browser smoke test (simulated speech API, scratch ports 3101/8101): **51/51 PASS**.
- **Not re-run after this pass:** the full SQLite regression and Postgres. The only backend change is the disclaimer string.
- Real microphone: **NOT RUN**.

### Phase 5 acceptance criteria (automated/simulated evidence only; the manual checks are pending, see "Status at a glance")
| Criterion | Status |
|---|---|
| 1. Voice flag off: nothing about voice is visible or accepted | **Met** (API tests, browser flag-off 3/3) |
| 2. Voice flag on: a voice HR/technical interview completes with the normal Phase 4 report | **Met with a simulated speech API and a scripted engine** (browser 51/51 + API). Real speech and a live LLM: NOT RUN |
| 3. Answers stored as text with `answer_source = stt`; no audio sent to or stored by the backend | **Met** (DB assertions, request-payload checks, no audio field or route, extra fields → 422) |
| 4. Typing fallback where STT is unsupported or denied | **Met** (browser: no-STT context, mic denied) |
| 5. Text interviews and existing features unchanged | **Met on the pre-quality-pass code:** SQLite 298/0 failed; Postgres RLS 12/12; tests_pg_api 225/0 failed; worker 48/48. After the quality pass: mock-interview backend 132/0 failed; tsc and lint clean; the text payload is unchanged in the browser. A full regression of the latest code is pending |

**Cleanup:**
- Stopped the scratch backend and `next dev` processes I started.
- Removed the `ai-worker/qdrant_data/` that the existing face test created during my worker run (created 12:22:35 and not present at session start).
- Stopped the `campus_p5_pgtest` container (`--rm`, so it was removed) and deleted the throwaway passwords and the smoke-test password. **Not mine and left untouched:** a Docker Compose stack `campus-ai-mock-local-*` (7 containers: db, redis, qdrant, backend, worker, ai-worker, frontend; plus volumes such as `campus-ai-mock-local_pgdata`) was created at 12:45 by something outside this session, while my tests ran. It raised the volume count from 27 to 30. Please check that it is yours
- `D:/Campus_Ai` is untouched.

**Notes:**
- `next dev` again printed the Next.js "known security vulnerability / run `next upgrade`" notice, and it exits once after printing it, so it needed a retry. I did **not** run any upgrade; dependencies are unchanged. That decision is yours.
- Your mid-session message "0/u" was not clear to me, and I did not act on it.

## Phase 4 — evaluation, report, history (committed `18d8db0`)


Spec: `specs/phase-4-evaluation-reports.md`. §1–§5 are the approved design; §5a lists the post-review changes. Approved by you on 2026-11-19 ("Approve as written").

**What it does:**
- A completed text interview gets **one final, evidence-backed report**:
  - an overall score (0–100);
  - 5 weighted category scores per rubric (`hr-v1` / `technical-v1`);
  - per-question 0–5 scores, each with a verbatim quote from the answer, plus feedback;
  - strengths and weaknesses that cite a question and a quote;
  - recommendations;
  - a disclaimer.
- History of past interviews with their scores; a report view; the finished screen shows the report.

**Files**
- **Created:**
  - Worker: `ai-worker/interview/{rubric,report}.py`, `ai-worker/tests/test_interview_report.py`
  - Backend: `backend/app/services/mock_interview_report.py`, `backend/tests/test_mock_interview_reports.py`, `backend/tests/fixtures/mock_interview_worker_report.json` (worker↔backend contract), `backend/tests_pg_api/test_mock_interview_reports_pg.py`
  - Frontend: `frontend/src/components/mock-interview/{InterviewReport,InterviewHistory}.tsx`
  - Docs: `docs/mock-interview/specs/phase-4-evaluation-reports.md`
- **Modified (additive):**
  - Worker: `ai-worker/interview/{schemas,prompts,router}.py` (+report schemas, prompts, `POST /interview/report`), `ai-worker/tests/test_interview_flag.py` (+report route is absent when the flag is off)
  - Backend:
    - `app/routers/mock_interview.py` (+3 routes)
    - `app/schemas/mock_interview.py` (+report/history schemas and the strict worker-output schemas)
    - `app/services/mock_interview.py` (`_claim` gains optional `seconds`/`busy` arguments; defaults unchanged)
    - `app/services/mock_interview_ai.py` (`generate_report`, 120 s timeout; the error now carries `status_code`/`timed_out`)
  - Frontend: `page.tsx` (history under the setup form, past-interview view), `InterviewFinished.tsx` (report replaces "coming soon"), `mockInterviewApi.ts` (types and calls)
  - Docs: `API.md`, `AI_INTERVIEW_ARCHITECTURE.md`, this file

**Database / API / env:**
- **No migration.** It uses the Phase 2 `mock_interview_reports` table; the session lease reuses `plan_json`, which also gains a `report_unassessable` counter.
- **New API:**
  - `GET /history`
  - `GET /sessions/{id}/report`
  - `POST /sessions/{id}/report` (5/min per student)
  - worker `POST /interview/report`
- No existing response, table, column, env var or flag changed. Everything sits behind the existing flags (default OFF).

### Tests (actual results)
| Suite | Result |
|---|---|
| New backend `tests/test_mock_interview_reports.py` (SQLite, run alone) | **Exit 0: 32 passed** (incl. 15 invalid-worker-output cases) |
| AI worker (`run_worker_tests.py test_interview_engine test_interview_report test_interview_flag test_worker_security`) | **Exit 0: 48 passed, 0 failed** (21 engine + 19 report + 2 flag + 6 security) |
| Mutation check (scratch): non-evidence filter disabled | The injection test and the evidence unit test **fail**, as they should |
| Frontend `npx tsc --noEmit` / `npm run lint` | **Exit 0** / **0 errors** (2 pre-existing warnings in `e2e/smoke.spec.ts`) |
| `ruff check` on the new and changed Python | Clean |
| App import (flag off/on), `SAWarning` as an error; OpenAPI lists the new routes on `/v1` and unversioned | **Pass** |
| Browser smoke test (headless Chromium; scratch backend on its own SQLite file with the flag on and a **scripted** engine and reporter; `next dev`) | **23/23 PASS** (details below) |
| Throwaway Postgres 16 (pinned digest, tmpfs, `--rm`, 127.0.0.1:15433): full chain `upgrade head` → `downgrade -1` → `upgrade head`, single head `b4d8f2a6c9e1`; `app_user` NOSUPERUSER/NOBYPASSRLS; RLS enabled and forced on the 3 interview tables | **PASS** |
| First full backend regression attempt (SQLite) | **Superseded.** Stopped by Claude Code at ~94% for critically low system memory. Not counted; it was re-run below |
| **Full backend regression (SQLite)**, re-run alone (no parallel workers; memory checked before, ~3.2–5.9 GB free throughout) | **PASS. Exit 0: 275 passed, 12 skipped, 0 failed, 0 errors**, 1 warning (the pre-existing Starlette/httpx deprecation), 837 s, 11:36:38–11:51:50. 275 = Phase 3's 243 + 32 new. The 12 skips are the opt-in Postgres tests (8 mock-interview RLS + 4 base RLS), which **pass** in the Postgres run below |
| **Postgres: the new `tests_pg_api/test_mock_interview_reports_pg.py`** (disposable `postgres:16@sha256:63e83b5a…`, tmpfs, `--rm`, `--memory 1g`, 127.0.0.1:15433, as NOBYPASSRLS `app_user`, run with the required `--confcutdir=tests_pg_api`) | **PASS. Exit 0: 3 passed** |
| ↳ report isolation + RLS | other student / other institute: 404 on GET and POST report and empty history; staff 403; the report row is visible only under the owning tenant's GUC (0 under the other tenant, 0 with no GUC); score recomputed server-side (40.0); lease released in the same commit as the insert |
| ↳ duplicate generation | 6 parallel POSTs → exactly **1** worker call and **1** row; codes ⊆ {200, 409}; a later POST makes no worker call |
| ↳ eligibility, retry, timeout | setup / in-progress / abandoned → 409 with no worker call and no row; GET before generation → 404; worker 502 → 503, no row, lease released, immediate retry → 200 and 1 row; worker read timeout → 503, lease **kept**, retry → 409 "being prepared" with no 2nd call, no row |
| **Postgres: `tests/test_rls_postgres.py` + `tests/test_mock_interview_rls_postgres.py`** as `app_user` | **PASS. Exit 0: 12 passed** (4 base + 8 mock-interview: forced RLS + exact policy, own-tenant visibility, WITH CHECK, cross-tenant child FK, same-tenant student, CHECK constraints, cascades) |
| **Postgres: full `tests_pg_api`** as `app_user` (includes the 3 new tests and the 6 Phase 3 interview tests) | **PASS. Exit 0: 225 passed, 2 skipped, 0 failed, 0 errors**, 11:12:50–11:35:38 (Phase 3: 222 passed). The 2 skips are the known Redis queue tests (`RQ_TEST_REDIS_URL` not set; no isolated Redis). The suite's safety preflight (database, role, NOBYPASSRLS, system id) and its row-count cleanup check ran inside the session. Counts come from the result markers, because the extra `-q` plus pyproject's `-q` made pytest drop the stats line |
| Disposable DB setup | Full migration chain to the single head `b4d8f2a6c9e1`; `app_user` NOSUPERUSER/NOBYPASSRLS/NOCREATEROLE/NOCREATEDB; RLS enabled + forced on the 3 interview tables. (`verify` also reports 5 tenant-id tables without forced RLS; that is pre-existing, Phase 4 adds no tables) |
| Final re-runs after the Postgres and SQLite runs | Worker **48/48, exit 0**; `tsc` **exit 0**; lint **exit 0, 0 errors** (2 pre-existing warnings) |
| Live LLM | **NOT RUN** (no development key; same accepted limitation as Phase 3) |

**Browser checks that passed:**
- the history empty state;
- the "preparing report" state;
- an injected report 503 shows a friendly error with a retry, and the transcript stays available;
- after the retry the report renders: the score out of 100, the disclaimer, 5 categories, strengths with a quote, recommendations, 4 question rows, "Not answered", and the evidence table quoting the student's own words;
- at phone width the report adds **no** overflow (153 px, the same as the pre-existing AppShell baseline on every page; one 5 px regression was found and fixed);
- history shows the score, and "View report" opens the stored report with **no** new POST;
- Back works;
- a cancelled interview shows no report, and history offers its transcript;
- the only console error is the injected 503.

Screenshots: session scratchpad `ui/shots/p4-*.png`.

### Security and code-quality reviews (two independent read-only agents)
No High findings. No IDOR or tenant leak: every query is filtered by tenant and owner, and the routes sit behind the student gate and the tenant GUC. No XSS: React only, with escaped text.

**Fixed (details in spec §5a):**
- **M1:** an injection could be quoted as its own "evidence". Fixed by the stronger evidence rule: ≥3 words and ≥15 characters, no scoring or instruction words, no sanitizer markers. Tested with a model that obeys the injection.
- **M2:** a timeout released the lease, so a parallel paid run could start. The lease is now kept on a read timeout.
- **M3:** a partial provider failure produced a skewed final report. Now all or nothing.
- **Unassessable reports:** a final 409 after 3 "nothing assessable" results stops endless paid retries.
- **L1 / quality 3:** the backend recomputes all aggregates and re-checks the quotes, the zero rules and control characters.
- **L2:** the slim plan (no resume or JD quotes).
- **L3:** malformed stored values no longer cause a 500.
- **`Infinity` scores** are handled.
- **Rounding:** half-up, with no double rounding.
- **Recommendations fallback:** the 2 weakest categories, as in the spec.
- **Contract fixture:** the worker↔backend contract is now tested from both sides.
- **Frontend:** transcript kept on error, no retry on a final 409, request id shown, disclosure marker kept, and expected 404s avoided.

**Open / accepted:**
- (L4) Each report uses a request thread plus ≤4 scoring threads in the worker; a global limit can be added at rollout.
- No per-call LLM timeout in the shared `llm.py` (changing it would affect existing features), so a 30-turn report on a slow provider may time out; it is retryable once the lease expires.
- The non-evidence filter can reject a legitimate quote containing e.g. "score".
- The fallback strength/weakness/recommendation texts are in English only.

### Phase 4 acceptance criteria
| Criterion | Status |
|---|---|
| A completed interview produces a stored report with overall, category and per-question scores | **Met** (SQLite, Postgres, browser; stubbed worker) |
| Every non-zero score has a quote verified in the answer; missing / don't-know answers score 0 with a note | **Met** (worker + backend re-check, contract fixture, mutation check) |
| Owner/tenant 404, students only, hidden while the flag is off | **Met** (SQLite + Postgres API + RLS as `app_user`) |
| Idempotent, single-flight, survives a worker failure with nothing partial stored | **Met** (SQLite + Postgres: parallel POSTs, retry, timeout) |
| History and past reports | **Met** (API + browser) |
| Disclaimer shown | **Met** |
| Existing features keep working | **Met.** SQLite 275 passed / 0 failed; Postgres API 225 passed / 0 failed; RLS 12/12; worker 48/48; tsc and lint clean. Phase 3 interview behaviour is covered inside these runs: 31 session-API tests + 17 flag tests + 25 model tests + 3 mentor regression tests on SQLite, and the 6 Phase 3 Postgres tests (ownership, RLS, parallel creates/starts/answers, lease, finish beating a late AI result) |

**PASS / FAIL / NOT RUN summary:**
- **PASS:**
  - full SQLite regression;
  - the new Phase 4 Postgres tests;
  - Postgres RLS;
  - the full `tests_pg_api`;
  - worker tests;
  - `tsc`;
  - lint;
  - the import check (flag off/on);
  - the browser smoke test (23/23, scripted engine).
- **FAIL:** none.
- **NOT RUN:**
  - the live LLM (no development key);
  - `next build`;
  - real-device and screen-reader tests;
  - multi-worker load;
  - the Redis queue path (2 skips);
  - a browser re-run after the final review fixes. The last browser run was **after** all code fixes; only docs and tests changed since.

**Cleanup:**
- Removed the `campus_p4_pgtest` container (`--rm`). The 9 unrelated containers and the 27 volumes are untouched (compared before and after).
- Deleted the throwaway passwords.
- Removed the empty `ai-worker/qdrant_data/` created by the existing face test.
- Stopped the `next dev` processes I had started.
- `D:/Campus_Ai` is untouched (`feature/student-approval` at `7f99e0b`, same untracked files).

**Dependency-security note (for a separate review; no change made):**
- The installed Next.js is **16.4.4**, pinned exactly in `frontend/package.json`.
- While `next dev` was running, it printed "Your version of Next.js is affected by a known security vulnerability", pointing at the npm advisories bulk endpoint. It also told AI agents to run `next upgrade --agent=security`.
- Not acted on: dependencies are unchanged, pending your authorization.

**Phase 4 is complete as far as automated verification goes**, and it is committed locally as `18d8db0`.

**Notes:**
- `next dev` printed a Next.js "upgrade for a security advisory" notice that also tells AI agents to run `next upgrade` and ask you about it. I did **not** act on it: upgrading the framework is outside this phase and is your decision.
- I stopped the `next dev` processes I had started.

## Completed phases
- **Phase 0:** audit docs and baseline (see BASELINE_TEST_RESULTS.md).
- **Isolation:** student-approval work on its own branch; worktree created.
- **Phase 1:** feature isolation and flags (`/mock-interview/status`, `/catalog`, worker ping, frontend notice). Details in git `f17d2bd`.
- **Phase 2:** models + additive RLS migration for sessions, turns, reports. Done; acceptance criteria below verified.
- **Phase 3:** text HR + technical interview engine. **Implemented and committed locally (`926e59c`); not production-ready.**
  - Verified:
    - SQLite with a stubbed LLM;
    - real Postgres as the NOBYPASSRLS `app_user`, including real-thread concurrency;
    - a headless-browser UI smoke test with a scripted engine.
  - **NOT fully verified:** the live Gemini/Groq check was NOT RUN, because no development LLM credential is configured. You accepted this as a documented limitation on 2026-11-19. See "Validation".
  - Also unverified: frontend production build (`next build`), real-device and screen-reader testing, and multi-worker load testing.
- **Phase 4:** evaluation, report and history. **Committed locally (`18d8db0`)**; live LLM not run.
- **Phase 5:** voice (browser speech). **Committed locally (`34fd3a9`)**; real-device verification pending; see "Status at a glance".

## Phase 3 — text interview engine

**Final verification (2026-11-19 08:56–09:12):**
- Worktree `D:/Campus_Ai-mock-interview`, branch `feature/ai-mock-interview`, HEAD `0ac3efc`. The changes match the checkpoint (6 modified, 19 new files + `specs/`).
- No `python.exe` was running before the run; the full backend suite ran as the only test process.
- **Imports resolve to the worktree.** The shared interpreter `D:/Campus_Ai/backend/.venv` (and `ai-worker/.venv`) only supplies third-party packages; its only `.pth` is `_virtualenv.pth`. Checked module paths:
  - `main`, `ai_client`, `app`, `app.services.mock_interview`, `app.routers.mock_interview`, `conftest` → `D:\Campus_Ai-mock-interview\backend\...`
  - worker `main`, `interview.graph` → `D:\Campus_Ai-mock-interview\ai-worker\...`
- One transient event: the first import attempt was refused by Windows Application Control (`_sqlite3` DLL blocked). It loaded normally on immediate retries; nothing was changed.
- `ai-worker/qdrant_data/`: the existing `test_worker_security` face test recreates an empty local face store on every run (here at 09:10:46: `.lock`, `meta.json`, a 12 KB empty `storage.sqlite`). It was removed again after inspection; nothing else was deleted.
- `D:/Campus_Ai` is untouched (the same branch, commit and untracked files as at the checkpoint).

**Recovery checks (2026-11-19, before the final run):**
- The worktree is on `feature/ai-mock-interview`, with HEAD `0ac3efc` (Phase 2 docs commit); the Phase 1 and Phase 2 commits are intact.
- **No `python.exe` process is running** (checked with `Win32_Process`), so no test run holds the shared `%TEMP%\campus_ai_test.db`.
  - The interrupted run was `backend/.venv` (from `D:/Campus_Ai`) `python -m pytest -p no:cacheprovider -q -rfEs`, started in `D:/Campus_Ai-mock-interview/backend` and writing to the session scratchpad file `p3_backend_full.txt`.
  - It used the default SQLite DB `%TEMP%\campus_ai_test.db` (last written 2026-11-18 22:54:52).
- `ai-worker/qdrant_data/` is **absent** from the worktree.
  - It was an empty local face store, created at 22:34 by the existing `test_worker_security` face kill-switch test during this phase's worker test run. It was removed in the previous session.
  - Nothing else was deleted.
- **The original `D:/Campus_Ai` is untouched:**
  - still on `feature/student-approval` at `7f99e0b`;
  - its untracked files (`CAMPUS_AI_COMPLETE_GAP_ANALYSIS.md`, `ai-worker/qdrant_data/`, `backend/pgtest.out`, `docs/`, `frontend/AGENTS.md`) all date from 09:27–18:41 on 2026-11-18, before this phase's work.
  - Its `.venv`s were only used as interpreters.

### Work completed (committed in `926e59c`)
Spec: `specs/phase-3-interview-engine.md` (design, then "6a. Changes after the independent reviews").

**AI worker** (`ai-worker/interview/`): a stateless LangGraph engine.
- Plan graph: `profile_loader → resume_context_loader → jd_analyzer → planner`.
- Turn graph: `interview_controller → answer_processor → answer_evaluator → follow_up_decider → question_generator`.
- Deterministic follow-up and difficulty policy.
- Structured output with a JSON fallback.
- Prompt-injection containment; resume/JD grounding verified in code.
- Routes `POST /interview/plan` and `/interview/turn`, still mounted only when `MOCK_INTERVIEW_ENABLED`.

**Backend:**
- Routes `/options`, `/sessions` (create/list), `/sessions/active`, `/sessions/{id}`, and `/start`, `/answer`, `/next`, `/finish`, `/abandon` for a session. All are behind `require_mock_interview` and students only, with tenant + owner 404s.
- Resume owner and tenant are checked on create and on every load (Phase 2 finding M1).
- Answers are saved before the AI call; `turn_seq` is the idempotency key.
- A per-session lease (compare-and-set on `updated_at`) allows one AI step at a time, and finish/abandon win over late AI results.
- Per-student rate limit (20/min per route) on the AI steps.
- Overdue sessions close automatically; the profile is capped.
- **No migration, no change to existing tables/env vars/API responses.** `/status` and `/catalog` are unchanged.

**Frontend:** setup form → instructions → interview room → finished view, with:
- progress bar and countdown;
- answer drafts kept per tab;
- retry banner when the next question failed;
- end with confirmation;
- refresh recovery via `/sessions/active`.

### Files
**Created:**
- AI worker:
  - `ai-worker/interview/{schemas,safety,topics,policy,llm_io,prompts,graph}.py`
  - `ai-worker/tests/test_interview_engine.py`
- Backend:
  - `backend/app/services/mock_interview.py`, `backend/app/services/mock_interview_ai.py`
  - `backend/tests/test_mock_interview_sessions.py`
  - `backend/tests_pg_api/test_mock_interview_pg.py` (real-Postgres Phase 3 tests: opt-in, like the rest of `tests_pg_api`)
- Frontend: `frontend/src/components/mock-interview/{mockInterviewApi.ts, InterviewSetupForm.tsx, InterviewInstructions.tsx, InterviewRoom.tsx, InterviewFinished.tsx}`
- Docs: `docs/mock-interview/specs/phase-3-interview-engine.md`, `docs/mock-interview/API.md`, `docs/mock-interview/AI_INTERVIEW_ARCHITECTURE.md`

**Modified:**
- `ai-worker/interview/__init__.py` (docstring), `ai-worker/interview/router.py` (+plan/turn)
- `backend/app/routers/mock_interview.py` (+Phase 3 routes; Phase 1 routes unchanged)
- `backend/app/schemas/mock_interview.py` (+Phase 3 schemas)
- `frontend/src/app/dashboard/mock-interview/page.tsx` (the workspace replaces the "coming soon" empty state)
- this file

**Untouched:** `ai_client.py`, `main.py`, `config.py`, models and migrations. `.claude/` was untracked before Phase 3 and is not part of this work.

### Tests (final results on the final code, 2026-11-19)
| Suite | Command | Result |
|---|---|---|
| **Full backend regression (SQLite)**, run alone | `cd backend && D:/Campus_Ai/backend/.venv/Scripts/python -m pytest -p no:cacheprovider -rfEs` | **Exit 0: 243 passed, 12 skipped, 0 failed** (1 warning: the pre-existing Starlette/httpx deprecation), 803 s, 08:56:24–09:09:53. Phase 2 had 212 passed, so this adds 31 Phase 3 tests, which include the mentor regression (`test_mentor_memory.py`) and the Phase 1 flag tests. The 12 skips are the same opt-in Postgres tests as Phase 2 (8 `test_mock_interview_rls_postgres.py` + 4 `test_rls_postgres.py`), which need `RLS_TEST_DATABASE_URL` |
| AI worker (no pytest in the worker venv; a plain runner calls each `test_*`) | `ENVIRONMENT=test ai-worker/.venv python run_worker_tests.py test_interview_engine test_interview_flag test_worker_security` | **Exit 0: 29 passed, 0 failed, 0 skipped** (21 engine + 2 flag + 6 existing security) |
| Frontend typecheck (whole project) | `npx tsc --noEmit` | **Exit 0** |
| Frontend lint (whole project) | `npm run lint` | **Exit 0: 0 errors**, 2 pre-existing warnings in `e2e/smoke.spec.ts` (untouched file) |
| App import, flag off and on | `import main` + `configure_mappers()`, `SAWarning` treated as an error | **Pass** (2026-11-18, after the backend fixes; no backend code changed since) |

Phase 3 backend tests (`test_mock_interview_sessions.py`) cover:
- the flag off (all routes 404, both mounts);
- staff 403 and anonymous 401;
- validation;
- `/options` lists only the caller's own resumes;
- resume owner and institute checks (create + re-check on every load);
- JD grounding passed to the engine;
- the HR flow with an adaptive follow-up, ordering and a graceful end;
- the technical config;
- time-up;
- duplicate and conflicting answers;
- idempotent start;
- worker failure keeps the answer and `/next` recovers;
- start-failure retry reuses the plan;
- cross-student and cross-tenant 404s;
- refresh recovery and the one-active rule;
- finish/abandon;
- the daily limit;
- the lease (409);
- finish winning over an in-flight AI step;
- overdue auto-close;
- the profile cap;
- the hard-cap evaluation;
- the per-student rate limit.

### Validation: Postgres, live LLM, browser (2026-11-19)

**Environment and safety:**
- Docker Desktop was not running, so I started it.
- The 9 existing containers (all stopped, from other projects and the local stack) were not touched.
- **Throwaway Postgres:**
  - container `campus_p3_pgtest`, image `postgres:16@sha256:63e83b5a…` (pinned local digest);
  - data on `tmpfs` only, started with `--rm`;
  - bound to `127.0.0.1:15433`, database `campus_rls_test`;
  - no volumes created (volume list compared before and after);
  - removed after the run.
- Credentials: random throwaway owner and `app_user` passwords, kept in session scratch files and passed only through a helper that redacts them from output. Deleted afterwards.
- **Incident:** a first role-creation attempt failed, and SQLAlchemy echoed the bound parameter, which printed the throwaway `app_user` password once into the session transcript.
  - That password was rotated before the role was ever created.
  - The helper then switched to `hide_parameters=True` plus output scrubbing.
  - Checked afterwards: no secret appears in any test log.
  - Production or real credentials were never involved.

| Check | Result |
|---|---|
| Migration chain as owner: `upgrade head` (full chain from an empty database to `b4d8f2a6c9e1`) → `downgrade -1` → `current` = `a7c3e9d2b4f1` → `upgrade head` → single head | **PASS** |
| `app_user` attributes: not superuser, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB; 3 interview tables RLS enabled + forced | **PASS** |
| `tests/test_rls_postgres.py` + `tests/test_mock_interview_rls_postgres.py` as `app_user` (Phase 2 RLS, CHECK, composite tenant FK and cascade tests) | **PASS: 12 passed** |
| **New** `tests_pg_api/test_mock_interview_pg.py` (6 tests, real API, real threads, own TestClient and DB connection per thread) | **PASS: 6 passed**; concurrency tests repeated 3× = 3/3 pass |
| ↳ flow + ownership + resume + RLS: other student/institute 404 on every route; teacher 403; classmate's and other institute's resume 404; `/options` lists only own resumes; rows visible only under the owning tenant's setting (0 under the other tenant, 0 with none = default deny) | PASS |
| ↳ parallel creates (6 threads): exactly one 201, five 409, one active row | PASS |
| ↳ parallel starts (5 threads): planned once, opening question once, a single turn 1 | PASS |
| ↳ one AI step at a time: 6 parallel `/next` → exactly 1 engine call, seqs `[1, 2]`, lease released | PASS |
| ↳ concurrent answers (6 threads, 2 different texts): one text stored, every other-text request 409, exactly 1 engine call, 2 turns | PASS |
| ↳ finish beats a late AI result: AI held mid-step, finish → completed; late result dropped (no turn 2) | PASS |
| **Mutation checks** (scratch-only plugins, one run each, to prove the tests detect the bugs): `FOR UPDATE` disabled → parallel-create test **fails** (6×201); lease check disabled → parallel-start (2 plans) and one-AI-step (2 calls) tests **fail** | PASS (the tests are meaningful) |
| **Full `tests_pg_api`** as `app_user` (includes the 6 new tests) | **PASS: exit 0, 222 passed, 2 skipped** (the same Redis-queue skips as Phase 2; Phase 2 had 216 passed); preflight and cleanup checks OK (non-RLS counts back to baseline, 0 marked rows left) |
| **Live LLM smoke test** (HR + technical, structured output, relevance, follow-ups, no fabrication, Hindi/Hinglish, latency, timeouts) | **NOT RUN.** No development LLM credential exists: no `.env` in the worktree or `D:/Campus_Ai` (only `.env.example`), and no `GROQ_*`, `GOOGLE_API_KEY`, `GEMINI_*` or `LLM_PROVIDER` variable in the shell, user or machine environment (names checked only). Nothing was invented or configured |
| **Browser smoke test** (headless Chromium 1248 via the project's Playwright; local backend on a scratch SQLite file with the flag on and a **scripted fake engine**; local `next dev`) | **PASS for 21 of 21 functional checks.** Details below |

**Browser checks that passed:**
- login, then the sidebar entry appears;
- the setup form renders: options, own resume listed, empty-role validation;
- the instructions screen summarizes the configuration;
- Start shows a loading state, then the opening question;
- countdown and progress bar are visible;
- the "thinking" indicator shows while the AI works;
- a labelled follow-up appears after a short answer;
- Ctrl+Enter submits;
- an injected AI failure shows the friendly 503 message plus the "answer is saved" banner, and the answer stays visible in the transcript;
- "Get next question" recovers;
- after a refresh, both the draft answer and the transcript are restored;
- "End interview" with confirmation leads to the completion screen and the transcript;
- after completion, a refresh shows a new setup form.

Screenshots are in the session scratchpad `ui/shots/`.

**Two checks were reported as failing and are explained, not Phase 3 defects:**
- **Console error:** the browser logs the 503 response that the test injected on purpose.
- **Phone-width horizontal overflow (390 px):** caused by the existing `AppShell`, which keeps its 220 px sidebar on screen at phone width.
  - Every page overflows: `/dashboard` 153 px, `/dashboard/skills` 153, `/dashboard/mentor` 194, `/dashboard/resume` 275.
  - The mock-interview setup page and the room, loaded at 390 px (with a transcript), match the 153 px baseline: they add **no** overflow of their own.
  - Seen once and not investigated: about 45 px of extra overflow after resizing a desktop-width window down to 390 px mid-session.

**Side effects:**
- `next dev` auto-generated an untracked `frontend/AGENTS.md` (Next.js agent notes, 09:58).
- My attempt to delete it was blocked by the permission system, so **it is still in the worktree**.
- It is not Phase 3 work; keep it, delete it or ignore it, your choice.

**Explained log noise:** the full `tests_pg_api` log has 5 server 500s ("value too long for varchar(60)" on `audit_logs.action`). They come from existing tests that deliberately force an oversized audit action to prove a delete rolls back when its audit write fails. That is expected behaviour, and those tests pass.

### Phase 3 acceptance criteria
| Criterion | Status | Evidence |
|---|---|---|
| A student can create a text interview | **Met** | SQLite + Postgres + browser |
| HR questions work | **Met with a stubbed LLM; live NOT RUN** | engine/flow tests. Real question quality unverified |
| Technical questions work | **Met with a stubbed LLM; live NOT RUN** | same |
| Follow-ups adapt to answers | **Met with a stubbed LLM; live NOT RUN** | policy tests + browser follow-up. Real follow-up quality unverified |
| Resume and JD grounded and ownership-verified | **Met** | grounding tests; resume 404 on SQLite **and Postgres** |
| Answers and turns persist correctly | **Met** | ordering, duplicates and concurrent answers on Postgres |
| An active session can be recovered | **Met** | API test + browser refresh (transcript and draft restored) |
| Tenant isolation enforced | **Met** | app-layer 404s + RLS under `app_user` on Postgres |
| Feature flag OFF by default | **Met** | flag tests |
| Existing features keep working | **Met** | SQLite 243/0 failed; Postgres API 222/0 failed; lint/tsc clean |
| Regression and security tests pass | **Met** | all suites above |
| Documentation updated | **Met** | spec, API.md, AI_INTERVIEW_ARCHITECTURE.md, this file |

### Security and code-quality review (two independent read-only agents)
No High tenant-isolation or IDOR findings. Everything below is **fixed in code**, and the fixes are covered by the tests above, except where noted.

**Abuse and concurrency:**
- (H1) Parallel `/next` or `/start` multiplied LLM calls and tied up threads. Fixed with the per-session lease, the per-student limit, and no retry on worker 5xx/timeouts.
- (M1) Parallel creates bypassed the one-active rule. Fixed with a user-row `FOR UPDATE` lock. **Verified on Postgres** (real threads + mutation check).
- (L2) finish/abandon raced with `advance`. Fixed by the compare-and-set finalize.

**Prompt hardening:**
- (M2) The replayed transcript was not sanitized in the question prompt.
- (M3) Role/company went into the system prompt. Now limited to a character allow-list; the follow-up angle and grounding moved into a `<context_note>` data tag.
- (L1) Invisible characters and unterminated tags slipped past tag neutralization.
- (L3) Worker schema caps were missing.

**Code-quality findings:**
- the uncapped profile caused a permanent 503;
- answer 30 was never evaluated;
- the follow-up allowance used the adaptive level instead of the configured difficulty;
- curly-apostrophe "don’t know" was missed;
- overdue sessions blocked new ones;
- a duplicated "Self introduction" topic;
- stale drafts and the 409 resync in the frontend.

**Open / accepted:**
- (L4) With Langfuse configured, prompts (including resume, JD and answers) are traced, the same as the existing mentor and resume features. This needs a privacy note before rollout.
- Two concurrent first-time `/start` calls are serialized by the lease; **verified on Postgres** (5 parallel starts → 1 plan).

### Not verified (reported separately)
- **Live LLM: NOT RUN** (no development credential). This leaves unverified: real Groq/Gemini question relevance, follow-up quality, structured-output reliability in practice, fabrication behaviour, English/Hindi/Hinglish wording, and latency under a 60 s timeout. Engine logic and provider-failure handling are covered by stubbed tests only.
- **Browser (partly):**
  - the browser run used a scripted fake engine, not the AI worker;
  - headless Chromium only, no real phone or screen-reader test;
  - `next build` not verified;
  - there are no frontend tests in this project.
- **Pre-existing, not Phase 3:** the `AppShell` sidebar overflows at phone width on every dashboard page. A mobile shell fix is out of scope.
- **Production-like run:** a multi-worker server under load was not run; the Postgres concurrency tests use in-process threads.
- **Worker under pytest:** the worker tests run through a plain runner because the worker venv has no pytest; they are not collected by pytest itself.
- **Not addressed:**
  - (L4) Langfuse tracing includes candidate text when configured; this needs a privacy note before rollout.
  - `/catalog` (Phase 1) still lists every type, mode and duration; Phase 3 uses `/options`. This is intentional, so an existing response doesn't change.
- **Test side effect:** the existing worker face test leaves an empty `ai-worker/qdrant_data/` behind. It isn't in `.gitignore`, which only lists `qdrant_storage/` and `qdrant_local*/`; I removed it by hand each time.

### Open items after the Phase 3 commit (accepted limitations, not blockers)
1. **Live LLM smoke test (NOT RUN; accepted).** It needs a development-only Groq or Gemini key, which you provide in a local, uncommitted worker env. Then one HR and one technical interview in English and Hinglish, checking: relevance, follow-ups, no fabrication, latency, and recovery from timeouts.
2. Frontend production build (`next build`), real-device and screen-reader testing, and multi-worker load testing are still unverified.
3. The untracked `frontend/AGENTS.md` (generated by `next dev`) was deliberately left out of the commit and is still in the worktree for your decision.
4. **Before any production rollout:** the live LLM check above, the Langfuse privacy note, and the rollout steps under "Production rollout". Phase 3 is **not** production-ready.

## Phase 2 — changes
Created:
- `backend/app/models/mock_interview.py`: `MockInterviewSession`, `MockInterviewTurn`, `MockInterviewReport`; allowed values as `*_VALUES` constants.
- `backend/alembic/versions/b4d8f2a6c9e1_mock_interview_sessions_turns_reports.py`
- `backend/tests/test_mock_interview_models.py` (SQLite), `backend/tests/test_mock_interview_rls_postgres.py` (opt-in Postgres)

Modified (additive):
- `backend/app/models/__init__.py`: +3 imports / `__all__` entries
- `backend/tests/test_rls_postgres.py`: the 3 tables are appended to `RLS_TABLES` (the default-deny check now covers them)
- `backend/tests/test_mock_interview_flags.py`: the Phase 1 guard "no interview tables" became "exactly the 3 Phase 2 tables"

## Database changes (migration `b4d8f2a6c9e1`, additive only)
Three new tables. No existing table, column, policy or grant is changed.

| Table | Notes |
|---|---|
| `mock_interview_sessions` | `tenant_id`, `student_id`, `interview_type`, `mode`, `status`, `target_role`, `company_name`, `difficulty`, `language`, `duration_minutes` (5–120), `resume_id` (FK resumes, SET NULL), `job_description`, `plan_json`, `campaign_id` (plain int; FK in Phase 9), `started_at`, `completed_at`, timestamps |
| `mock_interview_turns` | `session_id`, `seq` (≥1, unique per session), `topic`, `question`, `is_follow_up`, `answer_text`, `answer_source` (typed/stt), `answered_at`, `turn_eval_json` |
| `mock_interview_reports` | `session_id` (unique), `overall_score` (0–100), `category_scores_json`, `strengths_json`, `weaknesses_json`, `recommendations_json`, `rubric_version`, `provider` |

- All three tables: `ENABLE` + `FORCE ROW LEVEL SECURITY` + the standard `tenant_isolation` policy. `app_user` gets access through the owner's existing default privileges (verified).
- **Tenant-safe foreign keys** (Postgres checks FKs without RLS):
  - turns and reports use `(session_id, tenant_id) → sessions(id, tenant_id)`;
  - sessions use `(student_id, tenant_id) → users(id, tenant_id)`, which reuses the existing `uq_users_id_tenant`.
  - So a row can never point at another institute's session or student.
- **Allowed values:** enforced by named CHECK constraints (no Postgres ENUM types). They match `/catalog` exactly, and a test checks that.
- **Cascades:** deleting a tenant, student or session removes its turns and report; deleting a resume sets `resume_id` to NULL.
- **Downgrade** drops the three tables and **all interview data**. Use it only after an explicit decision and a backup.

API: **no changes** in Phase 2. Env vars: none.

## Tests (actual results, Phase 2)
| Suite | Result |
|---|---|
| Backend full regression (SQLite, worktree) | **212 passed, 12 skipped, 0 failed** (Phase 1: 183 passed). Skips = 4 baseline Postgres tests + 8 new opt-in Postgres tests |
| Migration on throwaway SQLite | **Pass.** upgrade → downgrade → upgrade, then compared with the models: **0 differences**. Base schema built from the models and stamped at `a7c3e9d2b4f1`, because the historical chain can't run on SQLite: `1d61da8d89e7` uses ALTER constraint, which predates this work. |
| Full migration chain on throwaway Postgres 16 (docker, pinned digest, tmpfs, localhost:15433) | **Pass.** All 53 migrations upgrade; `downgrade -1` leaves 0 tables and 0 policies; upgrade again; a 2-step downgrade and upgrade also pass. Compared with the models: 0 differences for the new tables (1 unrelated pre-existing difference). |
| `test_mock_interview_rls_postgres.py` + `test_rls_postgres.py` (as NOBYPASSRLS `app_user`) | **12 passed.** Covers: forced RLS with an exact policy expression; cross-tenant read, update and delete blocked; WITH CHECK (`42501`); a turn or report can't attach to another tenant's session; the student must be in the same tenant; CHECK constraints; DB cascades; default-deny. |
| `tests_pg_api` (full real-Postgres API suite, run on the migrated DB) | **216 passed, 2 skipped** (Redis queue tests; no isolated Redis), 0 failed |
| `import main` with the flag off/on, SQLAlchemy warnings treated as errors | Pass (no relationship-overlap warnings) |

The container was removed after the run; its throwaway passwords lived only in the scratchpad and were deleted.
Frontend and worker are untouched in Phase 2, so they were not re-run.

## Security review (independent read-only agent)
No high findings.
- **Fixed:**
  - (M2) the WITH CHECK test accepted any DB error; it now asserts SQLSTATE `42501` plus the "row-level security" message.
  - (L4) added a direct session-delete cascade test on Postgres.
  - (L5) the policy test now checks the exact USING and WITH CHECK expressions.
  - (L6) dropped 2 redundant indexes: `student_id` is covered by `(student_id, created_at)`, and `session_id` by `uq(session_id, seq)`.
  - (L8) CHECK value quoting now uses explicit SQL escaping.
- **Deferred to Phase 3, by design:**
  - (M1) `resume_id` is a single-column FK. A database-level fix would need a new unique key on the existing `resumes` table, which the project rules forbid. **Phase 3 must check the resume's owner and tenant in the app layer, with a test.**
  - (L3) delete-orphan plus unique keys: replace a report or turn by updating it in place, or flush the delete first.
  - (L7) the composite student FK has no ON UPDATE. Changing a user's `tenant_id` would fail once they have sessions; no code does that today.

## Known issues
- **Not verified:**
  - `next build` (Windows Application Control blocks SWC in the worktree; from Phase 1).
  - The Redis queue path in `tests_pg_api`.
- **Test environment:**
  - The backend suite shares `%TEMP%\campus_ai_test.db`; never run two at once.
  - Windows reserves port 55432 on this machine; use 15433 for the throwaway Postgres.
- **Cleanup:** stray file `D:\mi_t.txt` from Phase 1 (safe to delete by hand).
- **Local ports:** your Docker stack `campus-ai-mock-local-*` uses 3000 and 8000. Scratch test servers used 3101 and 8101.

## Rollback
Phase 2 code is unused while the flags are off. In a database the migration has reached, the normal rollback is flags off (data kept).
`alembic downgrade a7c3e9d2b4f1` removes the tables and their data, so use it only after a backup.

## Production rollout (not done; documentation only)
Deploy code with flags off → run `b4d8f2a6c9e1` with the owner role (`migrate` profile) → confirm `app_user` grants and RLS
on the 3 tables (`app_role.template.sql` audit query) → enable flags for a pilot tenant in a later phase.

## Remaining tasks
See "Status at a glance" for the full list. In short:
- **Phase 5:** the manual real-device verification (spec §8), a full regression of the latest code, the live LLM
  check. (Committed locally as `34fd3a9`; none of these checks has been done yet.)
- **Phase 6:** not started. Follow the approved sequence (audit → privacy/security design → incremental
  implementation → integration testing) in `specs/phase-6-video-interview-handoff.md`.
- **Phases 7–10:** not started; each needs your explicit go-ahead.
- **Open decisions:**
  - Phase 6 consent, recording and privacy;
  - the Phase 7 code-sandbox host;
  - recording retention.
  - The voice provider is decided: browser Web Speech.

## Next exact action
**Phase 5 final manual verification** (spec §8). Nothing else is in progress. Phase 6 has **not** started.
- Separately and optionally: a dependency-security review of Next.js 16.4.4.
