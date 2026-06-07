# TRON USDT payout destination activation design

## Summary

TRON USDT payouts should automatically activate an unactivated destination address through ProfeeX before broadcasting the token transfer. Activation is a submit/worker side effect only. Read-only quote and preflight endpoints remain diagnostic and must not spend funds.

The payout must fail closed: if ProfeeX activation is unavailable or still processing, no USDT transaction is broadcast and the execution remains retryable. We do not fall back to activating via our own fee or main wallet in the first version.

This design also includes a small Grither guard: same-version, pre-broadcast, transient diagnostic changes from SHKeeper must not move a payout into `RECONCILIATION_REQUIRED`.

## Goals

- Activate unactivated TRON payout destinations automatically through ProfeeX.
- Keep `/calc-tx-fee` and payout preflight read-only.
- Avoid duplicate activation spend for concurrent payouts to the same destination.
- Preserve fail-closed behavior when ProfeeX is unavailable.
- Prevent transient preflight errors from creating false Grither reconciliation incidents.
- Keep the change scoped to TRON USDT payouts.

## Non-goals

- Do not activate destinations through our own fee wallet or main wallet.
- Do not move TRON activation orchestration into Grither.
- Do not change ETH, TON, TRX, or deposit sweep behavior.
- Do not treat pre-broadcast provider unavailability as broadcast ambiguity.

## Current behavior

`tron-shkeeper` already detects an unactivated TRON destination during resource quotation. The ProfeeX fee estimate returns `is_new_address=true`; `payout_resources.py` maps that to:

- `activation_required=true`
- `blocking_code=DESTINATION_NOT_ACTIVATED`
- `submit_ready=false`

This blocks sidecar preflight. SHKeeper core currently receives a sidecar `503` for preflight failures and records `SIDECAR_PREFLIGHT_UNAVAILABLE` on the existing payout execution without bumping `event_version`.

Grither currently treats a same-version observation with changed error evidence as `STATE_VERSION_CONFLICT`, even when the payout is still `CREATED` and has no broadcast evidence. In the observed case, this left withdrawal `91205114` in user-visible processing after the provider payout completed.

## Design decision

Activation belongs in the TRON sidecar worker submit path.

Readiness endpoints stay read-only:

- `/USDT/calc-tx-fee/<amount>`
- `/USDT/payout/preflight`
- `/USDT/payout-executions/<id>/preflight`

The worker performs activation immediately before resource provisioning and USDT transfer, under the payout execution submit lock.

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

The preferred API is ProfeeX `/api/v1/activation/activate`, with task status polling through the documented status endpoint if activation returns a `task_id`.

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

The first implementation may use a process-local lock if worker concurrency is single-pod in the deployment, but the design should allow a durable lock or cache if multiple workers are introduced.

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

Terminal pre-broadcast outcomes:

- invalid TRON destination
- ProfeeX `INVALID_ADDRESS`
- invalid activation parameters
- explicit policy rejection

Retryable outcomes must not broadcast USDT and must not create reconciliation ambiguity. Terminal outcomes may fail the payout before broadcast with a clear reason.

## SHKeeper core behavior

SHKeeper core should preserve the distinction between:

- retryable sidecar/preflight diagnostics before broadcast
- terminal pre-broadcast failures
- actual broadcast ambiguity

For transient preflight unavailability, core should avoid changing observable provider evidence on the same `event_version` in a way that downstream consumers treat as a new state transition. If core needs to expose diagnostics, they should be clearly non-evidence diagnostics or should follow normal event version semantics.

If the sidecar returns structured error details, core should preserve the structured code where possible instead of collapsing every `5xx` into `SIDECAR_PREFLIGHT_UNAVAILABLE`. This is useful for operator visibility, but it must not make pre-broadcast transient failures terminal.

## Grither guard

Grither must not mark `RECONCILIATION_REQUIRED` for a same-version observation when all of these are true:

- current and incoming state are `CREATED`
- no `sidecar_execution_id`
- no `txids`
- no `message_hashes`
- no `broadcasted_at`
- no terminal provider state
- the only meaningful difference is transient diagnostic error fields, such as `SIDECAR_PREFLIGHT_UNAVAILABLE`

In that case, Grither should treat the observation as idempotent or update diagnostic fields without changing the public withdrawal into a manual reconciliation state.

Real conflicts still require reconciliation:

- different tx evidence on the same event version
- broadcasted state drift
- terminal provider conflict
- sidecar execution identity conflict

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

Auto-activation should be controlled by an environment flag and enabled only for TRON USDT payouts.

Suggested flag:

```text
TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true|false
```

Default can be `false` for the first deploy if rollout safety is preferred, then enabled in dev after tests pass.

## Testing

`tron-shkeeper` tests:

- unactivated destination quote still reports `activation_required=true`
- `/calc-tx-fee` does not call activation
- preflight does not call activation
- submit/worker calls activation before USDT transfer
- successful activation repeats quote and continues to transfer
- retryable ProfeeX activation error exits before broadcast
- terminal activation error exits before broadcast with a clear code
- duplicate/in-progress activation does not create duplicate spend

`shkeeper.io` core tests:

- sidecar preflight `503` before broadcast remains retryable and does not imply broadcast ambiguity
- structured sidecar preflight error details remain diagnostics unless they are terminal

`grither-pay` tests:

- same-version `CREATED` transient diagnostic update is not `STATE_VERSION_CONFLICT`
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
- ProfeeX temporary failure does not spend our own TRX and does not broadcast USDT.
- ProfeeX temporary failure does not force Grither into `RECONCILIATION_REQUIRED`.
- Completed provider payouts update Grither automatically through the normal callback/status path.
