"""Prometheus metrics for interview LLM steps (Mock Interview Phase 7.2).

Low-cardinality, content-free labels only:
  * ``step``     - engine step name (jd, plan, question, evaluate, closing,
                   report_score, report_summary) - a fixed set from llm_io
  * ``provider`` / ``model`` - configured provider and model ids
  * ``outcome``  - ok | json_fallback | unusable | provider_error
No tenant, user, session or request ids and no text ever become labels; per
institute numbers come only from the backend's RLS-protected usage ledger.

Same optional-dependency guard as ``metrics.py``: without prometheus_client
every helper is a no-op. Exposed through the existing ``/metrics`` endpoint
(the default registry).
"""

from __future__ import annotations

try:  # prometheus_client is declared in pyproject; guard so tests still import.
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - import guard
    _ENABLED = False

if _ENABLED:
    LLM_CALLS = Counter(
        "mock_interview_llm_steps_total",
        "Interview LLM steps by outcome.",
        ["step", "provider", "model", "outcome"],
    )
    LLM_SECONDS = Histogram(
        "mock_interview_llm_step_seconds",
        "Wall time of interview LLM steps (incl. JSON fallback).",
        ["step", "provider"],
        buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
    )
    LLM_TOKENS = Counter(
        "mock_interview_llm_tokens_total",
        "Provider-reported tokens of interview LLM steps.",
        ["provider", "model", "direction"],
    )

OUTCOMES = frozenset({"ok", "json_fallback", "unusable", "provider_error"})


def _label(value: str | None) -> str:
    return (value or "unknown")[:80]


def observe_step(
    step: str,
    provider: str | None,
    model: str | None,
    outcome: str,
    seconds: float,
) -> None:
    """Record one interview LLM step. Never raises."""
    if not _ENABLED:
        return
    try:
        out = outcome if outcome in OUTCOMES else "unusable"
        LLM_CALLS.labels(_label(step), _label(provider), _label(model), out).inc()
        LLM_SECONDS.labels(_label(step), _label(provider)).observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001 - metrics must never break an interview
        pass


def observe_tokens(
    provider: str | None, model: str | None, input_tokens: int | None, output_tokens: int | None
) -> None:
    """Add provider-reported tokens (unknown counts are skipped, not zero)."""
    if not _ENABLED:
        return
    try:
        if input_tokens:
            LLM_TOKENS.labels(_label(provider), _label(model), "input").inc(input_tokens)
        if output_tokens:
            LLM_TOKENS.labels(_label(provider), _label(model), "output").inc(output_tokens)
    except Exception:  # noqa: BLE001
        pass
