# Phase 002: Koinkyt AML Provider Documentation - Research

**Date:** 2026-05-05
**Research Mode:** Documentation-only, based on local vendor PDF and current codebase planning artifacts. Live Koinkyt probes are deferred.

## Research Summary

Koinkyt's API documentation describes an authenticated HTTP API under `https://explorer.coinkyt.com/openapi/v1`. Authentication is by `X-API-Key`; no Basic Auth sidecar is involved.

The PDF documents three AML check functions relevant to SHKeeper deposits:

| Function | Endpoint | Required Identity | Risk Score | Notes |
|---|---|---|---|---|
| Address check | `GET /address` | `blockchain`, `address` | `risk_score` | Useful for address monitoring, not the current transaction-level deposit gate. |
| Transaction check | `GET /transaction` | `blockchain`, `transaction` | `risk_score` | Best fit for existing SHKeeper `txid`-based AML flow. |
| Transfer check | `GET /transfer` | `blockchain`, `transaction`, `output_address`; `input_address` required for ETH/TRX | Not shown as top-level `risk_score` in the extracted example | May be required for token transfers or many-input cases; needs live validation. |

## Supported Assets From PDF

The provided PDF lists these accepted `blockchain` values:

- `btc`
- `eth`
- `trx`

The provided PDF lists these accepted `token` values:

- empty value for native token
- `USDT`
- `USDC`

Documentation and implementation should therefore treat all other SHKeeper-enabled assets as unsupported unless Koinkyt support is verified separately.

## Response Mapping

For `GET /transaction`, map Koinkyt fields into SHKeeper AML snapshot fields:

| Koinkyt Field | SHKeeper Field | Notes |
|---|---|---|
| `id` | `AmlCheck.uid` | Provider check UUID. |
| `risk_score` | `AmlCheck.score` | Decimal string coefficient. |
| `risk_score_grade` | `signals.risk_score_grade` | high/moderate/low/undefined. |
| `link` | `AmlCheck.report_url` | Platform/report link. |
| `from_entity`, `to_entity`, `indirects`, `alerts`, `too_many_indirects` | `signals_json` and `raw_response_json` | Preserve detailed evidence without making callback schema provider-specific. |
| full response | `raw_response_json` | Required for audit/debugging. |

SHKeeper policy should continue to compare `score <= AML_MAX_ACCEPT_SCORE` for `deposit_decision="credit"`.

## Error Policy From PDF

The PDF lists these relevant HTTP statuses:

- `400` bad request / invalid token
- `401` unauthorized
- `403` access denied / license period finished / not enough checks
- `404` transaction/address not found, no calculated data, calculation in progress, empty address, no alerts, no PDF
- `422` unprocessable entity
- `429` too many requests
- `500` internal server error
- `503` service unavailable

Recommended documentation policy:

- `401`, `403`, `400`, `422`: hard provider error, fail closed to manual review.
- `429`, `500`, `503`: retry until `AML_PENDING_TIMEOUT_SECONDS`, then manual review.
- `404`: ambiguous in the PDF. Treat `"No data, please try again later"` as retryable. Treat final not-found messages as manual review only after live response shape is known or timeout expires.
- Missing `risk_score` in an otherwise successful response: `incomplete_aml_result`, manual review.

## Deferred Live Probe Checklist

These probes are intentionally deferred. Do not treat them as a current execution gate. They are kept here so a later phase can validate the documented assumptions without reconstructing the commands.

When the project is ready for live validation, the developer can run these manually and paste sanitized JSON responses back into the session. Do not paste real API keys.

Set:

```bash
export KOINKYT_API_KEY='redacted'
export KOINKYT_HOST='https://explorer.coinkyt.com/openapi/v1'
```

BTC native transaction:

```bash
curl -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=btc' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=<btc_txid>'
```

ETH native transaction:

```bash
curl -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=eth' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=<eth_txid>'
```

TRX native transaction:

```bash
curl -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=trx' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=<trx_txid>'
```

TRC20 USDT transaction via `/transaction`:

```bash
curl -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=trx' \
  --data-urlencode 'token=USDT' \
  --data-urlencode 'transaction=<trc20_usdt_txid>'
```

TRC20 USDT transfer fallback:

```bash
curl -sS -G "$KOINKYT_HOST/transfer" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=trx' \
  --data-urlencode 'token=USDT' \
  --data-urlencode 'transaction=<trc20_usdt_txid>' \
  --data-urlencode 'input_address=<sender_address>' \
  --data-urlencode 'output_address=<deposit_address>'
```

Error-shape probes:

```bash
curl -i -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=btc' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=not-a-real-txid'
```

```bash
curl -i -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H 'X-API-Key: invalid-key' \
  --data-urlencode 'blockchain=btc' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=<btc_txid>'
```

## Planning Implications

- Documentation must call out `/transfer` uncertainty as a documented validation gap, not hide it.
- Tests should remain mocked and documentation-driven until live response samples are reviewed and sanitized fixtures are approved in a later phase.
- Runtime should preserve raw Koinkyt responses because the vendor schema is broad and provider-specific details should not leak into stable callback fields beyond `aml.signals`.

## Validation Architecture

Validation for this phase should check:

- `docs/koinkyt_deposit_gate.md` includes env vars, endpoints, supported assets, response mapping, failure policy, and deferred manual probe commands.
- `README.md` links to Koinkyt docs, not AMLBot docs.
- SHKeeper imports only `AmlShkeeperClient`; no active SHKeeper code imports a direct Koinkyt client.
- Production docs instruct deploying `AML_SHKEEPER_*` in SHKeeper and `KOINKYT_*` in `aml-shkeeper`.
- The documentation explicitly names unknowns requiring live probe responses.

## RESEARCH COMPLETE
