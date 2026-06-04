# USDT Payout Operations Runbook

This runbook covers client USDT withdrawals executed through SHKeeper payout
executions for TRON, TON, and ETH rails. It is intentionally fail-closed:
ambiguous payout states must stay reserved and operator-visible until evidence
proves the original execution cannot still complete.

## Ownership

- Grither Pay owns customer withdrawal state, balance reservation, fee
  accounting, customer-facing status, admin manual-resolution actions, and
  operator action audit rows.
- SHKeeper owns payout execution identity, rail routing, sidecar dispatch,
  sidecar status normalization, callback outbox delivery, and state-transition
  evidence.
- Sidecars own rail-specific signing, broadcast, nonce/seqno/resource guards,
  signed transaction/message evidence, and chain confirmation evidence.

Do not manually send a customer payout from any wallet until the Grither admin
view shows `SAFE_FOR_MANUAL_PAYOUT`. If the evidence is incomplete, leave the
withdrawal reserved and keep the payout in `RECONCILIATION_REQUIRED` or
`MANUAL_REVIEW`.

## Operator Entry Points

Grither Pay admin API:

- `GET /api/admin/wallet/shkeeper-payouts/{payoutExecutionId}`
- `POST /api/admin/wallet/shkeeper-payouts/{payoutExecutionId}/safe-for-manual-payout`
- `POST /api/admin/wallet/shkeeper-payouts/{payoutExecutionId}/manual-payout-pending`
- `POST /api/admin/wallet/shkeeper-payouts/{payoutExecutionId}/complete-manual-payout`

SHKeeper service-consumer API:

- `GET /api/v1/payout-executions/{external_id}`
- `POST /api/v1/payout-executions`

SHKeeper CLI loops inside the SHKeeper pod:

```bash
flask payout-execution-reconciler
flask payout-callback-dispatcher
flask payout-rail-sync
```

## Pre-Enable Release Gate

Before enabling `execution_enabled=true` for any rail, verify that the
deployed SHKeeper and sidecar images were built from the final payout commits.
Do not use a production overlay just because it renders successfully.

Required checks:

1. The SHKeeper, target sidecar, Helm chart, and Grither Pay worktrees used for
   the release are clean after review.
2. The image tag or digest in the environment values maps to the final reviewed
   commit for that repository. Prefer immutable digests (`image@sha256:...`) for
   the final production values.
3. The image exists in the registry and the registry digest matches the release
   evidence in the change ticket.
4. `helm template` with the exact environment values shows one rail, one
   dedicated payout worker, `execution_enabled=false` while paused or
   kill-switched, and no hot-wallet material in ConfigMaps.
5. Only after restore-drill and smoke-payout gates pass may the operator flip the
   Grither feature flag and the SHKeeper rail pause/kill-switch for that one
   rail.

If any repository still has uncommitted payout changes, every existing image tag
must be treated as stale for production payout enablement.

Local source gate before publishing images:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python3 scripts/verify_payout_release_gate.py
```

After all payout release commits are created, update the Helm production overlay
image tags to those commit SHAs and commit the chart update. When the
participating worktrees are clean, rerun the same gate with the clean-worktree
guard before any `docker buildx build --push` command:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python3 scripts/verify_payout_release_gate.py --require-clean
```

The clean gate intentionally fails while local payout changes are still
uncommitted, or when checked-in production overlay images do not match the clean
commit tags. Build tags from commit SHAs only after this command passes.

Core metrics:

- SHKeeper:
  - `shkeeper_payout_execution_count`
  - `shkeeper_payout_non_terminal_oldest_age_seconds`
  - `shkeeper_payout_reconciliation_required_count`
  - `shkeeper_payout_callback_outbox_backlog_count`
  - `shkeeper_payout_callback_outbox_oldest_age_seconds`
  - `shkeeper_payout_failure_count`
  - `shkeeper_payout_dispatch_backlog_count`
  - `shkeeper_payout_dispatch_backlog_oldest_age_seconds`
  - `shkeeper_payout_stuck_execution_count`
  - `shkeeper_payout_stuck_execution_oldest_age_seconds`
  - `shkeeper_payout_confirmation_sla_breach_count`
  - `shkeeper_payout_confirmation_sla_breach_oldest_age_seconds`
  - `shkeeper_payout_ordering_conflict_count`
  - `shkeeper_payout_rail_enabled`

`shkeeper_payout_failure_count` keeps `failure_class` exact but bounds
`error_code` labels. Unknown or non-machine-readable sidecar error strings are
reported as `error_code="OTHER"`; use the payout execution detail/audit record
for the full stored error.

