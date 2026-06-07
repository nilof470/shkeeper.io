# TRON USDT payout destination activation design

## Summary

TRON USDT payouts submitted through the payout-execution API should automatically activate an unactivated destination address through ProfeeX before broadcasting the token transfer. Activation is a submit/worker side effect only. Read-only quote and preflight endpoints remain diagnostic and must not spend funds.

The payout must fail closed: if ProfeeX activation is unavailable or still processing, no USDT transaction is broadcast and the execution remains retryable. We do not fall back to activating via our own fee or main wallet in the first version.

This design also includes a small Grither guard: same-version, pre-broadcast, transient diagnostic changes from SHKeeper must not move a payout into `RECONCILIATION_REQUIRED`.

## Goals

- Activate unactivated TRON payout destinations automatically through ProfeeX.
- Keep `/calc-tx-fee` and payout preflight read-only.
- Avoid duplicate activation spend for concurrent payouts to the same destination.
- Preserve fail-closed behavior when ProfeeX is unavailable.
- Prevent transient preflight errors from creating false Grither reconciliation incidents.
- Keep the change scoped to TRON USDT payouts submitted through the payout-execution API.

## Non-goals

- Do not activate destinations through our own fee wallet or main wallet.
- Do not move TRON activation orchestration into Grither.
- Do not change ETH, TON, TRX, or deposit sweep behavior.
- Do not treat pre-broadcast provider unavailability as broadcast ambiguity.
- Do not auto-activate legacy `/payout` or `/multipayout` TRON USDT payouts in v1.

## Current behavior

`tron-shkeeper` already detects an unactivated TRON destination during resource quotation. The ProfeeX fee estimate returns `is_new_address=true`; `payout_resources.py` maps that to:

- `activation_required=true`
- `blocking_code=DESTINATION_NOT_ACTIVATED`
- `submit_ready=false`

This blocks sidecar preflight. SHKeeper core currently receives a sidecar `503` for preflight failures and records `SIDECAR_PREFLIGHT_UNAVAILABLE` on the existing payout execution without bumping `event_version`.

Grither currently treats a same-version observation with changed error evidence as `STATE_VERSION_CONFLICT`, even when the payout is still `CREATED` and has no broadcast evidence. In the observed case, this left withdrawal `91205114` in user-visible processing after the provider payout completed.

## Design decision

Activation belongs in the TRON sidecar worker submit path for the payout-execution API only.

Readiness endpoints stay read-only:

- `/USDT/calc-tx-fee/<amount>`
- `/USDT/payout/preflight`
- `/USDT/payout-executions/<id>/preflight`

The worker performs activation immediately before resource provisioning and USDT transfer, under the payout execution submit lock.

Legacy `/payout` and `/multipayout` stay unchanged in v1. They may call the shared resource quote/provisioning helper, but destination auto-activation must be gated off unless the caller explicitly opts into payout-execution activation.

## Preflight and submit eligibility

The current preflight path blocks unactivated destinations before submit. That would prevent worker-side activation from ever running. Under `TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true`, payout-execution preflight must change only for the `DESTINATION_NOT_ACTIVATED` diagnostic:

- `/USDT/payout-executions/<id>/preflight` remains read-only.
- It still returns the resource quote with `activation_required=true` and `blocking_code=DESTINATION_NOT_ACTIVATED`.
- For payout-execution requests, that diagnostic is submit-eligible and must return preflight OK so SHKeeper core can transition to `PREFLIGHTED`.
- Other non-submit-ready conditions remain blocking unless explicitly classified as submit-eligible diagnostics.
- `/USDT/calc-tx-fee/<amount>` remains diagnostic and should continue to expose `submit_ready=false` for operator UI, because it is not part of the SHKeeper core state machine.
- Legacy `/payout/preflight` must keep the existing hard-block behavior in v1.

Implementation should make this explicit with a caller option such as `allow_destination_auto_activation=True` in payout-execution preflight, rather than changing all quote consumers.

## Worker flow

For a TRON USDT payout submit:

1. Canonicalize and validate the payout request.
2. Estimate the USDT transfer fee through ProfeeX.
3. If `is_new_address=false`, continue with the existing bandwidth and energy flow.
4. If `is_new_address=true`, call ProfeeX address activation for the destination.
5. Wait until activation reaches a successful active/completed status.
6. Re-run the ProfeeX fee estimate or equivalent chain resource check.
7. If the address is now active, provision bandwidth and energy as today.
8. Broadcast the USDT transfer.
9. Report the txid through the existing payout execution status contract.

If activation does not reach success, the worker exits before broadcast.

Retryable activation/resource failures must not transition the sidecar execution to `FAILED_PRE_BROADCAST`. The execution should return to a re-enqueueable pre-broadcast state without unsafe side effects, such as `RECEIVED` or `VALIDATED`, with `failure_class=TRANSIENT`, a retryable error code, and no tx evidence. The next reconciler retry can then submit/enqueue it again.

Only terminal activation/resource failures should become `FAILED_PRE_BROADCAST`.

## ProfeeX integration

Extend the existing `ProfeeXProvider` in `tron-shkeeper` with activation support:

- `activate_address(destination)`
- `wait_for_activation(task_id, destination)`
- `ensure_destination_activated(destination)`

The implementation should reuse existing ProfeeX client conventions:

- same auth and base URL settings
- same structured error taxonomy where possible
- same polling style as energy and bandwidth orders
- same logging hygiene for API keys and sensitive values

Use ProfeeX `POST /api/v1/activation/activate` with query parameters:

- `address=<destination>`
- `currency=<config.PROFEEX.currency>`, defaulting to `TRX`

Expected responses:

- `202`: accepted; response contains `task_id`, `target`, optional `status`, and optional balances.
- `409`: duplicate request or already activated. The implementation must immediately re-run the fee estimate or chain account check. If `is_new_address=false`, treat as success. If the address is still unactivated, treat as retryable duplicate/in-progress.
- `422`: inspect the structured error body if available. `INVALID_ADDRESS` and `INVALID_PARAMETERS` are terminal. Insufficient balance/provider errors are retryable operational failures unless ProfeeX documents them as terminal.
- `503` or request timeout: retryable.

Activation task status should be polled through `/api/v1/delegation/status/<task_id>` unless ProfeeX exposes a dedicated activation status endpoint. Activation success statuses are `ACTIVE` and `COMPLETED`; pending statuses are `QUEUED`, `PENDING`, and `PROCESSING`; terminal failure statuses are `FAILED`, `CANCELLED`, and `unknown`.

Do not reuse the existing `_wait_until_active` unchanged for activation, because it currently treats `COMPLETED` as a failure status for resource delegation.

## Idempotency

Activation idempotency is destination based, not payout based.

The logical operation key is:

```text
tron-destination-activation:<destination>
```

Requirements:

- Concurrent payouts to the same new destination must not buy multiple activations.
- A retry after partial activation must first re-check whether the destination is already active.
- ProfeeX duplicate or already-in-progress responses for the same destination should be treated as retryable/in-progress unless the API documents a terminal failure.
- Once ProfeeX fee estimate reports `is_new_address=false`, no activation call is made.

The first implementation must use distributed idempotency. A process-local lock is not enough.

Use a destination-scoped Redis lock:

```text
tron_usdt_destination_activation:<destination>
```

Also store a durable activation record or Redis key under the same logical destination key. It should include:

- destination
- ProfeeX `task_id` when present
- status: `PENDING`, `PROCESSING`, `ACTIVE`, `COMPLETED`, `FAILED`
- last ProfeeX error code/message
- created/updated timestamps
- expiration suitable for cleanup after activation is confirmed

The worker algorithm is:

1. Re-check fee estimate before acquiring the activation lock.
2. If active, continue.
3. Acquire the destination activation lock.
4. Re-check fee estimate again inside the lock.
5. If an in-progress activation record exists, poll/resume that task instead of creating a new one.
6. If no usable activation exists, call ProfeeX activation and persist the `task_id`.
7. Poll until success, retryable timeout, or terminal failure.
8. Re-check fee estimate before releasing the lock.

