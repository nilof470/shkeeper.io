# USDT Withdrawals Production Readiness

## Status

Approved design direction from brainstorming on 2026-06-03.

This document defines the production architecture for automated Grither Pay
client withdrawals through SHKeeper using USDT on TRON, TON, and Ethereum.
It is a design spec, not an implementation plan.

## Core Decision

Use option B from the architecture discussion:

- Grither Pay owns the customer withdrawal lifecycle and balance ledger.
- SHKeeper owns payout execution records and service-to-service execution API.
- Chain sidecars own network-specific preflight, queueing, and broadcast.
- Helm owns the production runtime topology as chart API, not as post-deploy
  shell patching.

The priority order is:

```text
Correctness > reliability > operational clarity > simplicity > speed
```

"Avoid overengineering" means avoiding components that do not materially reduce
the risk of duplicate payouts, lost payouts, stuck withdrawals, or poor
recovery. If a more complex component is required for correctness or reliable
recovery, it is in scope.

## Scope

Production client withdrawals support exactly these rails in the first release:

- `TRON-USDT`
- `TON-USDT`
- `ETH-USDT`

Native payouts (`TRX`, `TON`, `ETH`) and multipayout are not part of the
client withdrawal contract for this release. They may remain available for
existing admin/manual SHKeeper use, but Grither Pay must not depend on them.

## Repositories And Ownership

Controlled repositories:

- `shkeeper.io`: SHKeeper web app, execution API, payout execution state,
  callback/status handling.
- `tron-shkeeper`: TRON sidecar fork.
- `ton-shkeeper`: TON sidecar fork.
- `ethereum-shkeeper`: new Ethereum sidecar fork, following the same pattern as
  TRON and TON.
- `shkeeper-helm-charts`: production Helm chart fork.
- Grither Pay application repository: customer ledger and withdrawal state
  machine.

Ethereum fork requirement:

- Upstream remote: `https://github.com/vsys-host/ethereum-shkeeper`
- Fork remote: `https://github.com/nilof470/ethereum-shkeeper`
- Local checkout: `/Users/test/PycharmProjects/ethereum-shkeeper`
- Helm images must reference our owned image tags, not upstream-only images.

## Current System Assessment

TRON is the strongest current rail. It already has:

- USDT resource preflight and provider checks.
- Fail-closed behavior when the payout worker is unavailable.
- Dedicated `tron-usdt-payouts` worker container in the `tron-shkeeper` pod.
- Dedicated queue routing for feature-enabled USDT single payouts.
- Redis lock around fee-deposit resource provisioning and transfer.

TON fork is useful but not yet withdrawal-ready:

- The fork improves scanner/indexer resilience.
- Broadcast requests through `sendBoc` and `sendBocReturnHash` are not retried
  after timeout, which is correct for payout safety.
- Single payout is still routed through `make_multipayout`.
- There is no sidecar-local idempotent payout execution table.
- There is no dedicated `ton-usdt-payouts` queue or worker readiness check.

Ethereum is currently an upstream-style sidecar from SHKeeper's point of view:

- SHKeeper has an `Ethereum` adapter.
- Helm renders `ethereum-shkeeper` as `app + tasks + redis`.
- There is no local fork under our control yet.
- There is no ETH-USDT payout-specific queue, readiness contract, or nonce
  safety layer in our controlled codebase.

## Responsibility Boundaries

### Grither Pay

Grither Pay is the system of record for user withdrawals.

It owns:

- user balance ledger;
- withdrawal request creation;
- business approval and risk checks;
- balance reservation;
- user-facing status;
- retry policy exposed to users;
- final completion/failure accounting;
- reconciliation operator workflow.

SHKeeper must not become the customer ledger. If SHKeeper, Redis, or a sidecar
restarts, Grither Pay must still know that a withdrawal exists and that funds
are reserved until a terminal result is known.

### SHKeeper

SHKeeper is a crypto execution layer.

It owns:

- service-to-service execution API for Grither Pay;
- required idempotency by `external_id`;
- durable payout execution records;
- normalized state machine across rails;
- sidecar preflight and submit calls;
- polling/reconciliation of sidecar task or execution state;
- signed callbacks to Grither Pay;
- status lookup by `external_id`;
- operator-visible `RECONCILIATION_REQUIRED` state.

