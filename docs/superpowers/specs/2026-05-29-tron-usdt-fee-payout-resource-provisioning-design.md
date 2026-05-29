# TRON USDT Fee-Deposit Payout Resource Provisioning

Date: 2026-05-29
Status: Approved design, pending implementation plan
Target implementation repos:
- `../tron-shkeeper`
- `shkeeper.io`
Source docs and code:
- `docs/PROFEEX_API_DOCS_EN.md`
- `docs/openapi-profeex.json`
- `../tron-shkeeper/app/api/payout.py`
- `../tron-shkeeper/app/tasks.py`
- `../tron-shkeeper/app/wallet.py`
- `../tron-shkeeper/app/resource_providers/profeex.py`
- `shkeeper/api_v1.py`
- `shkeeper/auth.py`
- `shkeeper/services/payout_service.py`
- `shkeeper/services/webhook_hmac.py`
- `shkeeper/modules/classes/tron_token.py`
- `shkeeper/templates/wallet/payout_tron.j2`

## Context

TRON TRC-20 sweep already provisions energy and bandwidth before moving funds
from client onetime wallets to the main wallet. The single payout path does not
use this resource-provider flow. It sends USDT from the `fee_deposit` wallet and
currently depends on a static `TX_FEE` estimate and the wallet's TRX balance.

The desired payout flow is for client withdrawals and manual admin payouts to
send USDT TRC-20 from `fee_deposit` without burning TRX for normal transaction
fees. Energy and bandwidth should be rented from ProfeeX when the wallet does
not already have enough resources.

Client withdrawals will be initiated by Grither Pay. Grither Pay is responsible
for wallet ledger behavior: balance holds, preventing double withdrawals,
terminal failure handling, and deciding when a user may submit a new withdrawal.
SHKeeper remains the payout executor.

## Goals

- Add resource provisioning to USDT TRC-20 single payout from `fee_deposit`.
- Cover both admin manual payout and client withdrawal payout, because both map
  to a single payout from the same fee wallet.
- Show an admin estimate based on ProfeeX pricing instead of only static TRX
  burn cost.
- Block payout submission when resource estimation cannot be completed or the
  system already knows provisioning cannot be attempted.
- Never broadcast the USDT transaction until resources are confirmed active on
  chain.
- Keep the existing `/api/v1/<crypto_name>/payout` endpoint for both admin
  manual payouts and Grither Pay server-to-server payouts.
- Add a safe server-to-server auth path for Grither Pay without requiring an
  application-level IP allowlist, because the admin UI must remain publicly
  reachable.
- Keep the change additive and narrow because this codebase is a fork.

## Non-goals

- No multipayout changes in this phase.
- No native TRX payout changes.
- No USDC or other token expansion in this phase.
- No buffer strategy that intentionally rents resources for five future
  payouts.
- No ProfeeX webhook integration in this phase.
- No broad refactor of wallet signing or transaction broadcast code.
- No SHKeeper-side wallet ledger, balance reservation, or double-withdrawal
  state machine for Grither Pay withdrawals.
- No automatic SHKeeper retry loop for failed ProfeeX provisioning in this
  phase. Temporary provider failures should fail the payout attempt cleanly so
  Grither Pay can release/restore state and let the user initiate another
  withdrawal.
- No application-level IP allowlist in this phase. Network restrictions may be
  applied at Yandex Cloud/security-group/ingress level, but they are not the
  main safety control for this endpoint.

## Selected Strategy

Use per-payout resource readiness with conditional provider orders:

1. Each single payout performs its own resource readiness check.
2. The sidecar checks current `fee_deposit` energy and bandwidth on chain.
3. A ProfeeX order is created only when the current resource balance has a
   deficit for this payout.
4. After ProfeeX reports `ACTIVE`, the sidecar rechecks the on-chain resources.
5. The USDT transaction is broadcast only after the recheck confirms enough
   resources.

This is not a resource buffer strategy. The implementation does not buy
resources in advance for a planned batch of future payouts. It also does not
create a ProfeeX order when previous delegation, manual delegation, or recovered
resources already make `fee_deposit` ready for the current payout.

