# Grither Pay SHKeeper Payout Integration

This document is a handoff for an AI agent working in the Grither Pay repository.
It describes the current SHKeeper payout execution contract, the Grither-side
implementation shape, and the checks that must pass before enabling client
withdrawals in production.

Use this document together with:

- `docs/openapi-3.json` for SHKeeper API documentation. Use the
  `/api/v1/payout-executions` paths and the `shkeeperPayoutExecutionCallback`
  webhook section for this integration.
- `docs/runbooks/usdt-payout-operations.md`
- `docs/superpowers/specs/2026-06-03-grither-pay-shkeeper-payout-integration-design.md`

## Goal

Grither Pay creates customer USDT withdrawals and sends them to SHKeeper as
idempotent payout executions. SHKeeper owns rail dispatch and chain evidence.
Grither Pay owns customer-facing withdrawal state, balance reservation/refund,
manual resolution, and operator audit.

Supported rails:

- `USDT` on `TRON`
- `USDT` on `TON`
- `USDT` on `ETH`

## Contract Summary

SHKeeper inbound API:

- `POST /api/v1/payout-executions`
- `GET /api/v1/payout-executions/{external_id}`

Grither Pay webhook endpoint expected by SHKeeper:

- `POST /api/webhooks/shkeeper/payout-executions`

Authentication for all service-to-service calls uses `X-Payout-*` HMAC headers.
Do not use legacy SHKeeper Basic Auth or `X-Shkeeper-Api-Key` for payout
executions.

Signature base:

```text
<timestamp>
<nonce>
<METHOD>
<canonical_path>
<canonical_query>
<body_sha256>
```

Rules:

- `METHOD` is uppercase.
- `canonical_path` is the actual request path, for example
  `/api/v1/payout-executions`.
- `canonical_query` is the raw query string without `?`; use an empty string for
  no query.
- `body_sha256` is the lowercase hex SHA-256 of the exact HTTP body bytes. For
  `GET`, the body is empty bytes.
- `X-Payout-Key-Id` selects the secret but is not included in the signature base.
- Submit/status nonces are one-time-use. Replays must be rejected.
- Callback retries from SHKeeper reuse the same `event_id` as nonce. Treat an
  exact duplicate callback as idempotent using `event_id`, not as a failed
  business event.
- SHKeeper may refresh `X-Payout-Timestamp` and `X-Payout-Signature` for each
  callback delivery attempt while keeping the raw payload and `event_id` nonce
  stable. Deduplicate callbacks by `event_id`, not by signature bytes.

Required headers:

```http
X-Payout-Consumer: grither-pay
X-Payout-Key-Id: default
X-Payout-Timestamp: 1780560600
X-Payout-Nonce: <unique-request-nonce-or-callback-event-id>
X-Payout-Signature: <hex-hmac-sha256>
```

## Submit Request

```http
POST /api/v1/payout-executions
Content-Type: application/json
X-Payout-Consumer: grither-pay
X-Payout-Key-Id: default
X-Payout-Timestamp: 1780560600
X-Payout-Nonce: 2c338f55-05ba-4a9c-aaf4-caa8fbd3148f
X-Payout-Signature: <signature>
```

```json
{
  "external_id": "W123456789",
  "asset": "USDT",
  "network": "TRON",
  "amount": "25.000000",
  "destination": "TQZL6tWjV3L1y7mK7Q9..."
}
```

Submit rules:

- `external_id` must be the immutable Grither withdrawal public number or an
  equally stable provider idempotency key.
- Never reuse the same `external_id` for a different amount, destination, asset,
  network, or customer withdrawal.
- `asset` must be `USDT`.
- `network` must be `TRON`, `TON`, or `ETH`.
- `amount` must be a decimal string in USDT units. Normalize to exactly 6
  decimal places before signing and sending.
- Before calling SHKeeper, Grither must enforce all customer/business
  withdrawal policy: per-withdrawal bounds, daily rules, tier rules, wallet
  balance, compliance holds, and customer-facing eligibility. Do not send those
  policy fields to SHKeeper.