Legacy admin/manual payout endpoints may stay available, but Grither Pay must
use the new execution API only.

### Sidecars

Each sidecar is a chain-specific broadcast engine.

It owns:

- address validation;
- token support validation;
- hot wallet or fee wallet balance checks;
- network sync/readiness checks;
- fee/resource/gas checks;
- chain-specific serialization (`resource lock`, `seqno`, `nonce`);
- enqueueing to the dedicated payout queue;
- broadcast;
- sidecar-local execution state by `external_id`;
- terminal task result shape.

### Helm/Kubernetes

Helm is the production runtime API.

It owns:

- payout worker containers;
- queue names;
- worker concurrency and prefetch settings;
- probes and resource settings;
- required config validation;
- rollout strategy;
- chart tests;
- production values examples.

Post-deploy scripts may remain as smoke verifiers, but must not be the primary
mechanism that creates or patches payout runtime behavior.

## SHKeeper Execution API

Add a new service-to-service API for Grither Pay. Do not reuse the legacy
admin payout endpoint for client withdrawals.

### Submit

```http
POST /api/v1/payout-executions
```

Request:

```json
{
  "external_id": "grither-withdrawal-uuid",
  "asset": "USDT",
  "network": "TRON",
  "amount": "25.000000",
  "destination": "T...",
  "callback_url": "https://grither-pay/internal/shkeeper/payout-callback"
}
```

Rules:

- `external_id` is required.
- `asset` must be `USDT`.
- `network` must be one of `TRON`, `TON`, `ETH`.
- `amount` must be a positive decimal string.
- `destination` must be present and network-valid.
- `callback_url` must be HTTPS unless an explicit internal-network exception is
  configured for the deployed environment.

Response:

```json
{
  "status": "ACCEPTED",
  "execution_id": 123,
  "external_id": "grither-withdrawal-uuid",
  "network": "TRON",
  "asset": "USDT",
  "state": "QUEUED"
}
```

### Status

```http
GET /api/v1/payout-executions/{external_id}
```

Response:

```json
{
  "external_id": "grither-withdrawal-uuid",
  "network": "TRON",
  "asset": "USDT",
  "state": "QUEUED",
  "amount": "25.000000",
  "destination": "T...",
  "txids": [],
  "error_code": null,
  "error_message": null,
  "reconciliation_required": false
}
```

### Authentication

The new execution API must not use admin Basic Auth.

Use scoped service-to-service authentication for Grither Pay:

- service key or token scoped to payout execution;
- HMAC body signature and timestamp preferred;
- replay protection through timestamp tolerance;
- no reuse of the generic wallet API key if that key is also used for invoice
  callback trust.

The exact header names should be stable and documented in the implementation
plan. The auth scheme must support key rotation without a deploy-time code
change.

## Idempotency

Idempotency key:

```text
consumer + network + asset + external_id
```

For Grither Pay, `external_id` must be the Grither Pay withdrawal ID.

Behavior:

- First submit creates a SHKeeper execution record.
- Repeated submit with the same payload returns the existing execution.
- Repeated submit with the same `external_id` but different amount,
  destination, asset, or network returns `409 IDEMPOTENCY_CONFLICT`.
- Grither Pay must never retry by creating a new `external_id` for the same
  user withdrawal after a timeout.
- After a submit timeout, Grither Pay must call status lookup.

## SHKeeper Execution State Machine

States:

```text
CREATED
PREFLIGHTED
ENQUEUEING
ENQUEUED
BROADCAST
CONFIRMED
FAILED
RECONCILIATION_REQUIRED
```

Meaning:

- `CREATED`: durable row exists before sidecar submission.
- `PREFLIGHTED`: SHKeeper and sidecar preflight accepted the request.
- `ENQUEUEING`: SHKeeper is calling the sidecar submit endpoint.
- `ENQUEUED`: sidecar accepted the execution and returned a task/execution ID.
- `BROADCAST`: transaction hash is known, confirmation is pending.
- `CONFIRMED`: confirmation policy is satisfied.
- `FAILED`: terminal failure before broadcast or confirmed terminal chain error.
- `RECONCILIATION_REQUIRED`: SHKeeper cannot safely determine whether enqueue
  or broadcast happened.