SHKeeper payout metric collection is fail-open and snapshot-safe. If DB
collection fails, `/metrics` still responds and keeps the last successful payout
gauge snapshot instead of clearing critical alert series.

SHKeeper does not own client/business amount limits. Per-withdrawal and daily
limits must be enforced by the upstream product ledger before calling SHKeeper.
SHKeeper validates only technical execution invariants such as auth, rail
enablement, idempotency, supported asset/network, positive canonical USDT
amount, destination, sidecar routing, callback, and audit state.

- Sidecars:
  - `tron_payout_worker_ready`
  - `tron_payout_broker_queue_depth`
  - `tron_payout_broker_queue_oldest_age_seconds`
  - `tron_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}`
  - `tron_payout_fee_wallet_balance{asset="TRX",source_wallet="fee_deposit"}`
  - `tron_payout_failure_count`
  - `tron_payout_request_failed_total`
  - `ton_payout_worker_ready`
  - `ton_payout_broker_queue_depth`
  - `ton_payout_broker_queue_oldest_age_seconds`
  - `ton_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}`
  - `ton_payout_fee_wallet_balance{asset="TON",source_wallet="fee_deposit"}`
  - `ton_payout_failure_count`
  - `ton_payout_request_failed_total`
  - `ethereum_payout_worker_ready`
  - `ethereum_payout_broker_queue_depth`
  - `ethereum_payout_broker_queue_oldest_age_seconds`
  - `ethereum_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}`
  - `ethereum_payout_fee_wallet_balance{asset="ETH",source_wallet="fee_deposit"}`
  - `ethereum_payout_failure_count`
  - `ethereum_payout_request_failed_total`

Sidecar broker queue depth is the Redis `LLEN` of the dedicated payout queue.
`-1` means Redis queue depth could not be read; treat it as an observability or
broker health incident, not as an empty queue.

Sidecar broker queue oldest age is computed from the sidecar-owned
`payout_enqueued_at` Celery task header. Empty queue is `0`; `-1` means Redis or
message-age parsing failed and should be treated as a broker observability
incident.

Sidecar wallet balance gauges report the current payout source wallet
(`fee_deposit` in the first release). Hot-wallet balance is USDT; fee-wallet
balance is the native fee asset for the rail: TRX, TON, or ETH. `-1` means
balance collection failed or the source wallet row is missing; treat it as a
wallet observability incident. Do not read `-1` as a zero balance.

Sidecar failure metrics are diagnostic, not spend controls.
`*_payout_failure_count{state,failure_class,error_code}` is DB-backed terminal
or failed execution evidence. `*_payout_request_failed_total{operation,code}`
counts auth/HMAC and payout-contract rejects for `preflight`, `submit`, and
`status`; use `rate(...[5m])` for failure-rate dashboards. Error-code labels are
bounded to machine-readable values and fall back to `OTHER`, so destination
addresses, provider messages, and secrets are not exported as label values.

Sidecar DB-backed payout execution/callback gauges also keep the last successful
snapshot when DB collection fails. Worker readiness, Redis queue depth/age, and
wallet balance gauges still refresh during that failure path.
- Grither Pay:
  - `shkeeper_payout_scheduler_runs_total`
  - `shkeeper_payout_scheduler_processed`

Core alerts:

- `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED`
- stuck execution alert from `shkeeper_payout_stuck_execution_count`
- dispatch backlog alert from `shkeeper_payout_dispatch_backlog_count`
- rail disabled/drift alert from `shkeeper_payout_rail_enabled`
- enabled-rail disabled/missing, sidecar worker unavailable, and broker queue
  backlog/age alerts from the optional Helm `PrometheusRule`
- wallet-balance metric unavailable alerts from the optional Helm
  `PrometheusRule`
- optional low hot-wallet and fee-wallet alerts from the optional Helm
  `PrometheusRule` when `hotWalletMinimumBalance` or `feeWalletMinimumBalance`
  is explicitly configured for a rail
- confirmation SLA breach alert from
  `shkeeper_payout_confirmation_sla_breach_count`, based on `broadcasted_at`
- ordering conflict alert from `shkeeper_payout_ordering_conflict_count`
- per-rail allocator/lock alert from bounded sidecar
  `*_payout_failure_count{error_code=~".*(LOCK|NONCE|SEQNO|ALLOCATOR).*"}`
- stale manual-review alert from `ShKeeperPayoutManualReviewMonitor`
- callback backlog alert from `ShKeeperPayoutCallbackBacklogMonitor`

