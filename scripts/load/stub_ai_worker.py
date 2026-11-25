"""Stub AI worker for load tests and operational drills (Phase 10.8 / 10.9).

Speaks the AI worker's HTTP contract for the governed paths (mentor chat +
stream, resume draft, ATS, proof scoring, interview plan/turn/report) WITHOUT
any LLM: replies are fixed synthetic text after a configurable delay. No
provider is contacted, so it costs nothing (D4).

    python scripts/load/stub_ai_worker.py --port 8199 [--token <worker token>]

Behaviour is switched at runtime (loopback only) for drills:

    POST /__mode {"mode": "ok"|"quota"|"error"|"hang", "latency": 2.5}
      ok     200 after `latency` seconds (default 1.0, +/-20% jitter)
      quota  429 {"detail": "AI provider quota exhausted"} (provider quota drill)
      error  502 {"detail": "LLM call failed"}             (provider outage drill)
      hang   sleeps 300 s (timeout drill)
    GET /__stats  -> call counts per path and mode (no content)

Stopping the process is the "worker down" drill. Binds to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import hmac
import json
import random
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"mode": "ok", "latency": 1.0}
STATS: Counter = Counter()
LOCK = threading.Lock()
TOKEN = ""
USAGE = {"provider": "stub", "model": "stub-0", "llm_calls": 1, "input_tokens": 400,
         "output_tokens": 120, "tokens_reported": 1, "tokens_unreported": 0}

REPLIES = {
    "/mentor/chat": {"answer": "Synthetic mentor answer.", "provider": "stub", "usage": USAGE},
    "/resume/draft": {"markdown": "# Synthetic resume", "provider": "stub", "usage": USAGE},
    "/resume/ats-score": {"score": 61, "verdict": "synthetic", "matched_keywords": [],
                          "missing_keywords": [], "suggestions": [], "provider": "stub", "usage": USAGE},
    "/score/proof": {"score": 70, "verdict": "synthetic", "reasoning": "synthetic", "usage": USAGE},
    "/interview/plan": {"plan": {"topics": ["synthetic"]}, "provider": "stub", "usage": USAGE},
    "/interview/turn": {"action": "next_topic", "question": "Synthetic question?", "evaluation": None,
                        "provider": "stub", "usage": USAGE},
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def _send(self, status: int, body: dict | None = None, *, sse: bytes | None = None) -> None:
        data = sse if sse is not None else json.dumps(body or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream" if sse is not None else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        if not TOKEN:
            return True
        return hmac.compare_digest(self.headers.get("X-Worker-Token", ""), TOKEN)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"status": "healthy"})
        if self.path == "/__stats":
            with LOCK:
                return self._send(200, {"mode": STATE["mode"], "calls": dict(STATS)})
        self._send(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/__mode":
            req = json.loads(raw or b"{}")
            with LOCK:
                STATE["mode"] = req.get("mode", STATE["mode"])
                STATE["latency"] = float(req.get("latency", STATE["latency"]))
            return self._send(200, dict(STATE))
        if not self._authorised():
            return self._send(401, {"detail": "invalid worker token"})
        with LOCK:
            mode, latency = STATE["mode"], STATE["latency"]
            STATS[f"{self.path} {mode}"] += 1
        if mode == "quota":
            return self._send(429, {"detail": "AI provider quota exhausted"})
        if mode == "error":
            return self._send(502, {"detail": "LLM call failed"})
        time.sleep(300 if mode == "hang" else max(0.0, latency * random.uniform(0.8, 1.2)))
        if self.path == "/mentor/chat/stream":
            frames = [{"type": "token", "content": "Synthetic "}, {"type": "token", "content": "answer."},
                      {"type": "done", "provider": "stub"}]
            return self._send(200, sse="".join(f"data: {json.dumps(f)}\n\n" for f in frames).encode())
        reply = REPLIES.get(self.path)
        if reply is None:
            return self._send(404, {"detail": "not stubbed"})
        self._send(200, reply)


def main() -> None:
    global TOKEN
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8199)
    p.add_argument("--token", default="")
    a = p.parse_args()
    TOKEN = a.token
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.daemon_threads = True
    print(f"stub ai worker on 127.0.0.1:{a.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
