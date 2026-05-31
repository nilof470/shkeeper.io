# TRON USDT Fee-Deposit Payout Resource Provisioning

Date: 2026-05-29
Status: Revised design, pending implementation plan
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
- `shkeeper/modules/classes/tron_token.py`

## Context

TRON TRC-20 sweep already provisions energy and bandwidth before moving funds
from client onetime wallets to the main wallet. The USDT TRC-20 single payout
path does not use this resource-provider flow. It sends USDT from the
`fee_deposit` wallet and currently depends on a static `TX_FEE` estimate and
the wallet's TRX balance.

The desired TRON payout flow is for client withdrawals and manual admin payouts
to send USDT TRC-20 from `fee_deposit` without burning TRX for normal
transaction fees. Energy and bandwidth should be prepared through the configured
TRON resource provider layer when the wallet does not already have enough
resources. ProfeeX is the primary current provider, but the payout flow should
not be hardcoded to ProfeeX.

Client withdrawals may be initiated by an external API consumer such as Grither
Pay. The consumer is responsible for its own wallet ledger behavior: balance
holds, preventing double withdrawals, terminal failure handling, ambiguous
provider-call handling, and deciding when a user may submit a new withdrawal.
SHKeeper remains a generic payout executor and must not contain Grither
Pay-specific wallet or refund logic.

External consumers will call the existing SHKeeper single payout endpoint. In
this phase, SHKeeper keeps the current payout endpoint and auth model unchanged:
admin browser session and existing Basic Auth remain available. HMAC
server-to-server auth is intentionally out of scope for this iteration.

## Goals

- Add resource provisioning to USDT TRC-20 single payout from `fee_deposit`.
- Cover both admin manual USDT TRC-20 payout and client USDT TRC-20 withdrawal
  payout, because both map to a single payout from the same fee wallet.
- Use ProfeeX `GET /api/v1/delegation/fee` as the USDT transfer energy
  estimator, because the existing node-side estimate path is unreliable for
  this flow.
- Block backend payout submission when resource estimation cannot be completed
  or the system already knows provisioning cannot be attempted.
- Never broadcast the USDT transaction until resources are confirmed active on
  chain.
- Prevent known TRX burn cases in this phase. In particular, do not submit a
  payout to a destination that ProfeeX reports as a new/unactivated address.
- Keep the existing `/api/v1/<crypto_name>/payout` endpoint for both admin
  manual payouts and external API consumer payouts.
- Keep the existing payout auth behavior in this phase: browser session for
  admin UI and Basic Auth for API callers.
- Keep the change additive and narrow because this codebase is a fork.

## Non-goals

- No TRON USDT resource provisioning for multipayout in this phase. A minimal
  multipayout validation reorder is allowed only to prevent the new
  `(crypto, external_id)` unique index from creating a post-enqueue DB failure.
- No resource-provisioning changes for native TRX, TON, EVM, BTC-like, or
  other non-TRON-USDT payout paths in this phase.
- No new coin/network support in this phase.
- No HMAC server-to-server auth in this phase.
- No Basic Auth disabling or 2FA enforcement changes for API calls in this
  phase. This is accepted as a temporary compatibility tradeoff.
- No buffer strategy that intentionally rents resources for five future
  payouts.
- No admin UI modernization in this phase: no new provider rows, no cost
  display, no frontend stale-quote blocking, and no destination-aware admin
  estimate UI.
- No provider cost estimate in this phase. ProfeeX `precount` endpoints can be
  added later if the admin UI needs provider cost display.
- No destination activation flow in this phase. SHKeeper should not activate
  payout destinations or intentionally burn TRX for activation.
- No resource-provider webhook integration in this phase.
- No broad refactor of wallet signing or transaction broadcast code.
- No SHKeeper-side wallet ledger, balance reservation, refund, ambiguous
  business status, or double-withdrawal state machine for external consumer
  withdrawals.
- No automatic SHKeeper retry loop for failed resource provisioning in this
  phase. Clear provider failures should fail the SHKeeper payout attempt
  cleanly; each API consumer decides how to update its own business state.
  Ambiguous sidecar enqueue failures are not clear provider failures and must
  not be represented as safe-to-retry success or as an automatic duplicate path.
- No application-level IP allowlist in this phase. Network restrictions may be
  applied at Yandex Cloud/security-group/ingress level as defense in depth, but
  they are not required for this implementation scope.
- No crypto-scoped API-key redesign in this phase. The existing status API-key
  behavior is accepted temporarily and should be revisited with broader auth
  hardening.

## Selected Strategy

Use per-payout resource readiness with conditional provider acquisition:

1. Each USDT TRC-20 single payout performs its own resource readiness check.
2. The sidecar checks current `fee_deposit` energy and bandwidth on chain.
3. The configured energy and/or bandwidth provider is called only when the
   current resource balance has a deficit for this payout.
4. After the provider reports success, the sidecar rechecks the on-chain
   resources.
5. The USDT transaction is broadcast only after the recheck confirms enough
   resources.

This is not a resource buffer strategy. The implementation does not buy
resources in advance for a planned batch of future payouts. It also does not
call the configured resource provider when previous delegation, manual
delegation, or recovered resources already make `fee_deposit` ready for the
current payout.

For a client-facing API consumer, one withdrawal attempt should map to one
SHKeeper payout attempt identified by `external_id`. If SHKeeper fails before
broadcast because route-specific validation, resources, or the configured
provider is not ready, the failure is terminal for that SHKeeper payout
attempt. The consumer may allow a new withdrawal attempt only after it resolves
its own wallet state.

## Resource Sizing

The resource target is the `fee_deposit` TRON address, because that wallet signs
and broadcasts the outgoing USDT transfer.

Energy sizing:

- Estimate USDT transfer energy through ProfeeX
  `GET /api/v1/delegation/fee?receiver_address=<destination>`.
- Treat the ProfeeX response field `energy_required` as the required energy for
  a USDT transfer to that destination. This replaces the previous node-side
  estimate path, which can return invalid values for this use case.
- Also consume ProfeeX `is_new_address` and `trx_burned` fields. If
  `is_new_address=true`, reject the payout before enqueue with a controlled
  `DESTINATION_NOT_ACTIVATED` error, because resource rental does not cover
  TRON account activation burn.
- `PROFEEX` config is required when
  `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true`, even if the configured
  energy rental provider is `refee`, because ProfeeX is the estimator for this
  flow.
- The implementation must still use on-chain resource reads as the final
  readiness check, because the estimator does not prove that delegated
  resources are currently usable by `fee_deposit`.
- If energy estimation fails, the payout request is not submitted or broadcast.

Bandwidth sizing:

- Use the existing TRC-20 transfer bandwidth constant from sidecar config,
  currently `BANDWIDTH_PER_TRC20_TRANSFER_CALL`.
- Check free bandwidth on `fee_deposit`.
- Rent bandwidth only when available bandwidth is below the required amount.

Provider request sizing:

- Request only the current payout deficit from the configured provider layer,
  adjusted by that provider's minimum, maximum, fixed-order, or overprovision
  rules.
- If a provider minimum or fixed order amount is greater than the exact deficit,
  request the provider-valid amount for this single payout.
- For re:Fee fixed mode, `REFEE_FIXED_ENERGY_ORDER_AMOUNT=65_000` is considered
  sufficient for this payout flow. Do not add extra frontend or backend
  complexity to split one payout into multiple energy orders.
- For this phase, fee-wallet payout acquisition should use the external
  configured providers `refee` or `profeex` when there is an energy deficit.
  The sweep staking provider remains unchanged, but staking-based acquisition
  for `fee_deposit` payout deficits is out of scope unless a separate design
  handles delegation source, self-delegation, and release semantics.
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
it is still available, and it will call the configured provider only if there is
still a deficit.

Sequential execution reduces same-wallet contention, but it does not guarantee
that the provider will accept every rapid request. If a queued payout still
hits a provider cooldown or rate limit such as `DUPLICATE_REQUEST` or
`RATE_LIMIT_EXCEEDED`, this phase returns a controlled failure and does not
automatically create a retry.

## Provider Status Handling

External resource providers are asynchronous. The provider abstraction should
hide provider-specific API details behind the shared energy/bandwidth provider
interface.

For ProfeeX resource orders:

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

`INSUFFICIENT_BALANCE` is marked retryable in ProfeeX docs, but for this system
it should fail the payout attempt with an operational alert by default. The
provider account balance usually needs external action.

Provider implementations should classify provider errors so the task result can
expose a clear controlled failure reason. In this phase, classification is for
reporting and operational handling, not for automatic retry.

## Payout API Auth Scope

Use the existing SHKeeper payout endpoint for external payout consumers:

```text
POST /api/v1/<crypto_name>/payout
```

Do not introduce a new payout endpoint and do not change the auth decorators in
this phase. Admin UI behavior remains compatible with the current browser
session path. Server-to-server consumers can use the existing Basic Auth path.

This means 2FA protects browser login sessions, but it does not protect Basic
Auth API calls. That risk is accepted temporarily to keep the fork changes
small. Future hardening can replace Basic Auth for server-to-server payouts with
HMAC or another scoped machine credential.

