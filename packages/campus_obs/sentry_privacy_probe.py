"""Sentry privacy probe (Mock Interview Phase 7.1) - test helper, not synced.

Run by ``backend/tests/test_sentry_privacy.py`` and
``ai-worker/tests/test_sentry_privacy.py`` in a SUBPROCESS (Sentry patches
Starlette globally), with each service's own venv and module layout:

    python sentry_privacy_probe.py backend|worker

It calls the service's REAL ``init_sentry`` with a fake DSN, swaps in an
in-memory transport (nothing is sent anywhere), drives a tiny app wired with
the service's real logging middleware and error handlers through an error that
is full of sentinel values, and prints the serialized envelopes (the exact
bytes Sentry would upload) as JSON on stdout.
"""

from __future__ import annotations

import base64
import json
import logging
import sys

from fastapi import FastAPI, Request

SENTINELS = {
    "resume": "RESUME-SENTINEL-7f3a quantum basket weaving at Initech",
    "jd": "JD-SENTINEL-91bc we need a senior unicorn wrangler",
    "answer": "ANSWER-SENTINEL-c0de I optimised the flux capacitor",
    "auth": "AUTH-SENTINEL-4d2e-bearer-token",
    "cookie": "COOKIE-SENTINEL-55aa",
    "worker_token": "WORKERTOKEN-SENTINEL-77bb",
    "query": "QUERY-SENTINEL-0f0f",
    "email": "student.sentinel@example.invalid",
}
REQUEST_ID = "probe-req-1234"


def main(layout: str) -> None:
    if layout == "backend":
        from app.observability import observability as obs
        from app.observability.errors import register_exception_handlers
    else:
        import observability as obs
        from errors import register_exception_handlers

    import sentry_sdk
    from fastapi.testclient import TestClient
    from sentry_sdk.transport import Transport

    captured: list[bytes] = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):  # noqa: D401 - SDK hook
            captured.append(envelope.serialize())

        def flush(self, timeout, callback=None):
            return None

        def kill(self):
            return None

    real_init = sentry_sdk.init
    seen_options: dict = {}

    def init_with_memory_transport(**kwargs):
        seen_options.update(kwargs)
        kwargs["transport"] = MemoryTransport
        kwargs["traces_sample_rate"] = 1.0  # make the transaction deterministic
        return real_init(**kwargs)

    sentry_sdk.init = init_with_memory_transport
    enabled = obs.init_sentry("https://publickey@o0.ingest.invalid/1", "test", release="probe@1")
    sentry_sdk.init = real_init

    app = FastAPI()
    app.middleware("http")(obs.request_logging_middleware)
    register_exception_handlers(app)
    log = logging.getLogger("campus_ai.probe")

    @app.post("/v1/mock-interview/sessions/{session_id}/answer")
    async def answer(session_id: int, request: Request):
        body = await request.json()
        resume_text = body["resume"]  # noqa: F841 - frame local on purpose
        job_description = body["jd"]  # noqa: F841
        candidate_answer = body["answer"]
        sentry_sdk.set_extra("resume", resume_text)
        sentry_sdk.set_context("candidate", {"answer": candidate_answer})
        sentry_sdk.set_user({"email": SENTINELS["email"], "id": "42"})
        log.info("processing answer %s", candidate_answer)  # -> breadcrumb
        log.error("evaluation failed for %s", candidate_answer)  # -> error event
        raise ValueError(f"cannot score answer: {candidate_answer}")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        f"/v1/mock-interview/sessions/42/answer?token={SENTINELS['query']}",
        json={"resume": SENTINELS["resume"], "jd": SENTINELS["jd"], "answer": SENTINELS["answer"]},
        headers={
            "Authorization": f"Bearer {SENTINELS['auth']}",
            "Cookie": f"session={SENTINELS['cookie']}",
            "X-Worker-Token": SENTINELS["worker_token"],
            "X-Request-ID": REQUEST_ID,
        },
    )
    sentry_sdk.flush(2)

    # Direct scrubber checks on hand-built payloads the app can't easily emit.
    synthetic_tx = {
        "type": "transaction",
        "request": {"url": f"http://w/x?q={SENTINELS['query']}", "data": SENTINELS["answer"]},
        "spans": [
            {"op": "http.client", "description": f"POST http://worker/interview/turn?k={SENTINELS['query']}",
             "data": {"http.method": "POST", "http.query": SENTINELS["query"],
                      "body": SENTINELS["resume"]}},
            {"op": "db", "description": "SELECT 1", "data": {"db.params": [SENTINELS["answer"]]}},
        ],
        "contexts": {"trace": {"trace_id": "t", "span_id": "s", "data": {"x": SENTINELS["jd"]}},
                     "candidate": {"a": SENTINELS["answer"]}},
        "breadcrumbs": {"values": [{"category": "httplib", "message": SENTINELS["answer"],
                                    "data": {"url": f"http://w/p?t={SENTINELS['auth']}",
                                             "method": "POST", "status_code": 200,
                                             "body": SENTINELS["resume"]}}]},
        "threads": {"values": [{"stacktrace": {"frames": [{"function": "f",
                                                            "vars": {"a": SENTINELS["answer"]}}]}}]},
    }
    scrubbed_tx = obs.scrub_event(synthetic_tx, {})
    crumb = obs.scrub_breadcrumb({"category": "log", "message": SENTINELS["answer"],
                                  "data": {"answer": SENTINELS["answer"]}, "level": "info"}, {})

    class Exploding(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    print("PROBE_RESULT " + json.dumps({
        "enabled": enabled,
        "status": resp.status_code,
        "response_request_id": resp.headers.get("x-request-id"),
        "options": {k: v for k, v in seen_options.items()
                    if k in ("send_default_pii", "include_local_variables",
                             "max_request_body_size", "traces_sample_rate")},
        "hooks": {k: getattr(seen_options.get(k), "__name__", None)
                  for k in ("before_send", "before_send_transaction", "before_breadcrumb")},
        "envelopes": [base64.b64encode(b).decode() for b in captured],
        "synthetic_tx": scrubbed_tx,
        "crumb": crumb,
        "scrub_failure_drops_event": obs.scrub_event(Exploding(request={}), {}) is None,
        "sentry_version": sentry_sdk.VERSION,
    }), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