This protects against concurrent workers and crash/retry after ProfeeX accepted activation but before local confirmation.

## Error taxonomy

Retryable pre-broadcast outcomes:

- ProfeeX HTTP `503`
- ProfeeX timeout
- ProfeeX `SERVICE_UNAVAILABLE`
- ProfeeX `REQUEST_TIMEOUT`
- rate limit
- duplicate/in-progress activation for the same destination
- activation task still pending or processing
- temporary TRON node/account lookup failure
- ProfeeX `INSUFFICIENT_BALANCE` for activation funding, unless policy later decides it should be terminal

Terminal pre-broadcast outcomes:

- invalid TRON destination
- ProfeeX `INVALID_ADDRESS`
- invalid activation parameters
- explicit policy rejection

Retryable outcomes must not broadcast USDT and must not create reconciliation ambiguity. Terminal outcomes may fail the payout before broadcast with a clear reason.

Retryable sidecar error codes should be explicitly allowlisted, for example:

- `PAYOUT_DESTINATION_ACTIVATION_PENDING`
- `PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE`
- `PAYOUT_DESTINATION_ACTIVATION_DUPLICATE`
- `PAYOUT_DESTINATION_ACTIVATION_TIMEOUT`
- `PAYOUT_RESOURCE_PROVIDER_UNAVAILABLE`

`PayoutExecutionStore._mark_failed_or_reconciliation` should treat these codes like `PAYOUT_RESOURCE_LOCK_UNAVAILABLE`: no unsafe side effect means return to a re-enqueueable pre-broadcast state, set transient diagnostics, and keep `reconciliation_required=0`.

## SHKeeper core behavior

SHKeeper core should preserve the distinction between:

- retryable sidecar/preflight diagnostics before broadcast
- terminal pre-broadcast failures
- actual broadcast ambiguity

For transient preflight unavailability, core should avoid changing observable provider evidence on the same `event_version` in a way that downstream consumers treat as a new state transition. If core needs to expose diagnostics, they should be clearly non-evidence diagnostics or should follow normal event version semantics.

If the sidecar returns structured error details, core should preserve the structured code where possible instead of collapsing every `5xx` into `SIDECAR_PREFLIGHT_UNAVAILABLE`. This requires changing `HttpPayoutSidecarClient.preflight()` so `SidecarStatusUnavailable` can carry the parsed response payload and HTTP status, or changing the TRON sidecar to return machine-readable non-5xx diagnostics for submit-eligible preflight cases.

For `DESTINATION_NOT_ACTIVATED` under auto-activation, the preferred behavior is sidecar preflight `200 OK` with diagnostic quote data. Core then transitions to `PREFLIGHTED` and submits normally.

## Grither guard

Grither must not mark `RECONCILIATION_REQUIRED` for a same-version observation when all of these are true:

- current and incoming state are `CREATED`
- current and incoming event version are equal
- current and incoming state transition id are equal
- current and incoming request hash are equal
- current and incoming sidecar payload hash are equal
- no `sidecar_execution_id`
- no `txids`
- no `message_hashes`
- no `broadcasted_at`
- no terminal provider state
- the only meaningful difference is allowlisted transient diagnostic error fields

Allowed transient diagnostic codes:

- `SIDECAR_PREFLIGHT_UNAVAILABLE`
- `DESTINATION_NOT_ACTIVATED`
- `PROFEEX_ESTIMATE_UNAVAILABLE`
- `PAYOUT_DESTINATION_ACTIVATION_PENDING`
- `PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE`
- `PAYOUT_RESOURCE_PROVIDER_UNAVAILABLE`

In that case, Grither should treat the observation as idempotent or update diagnostic fields without changing the public withdrawal into a manual reconciliation state.

Real conflicts still require reconciliation:

- different tx evidence on the same event version
- broadcasted state drift
- terminal provider conflict
- sidecar execution identity conflict
- changed request hash, sidecar payload hash, or state transition id on the same version