## State Meaning

`FAILED_PRE_BROADCAST` means no unsafe broadcast window was reached. Grither may
refund/release the full reserved amount automatically.

`ENQUEUEING`, `ENQUEUED`, and `BROADCAST` mean the unsafe broadcast window may be
open. Do not create a second payout or manual payout.

`FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`, and `MANUAL_REVIEW` keep
funds reserved. Operators must inspect sidecar and chain evidence.

`SAFE_FOR_MANUAL_PAYOUT` means structured negative evidence has been recorded
and the original automatic execution is proven unable to complete.

`MANUAL_PAYOUT_PENDING` means an operator has started manual transfer. Complete
the action only after recording manual txid or message hash.

`MANUAL_PAYOUT_COMPLETED` means Grither has completed the original withdrawal
with manual payout evidence. Do not create another transfer.

## First Triage

1. Find the Grither withdrawal by public number and open the SHKeeper payout
   detail in the admin API.
2. Confirm these fields before action:
   - `walletWithdrawalId`
   - Grither withdrawal `publicNumber`
   - `externalId`
   - SHKeeper `executionId`
   - optional `sidecarExecutionId`
   - `asset`, `network`, `payoutAmount`, `networkFee`, `reservedAmount`
   - `destination`
   - `providerState`, `manualResolutionState`, `publicWithdrawalStatus`
   - `txids`, `messageHashes`
   - `failureClass`, `errorCode`, `errorMessage`
   - `nextSafeAction`
3. Query SHKeeper status by `externalId` and compare state, event version,
   `state_transition_id`, sidecar fields, txids/message hashes, and
   `reconciliation_required`.
4. Check SHKeeper `/metrics` and Grither scheduler metrics for stale age,
   callback backlog, scheduler failures, and reconciliation count.
5. Check Grither `shkeeper_payout_manual_resolution_audit` for prior operator
   actions before changing state.

Useful SQL in Grither:

```sql
SELECT *
FROM shkeeper_payout_executions
WHERE external_id = :external_id;

SELECT *
FROM shkeeper_payout_callback_events
WHERE external_id = :external_id
ORDER BY event_version, received_at;

SELECT *
FROM shkeeper_payout_manual_resolution_audit
WHERE payout_execution_id = :payout_execution_id
ORDER BY created_at;
```

Useful SQL in SHKeeper:

```sql
SELECT *
FROM payout_execution
WHERE consumer = :consumer
  AND external_id = :external_id;

SELECT *
FROM payout_callback_event
WHERE external_id = :external_id
ORDER BY event_version, received_at;
```

## Reconciliation Required

Symptoms:

- SHKeeper state is `RECONCILIATION_REQUIRED`.
- Grither state application moved the local payout to `MANUAL_REVIEW` or
  equivalent reserved/manual state.
- Alert `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED` fired.

Actions:

1. Do not retry the withdrawal from Grither and do not resubmit the same payout
   manually.
2. Compare Grither local row, SHKeeper row, sidecar status, and callback events.
3. If SHKeeper has `txids` or `messageHashes`, inspect chain finality first.
4. If the sidecar has a signed transaction/message artifact, collect rail-specific
   negative evidence before manual payout.
5. If evidence proves the original execution cannot complete, use the Grither
   admin API to mark `safe-for-manual-payout`.
6. If evidence is incomplete, keep the state reserved and add an operator note in
   the incident ticket. Do not manually pay.

## Worker Unavailable

Symptoms:

- SHKeeper `error_code` such as `SIDECAR_PREFLIGHT_UNAVAILABLE`,
  `SIDECAR_SUBMIT_REJECTED`, `SIDECAR_SUBMIT_UNKNOWN`, or
  `PAYOUT_DISPATCH_EXCEPTION`.
- Non-terminal execution age increases.
- Grither submit outbox retries are exhausted or delayed.

Actions:

1. Confirm whether the unsafe broadcast window was reached.
2. For `FAILED_PRE_BROADCAST`, no manual chain action is required; Grither can
   release according to normal failed-pre-broadcast handling.
3. For `ENQUEUEING`, `ENQUEUED`, `BROADCAST`, or `RECONCILIATION_REQUIRED`, keep
   funds reserved.
4. Restart or scale only the failed worker/sidecar pod; do not delete execution
   DB rows or queue rows.
5. Run one SHKeeper reconciler pass if needed:

```bash
PAYOUT_EXECUTION_RECONCILER_ONCE=true flask payout-execution-reconciler
PAYOUT_CALLBACK_DISPATCHER_ONCE=true flask payout-callback-dispatcher
```

