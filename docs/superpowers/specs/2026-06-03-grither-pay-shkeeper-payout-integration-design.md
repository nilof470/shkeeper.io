# Grither Pay SHKeeper Payout Integration Design

**Date:** 2026-06-03

**Target repository:** `/Users/test/IdeaProjects/grither-pay`

**Source SHKeeper contract:**
`docs/superpowers/specs/2026-06-03-usdt-withdrawals-production-readiness-design.md`

**Goal:** integrate Grither Pay USDT withdrawals with the new SHKeeper payout
execution API without coupling SHKeeper internals to Grither Pay names, and
without risking duplicate payout, unsafe refund, or ambiguous accounting.

---

## Current Code Reality

Validated against the local Grither Pay repository on 2026-06-03.

Implementation verification update, 2026-06-04:

- The local Grither Pay repository now contains wallet routing, payout
  persistence, submit outbox, signed SHKeeper payout client, callback ingestion,
  monotonic state application, status sync, and operator manual-resolution APIs.
- Targeted backend payout tests passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='ShKeeperPayout*Test,WalletShKeeperWithdrawalCreationServiceTest,WalletCryptoWithdrawalPayoutStateServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,AdminShKeeperPayoutResolutionControllerTest,ShKeeperConfigTest'`
  returned 63 tests, 0 failures, 0 errors.
- Additional wallet/provider identity regression tests passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='AltynWalletStatusMapperTest,AltynWalletStatusPollingAdapterTest,WalletDepositServiceTest,WalletDepositIntegrationTest,WalletCrossSystemIntegrationTest,WalletControllerIntegrationTest,WalletWebhookRoutingTest'`
  returned 187 tests, 0 failures, 0 errors.
- Wallet concurrency regression passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=WalletConcurrencyIntegrationTest`
  returned 8 tests, 0 failures, 0 errors.
- Full backend suite passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true`
  returned 3664 tests, 0 failures, 0 errors.
- Those tests started PostgreSQL through Testcontainers and applied Liquibase
  through `V089_create_shkeeper_payouts.sql`.
- `git diff --check` passed in `/Users/test/IdeaProjects/grither-pay`.

The Grither-side compatibility boundary now explicitly separates provider
payment reference from provider transaction id. Provider polling and webhook
lookup continue to target the stored payment reference; completed terminal facts
record the actual transaction id. Expired unbound USDT deposits without provider
external id or prior webhook marker stay fail-closed.

Existing SHKeeper support is deposit/invoice oriented:

- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/config/ShKeeperConfig.java`
  configures base API URL, API key, callback base URL, webhook HMAC settings, and
  the existing `ShKeeperApiClient`.
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/client/ShKeeperApiClient.java`
  exposes deposit/invoice APIs under `/api/v1`, but has no payout execution
  methods.
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/*` already has
  domain, persistence, reconciliation, callback, and client packages. New payout
  integration should live under this provider boundary rather than inside Altyn
  code.

Current wallet withdrawal flow is Altyn-centric:

- `WalletWithdrawalCreationService.createUsdtWithdrawal(...)` delegates directly
  to `WalletAltynWithdrawalCreationService.createUsdtWithdrawal(...)`.
- `WalletWithdrawalTransactionService.reserveBalanceAndCreateWithdrawal(...)`
  creates `WalletWithdrawal` with `provider("ALTYN")` and writes Altyn-specific
  fields: `altynExternalId`, `altynPayoutPhone`, `altynBankCode`.
- `WalletWithdrawalProviderStatusService.applyProviderPayoutStatus(...)` maps
  provider `FAILED` or `REFUNDED` into `failWithdrawal(...)`.
- `WalletWithdrawalTransactionService.failWithdrawal(...)` immediately credits a
  `WITHDRAWAL_REVERSAL` ledger entry and sets public withdrawal status to
  `FAILED`.
- `WalletWithdrawalProviderFactApplier` writes `altynPayoutStatus` from terminal
  provider facts.
- `WalletWithdrawalStatus` only has public states:
  `PENDING`, `PROCESSING`, `COMPLETED`, `CANCELLED`, `FAILED`.
- `WalletWithdrawal.publicNumber` is unique, non-updatable, user-visible, and is
  the best current candidate for SHKeeper `external_id`.

Critical implication: SHKeeper payouts must not be forced through the existing
Altyn status applier as-is. In particular, SHKeeper `FAILED_CHAIN_TERMINAL` is
not the same as "refund the customer now"; it means "automatic chain execution
failed after the unsafe broadcast window or cannot be proven safe, move to
manual review and keep funds reserved."

## Product Boundary

Grither Pay owns:

- user withdrawal request and public status;
- customer balance ledger and reservation accounting;
- risk, limits, feature flags, and operator pause;
- submit outbox and callback/status application;
- user-facing completion/failure/manual-review behavior;
- manual resolution evidence and final accounting.

SHKeeper owns:

- service-to-service payout execution API;
- idempotency by `(consumer, external_id)`;
- rail routing and sidecar dispatch;
- normalized execution state and callback/status schema;
- chain evidence and reconciliation flags.

Sidecars own:

- network-specific address validation, signing, broadcast, confirmation, and
  negative evidence.

## Integration Decision

Use SHKeeper as the USDT withdrawal provider only for enabled networks and only
behind explicit flags.

Recommended first release:

- `provider = "SHKEEPER"` for SHKeeper-backed USDT withdrawals.
- SHKeeper `consumer = "grither-pay"` configured in SHKeeper, not hard-coded in
  generic SHKeeper table or method names.
- SHKeeper `external_id = WalletWithdrawal.publicNumber`.
- Public `WalletWithdrawalStatus` can remain the existing five-state enum.
- Add SHKeeper-specific provider/internal state on a separate payout execution
  table instead of expanding the public status enum prematurely.

Do not reuse Altyn fields as the primary SHKeeper contract. It is acceptable to
populate generic existing fields such as `provider`, `cryptoAddress`,
`cryptoCurrency`, `cryptoNetwork`, and `txHash` when semantically correct.
Altyn-prefixed fields must not be the durable SHKeeper state source.

## SHKeeper API Contract

### Submit

```http
POST /api/v1/payout-executions
```

Request body from Grither Pay:

```json
{
  "external_id": "W123456789",
  "asset": "USDT",
  "network": "TRON",
  "amount": "25.000000",
  "destination": "T..."
}
```

Rules:

- `external_id` is `WalletWithdrawal.publicNumber`.
- The same `external_id` must never be reused for a different withdrawal, rail,
  destination, or amount.
- Amount is a decimal string normalized to 6 USDT decimals before submit.
- Grither Pay must not send arbitrary callback URLs. SHKeeper resolves callback
  endpoint from the configured `grither-pay` consumer.
- Submit timeout is ambiguous. Grither Pay must query status for the same
  `external_id`; it must not create a new withdrawal or new `external_id`.

Expected response shape:

```json
{
  "status": "ACCEPTED",
  "consumer": "grither-pay",
  "execution_id": 123,
  "sidecar_execution_id": null,
  "external_id": "W123456789",
  "contract_version": "usdt-payout-execution-v1",
  "network": "TRON",
  "asset": "USDT",
  "state": "CREATED",
  "event_version": 1,
  "state_transition_id": "transition-id",
  "occurred_at": "2026-06-03T10:15:00Z",
  "updated_at": "2026-06-03T10:15:00Z",
  "request_hash": "hash",
  "sidecar_payload_hash": "hash"
}
```

`POST` returning `CREATED` is expected. Grither Pay must treat it as accepted and
processing; it must not require immediate `ENQUEUED`. SHKeeper moves the
execution to `ENQUEUED` later through the DB-backed payout execution
worker/reconciler, then exposes that progression through callbacks and status
lookup.

`sidecar_execution_id` is optional correlation metadata. It must not be required
for lookup, dedupe, or reconciliation.

### Status

```http
GET /api/v1/payout-executions/{external_id}
```

Grither Pay uses status lookup:

- after submit timeout;
- after callback delay;
- during scheduled reconciliation;
- before any operator manual payout action.

The status response must include `event_version`, `state_transition_id`,
`occurred_at`, hashes, failure class, txids/message hashes, error fields, and
`reconciliation_required`.

### Callback

Recommended Grither endpoint:

```http
POST /api/webhooks/shkeeper/payout-executions
```

Callback requirements:

- HMAC over this exact signature base for both submit/status requests and
  callbacks:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- Required headers: `X-Payout-Consumer`, `X-Payout-Key-Id`,
  `X-Payout-Timestamp`, `X-Payout-Nonce`, `X-Payout-Signature`.
- Reject missing signature, stale timestamp, unknown key id, wrong method/path/
  query, and tampered body.
- `X-Payout-Key-Id` is required and selects the secret. It is not part of the
  current SHKeeper v1 signature base; adding it to the base would make Grither
  signatures incompatible with SHKeeper.
- Treat a repeated valid callback nonce as a replay observation, not an auth
  failure by itself. SHKeeper callback retries reuse the same event id as nonce.
  The callback event table must deduplicate by `event_id` and make exact
  duplicates idempotent while still rejecting or reconciling stale/conflicting
  payloads.
- SHKeeper may refresh callback timestamp/signature headers on each delivery
  attempt while keeping the raw callback payload and `event_id` nonce stable.
  Grither Pay must deduplicate by `event_id`; it must not require retry
  signatures to match earlier attempts byte-for-byte.
- Use fallback key only for compatibility:
  `execution_id + state + txid/message_hash + event_version`.
- Apply callbacks and status poll responses through the same monotonic state
  applier.

## Data Model

Create provider-owned payout tables in the Grither Pay backend.

### `shkeeper_payout_executions`

Required columns:

- `id`
- `wallet_withdrawal_id` unique, foreign key to `wallet_withdrawals(id)`
- `external_id` unique, equal to `wallet_withdrawals.public_number`
- `execution_id` unique nullable until accepted
- `sidecar_execution_id` nullable unique when present
- `contract_version`
- `asset`
- `network`
- `amount` / `payout_amount`: canonical USDT amount sent to SHKeeper
- `network_fee`: customer-charged network fee reserved by Grither Pay, not sent
  as the SHKeeper transfer amount
- `reserved_amount`: `payout_amount + network_fee`
- `destination`
- `state`
- `public_withdrawal_status`
- `event_version`
- `state_transition_id`
- `last_event_id`
- `request_hash`
- `sidecar_payload_hash`
- `failure_class`
- `txids_json`
- `message_hashes_json`
- `error_code`
- `error_message`
- `reconciliation_required`
- `manual_resolution_state`
- `manual_resolution_reason`
- `manual_resolution_evidence_json`
- `created_at`
- `updated_at`
- `submitted_at`
- `broadcasted_at`
- `confirmed_at`
- `terminal_at`

Recommended constraints:

- unique `wallet_withdrawal_id`
- unique `external_id`
- unique `execution_id` where not null
- unique `sidecar_execution_id` where not null
- unique `last_event_id` where not null, or a separate callback dedupe table
- optimistic `version` inherited from `BaseEntity` plus repository methods that
  acquire a row lock or perform a compare-and-set update before applying a new
  provider state
- index `(state, updated_at)`
- index `(reconciliation_required, updated_at)`

### `shkeeper_payout_submit_outbox`

Required columns:

- `id`
- `payout_execution_id`
- `external_id`
- `request_hash`
- `status`: `PENDING`, `SENDING`, `SENT`, `FAILED_RETRYABLE`, `FAILED_FINAL`
- `attempt_count`
- `next_attempt_at`
- `last_error_code`
- `last_error_message`
- `locked_at`
- `locked_by`
- `created_at`
- `updated_at`

Outbox requirements:

- Unique `payout_execution_id`.
- Unique `external_id`.
- Unique `(external_id, request_hash)`.
- Withdrawal row, ledger debit/reservation, `shkeeper_payout_executions`, and
  `shkeeper_payout_submit_outbox` must commit in one DB transaction.
- Dispatcher retries are idempotent by `external_id` and `request_hash`.
- Dispatcher claiming must use `FOR UPDATE SKIP LOCKED`, an atomic
  `UPDATE ... WHERE status in (...) AND locked_at is expired`, or the existing
  ShedLock pattern plus a row-level CAS. Two scheduler instances must not submit
  the same outbox row concurrently.
- A process crash after DB commit but before HTTP submit is recovered by the
  outbox.
- A process crash before DB commit creates no payout and no ledger reservation.

### `shkeeper_payout_callback_events`

Create this table. Do not collapse callback dedupe into `last_event_id` on the
execution row; one row cannot safely represent replay, stale events, same-version
conflicts, and raw payload audit history.

This table is not a duplicate of the SHKeeper callback outbox. SHKeeper owns
outbound delivery evidence; Grither Pay owns inbound dedupe, raw payload audit,
monotonic state application, and the atomic ledger effect that follows from an
accepted provider event.

Required columns:

- `event_id` unique
- `execution_id`
- `external_id`
- `event_version`
- `state_transition_id`
- `payload_hash`
- `raw_payload`
- `signature_key_id`
- `received_at`
- `applied_at`
- `apply_result`

Recommended constraints:

- unique `event_id`
- unique `(execution_id, event_version)`
- unique `state_transition_id`
- callback event insert, provider state update, wallet status update, and ledger
  effect commit in the same DB transaction
- stale rows where `applied_at is null` should alert operators as callback
  backlog, but Grither Pay should not add a callback replay worker in the first
  release; replay/recovery would risk duplicating provider event semantics that
  are already covered by SHKeeper delivery and Grither's atomic transaction.

### `shkeeper_payout_manual_resolution_audit`

Create an append-only audit table for state-changing operator actions in the
manual-resolution flow. Do not rely only on the latest
`manual_resolution_evidence_json` snapshot on `shkeeper_payout_executions`; the
snapshot is useful for current state, but it is not a complete audit trail.

Required columns:

- `payout_execution_id`
- `wallet_withdrawal_id`
- `external_id`
- `execution_id`
- `sidecar_execution_id`
- `actor_user_id`
- `action`: `NEGATIVE_EVIDENCE_RECORDED`, `MANUAL_PAYOUT_PENDING`,
  `MANUAL_PAYOUT_COMPLETED`
- `previous_provider_state`
- `new_provider_state`
- `previous_manual_resolution_state`
- `new_manual_resolution_state`
- `reason`
- `evidence`
- `manual_resolution_evidence_json`
- `negative_evidence_confirmed`
- `manual_tx_hash`
- `manual_message_hash`
- `request_hash`
- `sidecar_payload_hash`
- `state_transition_id`
- `last_event_id`
- `created_at`

Requirements:

- write audit rows in the same transaction as the manual-resolution state change;
- keep persistence behind a dedicated recorder service, not embedded directly in
  the manual-resolution service;
- never edit audit rows to correct mistakes; append a new operator action or
  incident note;
- expose the current manual-resolution snapshot in the admin view and keep the
  append-only audit available for incident review.

## State Mapping

Use a provider/internal SHKeeper state separate from the public withdrawal
status.

Recommended SHKeeper provider states:

- `CREATED`
- `PREFLIGHTED`
- `ENQUEUEING`
- `ENQUEUED`
- `BROADCAST`
- `CONFIRMED`
- `FAILED_PRE_BROADCAST`
- `FAILED_CHAIN_TERMINAL`
- `RECONCILIATION_REQUIRED`
- `MANUAL_REVIEW`
- `SAFE_FOR_MANUAL_PAYOUT`
- `MANUAL_PAYOUT_PENDING`
- `MANUAL_PAYOUT_COMPLETED`

Public wallet status mapping:

| SHKeeper state | WalletWithdrawalStatus | Ledger action |
| --- | --- | --- |
| `CREATED`, `PREFLIGHTED`, `ENQUEUEING`, `ENQUEUED` | `PROCESSING` | funds stay reserved |
| `BROADCAST` | `PROCESSING` | funds stay reserved |
| `CONFIRMED` | `COMPLETED` | finalize withdrawal, do not refund |
| `FAILED_PRE_BROADCAST` | `FAILED` | release/refund reserved funds exactly once |
| `FAILED_CHAIN_TERMINAL` | `PROCESSING` or operator-only manual state | keep funds reserved |
| `RECONCILIATION_REQUIRED` | `PROCESSING` or operator-only manual state | keep funds reserved |
| `MANUAL_REVIEW` | `PROCESSING` or operator-only manual state | keep funds reserved |
| `SAFE_FOR_MANUAL_PAYOUT` | `PROCESSING` | keep funds reserved |
| `MANUAL_PAYOUT_PENDING` | `PROCESSING` | keep funds reserved |
| `MANUAL_PAYOUT_COMPLETED` | `COMPLETED` | finalize withdrawal after evidence |

`CONFIRMING` is a Grither-internal provider state derived from SHKeeper
`BROADCAST` plus local polling/confirmation progress. SHKeeper does not need to
emit a separate `CONFIRMING` state for the first contract version.

Do not map `FAILED_CHAIN_TERMINAL` to `failWithdrawal(...)` because the current
implementation refunds the customer. That is unsafe for payouts that may have
entered the broadcast window or require manual accounting.

## Creation Flow

For `WalletWithdrawalMethod.USDT`:

1. Validate user, amount, destination address, and network.
2. Check SHKeeper payout feature flags:
   `shkeeper.payouts.enabled`, network enablement, and operator pause; amount
   policy remains in the existing Grither wallet/business limit layer.
3. If network is disabled, keep the existing Altyn path or fail closed according
   to product rollout policy.
4. Start a DB transaction.
5. Create `wallet_withdrawals` row with `provider = "SHKEEPER"`,
   `status = PENDING`, `cryptoCurrency = "USDT"`, normalized network and address.
6. Debit/reserve `reserved_amount = payout_amount + network_fee` through the
   existing wallet ledger operation. The SHKeeper submit amount is only
   `payout_amount`.
7. Create `shkeeper_payout_executions` with local state `PENDING_SUBMIT`, storing
   `payout_amount`, `network_fee`, `reserved_amount`, and the normalized
   destination/network.
8. Create `shkeeper_payout_submit_outbox` with status `PENDING`.
9. Commit the transaction.
10. Dispatcher submits to SHKeeper asynchronously or immediately after commit
    through the same outbox path.

The API response to the user can remain "withdrawal accepted/processing"; it
must not depend on a synchronous chain broadcast.

## Dispatcher Flow

Dispatcher responsibilities:

- claim pending outbox rows with a DB lock or existing scheduler lock pattern;
- before signing or submitting, lock the linked `WalletWithdrawal` and prove it
  is still reserved (`PENDING` or `PROCESSING`). If it is already terminal, do
  not call SHKeeper; mark the submit outbox final, move the payout execution to
  `RECONCILIATION_REQUIRED`, and alert;
- sign the SHKeeper request;
- submit `POST /api/v1/payout-executions`;
- on success, store `execution_id`, request hashes, ordering metadata, and apply
  the returned SHKeeper state (`CREATED`, `PREFLIGHTED`, `ENQUEUEING`,
  `ENQUEUED`, etc.) through the same monotonic state applier used for callbacks;
- on timeout, query `GET /api/v1/payout-executions/{external_id}`;
- on retryable network failure before any accepted response, retry same
  `external_id` and same request hash;
- on `409 IDEMPOTENCY_CONFLICT`, move to `RECONCILIATION_REQUIRED` and alert;
- never create a second Grither withdrawal or second SHKeeper `external_id`.

## Monotonic State Applier

Implement one service for both callbacks and status polling, for example:
`ShKeeperPayoutStateApplicationService`.

Rules:

- Load `ShKeeperPayoutExecution` by `external_id` or `execution_id` with a
  pessimistic row lock, or apply with optimistic `@Version` plus a retry-limited
  compare-and-set. Lock the linked `WalletWithdrawal` before ledger-visible state
  changes.
- Insert/deduplicate callback `event_id` in `shkeeper_payout_callback_events`
  before mutating provider state.
- Compare incoming `event_version` with stored `event_version`.
- If incoming version is older, ignore and record stale observation.
- If incoming version is equal with same `state_transition_id`, treat as
  idempotent.
- If incoming version is equal with different state/evidence, move to
  `RECONCILIATION_REQUIRED`, keep funds reserved, and alert.
- If incoming version is newer, apply exactly one state transition and update
  stored evidence.
- Public `WalletWithdrawalStatus` changes must happen in the same transaction as
  provider state and ledger effects.
- Concurrent callbacks/status polls must be deterministic: exactly one wins the
  CAS/lock and the loser reloads current state before deciding stale,
  idempotent, or conflict.

## Accounting Rules

Ledger invariants:

- Debit/reservation of `reserved_amount = payout_amount + network_fee` happens
  exactly once at withdrawal creation.
- `CONFIRMED` completes the withdrawal without refund.
- `CONFIRMED` records the chain amount as `payout_amount`; the reserved network
  fee remains charged according to existing product policy unless a separate
  operator fee-adjustment flow is added.
- `FAILED_PRE_BROADCAST` may release/refund the full `reserved_amount` exactly
  once because no unsafe chain side effect occurred.
- `FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`, and `MANUAL_REVIEW` never
  auto-refund and never auto-retry.
- Manual payout completion finalizes the original withdrawal; it must not create
  a second user withdrawal record.
- Manual payout completion must record manual tx evidence and actual fee
  accounting. Any fee delta against `network_fee` is handled by an explicit
  operator accounting decision, not by an automatic hidden ledger adjustment.
- Every ledger credit/refund idempotency key must be deterministic and include
  the withdrawal id plus terminal reason.

## Manual Review

Manual payout is allowed only after the system records enough negative evidence
that the original automatic execution cannot still complete.

Minimum evidence before `SAFE_FOR_MANUAL_PAYOUT`:

- latest SHKeeper status payload;
- sidecar state and failure class;
- txid/message hash list, even if empty;
- chain explorer or node query evidence for each known txid/message hash;
- rail-specific negative evidence:
  - TRON: source wallet, TRC20 contract events, txid/ref-block or expiration
    evidence, finalized block range, destination, amount, and resource
    reservation state;
  - TON: signed BOC/message hash, source seqno, source wallet history, Jetton
    master/wallet transfer history, masterchain range, and valid-until proof;
  - ETH: nonce state, same-nonce finalized transaction if consumed, ERC20
    `Transfer` logs for the USDT contract, source/destination/amount, chain id,
    and finalized block range. Txpool disappearance is not negative evidence.
- operator note explaining why automatic execution will not complete;
- second-operator approval for production if withdrawal amount exceeds the
  configured threshold.

The Grither Pay admin API must submit this negative evidence as a structured JSON
object, not as free text. The first-release implementation must reject
`SAFE_FOR_MANUAL_PAYOUT` unless the JSON contains common
`externalId`, `requestHash`, `network`, source/destination/amount,
finalized-range/status/node evidence, and the rail-specific fields above.
`externalId`, `requestHash`, `network`, `destination`, and `amount` must match
the local `ShKeeperPayoutExecution`; evidence for another payout must not unlock
manual payout. Free-form operator notes remain useful context, but they are not
sufficient to unlock manual payout.

Operator states:

- `MANUAL_REVIEW`: funds reserved; no retry, no refund.
- `SAFE_FOR_MANUAL_PAYOUT`: evidence recorded; manual payout can be initiated.
- `MANUAL_PAYOUT_PENDING`: operator has started manual payout.
- `MANUAL_PAYOUT_COMPLETED`: evidence includes manual txid/message hash; public
  withdrawal moves to `COMPLETED`.

Manual payout execution may be performed through the existing SHKeeper admin flow
or another approved operator wallet flow. Grither Pay's responsibility is to
record the manual payout evidence, txid/message hash, operator identity, and
accounting decision before marking the client withdrawal completed.

## Security

Required:

- separate payout HMAC/API credentials from deposit webhook secrets;
- HMAC covers raw body hash, timestamp, nonce, HTTP method, canonical path,
  and canonical query string; `X-Payout-Key-Id` is a required secret selector
  header for the current SHKeeper v1 contract;
- replay protection with timestamp tolerance, callback replay classification,
  and persistent `event_id` dedupe;
- no callback URL supplied by the user request;
- no secrets in logs;
- structured logging with masked destination address and external id;
- config validation fails closed when SHKeeper payouts are enabled without URL,
  key id, secret, callback secret, or network allowlist.

## Configuration

Suggested properties:

```yaml
shkeeper:
  payouts:
    enabled: false
    consumer: grither-pay
    api-url: https://shkeeper.example
    api-key-id: ""
    api-secret: ""
    callback-key-id: ""
    callback-secret: ""
    submit-timeout-ms: 15000
    status-timeout-ms: 10000
    callback-max-skew-seconds: 300
    networks:
      tron:
        enabled: false
      ton:
        enabled: false
      eth:
        enabled: false
```

Use existing `wallet.crypto.provider=SHKEEPER` only as a provider selection
input if it already gates SHKeeper wallet behavior. Payouts still need their own
explicit `shkeeper.payouts.enabled` and per-network flags.

Grither Pay must not add ShKeeper-specific amount or daily withdrawal caps in
this integration. Amount validation remains owned by Grither Pay's existing
wallet/business limit layer, for example `WalletMethodLimitOperations`, so
there is one product policy source for client withdrawal amounts.

The remaining `submit-batch-size`, `submit-lock-ttl-seconds`,
`submit-max-attempts`, `submit-retry-base-delay-seconds`,
`status-sync-batch-size`, and stale alert age/batch settings are technical
worker boundaries. They bound retry pressure, lock lifetime, and alert volume;
they are not client withdrawal amount limits.

## SHKeeper Helm Handoff

Grither Pay must integrate with the generic SHKeeper payout consumer contract.
Do not ask SHKeeper to add Grither-specific field or method names. The only
SHKeeper-side consumer identifier for this integration is the configured generic
consumer value:

```yaml
payouts:
  enabled: true
  consumer: grither-pay
```

The SHKeeper Helm chart owns callback endpoint resolution. Grither Pay must not
send arbitrary callback URLs in submit requests. The submit request carries
`callback_endpoint_id`, and SHKeeper resolves that id from
`PAYOUT_CALLBACK_ENDPOINTS_JSON`.

Required SHKeeper chart values for a Grither Pay rail:

```yaml
payouts:
  sidecarRequestTimeoutSeconds: 10
  authMaxAgeSeconds: 300
  networkPolicies:
    enabled: true
  storage:
    mode: singleNodeSqlitePvc
    claimName: shkeeper-db-claim
    allowSeparateWorkerDeployments: true
    backupRestoreEvidence: "restore-drill-id"
  rails:
    tronUsdt:
      enabled: true
      paused: false
      killSwitch: false
      queue: tron_usdt_fee_payouts
      sourceWalletRef: fee_deposit
      callbackEndpointId: grither-pay-main
      hotWalletMinimumBalance: ""
      feeWalletMinimumBalance: ""
      backupRestoreEvidence: "restore-drill-id"
      ownedImageRepository: nilof470/tron-shkeeper
```

`hotWalletMinimumBalance` and `feeWalletMinimumBalance` are optional SHKeeper
Prometheus alert thresholds only; empty values render no low-balance alert and
must not be interpreted by Grither Pay as payout
validation limits.
For the first production release `sourceWalletRef` must stay `fee_deposit`;
Grither Pay must not assume dedicated payout wallets are available until the
sidecar forks expose and prove a source-wallet override.
SHKeeper rail sync is desired-state for the configured `consumer`: when a rail
is removed from Helm values or rendered with `paused=true`/`killSwitch=true`,
SHKeeper disables client withdrawals for that rail in its DB catalog. Grither
Pay should still keep its own network disabled unless both sides explicitly
declare the rail enabled.

Secret payload shapes expected by SHKeeper/sidecars:

```json
{
  "PAYOUT_CONSUMER_KEYS_JSON": {
    "grither-pay": {
      "key_id": "grither-pay-v1",
      "secret": "secret-from-secret-manager",
      "enabled": true
    }
  }
}
```

```json
{
  "PAYOUT_SIDECAR_KEYS_JSON": {
    "grither-pay": {
      "shkeeper-to-sidecars-v1": {
        "secret": "secret-from-secret-manager",
        "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"]
      }
    }
  }
}
```

```json
{
  "PAYOUT_CALLBACK_KEYS_JSON": {
    "grither-pay": {
      "key_id": "shkeeper-callback-v1",
      "secret": "secret-from-secret-manager"
    }
  }
}
```

```json
{
  "PAYOUT_CALLBACK_ENDPOINTS_JSON": {
    "grither-pay-main": {
      "consumer": "grither-pay",
      "url": "https://grither-pay.example/api/provider/shkeeper/payout-callback",
      "enabled": true
    }
  }
}
```

The sidecar `PAYOUT_CONSUMER_KEYS_JSON` secret is separate from the SHKeeper
consumer key secret unless ops intentionally uses the same external-secret
source. SHKeeper validates Grither Pay requests; sidecars validate SHKeeper
requests. Do not reuse deposit webhook secrets for payouts.

Helm rail source-wallet mapping for the first release:

| Rail | SHKeeper `crypto_id` | Queue | Source wallet |
| --- | --- | --- | --- |
| USDT/TRON | `USDT` | `tron_usdt_fee_payouts` | current sidecar `fee_deposit` |
| USDT/TON | `TON-USDT` | `ton_usdt_payouts` | current sidecar `fee_deposit` |
| USDT/ETH | `ETH-USDT` | `eth_usdt_payouts` | current sidecar `fee_deposit` |

Grither Pay must treat rail enablement as deploy-time capability. If SHKeeper
rail config has `paused=true`, `killSwitch=true`, missing callback endpoint,
missing restore evidence, or missing owned image tag, Grither Pay must keep the
network disabled for customer withdrawals even if its own feature flag is true.

## Observability

First-release Grither Pay payout schedulers must expose Micrometer metrics for
each scheduled operation:

- a run counter tagged by constant `operation` and `result`;
- a processed-row summary tagged by constant `operation`;
- fail-open metric recording, so a registry/exporter failure cannot break payout
  processing.

Grither owns scheduler-level metrics. SHKeeper and the sidecar forks own
execution age, broker queue depth/age, hot-wallet balance, and fee/gas balance
metrics. Allocator errors, confirmation SLA, and cross-system dashboard wiring
remain part of the broader production observability phase.

## Rollout

1. Deploy code with `shkeeper.payouts.enabled=false`.
2. Run migration and verify tables/indexes.
3. Configure SHKeeper consumer `grither-pay` and callback secrets.
4. Enable one rail in staging.
5. Run a low-amount payout through test funds.
6. Verify callback, status polling, outbox retry, and manual review behavior.
7. Enable one production rail. Keep Grither Pay amount validation on the
   existing wallet/business limit layer; do not introduce SHKeeper-side
   amount/day caps.

## Required Tests

Blocking tests before production:

- USDT withdrawal creates wallet row, ledger debit, SHKeeper execution row, and
  submit outbox row in one transaction.
- Crash before DB commit creates no payout and no ledger debit.
- Crash after DB commit but before HTTP submit is recovered by outbox.
- Submit outbox recovery refuses to submit if the linked `WalletWithdrawal` has
  already become terminal; this must end in reconciliation, not a chain payout.
- Duplicate user idempotency key returns existing withdrawal and does not create
  a second SHKeeper external id.
- Submit timeout calls status lookup for the same external id.
- `409 IDEMPOTENCY_CONFLICT` moves to reconciliation and keeps funds reserved.
- Callback HMAC rejects missing, stale, wrong-path/query, and tampered requests;
  valid duplicate callback nonce/event id is classified as replay and handled by
  persistent callback-event dedupe.
- Duplicate callback event id is idempotent.
- Stale callback/status cannot regress state.
- Same-version conflicting callback/status keeps funds reserved and alerts.
- Concurrent callback/status application uses row lock or optimistic
  version/CAS; only one transition can commit.
- Callback event insert, state transition, wallet status change, and ledger
  effect commit atomically.
- `CONFIRMED` completes withdrawal exactly once.
- USDT creation reserves `payout_amount + network_fee`, submits only
  `payout_amount` to SHKeeper, and exposes/admin-displays the split.
- `FAILED_PRE_BROADCAST` refunds the reserved amount exactly once.
- `FAILED_CHAIN_TERMINAL` does not call `failWithdrawal(...)`, does not refund,
  and moves to manual review.
- `RECONCILIATION_REQUIRED` keeps public status non-terminal and funds reserved.
- Manual payout cannot start before evidence is recorded.
- Manual payout negative evidence for another `externalId`, `requestHash`,
  `network`, destination, or amount is rejected.
- Manual payout completion records tx evidence and completes the original
  withdrawal.
- Existing SBP/CARD and Altyn USDT fallback behavior remains unchanged when
  SHKeeper payouts are disabled.
- Altyn provider-status lookup, processing polling, ambiguous polling, and
  orphan recovery only handle Altyn withdrawals; they must not mutate or poll
  `provider=SHKEEPER` withdrawals.

## Non-Goals

- Do not add Kafka, Temporal, or a new withdrawal microservice for the first
  release.
- Do not expose SHKeeper internal states directly to customers unless product UI
  explicitly supports that.
- Do not modify SHKeeper to contain Grither-specific field or method names.
- Do not auto-retry after unsafe broadcast window failures.