- Do not send a callback URL in the request. SHKeeper resolves the callback
  endpoint from the configured `grither-pay` consumer.
- Submit timeout is ambiguous. Query status for the same `external_id`; do not
  create a replacement withdrawal or a new payout execution.

Expected success response:

```json
{
  "status": "ACCEPTED",
  "consumer": "grither-pay",
  "execution_id": 123,
  "sidecar_execution_id": null,
  "external_id": "W123456789",
  "contract_version": "usdt-payout-execution-v1",
  "event_version": 1,
  "state_transition_id": "5a383116-5f69-493f-b4e2-8b5c948c5d5e",
  "occurred_at": "2026-06-04T08:10:00Z",
  "updated_at": "2026-06-04T08:10:00Z",
  "asset": "USDT",
  "network": "TRON",
  "crypto_id": "USDT",
  "sidecar_symbol": "USDT",
  "payout_queue": "tron_usdt_fee_payouts",
  "source_wallet_ref": "fee_deposit",
  "state": "CREATED",
  "failure_class": null,
  "amount": "25.000000",
  "destination": "TQZL6tWjV3L1y7mK7Q9...",
  "callback_endpoint_id": "grither-pay-main",
  "request_hash": "0e6f...",
  "sidecar_payload_hash": "2dd4...",
  "sidecar_state": null,
  "sidecar_state_version": null,
  "txids": [],
  "message_hashes": [],
  "error_code": null,
  "error_message": null,
  "reconciliation_required": false
}
```

`state=CREATED` is accepted. It is not a failure and it is not proof of chain
broadcast. SHKeeper moves the execution through sidecar preflight/submit from
its reconciler loop.

## Status Request

```http
GET /api/v1/payout-executions/W123456789
X-Payout-Consumer: grither-pay
X-Payout-Key-Id: default
X-Payout-Timestamp: 1780560600
X-Payout-Nonce: 6a1f3072-d275-4a2e-b31b-f29d0926b2f3
X-Payout-Signature: <signature>
```

Use status lookup:

- after submit timeout;
- after callback delay;
- during scheduled reconciliation;
- before any operator manual payout action;
- when the Grither row is reserved but provider state is stale.

Status responses use the same `PayoutExecutionResponse` shape as submit, but
`status` is `OK`.

## Callback Payload

SHKeeper sends a callback on every state transition. Grither Pay must store
callbacks durably and apply them through the same monotonic state applier used
for status polling.

```json
{
  "event_id": "f6a491c4-9f5d-4a5b-87f5-f5a5d7e75688",
  "event_version": 2,
  "state_transition_id": "d6505bf7-d25e-47cf-a64d-9e544d9f2301",
  "occurred_at": "2026-06-04T08:11:00.000000Z",
  "consumer": "grither-pay",
  "execution_id": 123,
  "sidecar_execution_id": "123",
  "external_id": "W123456789",
  "asset": "USDT",
  "network": "TRON",
  "amount": "25.000000",
  "destination": "TQZL6tWjV3L1y7mK7Q9...",
  "previous_state": "ENQUEUED",
  "state": "BROADCAST",
  "failure_class": null,
  "txids": [
    "4c32969220743644e3480d96e95a423d351049ac6296b8315103225709881ae3"
  ],
  "message_hashes": [],
  "error_code": null,
  "error_message": null,
  "reconciliation_required": false,
  "callback_endpoint_id": "grither-pay-main",
  "request_hash": "0e6f...",
  "sidecar_payload_hash": "2dd4..."
}
```

Callback handling rules:

- Verify HMAC before parsing or applying business state.
- Deduplicate by `event_id`.
- Exact duplicate callback returns accepted and does not mutate state twice.
- Same `event_version` with conflicting payload is an ordering conflict and must
  move the Grither payout to manual/reconciliation handling.
- Lower `event_version` than already applied is stale and must not downgrade
  state.
