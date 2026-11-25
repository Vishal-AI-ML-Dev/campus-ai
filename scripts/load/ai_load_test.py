"""Controlled load test: AI calls vs ordinary requests (Phase 10.8, D8 pilot).

    python scripts/load/ai_load_test.py --base http://127.0.0.1:8010 \
        --tokens-file tokens.txt --duration 60 --ai-users 3 --other-users 20

* ``--tokens-file``: one bearer token per line for SYNTHETIC students (mint
  them for a local/disposable backend only; never real accounts).
* AI users loop ``POST /v1/mentor/chat`` (a long AI request; each with a fresh
  Idempotency-Key); other users loop ``GET /v1/auth/me`` and
  ``GET /v1/mentor/conversations`` (ordinary, non-AI pages' API calls).
* Bounded by design: fixed user counts, fixed duration, one request in
  flight per user, ``--max-requests`` hard stop. Refuses non-loopback targets
  unless ``--allow-remote`` is given (remote load needs owner approval).

Prints one JSON summary: per class request count, status histogram, refusal
reasons, p50/p95/p99/max latency (ms), throughput and error rates. No
response content is recorded.
"""

from __future__ import annotations

import argparse
import itertools
import json
import threading
import time
import uuid
from collections import Counter, defaultdict
from urllib.parse import urlparse

import httpx

ORIGIN = "http://localhost:3000"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))], 1)


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, Counter] = defaultdict(Counter)
        self.reasons: dict[str, Counter] = defaultdict(Counter)
        self.total = 0

    def add(self, kind: str, status: int | str, ms: float, reason: str | None = None) -> None:
        with self.lock:
            self.total += 1
            self.lat[kind].append(ms)
            self.status[kind][str(status)] += 1
            if reason:
                self.reasons[kind][reason] += 1

    def summary(self, seconds: float) -> dict:
        out = {}
        for kind, lat in self.lat.items():
            st = self.status[kind]
            n = sum(st.values())
            server_errors = sum(v for k, v in st.items() if k.startswith("5") or not k.isdigit())
            refusals = sum(self.reasons[kind].values())
            out[kind] = {
                "requests": n, "throughput_rps": round(n / seconds, 2), "status": dict(st),
                "refusal_reasons": dict(self.reasons[kind]),
                "p50_ms": percentile(lat, 0.50), "p95_ms": percentile(lat, 0.95),
                "p99_ms": percentile(lat, 0.99), "max_ms": round(max(lat), 1) if lat else None,
                "error_rate_excl_governed_refusals": round((server_errors - refusals) / n, 4) if n else None,
            }
        return out


def run(base: str, tokens: list[str], duration: float, ai_users: int, other_users: int,
        max_requests: int, extra_headers: dict | None = None) -> dict:
    rec = Recorder()
    stop = time.monotonic() + duration
    token_cycle = itertools.cycle(tokens)

    def worker(kind: str, token: str) -> None:
        headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN, **(extra_headers or {})}
        paths = itertools.cycle(["/v1/auth/me", "/v1/mentor/conversations"])
        with httpx.Client(base_url=base, headers=headers, timeout=150.0) as client:
            while time.monotonic() < stop and rec.total < max_requests:
                started = time.perf_counter()
                reason = None
                try:
                    if kind == "ai":
                        r = client.post("/v1/mentor/chat", json={"question": "Synthetic load question?"},
                                        headers={"Idempotency-Key": uuid.uuid4().hex})
                        if r.status_code in (429, 503):
                            reason = (r.json().get("error") or {}).get("reason")
                    else:
                        r = client.get(next(paths))
                    status: int | str = r.status_code
                except httpx.HTTPError as exc:
                    status = type(exc).__name__
                rec.add(kind, status, (time.perf_counter() - started) * 1000, reason)

    threads = [threading.Thread(target=worker, args=("ai", next(token_cycle))) for _ in range(ai_users)]
    threads += [threading.Thread(target=worker, args=("other", next(token_cycle))) for _ in range(other_users)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    return {"duration_s": round(elapsed, 1), "ai_users": ai_users, "other_users": other_users,
            "classes": rec.summary(elapsed)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", required=True)
    p.add_argument("--tokens-file", required=True)
    p.add_argument("--duration", type=float, default=60)
    p.add_argument("--ai-users", type=int, default=3)
    p.add_argument("--other-users", type=int, default=20)
    p.add_argument("--max-requests", type=int, default=20000)
    p.add_argument("--allow-remote", action="store_true")
    p.add_argument("--gate-header-file", help="file holding the staging-gate value (never printed)")
    a = p.parse_args()
    host = urlparse(a.base).hostname
    if host not in ("127.0.0.1", "localhost", "::1") and not a.allow_remote:
        raise SystemExit("refusing a non-loopback target without --allow-remote (needs owner approval)")
    tokens = [t.strip() for t in open(a.tokens_file, encoding="utf-8") if t.strip()]
    extra = {}
    if a.gate_header_file:
        extra["X-Staging-Gate"] = open(a.gate_header_file, encoding="utf-8").read().strip()
    print(json.dumps(run(a.base, tokens, a.duration, a.ai_users, a.other_users, a.max_requests, extra),
                     sort_keys=True))


if __name__ == "__main__":
    main()
