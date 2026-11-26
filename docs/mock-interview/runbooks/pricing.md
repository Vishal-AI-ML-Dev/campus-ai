# 6. Pricing configuration (decision P7-D9)

## Current status: **pricing not configured**

No approved model price table has been supplied, so the system records tokens but estimates **no cost**.
`ai_usage_events.cost_micros` is NULL, the admin usage API reports `cost.status = "pricing_not_configured"`,
and the Interview Ops page shows **"Not configured"** — never 0. **Required input:** an approved price table
(below) from the person responsible for AI spend. Prices must not be guessed or copied from public lists
without that approval.

## Format (`AI_PRICE_TABLE`, backend env, JSON)

```json
{
  "version": "<your label, e.g. the approval date>",
  "currency": "<ISO 4217 code>",
  "unit": "per_1m_tokens",
  "models": {
    "<provider>/<model id>": { "input": "<price>", "output": "<price>" }
  }
}
```

- `<provider>/<model id>` must match the worker's `LLM_PROVIDER` and model exactly as recorded in the ledger
  (Ops page → "By provider / model"), case-insensitive, e.g. `groq/<GROQ_MODEL value>`.
- Prices are currency units per **1,000,000 tokens**, input and output separately, decimal strings or numbers ≥ 0.
- One table = one `version`. An invalid table is ignored entirely (cost "not configured") and logged once,
  without its values.

## Semantics

- Cost is computed **when the ledger row is written** and stored with `price_version` and `cost_currency`; a
  later table never re-prices history. Raw tokens are kept, so history can be re-costed offline if needed.
- `cost_micros = input_tokens × input_price + output_tokens × output_price` (micro-units of the currency,
  exact decimal arithmetic, rounded half-up).
- No cost when: no table (`pricing_not_configured`), model not in the table (`model_not_priced`), or tokens
  unknown (`tokens_unknown`) — including a request where only some of its LLM calls reported usage
  (`tokens_unreported > 0`; the stored token sums are then a lower bound, not a basis for cost). The usage summary reports `partial` when some calls in the period are unpriced and
  `mixed_currency` when tables with different currencies were used.
- No currency conversion is ever done.
- Costs are **estimates** for operations, not billing.

## Changing prices

1. Get the new table approved. 2. Give it a new `version`. 3. Set `AI_PRICE_TABLE` and restart the backend.
Rows written before the change keep their old cost and version.