- Higher `event_version` applies only through the monotonic state machine.

## State Mapping

Use these SHKeeper states as provider states. Keep the public
`WalletWithdrawalStatus` small (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`,
`CANCELLED`) and store SHKeeper-specific state separately.

| SHKeeper state | Grither action |
| --- | --- |
| `CREATED` | Keep funds reserved, public status `PROCESSING`. |
| `PREFLIGHTED` | Keep funds reserved, public status `PROCESSING`. |
| `ENQUEUEING` | Unsafe window may be open. Keep funds reserved. |
| `ENQUEUED` | Unsafe window may be open. Keep funds reserved. |
| `BROADCAST` | Chain broadcast evidence exists or may exist. Keep funds reserved. |
| `CONFIRMED` | Complete withdrawal. Store txids/message hashes. |
| `FAILED_PRE_BROADCAST` | Original execution failed before unsafe side effects; fail and refund. |
| `FAILED_CHAIN_TERMINAL` | Keep reserved and require manual review unless policy proves safe. |
| `RECONCILIATION_REQUIRED` | Keep reserved; do not create a second payout. |
| `MANUAL_REVIEW` | Keep reserved; operator-only resolution. |
| `SAFE_FOR_MANUAL_PAYOUT` | Operator may initiate a manual payout. |
| `MANUAL_PAYOUT_PENDING` | Operator has started manual transfer; await evidence. |
| `MANUAL_PAYOUT_COMPLETED` | Complete withdrawal with manual evidence. |

Never refund or duplicate-send from `ENQUEUEING`, `ENQUEUED`, `BROADCAST`,
`FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`, or `MANUAL_REVIEW` without
explicit manual-resolution evidence.

## Grither Pay Implementation Map

When working in the Grither Pay repository, inspect or update these areas:

- Withdrawal routing:
  `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalCreationService.java`
- SHKeeper withdrawal creation:
  `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationService.java`
- Atomic reserve plus payout registration:
  `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalTransactionService.java`
- Provider payout registration:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperWalletCryptoWithdrawalPayoutService.java`
- SHKeeper HTTP client:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutClient.java`
- Submit outbox and timeout handling:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcher.java`
- Monotonic state applier:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`
- Callback webhook:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookController.java`
- Status sync scheduler:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStatusSyncService.java`
- Manual resolution:
  `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualResolutionService.java`
- Admin manual-resolution API:
  `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminShKeeperPayoutResolutionController.java`
- Database migrations:
  `apps/backend/src/main/resources/db/changelog/migrations/V089_create_shkeeper_payouts.sql`
  and any later ShKeeper payout audit migrations.

## SHKeeper Configuration Example

Use real secrets from the target environment. Do not commit them.

```json
{
  "grither-pay": {
    "default": {
      "secret": "replace-with-secret",
      "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"]
    }
  }
}
```

Set as `PAYOUT_CONSUMER_KEYS_JSON`. If callbacks use a separate key, set the
same shape as `PAYOUT_CALLBACK_KEYS_JSON`.

Callback endpoint configuration:

```json
{
  "grither-pay": {
    "grither-pay-main": {
      "url": "https://grither.example.com/api/webhooks/shkeeper/payout-executions",
      "path": "/api/webhooks/shkeeper/payout-executions",
      "query": ""
    }
  }
}
```

Set as `PAYOUT_CALLBACK_ENDPOINTS_JSON`.

Rail catalog example:

```json
{
  "consumer": "grither-pay",
  "rails": [
    {
      "consumer": "grither-pay",
      "asset": "USDT",
      "network": "TRON",
      "crypto_id": "USDT",
      "sidecar_service": "tron-shkeeper",
      "sidecar_symbol": "USDT",
      "payout_queue": "tron_usdt_fee_payouts",
      "source_wallet_ref": "fee_deposit",
      "execution_enabled": true,
      "callback_endpoint_id": "grither-pay-main",
      "contract_version": "usdt-payout-execution-v1",
      "decimals": 6
    },
    {
      "consumer": "grither-pay",
      "asset": "USDT",
      "network": "TON",
      "crypto_id": "TON-USDT",
      "sidecar_service": "ton-shkeeper",
      "sidecar_symbol": "TON-USDT",
      "payout_queue": "ton_usdt_payouts",
      "source_wallet_ref": "fee_deposit",
      "execution_enabled": true,
      "callback_endpoint_id": "grither-pay-main",
      "contract_version": "usdt-payout-execution-v1",
      "decimals": 6
    },
    {
      "consumer": "grither-pay",
      "asset": "USDT",
      "network": "ETH",
      "crypto_id": "ETH-USDT",
      "sidecar_service": "ethereum-shkeeper",
      "sidecar_symbol": "ETH-USDT",
      "payout_queue": "eth_usdt_payouts",
      "source_wallet_ref": "fee_deposit",
      "execution_enabled": true,
      "callback_endpoint_id": "grither-pay-main",
      "contract_version": "usdt-payout-execution-v1",
      "decimals": 6
    }
  ]
}
```

Set as `PAYOUT_RAILS_JSON`, then run:

```bash
flask payout-rail-sync
```

## Agent Checklist

Use this checklist in Grither Pay:

- The withdrawal creation path routes only enabled USDT networks to SHKeeper.
- Balance reservation, withdrawal row creation, payout execution row creation,
  and submit outbox insertion are one transaction.
- Submit outbox uses the immutable withdrawal public number as SHKeeper
  `external_id`.
- Submit idempotency conflict with same canonical request is treated as success.
- Submit conflict with different canonical request does not mutate funds and
  moves the provider row to reconciliation/manual handling.
- Submit timeout triggers status lookup for the same `external_id`.
- Callback webhook verifies `X-Payout-*` HMAC over the exact request bytes.
- Callback event storage is durable and deduplicates by `event_id`.
- Status polling and callback processing share one monotonic state applier.
- Public wallet status is derived from provider state and does not expose every
  SHKeeper internal state to users.
- Manual payout is blocked until the provider state is explicitly
  `SAFE_FOR_MANUAL_PAYOUT`.
- Manual resolution actions write audit rows with operator id, reason, evidence,
  and timestamps.
- All SHKeeper payout feature flags remain disabled by default in production
  configuration until staging smoke tests pass.

## Suggested Tests In Grither Pay

Run focused backend tests first:

```bash
./mvnw -pl apps/backend \
  -Dtest=ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutWebhookControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,WalletShKeeperWithdrawalCreationServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,AdminShKeeperPayoutResolutionControllerTest \
  test