## Observability

Add activation-specific telemetry in `tron-shkeeper`:

- `tron_payout_destination_activation_total{result=success|retryable_error|terminal_error}`
- `tron_payout_destination_activation_duration_seconds`

Logs should include:

- payout execution id when available
- destination
- activation task id
- activation result
- ProfeeX structured error code
- retryable vs terminal classification

Logs must not include API keys, private keys, or wallet secrets.

## Feature flag

Auto-activation should be controlled by an environment flag and enabled only for TRON USDT payouts submitted through the payout-execution API.

Suggested flag:

```text
TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true|false
```

Default can be `false` for the first deploy if rollout safety is preferred, then enabled in dev after tests pass.

Flag matrix:

- `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=false`: no ProfeeX resource provisioning and no destination auto-activation.
- `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` and `TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=false`: existing behavior; unactivated destination blocks preflight/submit.
- `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` and `TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true`: payout-execution preflight is submit-eligible for `DESTINATION_NOT_ACTIVATED`, and payout-execution submit may activate through ProfeeX.

Config validation should require `PROFEEX` when auto-activation is enabled. Helm values should expose the new flag with a dev default chosen during rollout.

## Testing

`tron-shkeeper` tests:

- unactivated destination quote still reports `activation_required=true`
- `/calc-tx-fee` does not call activation
- payout-execution preflight does not call activation but returns OK for `DESTINATION_NOT_ACTIVATED` when auto-activation is enabled
- legacy preflight keeps blocking `DESTINATION_NOT_ACTIVATED`
- submit/worker calls activation before USDT transfer
- successful activation repeats quote and continues to transfer
- retryable ProfeeX activation error exits before broadcast and leaves the execution re-enqueueable
- terminal activation error exits before broadcast with a clear code
- duplicate/in-progress activation does not create duplicate spend
- crash/retry after persisted activation `task_id` resumes polling rather than creating a new activation
- legacy `/payout` and `/multipayout` do not auto-activate in v1

`shkeeper.io` core tests:

- `DESTINATION_NOT_ACTIVATED` payout-execution preflight can transition to `PREFLIGHTED` when auto-activation is enabled
- sidecar preflight `503` before broadcast remains retryable and does not imply broadcast ambiguity
- structured sidecar preflight error details remain diagnostics unless they are terminal

`grither-pay` tests:

- same-version `CREATED` transient diagnostic update is not `STATE_VERSION_CONFLICT`
- same-version `CREATED` update with changed request hash, sidecar payload hash, or transition id still becomes `RECONCILIATION_REQUIRED`
- same-version conflicting tx evidence still becomes `RECONCILIATION_REQUIRED`
- same-version sidecar execution identity conflict still becomes `RECONCILIATION_REQUIRED`

## Rollout

1. Implement behind the TRON USDT auto-activation flag.
2. Deploy to dev with the flag enabled.
3. Create a payout to a fresh TRON destination.
4. Verify ProfeeX activation task is created once.
5. Verify USDT is broadcast only after destination activation.
6. Verify SHKeeper reaches broadcast/confirmed state.
7. Verify Grither withdrawal reaches completed state without manual admin recovery.
8. Watch activation metrics and reconciliation alerts.

## Acceptance criteria

- A TRON USDT payout to a new destination can complete without manual address activation.
- Preflight and fee calculation remain read-only.
- Payout-execution preflight is submit-eligible for `DESTINATION_NOT_ACTIVATED` under the auto-activation flag.
- ProfeeX temporary failure does not spend our own TRX and does not broadcast USDT.
- ProfeeX temporary failure leaves the sidecar execution retryable rather than failed/refunded.
- ProfeeX temporary failure does not force Grither into `RECONCILIATION_REQUIRED`.
- Concurrent or retried payouts to the same new destination do not buy duplicate activation.
- Legacy `/payout` and `/multipayout` behavior is unchanged in v1.
- Completed provider payouts update Grither automatically through the normal callback/status path.