`RECONCILIATION_REQUIRED` is not a terminal business failure. Grither Pay keeps
funds reserved until operator or automated reconciliation resolves the state.

## Grither Pay Withdrawal State Machine

Recommended states:

```text
REQUESTED
APPROVED
FUNDS_RESERVED
SUBMITTING
SUBMITTED
BROADCAST
CONFIRMED
COMPLETED
FAILED_VALIDATION
FAILED_PRECHECK
FAILED_BROADCAST
RECONCILIATION_REQUIRED
```

Rules:

- Funds are reserved before calling SHKeeper.
- Funds remain reserved for `SUBMITTING`, `SUBMITTED`, `BROADCAST`, and
  `RECONCILIATION_REQUIRED`.
- Funds are released only for terminal failures that are known to have happened
  before broadcast.
- Funds are finalized only after `CONFIRMED`.
- User retries create a new withdrawal only after the previous withdrawal is
  terminal and accounting has been resolved.

## SHKeeper Execution Storage

Add a durable execution table separate from legacy `Payout` or as a clearly
bounded extension if implementation review proves reuse is safer. The design
preference is a separate table because the new service-to-service contract has
different auth, idempotency, and state semantics from legacy admin payout.

Required fields:

- `id`
- `consumer`
- `external_id`
- `asset`
- `network`
- `amount`
- `destination`
- `callback_url`
- `state`
- `sidecar_execution_id`
- `sidecar_task_id`
- `txids`
- `error_code`
- `error_message`
- `request_hash`
- `created_at`
- `updated_at`
- `last_polled_at`

Required constraints:

- unique `(consumer, network, asset, external_id)`;
- store enough request data to detect idempotency conflicts;
- index non-terminal states for polling and reconciliation;
- index `external_id` for status lookup.

## Sidecar Contract

All three sidecars must expose the same minimum payout execution contract for
USDT.

### Preflight

```http
POST /USDT/payout/preflight
```

Request:

```json
{
  "external_id": "grither-withdrawal-uuid",
  "destination": "...",
  "amount": "25.000000"
}
```

Response:

```json
{
  "status": "ready",
  "network": "TRON",
  "asset": "USDT",
  "submit_ready": true,
  "blocking_code": null,
  "blocking_reason": null
}
```

Preflight checks:

- address format;
- USDT token support;
- USDT wallet balance;
- native fee/gas/resource balance;
- node/indexer sync;
- dedicated payout worker readiness;
- rail-specific constraints.

### Submit

```http
POST /USDT/payout/submit
```

Request:

```json
{
  "external_id": "grither-withdrawal-uuid",
  "destination": "...",
  "amount": "25.000000"
}
```

Response:

```json
{
  "external_id": "grither-withdrawal-uuid",
  "task_id": "...",
  "state": "ENQUEUED"
}
```

Submit rules:

- `external_id` is required.
- Sidecar creates or finds a durable local execution before enqueue.
- Duplicate same payload returns existing task/status.
- Duplicate changed payload returns `409`.
- Sidecar must not blind retry after ambiguous broadcast timeout.
- Terminal task result shape is normalized.

### Status

```http
GET /USDT/payout/status/{external_id}
```

Returns sidecar-local state, known txids, and terminal errors when available.

## Sidecar Rail Requirements

### TRON-USDT

Keep and normalize the current hardening:

- resource quote and resource preflight;
- ProfeeX-based destination/resource estimate for the current implementation;
- fail-closed when the payout worker is unavailable;
- dedicated `tron-usdt-payouts` queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- Redis lock around fee-deposit resource provisioning and transfer;
- reject unactivated destinations unless a separate activation design is
  approved.

Add:

- sidecar-local execution table keyed by `external_id`;
- `submit/status by external_id`;
- normalized terminal result shape;
- chart values/schema cleanup so payout worker enablement is chart API, not only
  env-derived behavior.

