"""AI cost estimation from an APPROVED price table only (Mock Interview Phase 7.4).

Decision P7-D9: model prices are never invented. Without ``AI_PRICE_TABLE`` the
cost of every call is ``None`` and the status is ``pricing_not_configured``.
Missing pricing is never treated as zero.

``AI_PRICE_TABLE`` (JSON; see docs/mock-interview/runbooks/pricing.md)::

    {
      "version": "<label you choose, e.g. the approval date>",
      "currency": "<ISO 4217 code, e.g. USD>",
      "unit": "per_1m_tokens",
      "models": {
        "<provider>/<model id>": {"input": "<price>", "output": "<price>"}
      }
    }

Prices are currency units per 1,000,000 tokens, input and output separately,
as decimal strings or numbers (>= 0). One table = one version; the version and
currency are stored on every priced ledger row, and costs are computed at write
time, so a later table never re-prices history. Currency conversion is never
done.

Cost is in micro-units of the table currency (1e-6): for prices per 1M tokens,
``micros = input_tokens * input_price + output_tokens * output_price``, rounded
half-up to an integer - exact and reproducible (Decimal arithmetic).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.config import settings

logger = logging.getLogger("campus_ai.ai_pricing")

STATUS_ESTIMATED = "estimated"
STATUS_NOT_CONFIGURED = "pricing_not_configured"
STATUS_MODEL_NOT_PRICED = "model_not_priced"
STATUS_TOKENS_UNKNOWN = "tokens_unknown"

_UNIT = "per_1m_tokens"
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_VERSION = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")


@dataclass(frozen=True)
class PriceTable:
    version: str
    currency: str
    models: dict[str, tuple[Decimal, Decimal]]  # "provider/model" -> (input, output)


@dataclass(frozen=True)
class CostEstimate:
    status: str
    cost_micros: int | None = None
    currency: str | None = None
    price_version: str | None = None


def _price(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("price must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("price must be a number") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("price must be a finite number >= 0")
    return number


def parse_price_table(raw: str) -> PriceTable | None:
    """Parse and validate a table. ``None`` for empty input.

    Raises ValueError for an invalid table (the caller treats it as "not
    configured"); no value from the table is put into the error message.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError("AI_PRICE_TABLE is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("AI_PRICE_TABLE must be a JSON object")
    version, currency = data.get("version"), data.get("currency")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("AI_PRICE_TABLE.version must be 1-40 chars [A-Za-z0-9._:-]")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise ValueError("AI_PRICE_TABLE.currency must be a 3-letter ISO 4217 code")
    if data.get("unit") != _UNIT:
        raise ValueError(f"AI_PRICE_TABLE.unit must be {_UNIT!r}")
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("AI_PRICE_TABLE.models must be a non-empty object")
    parsed: dict[str, tuple[Decimal, Decimal]] = {}
    for key, prices in models.items():
        if not isinstance(key, str) or key.count("/") < 1 or len(key) > 130:
            raise ValueError("AI_PRICE_TABLE.models keys must be '<provider>/<model>'")
        if not isinstance(prices, dict) or set(prices) != {"input", "output"}:
            raise ValueError("each model needs exactly 'input' and 'output' prices")
        parsed[key.strip().lower()] = (_price(prices["input"]), _price(prices["output"]))
    return PriceTable(version=version, currency=currency, models=parsed)


_cache: tuple[str, PriceTable | None] | None = None


def active_table() -> PriceTable | None:
    """The configured table, or None (empty or invalid). Re-parsed only when
    the setting changes; an invalid table is logged once, without values."""
    global _cache
    raw = settings.AI_PRICE_TABLE or ""
    if _cache is not None and _cache[0] == raw:
        return _cache[1]
    try:
        table = parse_price_table(raw)
    except ValueError as exc:
        logger.warning("AI price table ignored (pricing not configured): %s", exc)
        table = None
    _cache = (raw, table)
    return table


def pricing_status() -> str:
    return STATUS_ESTIMATED if active_table() is not None else STATUS_NOT_CONFIGURED


def estimate(
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> CostEstimate:
    """Cost of one call, or a status that says why there is none."""
    table = active_table()
    if table is None:
        return CostEstimate(STATUS_NOT_CONFIGURED)
    key = f"{(provider or '').strip().lower()}/{(model or '').strip().lower()}"
    prices = table.models.get(key)
    if prices is None:
        return CostEstimate(STATUS_MODEL_NOT_PRICED, price_version=table.version)
    if input_tokens is None or output_tokens is None:
        return CostEstimate(STATUS_TOKENS_UNKNOWN, price_version=table.version)
    micros = Decimal(input_tokens) * prices[0] + Decimal(output_tokens) * prices[1]
    return CostEstimate(
        STATUS_ESTIMATED,
        cost_micros=int(micros.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        currency=table.currency,
        price_version=table.version,
    )
