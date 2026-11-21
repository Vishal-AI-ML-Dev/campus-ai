"""Phase 7.1 (R2): Sentry's AI integrations never see a mock interview.

sentry-sdk 2.63.0 auto-enables integrations for installed libraries. In the
worker that includes LangChain, LangGraph and Hugging Face Hub (and Google
GenAI once LangChain is off). The LangChain integration wraps LangChain's
callback configuration, so it would observe every interview LLM call.
``observability.sentry_options`` disables them with the SDK's
``disabled_integrations`` option.

Each scenario runs in a fresh subprocess that imports the REAL ``main.py``
with ``SENTRY_DSN`` set, so the real ``init_sentry`` runs at import with the
SDK's default automatic integration discovery. Only the transport is swapped
for an in-memory one (nothing leaves the machine) and the trace sample rate is
forced to 1.0 (worst case: every span is kept). The probe then drives the real
interview routes and LangGraph graphs with a real LangChain chat model whose
prompts and responses carry sentinel text, plus one deliberately failing
route for the "error reporting still works" check.

Every Sentry output channel (errors, transactions, sessions, client reports)
goes through ``Transport.capture_envelope``, so the captured envelopes are
everything Sentry would upload. ``debug``, ``enable_logs``, profiling and
Spotlight are off in our options (asserted below).

The control scenario runs the same probe with the AI integrations left on and
must show Sentry attached to the interview's LangChain calls, proving the
harness detects attachment.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]

RESUME = "RESUME-SENTINEL-7f3a quantum basket weaving at Initech"
JD = "JD-SENTINEL-91bc we need a senior unicorn wrangler"
ANSWER = "ANSWER-SENTINEL-c0de I optimised the flux capacitor by 88 percent"
RESPONSE = "RESPONSE-SENTINEL-e1e1 describe your flux capacitor work"
PROVIDER_ERR = "PROVIDERERR-SENTINEL-3b3b"
ROUTE_ERR = "ROUTEERR-SENTINEL-9a9a"
SENTINELS = {"resume": RESUME, "jd": JD, "answer": ANSWER, "response": RESPONSE,
             "provider_error": PROVIDER_ERR, "route_error": ROUTE_ERR}
AI_IDS = {"langchain", "langgraph", "google_genai", "huggingface_hub", "openai", "anthropic"}

_PROBE = textwrap.dedent(
    r"""
    import base64, json, sys
    MODE, S = sys.argv[1], json.loads(sys.argv[2])

    import sentry_sdk
    from sentry_sdk.transport import Transport

    captured = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            captured.append(envelope.serialize())
        def flush(self, timeout, callback=None):
            return None
        def kill(self):
            return None

    real_init = sentry_sdk.init
    seen = {}
    def patched_init(**kw):
        seen.update(kw)
        kw["transport"] = MemoryTransport
        kw["traces_sample_rate"] = 1.0
        return real_init(**kw)
    sentry_sdk.init = patched_init

    if MODE == "control":
        import observability
        observability.disabled_ai_integrations = lambda: []

    import main  # real module: setup_logging() + init_sentry(settings.SENTRY_DSN, ...)
    sentry_sdk.init = real_init

    client = sentry_sdk.get_client()
    from langchain_core.callbacks import manager as lc_manager
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.runnables import RunnableLambda
    from fastapi.testclient import TestClient

    import interview.llm_io as llm_io
    from interview.schemas import LLMEvaluation, LLMQuestion

    handlers_seen = []

    class SpyChatModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "spy"
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            hs = list(run_manager.handlers) if run_manager is not None else []
            handlers_seen.append(sorted({type(h).__module__ + "." + type(h).__name__ for h in hs}))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="not json " + S["response"]))])

    model = SpyChatModel()
    script = {}

    def structured(schema):
        def call(messages):
            model.invoke(messages)  # real LangChain run with the interview's prompt
            item = script.get(schema.__name__)
            if item is None:
                raise RuntimeError("provider down " + S["provider_error"])
            return item
        return RunnableLambda(call)

    llm_io.get_structured_model = structured
    llm_io.get_chat_model = lambda: model

    @main.app.get("/__probe_error")
    def probe_error():
        raise ValueError("route failed " + S["route_error"])

    cfg = {"interview_type": "technical", "difficulty": "medium", "duration_minutes": 15,
           "target_role": "Developer", "language": "en"}
    plan = {"topics": [{"name": "Introduction", "focus": "intro"},
                       {"name": "Flux", "focus": "capacitors"}], "question_budget": 6}
    statuses = {}
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.post("/interview/plan", headers={"X-Request-ID": "sentry-plan"}, json={
            "config": cfg, "resume_text": S["resume"], "job_description": S["jd"]})
        statuses["plan"] = r.status_code
        script["LLMQuestion"] = LLMQuestion(preamble="", question=S["response"])
        r = c.post("/interview/turn", headers={"X-Request-ID": "sentry-open"}, json={
            "config": cfg, "plan": plan, "resume_text": S["resume"],
            "job_description": S["jd"], "transcript": [], "remaining_seconds": 600})
        statuses["turn_open"] = r.status_code
        statuses["turn_open_has_response"] = S["response"] in r.text
        script["LLMEvaluation"] = LLMEvaluation(
            coverage="partial", depth=2, evidence="", follow_up_recommended=True,
            follow_up_angle="more detail", difficulty_signal=0)
        r = c.post("/interview/turn", headers={"X-Request-ID": "sentry-answer"}, json={
            "config": cfg, "plan": plan, "resume_text": S["resume"], "job_description": S["jd"],
            "transcript": [{"seq": 1, "topic": "Introduction", "question": "Introduce yourself.",
                            "answer": S["answer"]}], "remaining_seconds": 600})
        statuses["turn_answer"] = r.status_code
        script.clear()  # every LLM call now fails with a sentinel-bearing error
        r = c.post("/interview/turn", headers={"X-Request-ID": "sentry-fail"}, json={
            "config": cfg, "plan": plan, "resume_text": S["resume"],
            "job_description": S["jd"], "transcript": [], "remaining_seconds": 600})
        statuses["turn_fail"] = r.status_code
        r = c.get("/__probe_error", headers={"X-Request-ID": "sentry-route-err"})
        statuses["route_error"] = r.status_code
    sentry_sdk.flush(5)

    print("PROBE_RESULT " + json.dumps({
        "integrations": sorted(client.integrations),
        "transport": type(client.transport).__name__,
        "options": {k: client.options.get(k) for k in (
            "auto_enabling_integrations", "default_integrations", "send_default_pii",
            "include_local_variables", "max_request_body_size", "debug", "enable_logs",
            "profiles_sample_rate", "profile_session_sample_rate", "spotlight")},
        "disabled": sorted(getattr(c, "identifier", "?") for c in seen.get("disabled_integrations") or []),
        # Sentry wraps with functools.wraps (keeps __module__); its code object
        # still lives in sentry_sdk/integrations/langchain.py.
        "configure_code_file": lc_manager._configure.__code__.co_filename.replace("\\", "/"),
        "handlers_seen": handlers_seen,
        "statuses": statuses,
        "envelopes": [base64.b64encode(b).decode() for b in captured],
    }), flush=True)
    """
)


def _run(mode: str) -> dict:
    env = {
        **os.environ,
        "ENVIRONMENT": "test",
        "SENTRY_DSN": "https://publickey@o0.ingest.invalid/1",
        "MOCK_INTERVIEW_ENABLED": "true",
        "AI_WORKER_TOKEN": "",
        "LLM_PROVIDER": "groq",
        "GROQ_API_KEY": "",
        "GOOGLE_API_KEY": "",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "LANGSMITH_TRACING": "",
        "LANGCHAIN_TRACING_V2": "",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, mode, json.dumps(SENTINELS)],
        cwd=WORKER, env=env, capture_output=True, text=True, timeout=240,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_RESULT ")), None)
    assert line, f"probe failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    out = json.loads(line[len("PROBE_RESULT "):])
    out["raw"] = [base64.b64decode(e) for e in out["envelopes"]]
    out["wire"] = b"\n".join(out["raw"]).decode("utf-8", "replace")
    out["items"] = []
    for raw in out["raw"]:
        for ln in raw.splitlines():
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict) and ("event_id" in obj or "spans" in obj or "exception" in obj):
                out["items"].append(obj)
    return out


_CACHE: dict = {}


def _probe(mode: str = "fixed") -> dict:
    if mode not in _CACHE:
        _CACHE[mode] = _run(mode)
    return _CACHE[mode]


def _spans(out):
    return [s for i in out["items"] if i.get("type") == "transaction" for s in i.get("spans", [])]


# --- Fixed configuration --------------------------------------------------------
def test_real_init_with_automatic_discovery_and_memory_transport():
    out = _probe()
    assert out["transport"] == "MemoryTransport"
    o = out["options"]
    assert o["auto_enabling_integrations"] is True and o["default_integrations"] is True
    assert o["send_default_pii"] is False and o["include_local_variables"] is False
    assert o["max_request_body_size"] == "never"
    # No side channels besides the transport.
    assert not o["debug"] and not o["enable_logs"] and not o["spotlight"]
    assert not o["profiles_sample_rate"] and not o["profile_session_sample_rate"]


def test_ai_integrations_are_disabled_and_not_installed():
    out = _probe()
    assert {"langchain", "langgraph", "google_genai", "huggingface_hub"} <= set(out["disabled"])
    assert not (AI_IDS & set(out["integrations"])), out["integrations"]
    # Ordinary integrations stay on.
    assert {"starlette", "fastapi", "logging", "httpx"} <= set(out["integrations"])


def test_langchain_is_not_wrapped_and_no_sentry_handler_on_interview_calls():
    out = _probe()
    assert "sentry_sdk" not in out["configure_code_file"], out["configure_code_file"]
    assert out["handlers_seen"], "the interview never reached the LLM"
    assert all(h == [] for h in out["handlers_seen"]), out["handlers_seen"]


def test_interview_routes_ran_the_real_engine():
    st = _probe()["statuses"]
    assert st["plan"] == 200
    assert st["turn_open"] == 200 and st["turn_open_has_response"] is True
    assert st["turn_answer"] == 200
    assert st["turn_fail"] == 502
    assert st["route_error"] == 500


def test_no_interview_sentinel_anywhere_in_the_sentry_payload():
    out = _probe()
    assert out["raw"], "nothing was captured"
    for name, value in SENTINELS.items():
        assert value not in out["wire"], f"{name} sentinel reached Sentry"
        assert value.split()[0] not in out["wire"], f"{name} sentinel token reached Sentry"


def test_no_ai_spans_in_transactions():
    out = _probe()
    txs = [i for i in out["items"] if i.get("type") == "transaction"]
    names = {t.get("transaction") for t in txs}
    assert {"/interview/plan", "/interview/turn"} <= names, names
    ops = {s.get("op") or "" for s in _spans(out)}
    assert not any(op.startswith(("gen_ai", "ai.")) for op in ops), ops


def test_safe_error_reporting_still_works():
    out = _probe()
    errors = [i for i in out["items"] if "exception" in i]
    route = [e for e in errors if (e.get("request") or {}).get("url", "").endswith("/__probe_error")]
    assert route, "the unhandled route error was not reported"
    ev = route[0]
    exc = ev["exception"]["values"][-1]
    assert exc["value"] == "[redacted]"
    assert any(f.get("function") == "probe_error" for f in exc["stacktrace"]["frames"])
    assert ev["request"]["method"] == "GET" and ev["request"]["url"].endswith("/__probe_error")
    assert ev["tags"]["request_id"] == "sentry-route-err"
    # The interview's own 502 is reported too: exception types and the stack
    # survive for diagnosis, every message is redacted.
    failed = [e for e in errors if e.get("tags", {}).get("request_id") == "sentry-fail"]
    assert failed, "the interview 502 was not reported"
    chain = [v["type"] for v in failed[0]["exception"]["values"]]
    assert chain[-2:] == ["QuestionGenerationFailed", "HTTPException"], chain
    assert "LLMUnavailable" in chain
    assert all(v["value"] == "[redacted]" for v in failed[0]["exception"]["values"])
    assert failed[0]["request"]["url"].endswith("/interview/turn")


# --- Control: the harness detects Sentry attachment when not disabled -----------
def test_control_without_the_fix_sentry_attaches_to_interview_calls():
    out = _probe("control")
    assert {"langchain", "langgraph"} <= set(out["integrations"])
    assert "sentry_sdk/integrations/langchain" in out["configure_code_file"]
    assert any(any("sentry_sdk" in h for h in hs) for hs in out["handlers_seen"])
    ops = {s.get("op") or "" for s in _spans(out)}
    assert any(op.startswith("gen_ai") for op in ops), ops


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASSED {name}")