```

Then run persistence, signing, sync, rollback, and manual-resolution tests:

```bash
./mvnw -pl apps/backend \
  -Dtest=ShKeeperPayoutPersistenceTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest,ShKeeperPayoutStateApplicationRollbackTest,ShKeeperPayoutManualResolutionServiceTest \
  test
```

If those pass, run the integration smoke tests that cover wallet withdrawal
creation and callback/status convergence:

```bash
./mvnw -pl apps/backend \
  -Dtest=WalletWithdrawalShKeeperIntegrationTest,ShKeeperWalletCallbackIntegrationTest \
  test
```

## End-To-End Acceptance Gates

Do not enable customer payout traffic until all gates pass:

- Grither Pay focused payout tests pass.
- SHKeeper core payout tests pass.
- TRON, TON, and ETH sidecar focused payout tests pass.
- OpenAPI contains both payout execution endpoints and `Payout_HMAC`.
- Staging has one successful low-value payout for each enabled network.
- Submit timeout/retry behavior is tested with no duplicate payout.
- Callback retry behavior is tested with idempotent duplicate delivery.
- Manual-resolution flow is tested from `RECONCILIATION_REQUIRED` to a terminal
  Grither state.
- Metrics and alerts are visible for SHKeeper, sidecars, and Grither scheduler.
- All payout changes are committed and reviewed across the relevant repositories.