For Grither Pay, one withdrawal attempt maps to one SHKeeper payout attempt.
If SHKeeper fails before broadcast because resources or ProfeeX are not ready,
the failure is terminal for that SHKeeper attempt. Grither Pay may allow the
user to create a new withdrawal attempt after it restores its own wallet state.

## Resource Sizing

The resource target is the `fee_deposit` TRON address, because that wallet signs
and broadcasts the outgoing USDT transfer.

Energy sizing:

- Estimate energy for the exact USDT transfer where possible:
  `fee_deposit -> destination`, `amount`.
- ProfeeX `GET /delegation/fee` may be used as a provider-side quote signal for
  USDT transfer energy, especially for destination/new-address behavior.
- The implementation must still use on-chain resource reads as the final
  readiness check, because provider quotes do not prove that delegated resources
  are currently usable by `fee_deposit`.
- If energy estimation fails, the payout request is not submitted or broadcast.

Bandwidth sizing:

- Use the existing TRC-20 transfer bandwidth constant from sidecar config,
  currently `BANDWIDTH_PER_TRC20_TRANSFER_CALL`.
- Check free bandwidth on `fee_deposit`.
- Rent bandwidth only when available bandwidth is below the required amount.

Order sizing:

- Order only the required deficit, adjusted to ProfeeX minimum and maximum
  volume constraints.
- If ProfeeX minimum volume is greater than the exact deficit, request the
  minimum valid ProfeeX volume.
- Do not multiply the order amount by projected future payout count.

## Queue Model

Use a dedicated Celery processing lane for USDT single payouts from
`fee_deposit`.

The sidecar should route this work to the dedicated queue
`tron_usdt_fee_payouts`, and deployment should run exactly one worker slot for
that queue. The goal is sequential processing for payouts that spend resources
from the same `fee_deposit` wallet.

Example timeline:

```text
t=0.0s   payout #1 is accepted and starts processing
t=0.5s   payout #2 is accepted and waits in the queue
t=1.0s   payout #3 is accepted and waits in the queue
t=8.0s   payout #1 finishes
t=8.1s   payout #2 starts, then rechecks resources from current chain state
t=12.0s  payout #2 finishes
t=12.1s  payout #3 starts
```

The queue is the primary ordering mechanism. A Redis lock on
`fee_deposit + USDT` may be added as a defensive guard against deployment
misconfiguration, but the design should not rely on ad hoc lock contention as
the main queue.

When a payout reaches the front of the queue, it always recomputes resource
availability. This means payout #2 can use remaining energy from payout #1 if
it is still available, and it will create a new ProfeeX order only if there is
still a deficit.

## ProfeeX Status Handling

ProfeeX resource orders are asynchronous:

- `POST /delegation/buyenergy` and `POST /delegation/buybandwidth` return
  `202 Accepted` with `task_id`.
- The sidecar must poll `GET /delegation/status/{task_id}`.

Status rules:

- `QUEUED`, `PENDING`, `PROCESSING`: keep polling until timeout.
- `ACTIVE`: treat provider order as successful, then perform an on-chain
  resource recheck.
- `FAILED` with temporary `error_code`: do not broadcast; fail the payout task
  with a controlled provider error.
- `FAILED` with non-temporary `error_code`: fail the payout task with a
  controlled provider error.
- `CANCELLED`, `COMPLETED`, `unknown`: fail the provider attempt and do not
  broadcast.

Temporary provider `error_code` values:

- `DUPLICATE_REQUEST`
- `RATE_LIMIT_EXCEEDED`
- `SERVICE_UNAVAILABLE`
- `REQUEST_TIMEOUT`

Non-temporary or operational-failure `error_code` values:

- `INVALID_ADDRESS`
- `INVALID_PARAMETERS`
- `INSUFFICIENT_BALANCE`
- `PROCESSING_FAILED`
- `CONFIGURATION_ERROR`
- `UNKNOWN_ERROR`

`INSUFFICIENT_BALANCE` is marked retryable in ProfeeX docs, but for this
system it should fail the payout attempt with an operational alert by default.
The provider account balance usually needs external action.

The current ProfeeX provider returns generic failure for all `FAILED` statuses.
The implementation should classify `error_code` so the task result can expose a
clear controlled failure reason. In this phase, classification is for reporting
and operational handling, not for automatic retry.

## Grither Pay API Security

