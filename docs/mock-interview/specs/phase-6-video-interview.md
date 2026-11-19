# Phase 6 — AI Video Interview (design specification)

Status: **design approved 2026-11-19; Phase 6.1 (foundations) implemented and committed as `1ad5628`; Phase 6.2
(camera infrastructure) implemented; Phase 6.3 (video lobby, room and session lifecycle) implemented, with
`VIDEO_UI_READY = true`.** Video is still OFF unless the build flag and the server gates allow it. Real-device
testing is pending. Remaining work: §6.
Supersedes the handoff `phase-6-video-interview-handoff.md` (kept for history). Overall status:
`../PROJECT_STATE.md`.

## 1. Approved decisions (2026-11-19)

| # | Decision |
|---|---|
| D1 | Phase 5 real-device tests are **deferred**. Phase 6 may proceed; Phase 5 stays marked incomplete. |
| D2 | **Browser-only camera preview.** No recording, no upload, no server-side or persistent video. |
| D3 | Separate `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS`. **Empty = no institute** (fail closed). |
| D4 | Separate frontend build flag `NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED` (default `false`). |
| D5 | Camera allowed **only on the mock-interview route**, only when the build flag is on. The existing microphone policy is preserved. |
| D6 | Pause/resume **deferred** (not in Phase 6). |
| D7 | Camera required to **enter** the video lobby; if it fails mid-interview the session continues without camera. |
| D8 | Per-session camera/privacy acknowledgement in the lobby. **No server-side consent storage.** |
| D9 | Video report: **frontend-only** note that camera video was not recorded or analysed. Backend disclaimer unchanged. |
| D10 | Camera stream requests `audio: false`. Browser speech recognition (Phase 5) owns the microphone. |
| D11 | Static interviewer tile with a speaking animation. No external avatar service. |
| D12 | Video sessions created before the flag is turned off may finish. |

## 2. Architecture

A video interview is a Phase 5 voice interview plus a **local camera self-view**:

```
camera ──getUserMedia({video, audio:false})──> <video> element (this tab only; never recorded/uploaded)
microphone ──browser SpeechRecognition──> editable transcript ──(student reviews)──> POST /answer {answer, source:"stt"|"typed"}
question text <── backend <── AI worker (unchanged)          speechSynthesis reads the question aloud
```

- **Server side:** unchanged engine, session state machine, lease, idempotency (`turn_seq`), timeout, report and
  history. The only data model value used is `mode = "video"`, which the Phase 2 CHECK constraint already allows.
  **No migration. No AI worker change. No scoring change.** The worker never receives `mode`.
- **Data flows:** camera frames reach only the local `<video>` element. Microphone audio is processed by the
  browser's speech service (Chrome: Google), as disclosed in Phase 5. Only reviewed answer text reaches the backend.
- **Forbidden:** facial recognition, emotion/sentiment detection, gaze or attention tracking, liveness or identity
  checks, any use of the face-attendance code or Qdrant `student_faces`, and `MediaRecorder` / canvas capture /
  frame encoding. No imports from `components/face`.

## 3. Access control (implemented in 6.1)

A student may create a video session only when **all** hold (`mock_interview_video_enabled_for` in
`backend/app/routers/mock_interview.py`):

1. `AI_MOCK_INTERVIEW_ENABLED=true` (master switch);
2. the institute passes `AI_MOCK_INTERVIEW_TENANT_IDS` (empty = all; invalid = none: unchanged Phase 1 rule);
3. `AI_MOCK_INTERVIEW_VIDEO_ENABLED=true`;
4. the institute is listed in `AI_MOCK_INTERVIEW_VIDEO_TENANT_IDS` (**empty or invalid = none**);
5. role is `student`.

| Caller | `/options` | `POST /sessions {mode:"video"}` |
|---|---|---|
| Master off, or institute not in master list, or recruiter | 404 | 404 |
| Teacher / TPO / admin (master gate passes) | 403 | 403 |
| Student, any of 3–4 failing | 200, no `video` mode | **422** "Video interviews are not available" |
| Student, all gates pass | 200, `video` appended after `text`/`voice` | 201, `mode:"video"` |