External consumer payload example:

```json
{
  "external_id": "client_withdrawal_123",
  "destination": "T...",
  "amount": "100.25",
  "callback_url": "https://consumer.example/shkeeper/payout-callback"
}
```

For external consumer payout requests:

- `external_id` is required by the consumer integration contract for
  idempotency and status lookup.
- Payout creation keeps using the existing Basic Auth/admin-session endpoint.
  The existing payout status lookup is API-key protected, so external consumers
  that need status reconciliation must be configured with the status API key as
  well as payout creation credentials.
- SHKeeper does not globally reject all payout requests missing `external_id`,
  because the unchanged endpoint also serves admin/manual and legacy API flows.
  Instead, it enforces race-safe duplicate protection whenever a non-empty
  `external_id` is present.
- The existing per-crypto payout schema remains unchanged. If a route requires
  `fee` today, it remains required. For TRON USDT, `fee` may be optional or
  ignored by the resource-provisioning path and must not be trusted as the
  source of truth for resource readiness.
- SHKeeper should reject duplicate `external_id` defensively and must not create
  a second payout for the same `external_id`.
- SHKeeper should normalize non-empty `external_id` by trimming whitespace
  before duplicate checks and before creating the database unique constraint.
- The API consumer remains responsible for deciding whether a failed or
  ambiguous withdrawal can be retried by the user under a new attempt.

## Backend Preflight

The existing public/admin estimate UI can keep returning and displaying the
legacy static `fee`. This phase does not change the admin payout template.

For backend payout creation, `tron_token.preflight_payout()` should call the
sidecar estimate endpoint with the destination address. When the sidecar
feature flag is enabled and `address` is provided, the sidecar returns a minimal
structured resource preflight result for backend use.

The preflight result should include:

- destination address and amount;
- estimated energy required from ProfeeX `/delegation/fee`;
- ProfeeX destination activation flag and estimated TRX burn fields;
- current energy available on `fee_deposit`;
- energy deficit;
- estimated bandwidth required;
- current bandwidth available on `fee_deposit`;
- bandwidth deficit;
- readiness flag for submitting the payout request;
- blocking code and blocking reason when the request cannot be safely
  submitted.

The preflight is only an early backend guard. It must not be trusted during task
execution. The Celery worker must recompute resources and provider readiness
before broadcast.

## API Submission Rules

Admin and API payout submission should be rejected before enqueue when:

- destination address is invalid;
- amount is invalid;
- TRON USDT preflight cannot confirm enough token balance;
- TRON USDT destination is known to be unactivated;
- non-empty `external_id` already exists for this crypto;
- resource estimation fails for a payout route that requires resource preflight;
- the sidecar estimate returns error JSON or a malformed response without
  either a legacy `fee` or structured resource preflight;
- configured provider settings are missing for a payout that requires external
  provisioning;
- configured bandwidth provider request sizing is below required transfer
  bandwidth;
- the system can determine before enqueue that provisioning cannot be attempted.

Expected API error classes:

- `400` for invalid request data, invalid destination, insufficient token
  balance, or unactivated destination;
- `409` for duplicate non-empty `(crypto, external_id)`;
- `503` for resource estimator, sidecar, or provider availability failures;
- `500` only for unexpected server errors.

A clear sidecar payout response without `task_id` must also be treated as a
failed payout creation response, not as an in-progress payout.

After enqueue, the task may still wait for a provider and may still fail if the
provider or chain state changes. In that case the task result should contain a
controlled error message, and no transaction should be broadcast.

To reduce the chance of a queued sidecar payout without a matching SHKeeper
record for idempotent external consumers, create the SHKeeper `Payout` record
before calling the sidecar when `external_id` is present. Then store the
returned `task_id`. If the sidecar rejects the request before enqueue, mark that
payout as failed. For legacy/admin requests without `external_id`, the current
sidecar-first flow may remain, but a clear sidecar response without `task_id`
must be rejected instead of creating a misleading `IN_PROGRESS` payout row.

If an `external_id` request creates the SHKeeper row and then hits an ambiguous
sidecar enqueue exception after the request may have left the process, keep the
row `IN_PROGRESS`, keep `task_id = null`, store the error, and expose that state
through payout status for manual reconciliation. Do not mark this ambiguous
state as safe to retry automatically.

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
- call the configured energy and/or bandwidth provider only for deficits;
- wait for provider success according to the selected provider implementation;
- classify provider errors;
- recheck on-chain resources after provider success;
- return a structured readiness result or raise a typed failure.