### TON-USDT

Add:

- dedicated `ton-usdt-payouts` queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- sidecar-local execution table keyed by `external_id`;
- worker readiness check;
- preflight for Jetton USDT balance and TON fee balance;
- serialized fee-deposit wallet seqno path;
- `submit/status by external_id`;
- normalized terminal result shape.

Keep:

- no retry for `sendBoc` or `sendBocReturnHash` after timeout.

Ambiguous TON broadcast timeout must produce `RECONCILIATION_REQUIRED`, not a
blind retry.

### ETH-USDT

Create and harden our fork.

Add:

- dedicated `eth-usdt-payouts` queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- sidecar-local execution table keyed by `external_id`;
- worker readiness check;
- address validation;
- ERC20 USDT balance preflight;
- ETH gas balance preflight;
- gas estimate;
- node sync/readiness check;
- serialized nonce path through single worker queue;
- `submit/status by external_id`;
- normalized terminal result shape.

Do not add a full nonce manager in the first release unless code analysis of the
Ethereum sidecar proves that serialized worker processing is insufficient for
correctness. If the sidecar has other code paths that can spend from the same
wallet concurrently, add a nonce or wallet-level lock before production.

## Worker Topology

Default Phase 1 topology:

```text
<rail>-shkeeper pod
  app
  tasks
  <rail>-usdt-payouts
  redis
```

Dedicated payout workers:

```text
TRON: tron-usdt-payouts -> tron_usdt_payouts
TON:  ton-usdt-payouts  -> ton_usdt_payouts
ETH:  eth-usdt-payouts  -> eth_usdt_payouts
```

Worker command pattern:

```bash
celery -A celery_worker.celery worker \
  -Q <rail>_usdt_payouts \
  --concurrency=1 \
  --prefetch-multiplier=1 \
  -n <rail>-usdt-payouts@%h
```

The normal `tasks` worker must not consume payout queues when dedicated payout
workers are enabled.

Same-pod dedicated worker is acceptable only if all reliability gates pass:

- durable execution state exists before enqueue;
- safe rollout prevents split-brain local Redis behavior;
- preflight fails closed when the payout worker is unavailable;
- reconciliation state exists for ambiguous enqueue/broadcast;
- queue loss does not lose the withdrawal because DB state is authoritative.

If these gates cannot be met with pod-local Redis, move directly to external
broker and separate payout worker Deployments.

## Redis And Celery Reliability

Redis/Celery is transport, not source of truth.

Source of truth hierarchy:

```text
Grither Pay withdrawal record
SHKeeper execution record
Sidecar execution record
Blockchain transaction
Celery/Redis queue
```

Celery ack settings alone cannot guarantee payout safety:

- early ack can lose a task after worker crash;
- late ack can redeliver after broadcast and risk duplicate payout.

Therefore each worker must update durable execution state before entering the
unsafe broadcast window. If a worker dies before broadcast starts, safe
re-enqueue is allowed. If it dies during or after broadcast attempt, the state
must become reconciliation-required unless the txid/task result can be safely
recovered.

## Helm/Kubernetes Design

Add first-class values for all three rails:

```yaml
tron_shkeeper:
  usdtPayoutWorker:
    enabled: true
    queue: tron_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1

ton_shkeeper:
  usdtPayoutWorker:
    enabled: true
    queue: ton_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1

ethereum_shkeeper:
  usdtPayoutWorker:
    enabled: true
    queue: eth_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1
```

Chart requirements:

- render payout worker container when enabled;
- restrict normal `tasks` worker to the default queue;
- set payout worker defaults to concurrency 1 and prefetch 1;
- provide `values.schema.json` or Helm `required/fail` checks;
- add resources/limits for `app`, `tasks`, `payouts`, and `redis`;
- add probes where supported by the sidecars;
- use owned image repositories/tags for controlled sidecar forks;
- include production values examples;
- include Helm template tests for worker rendering and queue isolation.

Rollout requirements with pod-local Redis:

- `replicas: 1` for sidecar deployments in Phase 1;
- rollout strategy must avoid two isolated Redis brokers serving payout submit
  traffic at the same time;