- Video is independent of the voice flag.
- Existing sessions are owner + tenant scoped (404 otherwise) exactly as before. A video session keeps working
  after access is withdrawn (D12): answer, next, finish and report do not consult the video flags.
- `answer_source = "stt"` is accepted for `voice` and `video` sessions (`SPOKEN_MODES`); `stt` on a text session is
  still 422 with the unchanged Phase 5 message.

## 4. Browser configuration (implemented in 6.1)

- Build flag: `NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED` (Dockerfile `ARG`, default `false`). Effective only together
  with `NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED=true`.
- `frontend/next.config.ts` → `buildHeaderRules()`:
  - always: the existing global rule `/:path*` (CSP, `Permissions-Policy` with `camera=()`, ...), unchanged;
  - only when both build flags are `"true"`: extra rules for `/dashboard/mock-interview` and
    `/dashboard/mock-interview/:path*` that re-send the **global** `Permissions-Policy` with only the camera
    directive changed to `camera=(self)`. Microphone and geolocation therefore follow the global header (currently
    the local Phase 5 edit `microphone=(self)`). Next applies the last matching rule for a header key.
- No CSP change: a `<video>` with `srcObject = MediaStream` is not a fetch.
- **Client-side navigation caveat (6.2 must handle):** `Permissions-Policy` is fixed per document. Reaching
  `/dashboard/mock-interview` via `next/link` from another page keeps that page's `camera=()`. The video lobby must
  detect this (`document.featurePolicy?.allowsFeature("camera")` in Chromium; otherwise the `getUserMedia` failure)
  and offer a "Reload to enable camera" button that does a full page load. Conversely, after the mock-interview
  page was the loaded document, the camera stays permitted for that document while navigating client-side; only
  mock-interview code ever calls `getUserMedia`.
- Setup form: `visibleModes()` (`mockInterviewModes.ts`) drops `video` unless
  `MOCK_INTERVIEW_VIDEO_AVAILABLE` (= build flag AND `VIDEO_UI_READY`). `VIDEO_UI_READY` is `false` in 6.1, so video
  is **never offered in the UI yet**; 6.3 flips it once the lobby and room exist. `MODE_LABELS` knows "Video".

## 5. Camera behaviour (implemented in 6.2; rendered by a page from 6.3)

All files are in `frontend/src/components/mock-interview/`.

**`cameraMedia.ts`** (no React; browser globals injected through `CameraEnv`, so it is unit-tested with fakes):
- `cameraSupport()`: `insecure-context` (checked first; `mediaDevices` is missing on plain-http LAN addresses),
  `unsupported`, or `ok`.
- `cameraPolicyAllowed()`: `document.permissionsPolicy` / `featurePolicy` `.allowsFeature("camera")` where the
  browser exposes it (Chromium), else `null` (unknown).
- `cameraConstraints(deviceId?)` = `{ video: { width: {ideal: 640}, height: {ideal: 360}, deviceId?: {exact} },
  audio: false }`. **Audio is never requested.**
- `classifyCameraError()` / `describeCameraError()`: `NotAllowedError` → `not-allowed` (or `policy-blocked` when the
  policy API says the camera is blocked), `SecurityError` → `security` (or `policy-blocked`), `NotFoundError` →
  `not-found`, `NotReadableError`/`AbortError` → `in-use`, `OverconstrainedError` → `overconstrained`, `TypeError` →
  `unsupported`, else `unknown`; plus `insecure-context`, `device-lost` and `policy-still-blocked`.
- `stopStream()` stops every track, each in its own try. `listCameras()` returns `videoinput` devices only, with
  numbered names until permission reveals labels.