The existing `Wallet.transfer()` should remain focused on building, signing,
and broadcasting the transaction. The new helper should run before
`Wallet.transfer()` in the USDT single payout path.

The existing sweep resource-provider behavior should not be broken. Shared
provider interfaces may be extended, but sweep must keep using the same provider
layer and must pass regression tests for the currently configured provider.
The sidecar provider configuration is global for TRON resource provisioning:
changing `ENERGY_PROVIDER` or `BANDWIDTH_PROVIDER` changes both sweep and this
fee-wallet payout flow, so provider switches require regression coverage for
both flows.

## Main App Integration

In `shkeeper.io`:

- `tron_token.estimate_tx_fee()` should be able to pass destination address to
  the sidecar for backend preflight, but the existing admin template does not
  need to change in this phase.
- Backend API payout should repeat validation/preflight instead of relying only
  on frontend state.
- The existing payout endpoint should keep accepting current admin
  session/basic-auth requests.
- Requests that provide `external_id` must never create a duplicate payout for
  the same `crypto + external_id`.
- Existing non-TRON or non-USDT payout execution behavior should remain
  unchanged. Generic validation should be limited to checks that are already
  safe for all routes, such as positive amount; balance/resource checks belong
  to crypto-specific preflight hooks.

## Failure Behavior

- If resources are already sufficient, no provider acquisition is attempted.
- If provider acquisition fails before a provider reference is available, fail
  without broadcast.
- If polling times out before `ACTIVE`, fail without broadcast.
- If `ACTIVE` is received but on-chain resources are still insufficient, wait
  for a short bounded recheck window. If resources are still insufficient,
  fail without broadcast.
- If transaction broadcast fails after resources were confirmed, return the
  existing payout failure path with the broadcast error.
- If `Wallet.transfer()` returns a result with `status != "success"`, treat it
  as a controlled payout failure even if the Celery task itself did not raise.
- Logs must include resource deficits, provider name, provider reference or
  task id when available, status, error code when available, and payout
  destination, but must never log API keys or private keys.

## Testing

Sidecar unit tests:

- resource helper skips providers when resources are sufficient;
- helper calls energy provider only when energy is deficient;
- helper calls bandwidth provider only when bandwidth is deficient;
- helper waits for provider success and then rechecks chain resources;
- helper does not broadcast when provider acquisition fails;
- helper classifies `DUPLICATE_REQUEST` and `RATE_LIMIT_EXCEEDED` as temporary
  provider failures;
- helper classifies validation/configuration errors as permanent or operational
  failures;
- helper blocks unactivated destination addresses reported by ProfeeX;
- helper blocks staking-based acquisition for fee-wallet payout deficits in
  this phase;
- single payout path calls resource helper before `Wallet.transfer()`;
- multipayout path does not receive resource provisioning.

Main app tests:

- TRON USDT backend preflight passes destination to the sidecar;
- admin/API payout rejects submission when backend preflight is not ready;
- existing admin payout request still works through browser session auth;
- existing Basic Auth payout still works for API callers;
- consumer documentation states `external_id` is required for idempotent
  integrations, while SHKeeper stays compatible with legacy requests without
  `external_id`;
- duplicate `external_id` does not create a second payout;
- payout record is created before sidecar enqueue and updated with `task_id`;
- payout status exposes `task_id`, `error`, `success`, and all `txids`;
- temporary sidecar/resource outages are returned as controlled `503` errors;
- balance endpoint outage does not become a false zero-balance rejection;
- old `fee` behavior remains compatible where templates expect it.

Integration or smoke tests:

- queue routing sends USDT single payouts to the dedicated queue;
- two rapid payouts process sequentially and recompute resources between runs;
- rapid payout smoke accepts either successful resource reuse or controlled
  provider-cooldown failure without broadcast;
- temporary provider failure leaves the payout unbroadcast and marks the payout
  attempt failed without automatic retry.

## Rollout Notes

- Gate the new behavior behind explicit configuration so the fork can deploy it
  safely.
- Deploy the dedicated payout queue worker with one worker slot before enabling
  the feature.
- Prefer routing server-to-server consumers such as Grither Pay to SHKeeper over
  the private Yandex Cloud network when possible. This is defense in depth while
  Basic Auth remains in use and the public HTTPS endpoint stays reachable from
  any IP.
- Keep the old static fee path available for non-USDT and disabled-feature
  cases.
- Add operational metrics for provider order count, provider failures,
  `DUPLICATE_REQUEST`, `RATE_LIMIT_EXCEEDED`, successful no-order payouts, and
  queue wait time.
