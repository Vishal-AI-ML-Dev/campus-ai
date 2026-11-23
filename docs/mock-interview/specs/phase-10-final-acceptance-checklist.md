# Phase 10 — Final acceptance checklist (skeleton)

Status: **skeleton created in Phase 7.10. Nothing below has been executed.** The comprehensive end-to-end
regression and all physical-device testing run once, as the Phase 10 final-acceptance milestone (testing
strategy approved 2026-11-20). Each row gets a date, an environment, the person who ran it and the evidence.

## A. Carried-over real-device checks (deferred, NOT passed)

| Ref | Item | Source | Status |
|---|---|---|---|
| A1 | Phase 5 voice: real microphones, Chrome/Edge desktop, Android Chrome, Hindi / Hinglish accuracy | `phase-5-voice-interview.md` §8 | pending |
| A2 | Phase 6 video real-device matrix RD-01 … RD-24 (incl. screen reader, keyboard only) | `phase-6-video-interview.md` §10.4 | pending |

## B. Full regression

| Ref | Item | Status |
|---|---|---|
| B1 | Full backend SQLite suite on the release commit | pending |
| B2 | `tests_pg_api` + `test_rls_postgres.py` + `test_mock_interview_rls_postgres.py` as NOBYPASSRLS `app_user` | pending |
| B3 | AI worker suite (interview + face filter; `test_face_qdrant_server.py` against a real test Qdrant) | pending |
| B4 | Frontend `tsc`, lint, unit tests, `next build` | pending |
| B5 | Live LLM end-to-end (HR + technical, text/voice/video, report) with a development key | pending |

## C. Phase 7 operations drills (staging, approved change required)

| Ref | Drill | Pass criteria | Status |
|---|---|---|---|
| C1 | Provider outage → breaker opens → recovery | Students get immediate 503; `ai_circuit_open` then `ai_circuit_closed` alerts; ledger `circuit_open` rows | pending |
| C2 | Worker down | `unreachable` outcomes, answers kept, recovery without data loss | pending |
| C3 | Institute pause / quota exhaustion mid-interview | New sessions blocked; running interview finishes and gets a report | pending |
| C4 | Metrics scrape with `METRICS_TOKEN` (backend + worker) | 200 with token, 401 without, no content labels | pending |
| C5 | Alert webhook delivery and throttling | One message per window; webhook outage does not affect students | pending |
| C6 | Kill switch (`AI_MOCK_INTERVIEW_ENABLED=false`) and back | Routes 404 / return; data intact | pending |
| C7 | Retention script dry run on a staging copy | Counts plausible; nothing deleted without `--apply --expected-count` | pending |
| C8 | Load test with several backend processes + Redis | Ledger complete (no `lost`), per-process breaker behaviour as documented | pending |
| C9 | Pricing (only once an approved table exists) | Estimated cost matches a hand calculation for sample rows | blocked: no approved price table |

## D. Security and privacy review

| Ref | Item | Status |
|---|---|---|
| D1 | Sentinel scan of a live Sentry project, Langfuse project, logs and metrics after C1–C6 | pending |
| D2 | Dependency review (Next.js patch decision from Phase 6 §12.4, sentry-sdk AI-integration list on upgrade) | pending |
| D3 | Platform-operator role design (cross-institute ops view, P7-D2) | Phase 10 scope |

## E. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Product owner | | | |
| Security / privacy | | | |
| Operations | | | |