- prefer `Recreate` or equivalent no-surge behavior for sidecar deployments
  using pod-local Redis;
- preStop/termination grace must allow in-flight worker tasks to exit cleanly;
- readiness must fail closed during startup/shutdown for payout submit.

If zero-downtime sidecar rollout becomes mandatory, pod-local Redis is no
longer acceptable. Move to an external broker and separate Deployments.

## Grither Pay Integration

Grither Pay changes:

- add withdrawal table/state machine;
- reserve funds before SHKeeper submit;
- call SHKeeper with withdrawal ID as `external_id`;
- handle duplicate submit safely;
- poll status after submit timeout;
- process signed callbacks;
- map SHKeeper states to user-facing states;
- keep funds reserved for `RECONCILIATION_REQUIRED`;
- expose operator workflow for reconciliation;
- do not allow user retry with a new `external_id` while the previous attempt is
  non-terminal.

User-facing behavior:

- validation failures are immediate and do not reserve funds;
- precheck failures release or avoid reservation and show temporary
  unavailable/invalid destination messaging;
- submitted/broadcast states show pending;
- reconciliation-required states show processing and raise an operator alert;
- confirmed states complete the withdrawal.

## Observability

Required metrics and alerts:

- non-terminal executions by state and age;
- `RECONCILIATION_REQUIRED` count;
- payout worker unavailable by rail;
- payout queue depth and age by rail;
- sidecar preflight failures by code;
- sidecar submit failures by code;
- callback delivery failures;
- TRON provider/resource failures;
- TON fee balance low;
- ETH gas balance low;
- USDT hot wallet balance low by rail;
- node/indexer sync lag;
- executions with txid but no confirmation after threshold;
- executions stuck in `ENQUEUEING` or `ENQUEUED`.

Operator views must identify:

- Grither Pay withdrawal ID;
- SHKeeper execution ID;
- sidecar execution/task ID;
- rail;
- destination;
- amount;
- txids;
- current state;
- last error code/message;
- next safe action.

## Testing Strategy

### SHKeeper

Unit/integration tests:

- service auth rejects missing/bad credentials;
- required `external_id`;
- duplicate same payload returns existing execution;
- duplicate changed payload returns `409`;
- execution row is created before sidecar submit;
- sidecar timeout after enqueueing path produces reconciliation state;
- status endpoint returns normalized states;
- callback payload is signed and includes external ID, rail, amount, txids, and
  terminal state;
- legacy payout endpoints remain compatible for admin/manual use.

### Sidecars

Shared tests:

- preflight success;
- preflight worker unavailable;
- preflight insufficient USDT balance;
- preflight insufficient fee/gas/native balance;
- duplicate submit same payload;
- duplicate submit changed payload;
- task result normalization;
- no blind retry after broadcast timeout;
- status by `external_id`;
- dedicated queue routing.

Rail-specific tests:

- TRON resource quote and provider failures;
- TRON unactivated destination rejection;
- TRON resource lock around transfer;
- TON seqno serialization;
- TON no retry for broadcast timeout;
- TON Jetton balance and TON fee balance preflight;
- ETH gas estimate and gas balance preflight;
- ETH nonce serialization or wallet lock behavior.

### Helm

Template tests:

- `tron-usdt-payouts`, `ton-usdt-payouts`, and `eth-usdt-payouts` render when
  enabled;
- normal `tasks` worker does not consume payout queues;
- payout workers use configured queue names;
- payout workers default to concurrency 1 and prefetch 1;
- required config validation fails for incomplete production values;
- rollout strategy is safe for pod-local Redis.

### End-To-End

For each rail:

- testnet or low-value mainnet smoke payout;
- submit timeout simulation followed by status lookup;
- worker unavailable simulation;
- sidecar restart during non-broadcast state;
- sidecar restart during broadcast ambiguity;
- callback retry simulation;
- confirmation polling.

## Implementation Phases

### Phase 0: Fork And Control Plane

- Create `nilof470/ethereum-shkeeper`.
- Checkout `/Users/test/PycharmProjects/ethereum-shkeeper`.
- Configure `origin` and `fork` remotes consistently with TON/TRON.
- Ensure Helm values reference owned images for TRON/TON/ETH.
- Document fork ownership and upstream sync process.