Use the existing SHKeeper payout endpoint for Grither Pay:

```text
POST /api/v1/<crypto_name>/payout
```

Admin UI behavior remains compatible with the current session/basic-auth path.
Grither Pay uses an additional server-to-server HMAC auth path on the same
endpoint. The HMAC path must not be required for browser/admin requests.

Grither Pay request headers:

```text
X-Shkeeper-Timestamp: <unix timestamp>
X-Shkeeper-Signature: <hex hmac sha256>
```

Signing algorithm:

```text
HMAC_SHA256(secret, "{timestamp}." + raw_request_body)
```

This matches the existing outbound webhook HMAC helper in
`shkeeper/services/webhook_hmac.py`. The implementation should reuse that
verification logic for inbound payout requests instead of inventing a second
signature scheme.

Recommended secret:

- Use a dedicated environment secret for Grither Pay payout requests, for
  example `GRITHER_PAY_PAYOUT_HMAC_SECRET`.
- Do not rely on IP allowlist as the main auth control, because the public
  admin UI must remain open from any IP.
- Do not require HMAC for admin UI requests. The existing login/basic auth
  behavior should keep working.

Grither Pay payload:

```json
{
  "external_id": "grither_withdrawal_123",
  "destination": "T...",
  "amount": "100.25",
  "callback_url": "https://grither-pay.example/shkeeper/payout-callback"
}
```

For HMAC-authenticated payout requests:

- `external_id` is required.
- `fee` is optional and must not be trusted as the source of truth for TRON USDT
  resource readiness.
- SHKeeper should reject duplicate `external_id` defensively and must not create
  a second payout for the same `external_id`.
- Grither Pay remains responsible for deciding whether a failed withdrawal can
  be retried by the user under a new attempt.

## API And Admin Estimate

The existing estimate endpoint returns a static `fee` value. For USDT TRC-20
payouts, replace or extend this response with a structured resource quote.

The quote should include:

- provider name, initially `profeex`;
- destination address and amount;
- estimated energy required;
- current energy available on `fee_deposit`;
- energy deficit;
- estimated ProfeeX energy order volume;
- estimated ProfeeX energy cost and currency;
- estimated bandwidth required;
- current bandwidth available on `fee_deposit`;
- bandwidth deficit;
- estimated ProfeeX bandwidth order volume;
- estimated ProfeeX bandwidth cost and currency;
- total provider cost and currency when both resources use the same currency;
- readiness flag for submitting the payout request;
- blocking reason when the request cannot be safely submitted.

Because USDT energy can depend on destination behavior, the frontend estimate
must include the destination address. The current admin JS calls
`/estimate-tx-fee/<amount>` with only amount, so the implementation should add a
destination-aware estimate call for TRON token payout.

The quote is only a preflight estimate. It must not be trusted during task
execution. The Celery worker must recompute resources and provider readiness
before broadcast.

## API Submission Rules

Admin and API payout submission should be rejected before enqueue when:

- destination address is invalid;
- amount is invalid or exceeds token balance;
- HMAC-authenticated Grither Pay request is missing `external_id`;
- HMAC-authenticated Grither Pay request has an invalid timestamp or signature;
- non-empty `external_id` already exists for this crypto;
- resource estimation fails;
- ProfeeX configuration is missing for a payout that requires external
  provisioning;
- ProfeeX price/precount fails and resources are not already sufficient;
- the system can determine before enqueue that provisioning cannot be attempted.

After enqueue, the task may still wait for ProfeeX and may still fail if the
provider or chain state changes. In that case the task result should contain a
controlled error message, and no transaction should be broadcast.

To reduce the chance of a queued sidecar payout without a matching SHKeeper
record, create the SHKeeper `Payout` record before calling the sidecar. Then
store the returned `task_id`. If the sidecar rejects the request before enqueue,
mark that payout as failed.

## Sidecar Architecture

Add a narrow helper or service in `../tron-shkeeper`:

```python
ensure_fee_deposit_resources_for_usdt_payout(
    destination: str,
    amount: Decimal,
    *,
    tron_client=None,
) -> ResourceReadinessResult
```

Responsibilities:

- resolve the `fee_deposit` address;
- estimate required USDT transfer energy;
- calculate required bandwidth;
- read current `fee_deposit` resources;
- create ProfeeX energy and/or bandwidth orders only for deficits;
- poll ProfeeX status;
- classify provider errors;
- recheck on-chain resources after provider success;
- return a structured readiness result or raise a typed failure.

The existing `Wallet.transfer()` should remain focused on building, signing,
and broadcasting the transaction. The new helper should run before
`Wallet.transfer()` in the USDT single payout path.

The existing sweep resource-provider behavior should not be changed except for
safe shared ProfeeX error classification if the same provider class is reused.

## Main App Integration

In `shkeeper.io`:

- `tron_token.estimate_tx_fee()` should pass through the structured USDT
  resource quote when the sidecar returns it.
- The admin payout template should render provider cost and readiness instead
  of comparing static `TX_FEE` against TRX balance for USDT.
- The payout submit handler should block send when the latest quote is missing
  or not submit-ready.
- Backend API payout should repeat validation/preflight instead of relying only
  on frontend state.
- The existing payout endpoint should accept Grither Pay HMAC-authenticated
  server-to-server requests in addition to current admin/session/basic-auth
  requests.
- HMAC-authenticated requests should require `external_id` and must never create
  a duplicate payout for the same `crypto + external_id`.
- Existing non-TRON or non-USDT payout behavior should remain unchanged.

## Failure Behavior

- If resources are already sufficient, no ProfeeX order is created.
- If ProfeeX order creation fails before `task_id`, fail without broadcast.
- If polling times out before `ACTIVE`, fail without broadcast.
- If `ACTIVE` is received but on-chain resources are still insufficient, wait
  for a short bounded recheck window. If resources are still insufficient,
  fail without broadcast.
- If transaction broadcast fails after resources were confirmed, return the
  existing payout failure path with the broadcast error.
- Logs must include resource deficits, ProfeeX `task_id`, status, `error_code`,
  and payout destination, but must never log API keys or private keys.

## Testing

Sidecar unit tests:

- resource helper skips ProfeeX when resources are sufficient;
- helper creates energy order only when energy is deficient;
- helper creates bandwidth order only when bandwidth is deficient;
- helper waits for `ACTIVE` and then rechecks chain resources;
- helper does not broadcast when ProfeeX returns temporary failure;
- helper classifies `DUPLICATE_REQUEST` and `RATE_LIMIT_EXCEEDED` as temporary
  provider failures;
- helper classifies validation/configuration errors as permanent or operational
  failures;
- single payout path calls resource helper before `Wallet.transfer()`;
- multipayout path is unchanged.

Main app tests:

- TRON USDT estimate proxies structured quote fields;
- admin/API payout rejects submission when quote/preflight is not ready;
- existing admin payout request still works without HMAC;
- HMAC-authenticated payout accepts a valid signature over raw body bytes;
- HMAC-authenticated payout rejects missing, expired, or invalid signatures;
- HMAC-authenticated payout requires `external_id`;
- duplicate `external_id` does not create a second payout;
- payout record is created before sidecar enqueue and updated with `task_id`;
- old `fee` behavior remains compatible where non-USDT templates expect it;
- frontend blocks payout when latest quote is missing or stale.

Integration or smoke tests:

- queue routing sends USDT single payouts to the dedicated queue;
- two rapid payouts process sequentially and recompute resources between runs;
- temporary ProfeeX failure leaves the payout unbroadcast and marks the payout
  attempt failed without automatic retry.

## Rollout Notes

- Gate the new behavior behind explicit configuration so the fork can deploy it
  safely.
- Deploy the dedicated payout queue worker with one worker slot before enabling
  the feature.
- Configure `GRITHER_PAY_PAYOUT_HMAC_SECRET` before enabling Grither Pay payout
  calls.
- Prefer routing Grither Pay to SHKeeper over the private Yandex Cloud network
  when possible. This is defense in depth; HMAC remains required because the
  admin UI and public HTTPS endpoint stay reachable from any IP.
- Keep the old static fee path available for non-USDT and disabled-feature
  cases.
- Add operational metrics for provider order count, provider failures,
  `DUPLICATE_REQUEST`, `RATE_LIMIT_EXCEEDED`, successful no-order payouts, and
  queue wait time.
