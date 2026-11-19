# Phase 6 — AI Video Interview (handoff, NOT a design)

Status: **not started.** No Phase 6 code, schema, flag or configuration exists. This document records the constraints
and the execution sequence you set on 2026-11-19, plus facts about the current code, so the Phase 6 audit can start
from them. It is **not** an approved design: every item under "Open questions" needs an explicit decision.

Overall status: `../PROJECT_STATE.md` → "Status at a glance". Plan row: `../IMPLEMENTATION_PLAN.md` §6 (Phase 6).

## 1. Approved execution sequence

1. **Phase 5 final manual verification.** Real-device speech checks M1–M9 in `phase-5-voice-interview.md` §8, then
   any follow-up fixes (Phase 5 is committed locally as `34fd3a9`).
2. **Phase 6 architecture audit.** Read-only: inspect the existing code first (§3), then report options.
3. **Phase 6 privacy and security design.** The consent, recording and permissions decisions need your approval.
4. **Phase 6 incremental implementation.** Small, flag-gated steps.
5. **Phase 6 integration and regression testing.**

Phase 5's open items are tracked separately in `PROJECT_STATE.md` and are **not** absorbed into Phase 6.

## 2. Constraints (set by you; binding for any Phase 6 design)

- **Reuse** the existing interview engine, session management and report infrastructure where appropriate. A video
  answer should reach the backend the same way as a voice or typed answer: as text.
- **Inspect existing video-related code** before designing new components (§3).
- **Preserve** tenant isolation, Postgres RLS and student-only access (owner/tenant 404, staff 403, as in Phases 3–5).
- **Video is disabled by default.** It needs its own flag, defaulting OFF, behind the existing master flag, and the
  **tenant allow-list is required**.
- **Explicit camera permission:** the camera is requested only after a clear student action, never on page load.
- **Camera tracks must stop** at session completion and on component unmount (and on failure or navigation).
- **No recording and no video upload by default.**
- **Video privacy and consent decisions must be approved before implementation.**
- **Forbidden:** facial recognition, emotion detection, gaze tracking and biometric identity analysis.
- **No paid external video providers** without approval.
- Phase 5's outstanding work stays separately tracked.
- Project rules (from `CLAUDE.md`) still apply:
  - migrations are additive only;
  - no changes to existing API responses, tables, columns or env vars;
  - no pushes or deploys without approval.

## 3. Facts about the current code (verified read-only on 2026-11-19)

| Fact | Where |
|---|---|
| The data model already allows `mode = "video"` (CHECK constraint values) | `backend/app/models/mock_interview.py` (`MODE_VALUES = ("text", "voice", "video")`) |
| `/catalog` (Phase 1, static) lists `video` | `backend/app/routers/mock_interview.py` |
| `/options` offers only `text` and, with the voice flag, `voice`. Session creation accepts only `mode: "text" \| "voice"` (`Literal`); video is rejected with 422 today | `backend/app/routers/mock_interview.py`, `backend/app/schemas/mock_interview.py` |
| No live-camera code exists in the frontend (no `getUserMedia` / `mediaDevices` use) | `frontend/src` (searched) |
| The browser policy blocks the camera: `Permissions-Policy: camera=()`. Your local change sets `microphone=(self)` only | `frontend/next.config.ts` (uncommitted local edit) |
| **Existing biometric features, which must stay separate from interviews:** face enrolment and face attendance | `frontend/src/app/dashboard/{face,face-enroll,face-attendance}/`, `frontend/src/components/face/`, `backend/app/routers/face.py`, `backend/app/routers/attendance.py`, `ai-worker/face.py` |
| Voice (Phase 5) already provides browser speech-to-text and text-to-speech with an editable transcript | `frontend/src/components/mock-interview/{speechDictation,useSpeech}.ts`, `VoiceAnswerBox.tsx` |
| Reports are built from answer text only, and the disclaimer is modality-independent | `backend/app/services/mock_interview_report.py` |

## 4. Open questions for the audit and design (proposals, NOT approved)

These are things to decide, not decisions. None may be implemented until approved.

- **Scope of "video".** Options include a camera self-view only (the student sees themselves), questions read aloud
  as in voice mode, answers by speech-to-text or typing, live captions and a timer. Is any interviewer avatar
  wanted? If so, which kind, given that no paid providers are allowed without approval?
- **Answer capture.** Reuse the Phase 5 dictation path (text only) vs. anything else. Any audio or video leaving the
  browser would change the privacy architecture and needs separate approval.
- **Consent.** The wording and when it is shown, a per-session acknowledgement, and whether consent is recorded (and
  where).
- **Recording and retention.** Default is none. If ever wanted: storage location, access (owner/faculty), retention
  period and deletion. Each needs a decision (see `IMPLEMENTATION_PLAN.md` §7 "Recording retention").
- **Configuration.** The camera policy change in `next.config.ts` (`camera=(self)`) and its rollout. The flag name and
  its tenant allow-list semantics.
- **Schema/API.** Whether `mode = "video"` is enough (no migration expected, since the model already allows it), and
  which additive fields, if any, are needed.
- **Testing approach.** A fake `getUserMedia` / `MediaStream` in unit and browser tests, track-stop assertions, and a
  manual real-device checklist (as in Phase 5 §8).

## 5. Out of scope unless separately approved

- Facial recognition, emotion or sentiment detection, gaze or attention tracking, liveness or identity checks, and
  any use of the face-attendance models or Qdrant `student_faces` data.
- Server-side video or audio processing, cloud transcription, and paid video, avatar or STT/TTS providers.
- Recording, uploading or storing camera or microphone media.
- A natural or neural TTS upgrade (deferred from Phase 5).
