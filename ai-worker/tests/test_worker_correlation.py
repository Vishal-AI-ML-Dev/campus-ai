"""Phase 7.1: request / invocation id correlation in the AI worker.

Runs the REAL worker app (main.py) in a fresh subprocess with the interview
engine enabled and NO LLM key, so every interview LLM call fails offline on
the real provider path and logs its real warning lines. The probe sends the
backend's correlation headers and captures the JSON log lines from stderr.

Proves: the worker logs request_id + invocation_id on the access log and on
engine log lines (including the report scorer's worker threads), echoes the
ids back, and ignores malformed / content-bearing ids. Offline, no keys.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
RID = "req-7f3a9c2b"
IID = "0123456789abcdef0123456789abcdef"
SENTINEL = "ANSWER-SENTINEL-c0de I optimised the flux capacitor"

_PROBE = textwrap.dedent(
    """
    import json, sys
    from fastapi.testclient import TestClient
    import main
    RID, IID, SENTINEL = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = {"interview_type": "hr", "difficulty": "medium", "duration_minutes": 15,
           "target_role": "Developer", "language": "en"}
    plan = {"topics": [{"name": "Introduction", "focus": "intro"},
                       {"name": "Teamwork", "focus": "team"}], "question_budget": 6}
    good = {"X-Request-ID": RID, "X-AI-Invocation-ID": IID}
    out = {}
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.post("/interview/turn", headers=good, json={
            "config": cfg, "plan": plan, "transcript": [], "remaining_seconds": 600})
        out["turn"] = [r.status_code, r.headers.get("x-request-id"),
                       r.headers.get("x-ai-invocation-id")]
        long_answer = ("In my final year project I led a team of four and we split "
                       "the work by module and delivered two weeks early " + SENTINEL)
        r = c.post("/interview/report", headers={"X-Request-ID": RID + "-r",
                                                 "X-AI-Invocation-ID": IID[::-1]}, json={
            "config": cfg, "transcript": [
                {"seq": 1, "topic": "Teamwork", "question": "Q1?", "answer": long_answer}]})
        out["report"] = [r.status_code, r.headers.get("x-ai-invocation-id")]
        bad = {"X-Request-ID": "x" * 65, "X-AI-Invocation-ID": SENTINEL}
        r = c.get("/health", headers=bad)
        out["bad"] = [r.headers.get("x-request-id"), r.headers.get("x-ai-invocation-id")]
        r = c.get("/health")
        out["none"] = [r.headers.get("x-request-id"), r.headers.get("x-ai-invocation-id")]
    print("PROBE_RESULT " + json.dumps(out), flush=True)
    """
)


def _run() -> tuple[dict, list[dict], str]:
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "MOCK_INTERVIEW_ENABLED": "true",
        "AI_WORKER_TOKEN": "",
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "SENTRY_DSN": "",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, RID, IID, SENTINEL],
        cwd=WORKER, env=env, capture_output=True, text=True, timeout=180,
    )
    result_line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_RESULT ")), None
    )
    assert result_line, f"probe failed:\n{proc.stdout}\n{proc.stderr[-3000:]}"
    logs = []
    for line in proc.stderr.splitlines():
        try:
            logs.append(json.loads(line))
        except ValueError:
            continue
    return json.loads(result_line[len("PROBE_RESULT "):]), logs, proc.stderr


_CACHE: dict = {}


def _probe():
    if not _CACHE:
        _CACHE["v"] = _run()
    return _CACHE["v"]


def test_turn_ids_are_echoed_and_logged_on_access_and_engine_lines():
    out, logs, _ = _probe()
    status, rid, iid = out["turn"]
    assert status == 502  # no LLM key: real provider path fails offline
    assert (rid, iid) == (RID, IID)
    turn_lines = [ln for ln in logs if ln.get("request_id") == RID]
    access = [ln for ln in turn_lines if ln.get("msg") == "request"]
    assert access and access[0]["path"] == "/interview/turn"
    assert all(ln.get("invocation_id") == IID for ln in turn_lines)
    engine = [ln for ln in turn_lines if ln.get("logger") == "campus_ai_worker.interview"]
    assert engine, "expected engine warning lines from the failed LLM calls"


def test_report_scorer_threads_keep_the_ids():
    out, logs, _ = _probe()
    assert out["report"] == [502, IID[::-1]]
    engine = [ln for ln in logs if ln.get("logger") == "campus_ai_worker.interview"
              and "report_score" in ln.get("msg", "")]
    assert engine, "expected report_score warnings from the scorer thread"
    assert all(ln.get("request_id") == RID + "-r" for ln in engine)
    assert all(ln.get("invocation_id") == IID[::-1] for ln in engine)


def test_malformed_or_content_bearing_ids_are_rejected():
    out, logs, stderr = _probe()
    rid, iid = out["bad"]
    assert rid and rid != "x" * 65 and len(rid) <= 64
    assert iid is None
    assert SENTINEL not in stderr  # never logged, not even the rejected header
    assert "x" * 65 not in stderr


def test_requests_without_ids_get_a_fresh_request_id_and_no_invocation_id():
    out, logs, _ = _probe()
    rid, iid = out["none"]
    assert rid and iid is None
    health = [ln for ln in logs if ln.get("request_id") == rid]
    assert health and all("invocation_id" not in ln for ln in health)


def test_invocation_middleware_wraps_the_logging_middleware():
    src = (WORKER / "main.py").read_text(encoding="utf-8")
    assert src.index("app.middleware(\"http\")(request_logging_middleware)") < src.index(
        "app.middleware(\"http\")(invocation_id_middleware)"
    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASSED {name}")
