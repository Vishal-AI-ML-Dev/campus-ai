"""Phase 7.4: cost estimation from an approved price table only (decision P7-D9).

ALL PRICES IN THIS FILE ARE SYNTHETIC TEST FIXTURES, NOT REAL MODEL PRICES.
The model names below are made up for the same reason.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import ai_pricing, ai_usage

TEST_ONLY_TABLE = {
    "version": "test-fixture-1",
    "currency": "USD",
    "unit": "per_1m_tokens",
    "models": {
        # synthetic numbers, chosen so the arithmetic is easy to check by hand
        "fakeprov/fake-model-a": {"input": "2.50", "output": "10"},
        "fakeprov/fake-model-b": {"input": 0, "output": "0.333333"},
    },
}


@pytest.fixture
def table(monkeypatch):
    def set_table(value):
        monkeypatch.setattr(settings, "AI_PRICE_TABLE", value if isinstance(value, str) else json.dumps(value))

    yield set_table
    ai_pricing._cache = None


def test_no_table_means_pricing_not_configured_never_zero(table):
    table("")
    est = ai_pricing.estimate("fakeprov", "fake-model-a", 1000, 1000)
    assert est.status == ai_pricing.STATUS_NOT_CONFIGURED
    assert est.cost_micros is None and est.currency is None and est.price_version is None
    assert ai_pricing.pricing_status() == "pricing_not_configured"


def test_input_and_output_are_priced_separately_and_exactly(table):
    table(TEST_ONLY_TABLE)
    est = ai_pricing.estimate("FakeProv", "Fake-Model-A", 1_000, 200)
    # 1000 * 2.50 + 200 * 10 = 4500 micro-USD (= 0.0045 USD)
    assert (est.status, est.cost_micros, est.currency, est.price_version) == (
        "estimated", 4500, "USD", "test-fixture-1")


def test_rounding_is_half_up_and_reproducible(table):
    table(TEST_ONLY_TABLE)
    # 3 * 0.333333 = 0.999999 -> 1 ; 0 input price
    assert ai_pricing.estimate("fakeprov", "fake-model-b", 7, 3).cost_micros == 1
    assert ai_pricing.estimate("fakeprov", "fake-model-b", 7, 1).cost_micros == 0  # 0.333333 -> 0


def test_unknown_model_or_tokens_give_no_cost(table):
    table(TEST_ONLY_TABLE)
    assert ai_pricing.estimate("fakeprov", "other", 10, 10).status == "model_not_priced"
    unknown = ai_pricing.estimate("fakeprov", "fake-model-a", None, 10)
    assert unknown.status == "tokens_unknown" and unknown.cost_micros is None


@pytest.mark.parametrize(
    "bad",
    [
        "{not json",
        [],
        {**TEST_ONLY_TABLE, "currency": "usd"},
        {**TEST_ONLY_TABLE, "unit": "per_token"},
        {**TEST_ONLY_TABLE, "version": "has spaces"},
        {**TEST_ONLY_TABLE, "models": {}},
        {**TEST_ONLY_TABLE, "models": {"no-slash": {"input": 1, "output": 1}}},
        {**TEST_ONLY_TABLE, "models": {"p/m": {"input": -1, "output": 1}}},
        {**TEST_ONLY_TABLE, "models": {"p/m": {"input": "NaN", "output": 1}}},
        {**TEST_ONLY_TABLE, "models": {"p/m": {"input": True, "output": 1}}},
        {**TEST_ONLY_TABLE, "models": {"p/m": {"input": 1}}},
    ],
)
def test_invalid_tables_are_treated_as_not_configured(table, bad, caplog):
    table(bad)
    assert ai_pricing.active_table() is None
    assert ai_pricing.estimate("p", "m", 1, 1).status == "pricing_not_configured"
    # the warning never echoes table values
    assert "TEST" not in caplog.text and "0.333333" not in caplog.text


def test_ledger_event_records_cost_and_version_only_when_priced(table):
    ctx = ai_usage.UsageContext(tenant_id=1, user_id=None)
    usage = {"provider": "fakeprov", "model": "fake-model-a", "input_tokens": 1000, "output_tokens": 200}
    table("")
    e = ai_usage.build_event(ctx=ctx, event_id="c1", step="turn", outcome="ok", latency_ms=1, usage=usage)
    assert (e["cost_micros"], e["cost_currency"], e["price_version"]) == (None, None, None)
    table(TEST_ONLY_TABLE)
    e = ai_usage.build_event(ctx=ctx, event_id="c2", step="turn", outcome="ok", latency_ms=1, usage=usage)
    assert (e["cost_micros"], e["cost_currency"], e["price_version"]) == (4500, "USD", "test-fixture-1")
    # unknown tokens: never a fabricated cost
    e = ai_usage.build_event(ctx=ctx, event_id="c3", step="turn", outcome="ok", latency_ms=1,
                             usage={**usage, "input_tokens": None})
    assert e["cost_micros"] is None and e["price_version"] is None


def test_partially_reported_usage_gives_no_cost_but_keeps_raw_tokens(table):
    """Some calls in the request reported no usage: the sums are a lower bound, so no cost."""
    ctx = ai_usage.UsageContext(tenant_id=1, user_id=None)
    usage = {"provider": "fakeprov", "model": "fake-model-a", "input_tokens": 1000, "output_tokens": 200,
             "tokens_reported": 1, "tokens_unreported": 1}
    table(TEST_ONLY_TABLE)
    e = ai_usage.build_event(ctx=ctx, event_id="p1", step="report", outcome="ok", latency_ms=1, usage=usage)
    assert (e["cost_micros"], e["cost_currency"], e["price_version"]) == (None, None, None)
    assert (e["input_tokens"], e["output_tokens"], e["tokens_unreported"]) == (1000, 200, 1)
    # fully reported (tokens_unreported == 0) is still priced
    e = ai_usage.build_event(ctx=ctx, event_id="p2", step="report", outcome="ok", latency_ms=1,
                             usage={**usage, "tokens_unreported": 0})
    assert e["cost_micros"] == 4500


def test_repository_ships_no_price_table():
    """P7-D9: no approved table has been supplied, so none may be configured by default."""
    assert settings.model_fields["AI_PRICE_TABLE"].default == ""