Gate: all payout rails are under code ownership.

### Phase 1: SHKeeper Execution API

- Add service-to-service execution API.
- Add scoped auth/HMAC.
- Add durable execution table and state machine.
- Add idempotent submit/status.
- Add callback payload and signing.
- Keep legacy payout API intact.

Gate: Grither Pay can submit/retry/status without duplicate withdrawal risk at
the SHKeeper API boundary.

### Phase 2: Sidecar Hardening

- Implement shared preflight/submit/status contract in TRON, TON, and ETH.
- Add sidecar-local execution records.
- Add dedicated payout queues and worker readiness.
- Normalize task results.
- Protect broadcast windows and ambiguous timeouts.

Gate: each sidecar protects its own network-specific broadcast path.

### Phase 3: Helm/Kubernetes

- Add first-class payout worker values for TON and ETH.
- Clean up TRON payout worker chart API.
- Add schema/required validation.
- Add resources/probes/rollout strategy.
- Add chart tests and production values examples.

Gate: production topology is chart-rendered and verified, not patched by
runbook scripts.

### Phase 4: Grither Pay Integration

- Add withdrawal state machine and reservation flow.
- Call SHKeeper execution API with withdrawal ID as `external_id`.
- Handle callbacks and polling.
- Implement operator reconciliation flow.

Gate: Grither Pay never loses a withdrawal and never releases reserved funds
without a terminal state.

### Phase 5: Observability And Ops

- Add metrics and alerts.
- Add operator status views.
- Add runbooks for reconciliation, worker unavailable, low balance, and provider
  failures.

Gate: operators can detect and act on stuck or ambiguous withdrawals before
users are affected at scale.

### Phase 6: Production Rollout

- Roll out one rail at a time.
- Start with low limits.
- Run smoke payout per rail.
- Observe metrics for a defined stability window.
- Increase limits only after each rail is stable.

Gate: real client withdrawals are allowed only after all reliability gates for
that rail pass.

## Acceptance Criteria

The system is production-ready for a rail only when:

- Grither Pay stores the withdrawal and reserves funds before SHKeeper submit.
- SHKeeper creates durable execution state before sidecar submit.
- Submit is idempotent by Grither Pay withdrawal ID.
- Sidecar submit is idempotent by `external_id`.
- Dedicated payout worker and queue are enabled.
- Broadcast path is serialized for that rail.
- No blind retry can occur after ambiguous broadcast.
- Status lookup can recover after Grither Pay submit timeout.
- `RECONCILIATION_REQUIRED` is visible and alerted.
- Callback delivery retries are bounded and observable.
- Helm chart renders the production topology from values.
- Rollout cannot split payout traffic across two isolated pod-local Redis
  brokers.
- End-to-end smoke payout passes on that rail.

## Known Risks And Mitigations

Pod-local Redis risk:

- Mitigation: durable execution records are authoritative; use safe rollout;
  move to external broker if reliability gates cannot be met.

Ambiguous broadcast timeout:

- Mitigation: no blind retry; mark reconciliation-required; recover by sidecar
  status or operator workflow.

ETH nonce risk:

- Mitigation: dedicated serialized worker first; add wallet-level nonce lock if
  any other path can spend from the same hot wallet.

TON seqno risk:

- Mitigation: dedicated serialized worker; keep no-retry broadcast behavior;
  mark ambiguous sends for reconciliation.

TRON provider/resource risk:

- Mitigation: preflight fail-closed; dedicated worker; resource lock; alerts for
  provider failures and low resources.

Legacy SHKeeper payout risk:

- Mitigation: Grither Pay uses only the new service-to-service execution API;
  legacy admin/manual endpoints remain separate.

## Non-Goals For This Release

- Native TRX/TON/ETH withdrawals.
- Multipayout for client withdrawals.
- High-volume parallel payout engine.
- Full zero-downtime sidecar rollout with pod-local Redis.
- New standalone payout microservice between Grither Pay and SHKeeper.
- User-facing withdrawal UI redesign beyond required status mapping.
