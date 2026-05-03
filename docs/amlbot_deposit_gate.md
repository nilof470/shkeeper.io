# AMLBot Deposit Gate

This integration is AMLBot-only. SHKeeper does not integrate AML from re:Fee for deposit decisions.

## Ownership

SHKeeper accepts deposits, applies local AML policy, calls `aml-shkeeper` for AMLBot checks, stores the AML snapshot, and sends callbacks to `grither-pay`.

`grither-pay` owns balance crediting and manual review. SHKeeper never credits `grither-pay` balances.

## Static Addresses

Static address mode can reuse a large invoice per user and coin. Invoice fields such as `paid`, `status`, `balance_fiat`, and `balance_crypto` are invoice accounting fields, not credit decisions.

`grither-pay` credits only the trigger transaction where `transactions[].trigger == true` and `deposit_decision="credit"`. Any other `deposit_decision` goes to manual review in `grither-pay`.

## Decisions

Canonical `deposit_decision` values:

- `credit`
- `manual_review`

Canonical `decision_reason` values:

- `score_below_threshold`
- `amount_below_aml_threshold`
- `risk_score_above_threshold`
- `aml_pending_timeout`
- `aml_provider_error`
- `unsupported_asset`
- `incomplete_aml_result`
- `limited_analysis_requires_review`
- `cumulative_threshold_exceeded`

## Default Limits

- `AML_MIN_CHECK_AMOUNT_FIAT=100`
- `AML_SKIP_CUMULATIVE_LIMIT_FIAT=300`
- `AML_SKIP_CUMULATIVE_WINDOW=24h`
- `AML_MAX_ACCEPT_SCORE=0.10`

Sweep is separate operational logic. Recommended starting value: `SWEEP_MIN_AMOUNT_FIAT=300`.

KYT/address monitoring is deferred until AMLBot confirms non-per-transaction pricing.

## aml-shkeeper Contract

SHKeeper calls `aml-shkeeper`, not AMLBot directly.

- `POST /api/v1/checks`
- `GET /api/v1/checks/<deposit_id>`
- Basic Auth with `AML_SHKEEPER_USERNAME` and `AML_SHKEEPER_PASSWORD`
- Create is idempotent by `deposit_id` and `idempotency_key`

Required create fields:

```json
{
  "deposit_id": "shkeeper-tx-123",
  "idempotency_key": "BTC:txid:shkeeper-tx-123",
  "crypto": "BTC",
  "txid": "txid",
  "address": "bc1q...",
  "amount_crypto": "0.25",
  "asset": "BTC",
  "network": "BTC",
  "direction": "deposit",
  "threshold": "0.10"
}
```

## Approved Trigger Example

```json
{
  "status": "PARTIAL",
  "paid": false,
  "transactions": [
    {
      "txid": "txid",
      "trigger": true,
      "deposit_id": "shkeeper-tx-123",
      "idempotency_key": "BTC:txid:shkeeper-tx-123",
      "deposit_decision": "credit",
      "decision_reason": "score_below_threshold",
      "aml": {
        "provider": "amlbot",
        "provider_status": "success",
        "status": "approved",
        "score": "0.04",
        "threshold": "0.10",
        "uid": "amlbot-check-id",
        "asset": "BTC",
        "network": "BTC",
        "signals": {}
      }
    }
  ]
}
```

## Manual Review Trigger Example

```json
{
  "transactions": [
    {
      "trigger": true,
      "deposit_decision": "manual_review",
      "decision_reason": "risk_score_above_threshold",
      "aml": {
        "provider": "amlbot",
        "provider_status": "success",
        "status": "manual_review",
        "score": "0.72",
        "threshold": "0.10",
        "signals": {
          "risky_exchange": 0.403
        }
      }
    }
  ]
}
```

## Skipped Trigger Example

```json
{
  "transactions": [
    {
      "trigger": true,
      "deposit_decision": "credit",
      "decision_reason": "amount_below_aml_threshold",
      "aml": {
        "provider": "amlbot",
        "provider_status": null,
        "status": "skipped",
        "score": null,
        "threshold": "0.10",
        "signals": {},
        "skip_reason": "amount_below_threshold",
        "min_check_amount_fiat": "100",
        "cumulative_window": "24h",
        "cumulative_amount_fiat": "50",
        "cumulative_limit_fiat": "300"
      }
    }
  ]
}
```
