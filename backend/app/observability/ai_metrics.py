"""Backend Prometheus metrics for AI calls (Mock Interview Phase 7.3 / 7.5).

A new module, so the CI-synced ``metrics.py`` stays byte-identical with the
worker copy. Same optional-dependency guard: a no-op without prometheus_client.

Labels are low-cardinality and content-free (``step``, ``outcome``,
``result``, ``state``). No tenant, user, session or request ids - per-institute
numbers live only in the RLS-protected ``ai_usage_events`` ledger.
"""

from __future__ import annotations

try:  # prometheus_client is declared in pyproject; guard so tests still import.
    from prometheus_client import Counter, Gauge, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - import guard
    _ENABLED = False

if _ENABLED:
    WORKER_CALLS = Counter(
        "mock_interview_worker_calls_total",
        "Backend -> AI worker interview calls by outcome.",
        ["step", "outcome"],
    )
    WORKER_SECONDS = Histogram(
        "mock_interview_worker_call_seconds",
        "Round trip of backend -> AI worker interview calls.",
        ["step"],
        buckets=(0.05, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 60, 120),
    )
    LEDGER_WRITES = Counter(
        "ai_usage_ledger_writes_total",
        "AI usage ledger writes (ok | duplicate | queued | lost | skipped).",
        ["result"],
    )
    CIRCUIT_STATE = Gauge(
        "ai_circuit_state",
        "AI circuit breaker state per step in this process (0 closed, 1 half-open, 2 open).",
        ["step"],
    )
    QUOTA_BLOCKS = Counter(
        "mock_interview_quota_blocks_total",
        "New interviews refused by an institute control or quota.",
        ["reason"],
    )
    # Phase 10.3: AI governance (every AI feature). Labels are fixed names.
    GOVERNANCE_REFUSALS = Counter(
        "ai_governance_refusals_total",
        "AI calls refused before reaching the worker (kill switch, caps, cooldown, busy).",
        ["feature", "reason"],
    )
    FEATURE_CALLS = Counter(
        "ai_feature_calls_total",
        "Governed backend -> AI worker calls for non-interview features by outcome.",
        ["feature", "outcome"],
    )

_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


def observe_call(step: str, outcome: str, seconds: float) -> None:
    if not _ENABLED:
        return
    try:
        WORKER_CALLS.labels(step, outcome).inc()
        if outcome != "circuit_open":
            WORKER_SECONDS.labels(step).observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001 - metrics must never break a request
        pass


def ledger_write(result: str) -> None:
    if not _ENABLED:
        return
    try:
        LEDGER_WRITES.labels(result).inc()
    except Exception:  # noqa: BLE001
        pass


def circuit_state(step: str, state: str) -> None:
    if not _ENABLED:
        return
    try:
        CIRCUIT_STATE.labels(step).set(_STATE_VALUE.get(state, 0))
    except Exception:  # noqa: BLE001
        pass


def quota_block(reason: str) -> None:
    if not _ENABLED:
        return
    try:
        QUOTA_BLOCKS.labels(reason).inc()
    except Exception:  # noqa: BLE001
        pass


def governance_refusal(feature: str, reason: str) -> None:
    if not _ENABLED:
        return
    try:
        GOVERNANCE_REFUSALS.labels(feature, reason).inc()
    except Exception:  # noqa: BLE001
        pass


def feature_call(feature: str, outcome: str) -> None:
    if not _ENABLED:
        return
    try:
        FEATURE_CALLS.labels(feature, outcome).inc()
    except Exception:  # noqa: BLE001
        pass