6. If sidecar state is still unavailable after restart, continue with
   reconciliation-required evidence collection.

## Low Balance Or Gas

Symptoms:

- Sidecar preflight fails for source-wallet USDT, native gas, TRON bandwidth or
  energy, TON balance, or ETH gas.
- Execution becomes `FAILED_PRE_BROADCAST` when failure is before signing or
  broadcast.

Actions:

1. Top up the configured source wallet only through the approved treasury
   process.
2. Do not change the payout source wallet for an existing execution.
3. Re-enable or resume rails only after low-balance alerts clear and a low-value
   smoke payout passes.
4. If the execution reached the unsafe window, do not retry automatically; use
   reconciliation flow.

## Provider Or Callback Failure

Symptoms:

- SHKeeper callback outbox backlog grows.
- Grither callback event exists but `applied_at` is null.
- Grither rejected callback due HMAC, event conflict, or stale event version.

Actions:

1. Verify callback endpoint URL, key id, secret, timestamp skew, and canonical
   path/query.
2. Do not regenerate callback payloads. SHKeeper must resend the stored raw
   payload and stored signature base.
3. Dispatch older undelivered callback events before newer events for the same
   execution.
4. If Grither has already applied a newer conflicting state, keep the payout in
   reconciliation and do not force-apply the old event.

## Ambiguous Broadcast

Manual payout is forbidden while any of these are true:

- signed TRON transaction may still be accepted within its ref-block/expiration
  window;
- signed TON BOC/message may still execute before `valid_until` or without
  resolved seqno evidence;
- signed ETH raw transaction can still be rebroadcast because the nonce is unused
  or not finalized by another same-nonce transaction;
- chain/indexer queries do not cover source wallet, destination, amount, contract
  or Jetton master, and finality range;
- any known txid/message hash is pending, confirmed, or not checked.

Keep the payout reserved and visible. The customer-facing withdrawal should stay
non-terminal until evidence supports a safe operator action.

## Paused Rail

When a rail is paused or disabled:

1. New Grither withdrawals for that rail must fail before reservation or remain
   disabled by feature flag.
2. Existing SHKeeper executions continue through reconciliation and callback
   handling. Do not delete in-flight rows.
3. Existing `RECONCILIATION_REQUIRED` or `MANUAL_REVIEW` payouts stay reserved.
4. Resume one rail at a time only after the upstream product ledger/feature flags
   are configured and a low-value smoke payout succeeds.

## Manual Payout Evidence

The Grither `safe-for-manual-payout` request must include:

- `reason`
- `negativeEvidenceConfirmed = true`
- structured `evidence` JSON with common fields:
  - `originalExecutionWillNotComplete = true`
  - `noMatchingTransfer = true`
  - `sourceWallet`
  - `destination`
  - `amount`
  - `finalizedBlockRange`
  - `statusEvidence`
  - `sidecarState`
  - `nodeQueryEvidence`
  - `knownTxidsOrMessageHashes`

TRON evidence must also include:

- `refBlockExpired = true`
- `trc20TransferEventQuery`
- `refBlockEvidence`
- `resourceState`

TON evidence must also include:

- `validUntilExpired = true`
- `seqnoResolved = true`
- `signedBocOrMessageHash`
- `sourceSeqno`
- `sourceWalletHistory`
- `jettonTransferHistory`
- `masterchainRange`

ETH evidence must also include:

- `nonceConsumedByFinalizedSameNonceTx = true`
- `finalizedSameNonceTxHash`
- `chainId`
- `nonce`
- `erc20TransferLogQuery`

After evidence is accepted:

1. Move to `MANUAL_PAYOUT_PENDING` before sending manual funds.
2. Send the manual transfer only through the approved operator wallet flow.
3. Complete with `manualTxHash` or `manualMessageHash`.
4. Confirm `MANUAL_PAYOUT_COMPLETED`, public withdrawal `COMPLETED`, and an audit
   row with action `MANUAL_PAYOUT_COMPLETED`.

## Audit Expectations

SHKeeper stores state-transition evidence in `payout_callback_event` with durable
raw payload, payload hash, event version, and `state_transition_id`.

Grither stores operator action evidence in
`shkeeper_payout_manual_resolution_audit` with the operator id, previous and new
provider/manual states, reason, raw evidence, manual tx/message hashes, request
hash, sidecar payload hash, state transition id, and timestamp.

Audit rows must not be edited to correct mistakes. Add a new operator action or
incident note instead.
