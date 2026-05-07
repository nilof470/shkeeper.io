---
status: in_progress
created_at: 2026-05-07
type: quick-spec
---

# AML Callback Enrichment Contract

## Goal

SHKeeper should send payment facts and AML enrichment facts in merchant callbacks.
Business decisions such as crediting, rejecting, or manual review belong to
grither-pay.

## Current Problem

Confirmed payment callbacks currently mix three concerns:

- Blockchain/payment facts: txid, amount, invoice status, fee, trigger.
- AML provider facts: provider, score, asset, network, signals, report URL.
- Business decisions: `deposit_decision`, `decision_reason`, AML `status`, and
  `threshold`.

This makes SHKeeper responsible for application policy and creates unwanted
callback states such as `manual_review` for assets not covered by Koinkyt.

## Target Contract

For every trigger transaction, SHKeeper should include an `aml` object with:

- `checked`: whether an AML provider produced a usable AML result.
- `supported`: whether the configured AML provider supports this asset/network.
- `check_status`: technical AML state such as `success`, `skipped`,
  `unsupported`, `timeout`, `error`, or `incomplete`.
- `reason_code`: technical explanation when `checked=false`.
- `provider`: configured AML provider, for example `koinkyt`.
- `provider_status`: technical provider status such as `success`, `pending`,
  `error`, `timeout`, or `unsupported`.
- `score`: provider risk score when available.
- `uid`: provider check/report identifier when available.
- `asset`: provider asset symbol when available.
- `network`: provider network when available.
- `signals`: provider evidence object.
- `report_url`: provider report URL when available.
- `error_code`: technical error code when AML was not checked successfully.
- `error_message`: technical error message when AML was not checked successfully.
- `policy`: local SHKeeper AML skip policy metadata when applicable.

SHKeeper callback payloads must not expose:

- `deposit_decision`
- `decision_reason`
- AML `status` values such as `approved` or `manual_review`
- AML `threshold`

## Unsupported Assets

If the configured AML provider does not cover an asset, for example BNB/BEP20
or TON assets, SHKeeper must not create a business `manual_review` result and
must not block the payment callback. It should send:

```json
{
  "aml": {
    "supported": false,
    "checked": false,
    "check_status": "unsupported",
    "reason_code": "unsupported_asset",
    "provider": "koinkyt",
    "provider_status": "unsupported",
    "score": null,
    "uid": null,
    "asset": null,
    "network": null,
    "signals": {},
    "report_url": null,
    "error_code": "unsupported_asset",
    "error_message": "AML provider does not support this asset",
    "policy": {}
  }
}
```

## Supported Assets

For supported AML assets, SHKeeper may still wait for the provider result before
the final callback. Once a result is terminal, it sends the provider data without
making a credit/manual-review decision.

Provider errors and timeouts should also produce callback data with
`checked=false`, not a business decision.

## Responsibility Boundary

SHKeeper owns:

- blockchain detection
- invoice/payment accounting
- AML provider enrichment
- idempotency identifiers

grither-pay owns:

- score thresholds
- credit/manual-review/reject decisions
- user-specific policy
- any delayed or secondary AML review workflow
