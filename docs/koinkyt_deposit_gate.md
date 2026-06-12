# Koinkyt Deposit Gate

SHKeeper uses Koinkyt for AML enrichment by default. AMLBot is retained only
as an explicit fallback provider for legacy deployments.

## Ownership

SHKeeper accepts deposits, calls `aml-shkeeper` for supported AML assets, stores
the AML snapshot returned by the sidecar, and sends callbacks to `grither-pay`.
`aml-shkeeper` owns the Koinkyt provider call.

`grither-pay` owns balance crediting, thresholds, and manual review. SHKeeper
never credits `grither-pay` balances and does not emit merchant-facing business
decision fields such as `deposit_decision` or `decision_reason`.

## Static Addresses

Static address mode can reuse a large invoice per user and coin. Invoice fields such as `paid`, `status`, `balance_fiat`, and `balance_crypto` are invoice accounting fields, not credit decisions.

`grither-pay` evaluates the trigger transaction where
`transactions[].trigger == true`. If SHKeeper has AML data for that transaction,
it is attached as factual `transactions[].aml` metadata.

## Merchant Callback Contract

SHKeeper callback payloads keep invoice accounting fields such as `paid`,
`status`, `balance_fiat`, and `balance_crypto`. Those are blockchain/payment
state fields, not credit decisions.

Every trigger transaction includes `aml` metadata:

- `aml.checked`: whether a provider produced a usable AML result.
- `aml.supported`: whether the configured provider supports this asset/network.
- `aml.check_status`: technical AML state such as `success`, `skipped`,
  `unsupported`, `timeout`, `error`, or `incomplete`.
- `aml.reason_code`: technical explanation when `aml.checked=false`.
- `aml.provider`
- `aml.provider_status`
- `aml.score`
- `aml.uid`
- `aml.asset`
- `aml.network`
- `aml.signals`
- `aml.report_url`
- `aml.error_code`
- `aml.error_message`
- `aml.policy`: local SHKeeper AML skip policy metadata when applicable.

Unsupported AML assets, for example BNB/BEP20 or TON assets while the provider
does not cover them, are sent as normal payment callbacks with
`aml.checked=false` and `aml.provider_status=unsupported`.

Supported assets skipped by local SHKeeper thresholds are sent with
`aml.supported=true`, `aml.checked=false`, `aml.check_status=skipped`, and
`aml.reason_code=amount_below_shkeeper_threshold`. Threshold details are placed
under `aml.policy` so grither-pay can make its own final decision.

SHKeeper merchant callbacks do not include:

- `deposit_decision`
- `decision_reason`
- AML `status`
- AML `threshold`

## Default Limits

- `AML_MIN_CHECK_AMOUNT_FIAT=100`
- `AML_SKIP_CUMULATIVE_LIMIT_FIAT=300`
- `AML_SKIP_CUMULATIVE_WINDOW=24h`
- `AML_MAX_ACCEPT_SCORE=0.70`

Sweep is separate operational logic. Recommended starting value: `SWEEP_MIN_AMOUNT_FIAT=300`.

## Koinkyt Contract

SHKeeper calls `aml-shkeeper`; `aml-shkeeper` calls Koinkyt.

SHKeeper sidecar contract:

- `POST /api/v1/checks`
- `GET /api/v1/checks/<deposit_id>`
- Basic Auth with `AML_SHKEEPER_USERNAME` and `AML_SHKEEPER_PASSWORD`
- Sidecar host from `AML_SHKEEPER_HOST`
- Provider selector in SHKeeper: `AML_PROVIDER=koinkyt` (or `CURRENT_PROVIDER=koinkyt`
  for shared deployment environments)

Koinkyt provider contract inside `aml-shkeeper`:

- `GET /openapi/v1/transaction`
- Authentication: `X-API-Key: <KOINKYT_API_KEY>`
- Default host: `KOINKYT_HOST=https://explorer.coinkyt.com/openapi/v1`
- Required API key: `KOINKYT_API_KEY`
- Optional risk profile IDs: `KOINKYT_RISK_PROFILE_IDS` as a comma-separated list of integer Koinkyt risk profile IDs, sent as repeated `risk_profile_ids` query parameters.
- Optional HTTP timeout: `KOINKYT_REQUEST_TIMEOUT_SECONDS` (fallback alias: `REQUESTS_TIMEOUT`, default `10`)
- Provider selector: `CURRENT_PROVIDER=koinkyt`

The downloaded Koinkyt OpenAPI 3.1 schema is saved at `docs/koinkyt_openapi.json`.
It defines server URL `https://explorer.coinkyt.com/openapi/` and paths such as
`/v1/transaction`, so the configured host intentionally includes `/openapi/v1`.

Required request fields derived from the deposit:

```json
{
  "blockchain": "btc",
  "token": "",
  "transaction": "txid"
}
```

## Supported Coverage

Documented Koinkyt coverage from `API_Documentation.pdf`:

- `BTC -> blockchain=btc, token=`
- `ETH -> blockchain=eth, token=`
- `ETH-USDT -> blockchain=eth, token=USDT`
- `ETH-USDC -> blockchain=eth, token=USDC`
- `TRX -> blockchain=trx, token=`
- `USDT -> blockchain=trx, token=USDT`
- `USDC -> blockchain=trx, token=USDC`

Other enabled SHKeeper assets bypass AML enrichment until Koinkyt support is
verified separately. Business handling for those deposits belongs in
`grither-pay`.

## Response Mapping

Koinkyt returns `risk_score`; SHKeeper stores and forwards it as AML metadata.
`grither-pay` decides what score, missing data, alerts, or provider errors mean
for crediting.

| Koinkyt field | SHKeeper field | Notes |
|---|---|---|
| `id` | `aml.uid` | Koinkyt check UUID. |
| `risk_score` | `aml.score` | Decimal risk coefficient. |
| `risk_score_grade` | `aml.signals.risk_score_grade` | `high`, `moderate`, `low`, or `undefined`. |
| `link` | `aml.report_url` | Koinkyt platform/report link. |
| `from_entity`, `to_entity`, `indirects`, `alerts`, `too_many_indirects` | `aml.signals` and raw snapshot | Provider evidence retained for review/debugging. |
| full JSON body | `raw_response_json` | Internal audit snapshot, not a stable merchant callback contract. |

SHKeeper does not expose `deposit_decision`, `decision_reason`, AML `status`, or
AML `threshold` in merchant callbacks. If `risk_profile_ids` are configured and
Koinkyt returns `alerts`, the alerts are forwarded in `aml.signals` for
`grither-pay` to evaluate.

OpenAPI notes for risk profiles:

- `GET /v1/risk-profile` returns profile IDs for the current Koinkyt account.
- `PUT /v1/risk-profile/{risk_profile_id}` expects exactly 33 `profile` rows.
- The OpenAPI example uses `amount=100` and `assets=0.01` for hard-risk categories such as `SCAM`, `SANCTIONS`, `TERRORISM_FINANCING`, `MIXING_SERVICE`, `RANSOM`, `DARKNET_*`, and similar categories.
- The current OpenAPI enum for token checks is native token, `USDT`, and `USDC` on `btc`, `eth`, and `trx`.

## Failure Policy

Provider failures never create a SHKeeper credit decision.

- `401`, `403`, `400`, `422`: hard provider error, forwarded as AML error metadata.
- `429`, `500`, `503`: retry until `AML_PENDING_TIMEOUT_SECONDS`, then forward timeout/error metadata.
- Transport errors and request timeouts: retry until `AML_PENDING_TIMEOUT_SECONDS`, then forward timeout/error metadata.
- `404`: ambiguous in the PDF. Treat `"No data, please try again later"` as retryable; final not-found shapes require live response validation or timeout before forwarding error metadata.
- Missing `risk_score` in an otherwise successful response: forwarded as incomplete AML metadata.
- Missing `KOINKYT_API_KEY` in `aml-shkeeper`: forwarded as provider error metadata.

## Live Probe Checklist

Live probes are deferred. The current integration follows `API_Documentation.pdf`; these commands are retained for later validation only.

Use environment variables so the API key never appears in shell history as a literal argument in committed docs:

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

Invalid transaction response shape:

```bash
curl -i -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode 'blockchain=btc' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=not-a-real-txid'
```

Invalid API key response shape:

```bash
curl -i -sS -G "$KOINKYT_HOST/transaction" \
  -H 'accept: application/json' \
  -H 'X-API-Key: invalid-key' \
  --data-urlencode 'blockchain=btc' \
  --data-urlencode 'token=' \
  --data-urlencode 'transaction=<btc_txid>'
```

## Open Questions

- Does `GET /transaction` return usable `risk_score` for TRC20/ERC20 token transfer txids, or must SHKeeper call `GET /transfer` with `input_address` and `output_address`?
- If `/transfer` is required, can SHKeeper reliably determine the sender `input_address` from existing walletnotify payloads for ETH/TRX token deposits?
- Which Koinkyt 404 response bodies are final not-found cases and which are temporary calculation states?
- Do `risk_profile_ids` affect only `alerts`, or can they change the `risk_score` used by SHKeeper policy?

Until live probe responses are run later, `/transaction` is the documented primary path from `API_Documentation.pdf`, `/transfer` remains the fallback candidate, and token checks remain manual-review if Koinkyt returns no usable `risk_score`.

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
        "provider": "koinkyt",
        "provider_status": "success",
        "status": "approved",
        "score": "0.04",
        "threshold": "0.10",
        "uid": "koinkyt-check-id",
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
        "provider": "koinkyt",
        "provider_status": "success",
        "status": "manual_review",
        "score": "0.72",
        "threshold": "0.10",
        "signals": {
          "risk_score_grade": "high"
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
        "provider": "koinkyt",
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