- `lobbyReady(state, acknowledged)` = acknowledged AND status `on` AND a live video track.
- **`CameraController`** owns at most one stream:
  - the camera is requested only by `start()`; the constructor and `attach()` never request it;
  - concurrent `start()` calls share one request;
  - stale guard: a request that finishes after `stop()`, a newer request or `detach()` is stopped at once;
  - the old stream is stopped **before** a new one is requested (switching camera, restarting);
  - a saved camera that no longer exists (`OverconstrainedError`/`NotFoundError` with an exact id) is forgotten and
    the default camera is tried once, with a notice;
  - track `ended` (or `devicechange` with no live track) → `device-lost` error, tracks stopped; track
    `mute`/`unmute` → a notice only;
  - `devicechange` refreshes the list; a selected camera that disappears while off is forgotten, with a notice;
  - `stop()`, `detach()` (unmount) and `pagehide` stop every track;
  - if the policy API says the camera is blocked, it fails with `policy-blocked` **without** calling `getUserMedia`.
- The chosen camera's `deviceId` is kept in `localStorage` (`mock-interview:camera-device`, try/catch) on this device
  only; it is never sent to the server.

**Reload fallback** (the client-side-navigation caveat in §4): on `policy-blocked` or `security` the lobby offers
**"Reload to enable camera"**, which stops the camera, sets a `sessionStorage` marker and calls
`window.location.reload()` (a full load of the same mock-interview URL, so the route's `camera=(self)` applies).
It is never automatic. If the camera is still policy-blocked after that reload, the error becomes
`policy-still-blocked` and no reload button is offered again (no loop). A working camera clears the marker. The
global camera, microphone and geolocation policies are unchanged.

**`useCamera.ts`**: `useSyncExternalStore` over one `CameraController`; the mount effect only calls `attach()` and
the cleanup calls `detach()`. Returns the state plus `start`, `stop`, `switchDevice`, `refreshDevices` and
`reloadToEnableCamera`. Meant to be called once in the interview workspace (6.3) and passed down, so the lobby
and the room share one stream.

**Components** (props only; no API calls):
- `CameraPreview`: `<video autoPlay muted playsInline>` with `srcObject`, mirrored self-view, "Camera on · preview
  only" indicator, off / starting / error placeholders; on unmount it detaches `srcObject` (the owner stops tracks).
- `CameraDeviceSelect`: "Default camera" plus the listed cameras; hints when names are hidden before permission or
  no camera is detected.
- `VideoPrivacyNotice`: preview only; not recorded, not uploaded, not analysed (no face, emotion, eye or identity
  analysis); only reviewed answer text is saved; the browser's speech service may process microphone audio; plus
  the per-session acknowledgement checkbox (component state; nothing sent to the server).
- `VideoLobby`: preview, Turn on / Turn off camera, camera picker, readiness checklist, privacy notice, reload
  fallback and notices. **Continue** is enabled only when `lobbyReady()` (live camera AND acknowledgement). The
  camera button does not wait for the acknowledgement; the camera starts only when it is clicked. On mount the
  lobby only lists cameras (no permission prompt, no stream).

**From 6.3:** these components are rendered by `VideoInterviewWorkspace` / `VideoInterviewRoom` (§5a).

**Browser notes:** device labels are empty until permission is granted (Chrome also hides device ids); iOS Safari
needs `playsInline muted autoPlay`; Firefox/Brave have a camera but no usable speech recognition (typing fallback);
a camera can be held by another app (`NotReadableError`); mobile browsers may pause (`mute`) or end the camera
track in the background.

## 5a. Video lobby, room and session lifecycle (implemented in 6.3)

All files are in `frontend/src/components/mock-interview/`.

**Routing.** `page.tsx` sends a session with `mode === "video"` to `VideoInterviewWorkspace`; text and voice keep
`InterviewInstructions` / `InterviewRoom` unchanged. The setup form offers "Video" only when the server lists it
AND `MOCK_INTERVIEW_VIDEO_AVAILABLE` (build flag AND `VIDEO_UI_READY`), and then sends `mode: "video"`.

**`VideoInterviewWorkspace.tsx`** is the single camera owner (`useCamera()` is called only here; a test enforces
it). It picks the screen with `videoStage()` (`videoInterviewFlow.ts`), using the same rules as the text/voice page:
- `setup` (or awaiting start) → lobby step: a session summary and `VideoLobby`. **Start interview** needs a live
  camera AND the acknowledgement (D7, D8) and calls the existing `POST /start`. **Cancel interview** calls the existing
  `POST /abandon` and stops the camera. On failure it re-reads the session, as `InterviewInstructions` does.
- `in_progress` → `VideoInterviewRoom` with the SAME camera handle: the lobby's stream continues, no second request.
- ended → the existing `InterviewFinished` (report). "Start another interview" unmounts the workspace, which stops
  the camera.
- An effect calls `releaseCameraIfEnded()` on every status change: any terminal status (`completed`, `abandoned`,
  `failed`, `evaluating`) stops every track, whoever ended the session (student, time-out, the engine).

**`VideoInterviewRoom.tsx`** uses the same session API and behaviour as the text/voice room: `POST /answer` with the
idempotent `turn_seq` (source `stt` or `typed`), the `next_question` retry (`POST /next`), `POST /finish`, a resync
after failures, the exported `Countdown`, and the same "read each question once" rule and storage key as the voice
room. Layout:
- **Toolbar:** title and progress; **Mute mic**, **Stop/Start camera** and **Mute voice** (interviewer TTS) buttons
  with `aria-pressed`; timer; End interview with confirmation; a status line with the camera status, the microphone
  status and, when the browser is offline, an offline notice. There is no live connection (plain requests), so
  connectivity is the browser's online state plus request errors.
- **Interviewer tile:** a static "AI" avatar, a speaking animation driven by the existing `useSpeechSynthesis`
  `speaking` state, and the current question with its topic and follow-up label. No avatar provider.
- **Candidate tile:** `CameraPreview` (camera-on indicator, off/starting/error placeholders), Start/Try camera
  again, the reload fallback, and a camera picker when more than one camera is listed.
- **Answer:** the Phase 5 `VoiceAnswerBox` (dictation or typing; the student reviews the text before submitting),
  the retry block, a "thinking" state, and a collapsible "Conversation so far".
- Responsive: two columns from `lg`, stacked below; the title wraps on phones (a no-wrap title widened the shell).

**Camera and microphone (D10).** The camera stream is video-only. The microphone belongs to browser speech
recognition inside `VoiceAnswerBox`, which gained two optional props used only by the video room: `micMuted`
(stops dictation and disables Start speaking; typing still works) and `onMicStateChange` (feeds the toolbar). The
voice room passes neither, so voice behaviour is unchanged. Muting the mic never touches the camera; stopping the
camera never touches dictation. No second microphone stream, no server-side audio.

**Lifecycle rules.**
- The camera is requested only from clicks (lobby, room); never on load, never after a refresh. After a refresh the
  room resumes from the server with the camera off; the unsent answer draft survives, as before.
- Camera failure after the start (track ended, unplugged, denied) shows a status and the interview continues (D7);
  the student may try again.
- Finish and Cancel stop the camera at once; the workspace effect also stops it on any terminal status; unmount and
  `pagehide` stop it (6.2).
- Double clicks: every lobby and room action goes through `createActionGuard()` (one action at a time, even within
  the same tick), on top of the disabled buttons and the server's idempotency.
- No pause/resume (D6, deferred).

**Report and history (approved D9).** `InterviewFinished` shows a video-only note: the camera was only a preview on
screen, was not recorded, uploaded or analysed, played no part in the feedback, and the feedback is based only on the
submitted answer text. The history list labels video interviews "· Video" (voice keeps "· Voice"). The backend
disclaimer and scoring are unchanged.

## 6. Milestones

| Milestone | Scope | Status |
|---|---|---|
| 6.1 | Spec, backend flags + gate, `video` in schema/options, `stt` for video, frontend build flag, Dockerfile arg, route-scoped camera policy, tests, docs | **Implemented, committed `1ad5628`** |
| 6.2 | `cameraMedia.ts` + `useCamera`, acknowledgement, device selection, preview, error mapping, reload fallback; unit tests with fake `mediaDevices` | **Implemented**; not rendered by any page; real-device tests pending |
| 6.3 | `VideoLobby`, `VideoInterviewRoom` (interviewer tile, toolbar, timer, status, end/abandon), refresh recovery, `VIDEO_UI_READY = true` | **Implemented** (§5a, §9); real-device tests pending |
| 6.4 | Wire to existing TTS / dictation / answer / retry-next / finish | **Wiring done in 6.3** (needed before the UI could be enabled). Remaining: see §9 "Phase 6.4 remaining scope" |
| 6.5 | Report + history: "Video" label, frontend-only camera note (D9) | **Done in 6.3** (D9 note, history label); no backend report change by design |
| 6.6 | Responsive/mobile, accessibility, privacy guards, full regression, real-device checklist | Not started |

## 7. Phase 6.1 verification record (2026-11-19)

| Check | Result |
|---|---|
| New `backend/tests/test_mock_interview_video.py` | 30 passed |
| All mock-interview backend suites (flags, models, sessions, reports, voice, video), SQLite | 162 passed, 0 failed |
| Full backend regression (SQLite) | 332 passed, 12 skipped (opt-in Postgres RLS), 0 failed |
| Live headers via `next dev` (flags on / off) | camera `(self)` only on `/dashboard/mock-interview(/…)` when on; `camera=()` everywhere when off; microphone `(self)` unchanged |
| Frontend `npm run test:unit` (26 Phase 5 + 18 new in `mockInterviewVideoFoundations.test.mjs`) | 44/44 passed |
| `npx tsc --noEmit` | passed |
| ESLint (touched files) | 0 problems |
| ruff (touched Python) | clean |
| Real camera / browser | **not applicable in 6.1** (no camera code) |

The live-header rows above were run on the working tree, which also had the uncommitted Phase 5
`microphone=(self)` edit; the committed global header has `microphone=()`. Before commit `1ad5628`, one unit test
that asserted the global `microphone=(self)` was made independent of it; afterwards `npm run test:unit` passed 44/44
on both the working tree and an export of the staged snapshot. Details: `../PROJECT_STATE.md` → Phase 6.1.

## 8. Phase 6.2 verification record (2026-11-19)

| Check | Result |
|---|---|
| New `frontend/tests/unit/cameraMedia.test.mjs` (fake `mediaDevices`, tracks, streams, storage, window) | 46/46 passed |
| All frontend unit tests (`npm run test:unit`) | 90/90 passed (44 before + 46 new) |
| Mutation check (scratch copy): no stop-before-request / no stale guard / `attach()` starting the camera | caught by 2 / 2 / 3 failing tests |
| `npx tsc --noEmit` | passed |
| ESLint, touched files | 0 problems; full `npm run lint`: 0 errors, 2 pre-existing warnings (`e2e/smoke.spec.ts`) |
| Scratch browser check: real Chromium (Playwright, installed build 1248) with Chromium's **fake** camera device, `cameraMedia.ts` transpiled, local server sending the route policies | 15/15 passed (below) |
| Backend | not run: no backend change in 6.2 |
| Real camera, real devices | **not done** (pending; see 6.6 checklist) |

Scratch browser check (simulated camera, not a real device):
- with `camera=(self)`: policy API reports allowed; `start()` goes live; one video track, no audio track; the fake
  device honours 640×360; labelled cameras listed after permission; chosen camera saved; `stop()` and `pagehide`
  end every track;
- page loaded with `camera=()`, then `history.pushState` to `/dashboard/mock-interview` (like `next/link`): the
  policy stays blocked, the controller reports `policy-blocked` without requesting, and a raw `getUserMedia` call is
  refused by Chromium (`NotAllowedError`);
- after a full reload of that URL the route policy applies and the camera starts; the reload marker is cleared;
- still blocked after a reload: `policy-still-blocked`, no reload button offered.

The React components were type-checked and linted but not rendered in a browser: no page uses them until 6.3.

## 9. Phase 6.3 verification record (2026-11-19)

| Check | Result |
|---|---|
| New `frontend/tests/unit/videoInterviewFlow.test.mjs` (fake media APIs + source checks) | 24/24 passed |
| All frontend unit tests (`npm run test:unit`) | 114/114 passed (90 before + 24 new) |
| `npx tsc --noEmit` | passed |
| ESLint, touched files | 0 problems |
| Browser smoke test, final code (scratch, not in the repo) | **34/34 passed** (below) |
| Backend | not run: no backend change in 6.3 |
| **Real camera / real microphone / real devices** | **not done; pending** |

Two existing unit tests encoded the 6.1/6.2 state (`VIDEO_UI_READY === false`, "camera UI not wired into any page")
and were updated to the 6.3 state: the UI exists, video stays hidden while the build flag is off, and the camera
components are used only by the video files.

**Browser smoke test** (simulated devices; it proves the wiring, not real-device behaviour):
- Setup:
  - the real backend on a throwaway SQLite file with the scripted test engine and reporter
    (`FakeEngine`/`FakeReporter`) and fresh random secrets; flags were set in the process environment only;
  - `next dev` with both build flags on;
  - Playwright Chromium (installed build 1248) with `--use-fake-device-for-media-stream` and
    `--use-fake-ui-for-media-stream`, plus injected fake `SpeechRecognition`/`speechSynthesis`;
  - login through the API, as in the repo's e2e suite.
- Gates: an institute outside the video allow-list sees Text and Voice only (A1); the pilot institute sees all three
  (B1).
- Lobby: renders without a camera request (B2); Start disabled until the camera is on AND the acknowledgement is
  ticked (B3, B6, B7); one request after the click, `{video: {width: {ideal: 640}, height: {ideal: 360}}, audio: false}`
  (B4); the preview plays with the indicator (B5).
- Room:
  - same stream as the lobby, no second request (B8);
  - the question is shown and read aloud (B9);
  - dictation through `VoiceAnswerBox` (B10); Mute mic stops dictation while the camera stays on (B11); the
    dictated text is kept for review and saved (B12, B13);
  - Stop camera ends every track and the interview continues (B14); Start camera requests video only (B15);
  - a camera track ended by the system shows the status, and answering still works (B16);
  - at 390 px the room adds no horizontal overflow beyond the dashboard shell's own (B17, below);
  - a double click on "Yes, end" sends one `/finish` (B18); End stops every track (B19);
  - the finished view shows the D9 note and the report (B20); history lists "HR · Video" (B21).
- Abandon in the lobby stops every track (C1).
- Refresh during the interview: the room resumes with the camera off and no request; the unsent draft survives; the
  camera returns only on a click; End stops it (D1–D4).
- Client-side navigation from `/dashboard` via the sidebar: camera blocked, "Reload to enable camera" offered, no
  request; after the reload the camera starts on click (E1, E2).
- Regressions: a text interview uses the existing room (no camera UI, no request) and its report works (F1, F2); a
  voice interview uses the existing room (no camera, no mic toolbar), saves a spoken answer and its report works
  (G1–G3).

Harness notes (not app defects):
- Git Bash rewrote a `NEXT_PUBLIC_API_PREFIX=/v1` environment value into a Windows path. The harness now relies on
  the default prefix.
- The login form was not driven through the UI; the API login is the same approach as the repo's e2e suite.

**Pre-existing, outside Mock Interview:** at a 390 px viewport the dashboard shell (always-visible sidebar, page
header) already scrolls horizontally: about 489 px of content on the text room and setup form too. Not changed here.

### Phase 6.4 remaining scope
- Real-device checks (not done): camera and browser speech recognition together on Chrome/Edge desktop, Android
  Chrome (camera + dictation concurrency, background/screen lock) and iOS Safari; camera unplug and permission
  revocation on real hardware; the reload fallback on Firefox/Safari, which have no Permissions-Policy API.
- Optional clean-up: `VideoInterviewRoom` repeats the answer/next/finish/resync logic of `InterviewRoom` (kept
  separate so the text/voice room is unchanged); a shared hook could remove the duplication after real-device
  results.
- 6.6 as planned: responsive and accessibility review, full regression, real-device checklist.
