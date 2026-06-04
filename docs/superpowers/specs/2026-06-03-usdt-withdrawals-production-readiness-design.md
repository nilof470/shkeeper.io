# USDT Withdrawals Production Readiness

## Status

Approved design direction from brainstorming on 2026-06-03. Updated with
code-validated findings from the payout architecture review.

This document defines the production architecture for automated Grither Pay
client withdrawals through SHKeeper using USDT on TRON, TON, and Ethereum.
It is a design spec with phased implementation and rollout gates.

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

Client payout safety position:

- Payout correctness is intentionally held to a higher bar than deposit
  processing because payout mistakes can irreversibly send funds twice.
- Infrastructure stability on a single VPS/Kubernetes cluster may simplify
  topology, but it must not justify blind retry after an ambiguous sidecar submit
  or broadcast.
- The target is correctness-complete payout execution, not a best-effort wrapper
  around the existing admin payout task API.
- The modernization must stay isolated to the Grither Pay client withdrawal path,
  SHKeeper payout execution API/state, sidecar payout execution, and Helm payout
  topology. It must not rewrite invoice/deposit processing, `walletnotify`, or
  the existing admin payout UI.
- Automatic recovery is allowed for states that are proven to be before broadcast
  and cannot create a duplicate payout.
- Automatic recovery is out of scope only for critical ambiguous states where the
  system cannot prove whether sidecar enqueue or on-chain broadcast happened.
  Prefer durable `RECONCILIATION_REQUIRED` with operator action over any automatic
  retry that could create a duplicate payout.
- Ambiguous critical payouts must not be marked as a business failure until
  evidence proves that the requested USDT transfer did not complete and no
  pending original transaction/message can still complete. After that
  verification, the business outcome may be manual payout outside the automatic
  execution pipeline.
- Keep the first release mechanically simple: existing SHKeeper service, existing
  sidecar repos, SQL-backed execution tables, bounded outbox workers, and
  chart-owned runtime topology. Do not introduce a standalone payout
  microservice, Kafka, Temporal, or a distributed workflow engine unless the
  simpler design fails a concrete reliability gate.
- Every rail must have an operator kill switch before it can be enabled for
  client withdrawals. Client amount/day limits are upstream product policy and
  must not be implemented as SHKeeper rail fields or Helm values.

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
- There is no dedicated TON payout worker, catalog queue, or worker readiness
  check.

Ethereum has now been forked and locally hardened for the sidecar execution
contract:

- SHKeeper has an `Ethereum` adapter.
- Helm renders `ethereum-shkeeper` as `app + tasks + redis + eth-usdt-payouts`
  when the ETH-USDT rail is enabled.
- The owned fork checkout exists at
  `/Users/test/PycharmProjects/ethereum-shkeeper`.
- Local sidecar code now has ETH-USDT scoped payout auth, preflight/submit/status,
  sidecar execution state, nonce lock, signed transaction hash/ref evidence,
  source-wallet address evidence, ERC20 Transfer confirmation, and legacy
  same-wallet nonce guards. Independent review follow-up requires pending nonce
  reads, source-wallet address persistence at nonce reservation, required
  SHKeeper-provided `execution_id`, fail-closed dispatch configuration, and
  execution ownership recheck before broadcast.
- ETH-USDT remains disabled for production client withdrawals until an owned
  image tag is published, environment values reference that tag, restore-drill
  evidence is configured, and staging/testnet smoke payout passes.

## Original Validated Code Findings

This section records the follow-up code validation performed after the initial
architecture review and before the payout-execution implementation passes in the
forks. These were not theoretical gaps at review time. Later verification
sections in this document record the current implemented and tested state after
the fixes.

### Critical Findings

SHKeeper currently reserves `external_id` before sidecar submit, but execution is
still represented by legacy `Payout.task_id` and optional `PayoutTx` rows:

- `shkeeper/services/payout_service.py` creates a payout row before sidecar
  submit for the `external_id` path.
- `shkeeper/models.py` only has `IN_PROGRESS`, `SUCCESS`, and `FAIL`, with no
  durable execution state, request hash, sidecar execution id, attempt id, lease,
  nonce/seqno, signed payload, or broadcast evidence.
- the flat `FAIL` state cannot distinguish a safe pre-broadcast failure from a
  confirmed chain-terminal failure, so Grither Pay cannot safely map it to
  reserve release, retry, or manual resolution.
- if sidecar submit raises after the reserved row is committed, SHKeeper stores
  `Sidecar enqueue result is unknown` but has no deterministic way to prove
  whether the sidecar accepted or broadcast the payout.
- the current legacy status endpoint is scoped as
  `/<crypto>/payout/status?external_id=...`, and the legacy unique guard is
  `(crypto, external_id)`. The new service API must either include rail in status
  lookup or make `external_id` globally unique per consumer. This spec chooses
  global uniqueness per consumer because client withdrawal identifiers are the
  business idempotency boundary.

TRON sidecar has a useful dedicated worker path, but no durable execution model:

- `tron-shkeeper/app/api/payout.py` exposes `/payout/<to>/<amount>` and returns a
  Celery `task_id`; it does not accept `external_id`, `execution_id`, or
  `request_hash`.
- `tron-shkeeper/app/api/__init__.py` protects the API blueprint with generic
  Basic Auth, but there is no scoped SHKeeper-to-sidecar payout execution
  identity, HMAC body signature, replay protection, or consumer authorization.
- `tron-shkeeper/app/wallet.py` builds, signs, broadcasts, and waits in one
  function call; the signed transaction and txid are not stored before broadcast.
- `tron-shkeeper/app/models.py` has key/settings/balance tables, but no payout
  execution table.

TON sidecar is not yet payout-production-ready:

- `ton-shkeeper/app/api/payout.py` routes single payout through
  `make_multipayout` and returns only a Celery `task_id`.
- `ton-shkeeper/app/api/__init__.py` also uses generic Basic Auth for the API
  blueprint, not scoped payout execution auth.
- `ton-shkeeper/app/coin.py` builds BOC messages and sends them immediately; BOC,
  message hash, and seqno are not stored before broadcast.
- `ton-shkeeper/app/models.py` has no payout execution table.
- `ton-shkeeper/app/coin.py` currently maps multipayout results using the loop
  variable from the previous build loop, so batch payout results can report the
  last destination/amount for every sent transaction.

The original ETH gap was validated and partially closed on 2026-06-04 in the
owned local fork. ETH-USDT still cannot be enabled for production client
withdrawals until Helm references an owned image and renders the payout worker,
storage/migration readiness, and backup/restore posture.

### High Findings

Current callback and polling flows are not sufficient as execution recovery:

- `shkeeper/services/payout_service.py` accepts request-provided `callback_url`
  values with only basic URL scheme validation. That is acceptable for legacy
  admin payout behavior, but client payout callbacks need configured or
  allowlisted targets.
- SHKeeper `poll_all_pending_payouts` only polls rows with a non-null `task_id`,
  so reserved payouts with unknown sidecar enqueue are not recovered
  automatically.
- SHKeeper `/payoutnotify/<crypto>` authenticates and logs sidecar payout
  notifications, but does not update payout state.
- Original sidecar callback delivery used infinite `post_payout_results` retry
  loops. Current TRON, TON, and ETH sidecar worktrees use durable sidecar
  callback outboxes with bounded retry/claim semantics instead; SHKeeper status
  polling remains authoritative for payout execution recovery.

Confirmation logic is too generic for token withdrawals:

- SHKeeper marks payout success from generic confirmation count alone.
- The current path does not verify token transfer event details for contract,
  source wallet, destination, amount, and chain/network.

Hot-wallet serialization is incomplete:

- TRON has a dedicated USDT payout worker and resource lock, but there are other
  broadcast paths in the sidecar for sweeping, staking, AML, and resource flows.
- TON uses fee-deposit seqno in payout and funding/drain paths; a dedicated payout
  queue alone is not sufficient if other code can spend from the same fee-deposit
  wallet concurrently.
- ETH needs a wallet-level nonce policy before it can be accepted for production
  withdrawals.

The Helm fork now expresses the payout runtime as chart API instead of wrapper
logic:

- TRON, TON, and ETH rails render dedicated payout workers when enabled.
- SHKeeper renders execution reconciler and callback dispatcher workers.
- Rail sync is chart-owned through `flask payout-rail-sync` and generated
  `PAYOUT_RAILS_JSON`.
- Enabled rails fail rendering unless required Secret refs, backup/restore
  evidence, operational resource bounds, callback endpoint id, source wallet ref,
  sidecar service, queue, storage, migrations, NetworkPolicy, and owned image
  repository are configured.
- Pod-local Redis is rendered as a Phase 1 single-node compromise with
  `replicas: 1`, `Recreate`, PVC-backed AOF, probes, `preStop`, and termination
  grace.
- Payout-critical sidecar env is rail-scoped. Legacy `extraEnv` cannot override
  queue/provisioning/auth env for an enabled payout rail.

Remaining Helm-side production inputs are environment values, not chart behavior:
published owned image tags or immutable digests built from the final reviewed
commits, real Secret/external-secret objects, restore-drill evidence values, and
one-rail-at-a-time staging/production smoke evidence. A production overlay that
renders is not enablement evidence until its image tags or digests map to the
final SHKeeper and sidecar commits and the registry digests are recorded.

### Local Validation Limits

The Grither Pay repository was validated separately for integration handoff.
Grither-specific implementation details, exact Java package paths, and ledger
integration steps are intentionally documented outside the SHKeeper execution
contract so SHKeeper does not grow Grither-only API fields or model names.

## Responsibility Boundaries

### Grither Pay

Grither Pay is the system of record for user withdrawals.

It owns:

- user balance ledger;
- withdrawal request creation;
- business approval and risk checks;
- balance reservation;
- atomic creation of the withdrawal row, ledger reservation entry, and outbound
  SHKeeper-submit outbox event in one database transaction;
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

- service-to-service execution API for authenticated payout consumers;
- required idempotency by `external_id`;
- durable payout execution records;
- normalized state machine across rails;
- sidecar preflight and submit calls;
- polling/reconciliation of sidecar task or execution state;
- signed callbacks to configured payout consumers;
- status lookup by `external_id`;
- operator-visible `RECONCILIATION_REQUIRED` state.

Legacy admin/manual payout endpoints may stay available, but Grither Pay must
use the new execution API only.

When a rail is enabled for service-consumer client withdrawals, Grither Pay and
other automatic/service consumers must not be able to bypass the new execution API
through legacy payout, legacy autopayout, legacy multipayout, or direct
`crypto.mkpayout`/`crypto.multipayout` paths. Manual/admin SHKeeper payouts may
remain available, including from the same current `fee_deposit` source wallet,
but same-wallet manual spend must be an explicit operator action with audit
metadata and must use the same wallet lock/nonce/seqno/resource guard needed to
avoid conflicting with client withdrawals.

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
- sidecar-local execution state by `execution_id`;
- terminal execution result shape.

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

## Payout Rail Catalog

SHKeeper must have an explicit rail catalog for client withdrawals. Do not infer
the sidecar route from `asset` and `network` string concatenation.

Required catalog fields:

- `consumer`
- `asset`
- `network`
- `crypto_id`
- `sidecar_service`
- `sidecar_symbol`
- `token_contract` or `jetton_master`
- `chain_id_or_network_id`
- `decimals`
- `source_wallet_ref`
- `payout_queue`
- `payout_enabled`
- `callback_endpoint_id`
- `hot_wallet_policy`
- `contract_version`

SHKeeper must not own client/business payout amount limits. Per-withdrawal,
daily, tier, wallet, and compliance limits belong to the upstream product ledger
before it submits a payout execution request. SHKeeper validates only technical
execution invariants: auth, rail enablement, idempotency, supported
asset/network, positive canonical USDT amount, destination, sidecar routing,
callbacks, and audit state.

First-release catalog mapping:

| API asset | API network | SHKeeper `crypto_id` | sidecar service | sidecar symbol | default payout queue | Phase 1 source wallet |
| --- | --- | --- | --- | --- | --- | --- |
| `USDT` | `TRON` | `USDT` | `tron-shkeeper` | `USDT` | `tron_usdt_fee_payouts` | existing TRON `fee_deposit` key |
| `USDT` | `TON` | `TON-USDT` | `ton-shkeeper` | `TON-USDT` | `ton_usdt_payouts` | existing TON `fee_deposit` account |
| `USDT` | `ETH` | `ETH-USDT` | `ethereum-shkeeper` | `ETH-USDT` | `eth_usdt_payouts` | existing ETH `fee_deposit` account; production disabled until owned image tag, restore evidence, and smoke payout gates pass |

Phase 1 must keep the sidecar source-wallet model as-is. Do not rename
`fee_deposit`, do not introduce a dedicated payout wallet migration, and do not
change manual admin payout semantics as part of client-withdrawal hardening.
`source_wallet_ref`/source-wallet reference must record the existing sidecar
source exactly as the sidecar uses it. The reliability work wraps the existing
sidecar payout transfer primitive with durable execution, idempotency, status,
and reconciliation boundaries.

`tron_usdt_fee_payouts` is retained for TRON Phase 1 because the current TRON
sidecar and Helm fork already use that queue for the dedicated resource/payout
path. Rename it only through a coordinated sidecar, Helm, readiness, deploy
verification, and test migration. The invariant is more important than the exact
name: SHKeeper rail catalog, sidecar enqueue config, worker `-Q`, readiness
check, and Helm values must all resolve to the same queue.

## SHKeeper Execution API

Add a new service-to-service API for authenticated payout consumers. Do not reuse
the legacy admin payout endpoint as the consumer-facing client-withdrawal API.
The implementation may call the same sidecar transfer primitive that the current
sidecar `/payout` path uses, but only after creating durable execution state and
only through the safe submit/status/reconciliation boundaries defined here.

### Submit

```http
POST /api/v1/payout-executions
```

Request:

```json
{
  "external_id": "client-withdrawal-uuid",
  "asset": "USDT",
  "network": "TRON",
  "amount": "25.000000",
  "destination": "T..."
}
```

Rules:

- `external_id` is required and must be globally unique within the authenticated
  consumer. It must be the consumer's immutable withdrawal/business idempotency
  identifier.
- `asset` must be `USDT`.
- `network` must be one of `TRON`, `TON`, `ETH`.
- `amount` must be a positive decimal string in canonical USDT units. SHKeeper
  rejects more than 6 decimal places, normalizes accepted values to exactly 6
  decimal places for hashing/storage/callbacks, and never uses binary floating
  point for payout amounts.
- `destination` must be present and network-valid.
- The authenticated consumer configuration must allow the requested rail, asset,
  and callback endpoint before SHKeeper creates an execution. Amount/day policy
  is enforced by the upstream product before calling SHKeeper.
- Callback target is resolved from SHKeeper consumer configuration. The API
  consumer must not send an arbitrary callback URL in the payout request. A
  request-level callback override is allowed only if it matches an explicit
  allowlist for that consumer and environment.

Response:

```json
{
  "status": "ACCEPTED",
  "execution_id": 123,
  "external_id": "client-withdrawal-uuid",
  "network": "TRON",
  "asset": "USDT",
  "state": "CREATED"
}
```

`POST /api/v1/payout-executions` is a durable accept boundary, not a synchronous
sidecar broadcast/enqueue guarantee. The response may return `CREATED` after the
execution row is committed. A separate DB-backed SHKeeper payout execution
worker/reconciler performs sidecar preflight and submit, then moves the execution
through `PREFLIGHTED`, `ENQUEUEING`, and `ENQUEUED`. Consumers must use status
lookup and callbacks for monotonic state progression.

### Status

```http
GET /api/v1/payout-executions/{external_id}
```

The authenticated service identity determines `consumer`. This endpoint is safe
because `external_id` is unique per consumer across all payout rails and assets.
Do not allow the same consumer withdrawal identifier to represent both a TRON and
ETH execution.

Response:

```json
{
  "consumer": "wallet-client",
  "execution_id": 123,
  "sidecar_execution_id": null,
  "external_id": "client-withdrawal-uuid",
  "contract_version": "usdt-payout-execution-v1",
  "network": "TRON",
  "asset": "USDT",
  "state": "ENQUEUED",
  "failure_class": null,
  "event_version": 4,
  "state_transition_id": "state-transition-id",
  "occurred_at": "2026-06-03T10:15:00Z",
  "updated_at": "2026-06-03T10:15:02Z",
  "amount": "25.000000",
  "destination": "T...",
  "request_hash": "shkeeper-canonical-request-hash",
  "sidecar_payload_hash": "sidecar-canonical-payload-hash",
  "sidecar_state": "RECEIVED",
  "sidecar_state_version": 1,
  "txids": [],
  "message_hashes": [],
  "error_code": null,
  "error_message": null,
  "reconciliation_required": false
}
```

Status response must include both SHKeeper `execution_id` and
`sidecar_execution_id` when known. In the first release, SHKeeper `execution_id`
is the only required cross-service execution key. `sidecar_execution_id` is
optional correlation metadata; if present, it must be unique, immutable, and
never used as the idempotency or status lookup key. Its absence must not block
reconciliation. Status must also include the current
`event_version`, `state_transition_id`, `occurred_at`, and `updated_at`.
`event_version` is the same monotonically increasing execution ordering value
used by callback events, and `state_transition_id` is the transition that
produced the current state. Status must expose the same business-critical fields
as callback payloads: rail, amount, destination, hashes, failure class, txids or
message hashes, and reconciliation flag.

Grither Pay must apply callback and polling updates monotonically by
`event_version` and `state_transition_id`. A delayed callback or stale status
response must never regress the withdrawal state. If the same `event_version` or
`state_transition_id` is observed with conflicting state, txid/message hash, or
failure data, Grither Pay must keep funds reserved and move the withdrawal to
operator reconciliation.

### Authentication

The new execution API must not use admin Basic Auth.

Use scoped service-to-service authentication for payout consumers:

- service key or token scoped to payout execution;
- HMAC-SHA256 signature over:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`;
- required headers: `X-Payout-Consumer`, `X-Payout-Key-Id`,
  `X-Payout-Timestamp`, `X-Payout-Nonce`, `X-Payout-Signature`;
- replay protection through timestamp tolerance and nonce storage;
- no reuse of the generic wallet API key if that key is also used for invoice
  callback trust.

The auth scheme must support key rotation without a deploy-time code change.

## Idempotency

Idempotency key:

```text
consumer + external_id
```

`external_id` must be the consumer's immutable withdrawal identifier and must not
be reused for a different rail, asset, destination, or amount.

`request_hash` is computed from the canonical payout request:

```text
consumer
external_id
asset
network
amount
destination
configured_callback_endpoint_id
contract_version
```

`amount` in both hashes is the canonical 6-decimal USDT string. Payloads such as
`25`, `25.0`, and `25.000000` are the same request only after normalization and
must produce one canonical hash. Payloads with more than 6 decimal places are
invalid rather than rounded.

There are two distinct hashes:

- `request_hash`: SHKeeper canonical request hash. It covers the full
  client-facing execution request, including SHKeeper-only fields such as
  `configured_callback_endpoint_id`. It is stored by SHKeeper and sent to sidecars
  only as opaque correlation/audit metadata.
- `sidecar_payload_hash`: sidecar canonical payload hash. It covers only fields
  available to the sidecar: `consumer`, `execution_id`, `external_id`, `asset`,
  `network`, `amount`, `destination`, and `contract_version`. Sidecars verify this
  hash before creating or reusing an execution.

Behavior:

- First submit creates a SHKeeper execution record.
- Repeated submit with the same payload returns the existing execution.
- Repeated submit with the same `external_id` but different amount,
  destination, asset, or network returns `409 IDEMPOTENCY_CONFLICT`.
- Repeated submit with equivalent non-canonical amount formatting returns the
  existing execution after canonicalization, not a new execution and not a false
  conflict.
- A consumer must never retry by creating a new `external_id` for the same user
  withdrawal after a timeout.
- After a submit timeout, the consumer must call status lookup.

## SHKeeper Execution State Machine

States:

```text
CREATED
PREFLIGHTED
ENQUEUEING
ENQUEUED
BROADCAST
CONFIRMED
FAILED_PRE_BROADCAST
FAILED_CHAIN_TERMINAL
RECONCILIATION_REQUIRED
```

Meaning:

- `CREATED`: durable row exists before sidecar submission.
- `PREFLIGHTED`: SHKeeper and sidecar preflight accepted the request.
- `ENQUEUEING`: SHKeeper is calling the sidecar submit endpoint.
- `ENQUEUED`: sidecar accepted the execution and returned a sidecar execution ID.
- `BROADCAST`: transaction hash is known, confirmation is pending.
- `CONFIRMED`: confirmation policy is satisfied.
- `FAILED_PRE_BROADCAST`: deterministic terminal failure before any possible
  broadcast.
- `FAILED_CHAIN_TERMINAL`: the intended transaction/message is known and chain
  verification proves it reached a terminal failed state without the requested
  USDT transfer.
- `RECONCILIATION_REQUIRED`: SHKeeper cannot safely determine whether enqueue
  or broadcast happened.

`RECONCILIATION_REQUIRED` is not a terminal business failure. Grither Pay keeps
funds reserved until operator reconciliation resolves the state.

Recovery policy by risk class:

- Safe automatic retry is allowed before sidecar submit, before signing, or before
  any state where a chain transaction/message could have been broadcast.
- Automatic business failure is allowed only for deterministic pre-broadcast
  failures: validation, preflight, insufficient balance, unsupported rail,
  invalid destination, or worker unavailable before submit. A confirmed terminal
  chain failure is an execution terminal condition, but Grither Pay must map it
  to manual review with funds reserved until operator accounting resolution.
- `FAILED_PRE_BROADCAST` and `FAILED_CHAIN_TERMINAL` must be separate states or
  separate failure classes in storage and callbacks. Grither Pay must never infer
  "safe to release customer funds" from a generic `FAILED`.
- Manual reconciliation is required when SHKeeper timed out calling sidecar submit,
  sidecar accepted work but status is lost, worker died during signing/broadcast,
  RPC returned an unknown broadcast result, or tx hash/message hash exists but
  confirmation status is unclear.
- Manual payout is allowed only after reconciliation records evidence that the
  original automatic execution did not complete the requested USDT transfer and
  cannot still complete later.

## Grither Pay Withdrawal State Machine

Recommended states:

```text
REQUESTED
APPROVED
FUNDS_RESERVED
SUBMITTING
SUBMITTED
BROADCAST
CONFIRMING
CONFIRMED
COMPLETED
FAILED_VALIDATION
FAILED_PRECHECK
FAILED_PRE_BROADCAST
FAILED_CHAIN_TERMINAL
RECONCILIATION_REQUIRED
MANUAL_REVIEW
SAFE_FOR_MANUAL_PAYOUT
MANUAL_PAYOUT_PENDING
MANUAL_PAYOUT_COMPLETED
```

Rules:

- Funds are reserved before calling SHKeeper.
- Funds remain reserved for `SUBMITTING`, `SUBMITTED`, `BROADCAST`,
  `CONFIRMING`, `RECONCILIATION_REQUIRED`, `MANUAL_REVIEW`,
  `SAFE_FOR_MANUAL_PAYOUT`, and `MANUAL_PAYOUT_PENDING`.
- Funds are released automatically only for terminal failures that are known to
  have happened before broadcast: `FAILED_VALIDATION`, `FAILED_PRECHECK`, or
  `FAILED_PRE_BROADCAST`.
- `FAILED_CHAIN_TERMINAL` must be handled through an explicit accounting policy:
  chain proof says the USDT transfer did not complete, but gas/resource cost and
  customer-facing retry/refund behavior must be deliberate and auditable.
- First release accounting policy for `FAILED_CHAIN_TERMINAL`: move to
  `MANUAL_REVIEW`, keep funds reserved, and require operator resolution. The
  operator may refund/release principal, create a new withdrawal attempt, or mark
  the withdrawal resolved only after recording chain proof, gas/resource cost, and
  the chosen accounting action. Automatic release for chain-terminal failures is
  out of scope for the first release.
- Funds are finalized only after `CONFIRMED`.
- `SAFE_FOR_MANUAL_PAYOUT` is reachable only after SHKeeper reconciliation
  evidence proves the original automatic execution did not complete the requested
  USDT transfer and cannot still complete later.
- `MANUAL_PAYOUT_COMPLETED` stores the manual transaction evidence and closes the
  withdrawal without creating a second automatic SHKeeper execution.
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
- `contract_version`
- `asset`
- `network`
- `amount`
- `destination`
- `canonical_request`
- `callback_endpoint_id`
- `callback_url_snapshot`
- `state`
- `failure_class`
- `sidecar_execution_id`: nullable correlation ID from the sidecar when the
  sidecar keeps an internal ID distinct from SHKeeper `execution_id`.
- `sidecar_task_id`
- `last_sidecar_state`
- `last_sidecar_state_version`
- `last_sidecar_state_transition_id`
- `last_sidecar_status_hash`
- `last_sidecar_status_observed_at`
- `txids`
- `message_hashes`
- `error_code`
- `error_message`
- `request_hash`
- `sidecar_payload_hash`
- `attempt_count`
- `submitted_at`
- `broadcasted_at`
- `confirmed_at`
- `state_version`
- `last_state_transition_id`
- `last_state_occurred_at`
- `last_callback_event_id`
- `last_callback_event_version`
- `last_callback_dispatched_at`
- `resolution_status`
- `resolution_evidence`
- `resolved_by`
- `resolved_at`
- `created_at`
- `updated_at`
- `last_polled_at`
- `lease_owner`
- `lease_acquired_at`
- `lease_expires_at`
- `attempt_id`
- `claim_token`

Required constraints:

- unique `(consumer, external_id)`;
- `request_hash` is immutable after creation;
- `sidecar_payload_hash` is immutable after creation;
- store enough request data to detect idempotency conflicts;
- `state_version` increases transactionally with every execution state
  transition and is the source of the status `event_version`;
- `last_state_transition_id` is immutable for a given state transition and is
  returned by both status and callback payloads for ordering/deduplication;
- sidecar status observations are applied monotonically by
  `last_sidecar_state_version` and `last_sidecar_state_transition_id`; stale
  sidecar status must not overwrite newer SHKeeper execution state;
- conflicting sidecar status for the same sidecar version/transition keeps the
  execution reserved and moves it to `RECONCILIATION_REQUIRED`;
- index non-terminal states for polling and reconciliation;
- index `(consumer, external_id)` for status lookup;
- dispatcher/reconciler leases are persisted with `lease_owner`,
  `lease_acquired_at`, `lease_expires_at`, `attempt_id`, and `claim_token`, or an
  equivalent row-lock/compare-and-set claim mechanism. Concurrent dispatchers and
  reconcilers must not be able to submit sidecar work or create callback events
  twice for the same state transition;
- manual resolution must be auditable and must record evidence before a payout is
  marked safe for manual payout.

Manual resolution evidence:

- `resolution_status` must be one of `UNRESOLVED`,
  `SAFE_FOR_MANUAL_PAYOUT`, `CHAIN_BROADCAST_FOUND`,
  `MANUAL_PAYOUT_COMPLETED`, or `CANCELLED_PRE_BROADCAST`.
- Evidence must include rail, asset, SHKeeper execution ID, sidecar execution ID
  when known, external ID, destination, amount, last SHKeeper state, and last
  sidecar state.
- Evidence must include what was checked on-chain: txids/message hashes, nonce or
  seqno range when applicable, source wallet, destination, amount, token contract,
  queried RPC/indexer/explorer source, and searched block/time range.
- `SAFE_FOR_MANUAL_PAYOUT` is allowed only when the evidence shows no finalized
  matching token transfer and no pending original transaction/message that can
  still complete.
- `CHAIN_BROADCAST_FOUND` must move the business process back to broadcast,
  confirmation, or `FAILED_CHAIN_TERMINAL` handling, not directly to manual
  payout.
- High-value thresholds may require a second operator approval, but the first
  release must at least store actor, timestamp, evidence, and operator note.

## Callback Event Contract

Callbacks from SHKeeper to Grither Pay are part of the payout state machine, not
best-effort notifications. Every callback event must be durable, signed, and
idempotent.

Required callback payload fields:

- `event_id`: globally unique callback event ID.
- `event_version`: monotonically increasing integer for the SHKeeper execution.
- `state_transition_id`: unique ID of the SHKeeper state transition that produced
  the event.
- `occurred_at`: server timestamp when the state transition occurred.
- `consumer`
- `execution_id`
- `sidecar_execution_id`: nullable correlation ID from the sidecar when the
  sidecar keeps an internal ID distinct from SHKeeper `execution_id`.
- `external_id`
- `asset`
- `network`
- `amount`
- `destination`
- `previous_state`
- `state`
- `failure_class`
- `txids`
- `message_hashes`
- `error_code`
- `error_message`
- `reconciliation_required`

Callback rules:

- `event_id` is unique and immutable.
- `event_version` must increase for each emitted event for the same
  `execution_id`.
- `state_transition_id` must be stable across callback retries for the same state
  transition.
- Callback retries must resend the exact same payload and signature base for the
  same `event_id`.
- `sidecar_execution_id` may be `null`; the consumer must not require it for
  deduplication, status lookup, or reconciliation.
- The consumer must deduplicate by `event_id` first. The fallback stable key is
  `execution_id + state + txid/message_hash + event_version`.
- Callback delivery is not the source of truth. The consumer may poll status after
  missed or delayed callbacks, but polling must not create a new execution.
- Status polling and callbacks share the same execution ordering metadata:
  `event_version`, `state_transition_id`, and `occurred_at`.

Required callback outbox storage:

- `event_id`
- `execution_id`
- `consumer`
- `external_id`
- `event_version`
- `state_transition_id`
- `occurred_at`
- `callback_endpoint_id`
- `payload`
- `payload_hash`
- `signature_base`
- `signature_headers`
- `dispatch_status`
- `attempt_count`
- `next_attempt_at`
- `last_attempt_at`
- `last_error`
- `created_at`
- `updated_at`

Callback outbox constraints:

- unique `event_id`;
- unique `(execution_id, event_version)`;
- unique `state_transition_id`;
- `payload`, `payload_hash`, `signature_base`, and `signature_headers` are
  immutable after event creation;
- retries resend the exact stored payload and signature base, not a regenerated
  payload from the current execution row;
- callback dispatcher progress is tracked only through dispatch metadata, never by
  mutating the event payload.

### Manual Payout Negative Evidence

Manual payout is allowed only after rail-specific negative evidence proves that
the automatic execution did not send the requested USDT transfer. One generic
"not found" lookup is not enough.

Shared requirements:

- Evidence must be attached to the SHKeeper execution before Grither Pay can enter
  `SAFE_FOR_MANUAL_PAYOUT`.
- Evidence must include the exact source hot wallet, destination, amount, USDT
  contract or Jetton master, execution IDs, request hash, signed artifact hash
  when known, and the operator who performed the check.
- Evidence must include the queried data sources, block or masterchain range, time
  range, and finality/wait window used for the rail.
- Evidence must prove that any known signed artifact is no longer capable of
  completing. Waiting only for a generic confirmation count is not enough if the
  original transaction/message can still be accepted later.
- If any required check cannot be completed, the execution remains
  `RECONCILIATION_REQUIRED`; do not manually pay.

TRON-USDT negative evidence:

- Check known txid if signed evidence exists.
- Search TRC20 `Transfer` events for source wallet, destination, amount, and USDT
  contract.
- Search source wallet transaction history across the signed/ref-block to current
  finalized range.
- If signed TRON transaction evidence exists, wait until the transaction
  expiration/ref-block validity window has passed and verify the txid is not
  pending or confirmed.
- Query the configured fullnode/Solidity node and, when available, an independent
  indexer or explorer source.
- Wait for the configured TRON negative-evidence confirmation count or minimum
  elapsed time before marking safe for manual payout.

TON-USDT negative evidence:

- Check message hash, signed BOC hash, source wallet seqno, and source Jetton
  wallet when known.
- Search source wallet transactions and Jetton transfer history for destination,
  amount, Jetton master, and response wallet.
- Cover the masterchain seqno or time window from signing/enqueue to the current
  finalized point.
- If signed BOC/message evidence exists, verify the message hash, valid-until
  window, source wallet seqno progression, and Jetton wallet history prove the
  original message cannot still execute.
- Query the configured TON center/indexer and, when available, a second explorer
  or indexer source.
- Wait for the configured TON negative-evidence masterchain/finality window before
  marking safe for manual payout.

ETH-USDT negative evidence:

- Check known tx hash and receipt if signed evidence exists.
- Check source address nonce progression and detect replacement transactions with
  the same nonce.
- If signed ETH transaction evidence exists, manual payout is safe only when the
  nonce has been consumed by a finalized same-nonce transaction and chain/log
  evidence proves there is no matching USDT `Transfer` for the requested payout.
  If a matching USDT `Transfer` exists, the automatic execution completed and
  manual payout is forbidden.
- If the nonce remains unused or unfinalized, keep the execution in
  `RECONCILIATION_REQUIRED` even if the original tx disappears from txpool,
  provider, explorer, or indexer views. Disappearance from provider views after a
  wait window is not negative proof for a signed raw ETH transaction because the
  raw transaction can be rebroadcast while the nonce is still available.
- Search ERC20 `Transfer` logs for source wallet, destination, amount, and USDT
  contract across the signed/enqueue block to current finalized block.
- Query the configured RPC provider and, when available, an independent explorer
  or indexer. If txpool access is available, verify no pending original tx remains.
- Wait for the configured ETH negative-evidence block count and minimum elapsed
  time before marking safe for manual payout.

## Sidecar Contract

All three sidecars must expose the same minimum payout execution contract for
USDT. Path prefixes are rail-specific: TRON uses `/USDT`, TON uses `/TON-USDT`,
and ETH uses `/ETH-USDT` in the first release.

### Sidecar Authentication And Authorization

Client payout execution endpoints must not rely on the existing generic sidecar
Basic Auth. Legacy Basic Auth remains acceptable for legacy internal/admin
endpoints, but automated client withdrawals use scoped payout HMAC as the
production trust boundary. TRON, TON, and ETH expose
`/payout-executions/<execution_id>` endpoints that can be called by SHKeeper with
HMAC and without loading or authenticating through legacy wallet modules.

Requirements:

- only SHKeeper's payout execution service can call preflight, submit, and status;
- use a scoped sidecar payout credential and HMAC-SHA256 request signature with
  these headers: `X-Payout-Consumer`, `X-Payout-Key-Id`,
  `X-Payout-Timestamp`, `X-Payout-Nonce`, `X-Payout-Signature`;
- HMAC signature base is:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`;
- mTLS or service-mesh identity may be added as a transport-level defense, but it
  does not replace the signed payout request contract;
- the authentication layer must cover the raw request body so `consumer`,
  `execution_id`, `external_id`, amount, destination, and hashes cannot be
  modified in transit;
- replay protection is required through timestamp/nonce tolerance;
- authenticated caller identity must be mapped to allowed rails and consumers;
- sidecar must reject a request where body `consumer` is not allowed for the
  authenticated SHKeeper caller;
- sidecar must reject path/body `execution_id` mismatches on v1
  `/payout-executions/<execution_id>` routes;
- production Kubernetes must restrict sidecar payout endpoints to internal
  service traffic with NetworkPolicy or equivalent ingress controls;
- key rotation must be possible without changing application code.

### Preflight

Examples below use the TRON `/USDT` prefix. TON and ETH use the same contract
under `/TON-USDT` and `/ETH-USDT`.

```http
POST /USDT/payout/preflight
```

Request:

```json
{
  "consumer": "wallet-client",
  "execution_id": "shkeeper-execution-id",
  "external_id": "client-withdrawal-uuid",
  "contract_version": "usdt-payout-execution-v1",
  "asset": "USDT",
  "network": "TRON",
  "request_hash": "shkeeper-canonical-request-hash",
  "sidecar_payload_hash": "sidecar-canonical-payload-hash",
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
  "consumer": "wallet-client",
  "execution_id": "shkeeper-execution-id",
  "external_id": "client-withdrawal-uuid",
  "contract_version": "usdt-payout-execution-v1",
  "asset": "USDT",
  "network": "TRON",
  "request_hash": "shkeeper-canonical-request-hash",
  "sidecar_payload_hash": "sidecar-canonical-payload-hash",
  "destination": "...",
  "amount": "25.000000"
}
```

Response:

```json
{
  "consumer": "wallet-client",
  "execution_id": "shkeeper-execution-id",
  "sidecar_execution_id": null,
  "external_id": "client-withdrawal-uuid",
  "request_hash": "shkeeper-canonical-request-hash",
  "sidecar_payload_hash": "sidecar-canonical-payload-hash",
  "task_id": null,
  "state": "ENQUEUED",
  "state_version": 1,
  "state_transition_id": "sidecar-state-transition-id",
  "state_updated_at": "2026-06-03T10:15:00Z",
  "failure_class": null,
  "reconciliation_required": false
}
```

Submit rules:

- `consumer`, `execution_id`, `external_id`, `contract_version`, `request_hash`,
  and `sidecar_payload_hash` are required.
- `asset` and `network` in the body must match the sidecar rail and endpoint.
- Sidecar must canonicalize the sidecar-visible payload, recompute
  `sidecar_payload_hash`, and reject the submit if the computed hash differs from
  the provided value.
- `request_hash` is SHKeeper-only canonical metadata. Sidecar stores it for audit
  and correlation but must not claim to verify it unless SHKeeper sends every
  field covered by that hash.
- Sidecar must persist the canonical request snapshot with the execution record.
- Sidecar creates or finds a durable local execution before enqueue.
- Duplicate same payload returns existing execution/status.
- Duplicate changed payload returns `409`.
- `task_id` is optional transport metadata only; it must never be the durable
  execution identity.
- Submit response must include the sidecar execution ordering metadata
  (`state_version`, `state_transition_id`, `state_updated_at`) for the returned
  execution. Duplicate same-payload submit returns the current execution/status
  with the same ordering rules as `GET /USDT/payout/status/{execution_id}`.
- Submit must repeat critical preflight checks immediately before enqueue/signing
  to close the preflight/submit time-of-check/time-of-use gap.
- Sidecar must not blind retry after ambiguous broadcast timeout.
- Terminal execution result shape is normalized.

### Status

```http
GET /USDT/payout/status/{execution_id}
```

Lookup by `(consumer, external_id)` may be supported as a secondary idempotency
lookup, but `execution_id` is the primary sidecar status key for SHKeeper.

Required response shape:

```json
{
  "consumer": "wallet-client",
  "execution_id": "shkeeper-execution-id",
  "sidecar_execution_id": null,
  "external_id": "client-withdrawal-uuid",
  "contract_version": "usdt-payout-execution-v1",
  "asset": "USDT",
  "network": "TRON",
  "state": "SIGNED",
  "state_version": 5,
  "state_transition_id": "sidecar-state-transition-id",
  "state_updated_at": "2026-06-03T10:15:00Z",
  "updated_at": "2026-06-03T10:15:02Z",
  "request_hash": "shkeeper-canonical-request-hash",
  "sidecar_payload_hash": "sidecar-canonical-payload-hash",
  "source_wallet": "hot-wallet-address",
  "token_contract": "usdt-contract-address",
  "jetton_master": null,
  "jetton_wallet": null,
  "chain_id_or_network_id": "tron-mainnet",
  "reference_block_or_masterchain_seqno": "ref-block-or-seqno",
  "transaction_expiration_or_valid_until": "2026-06-03T10:20:00Z",
  "nonce_or_seqno": "123",
  "signed_payload_hash": "signed-raw-tx-or-boc-hash",
  "signed_payload_stored_at": "2026-06-03T10:15:01Z",
  "txid_or_message_hash": "txid-or-message-hash",
  "broadcast_attempted_at": null,
  "broadcast_provider": null,
  "last_chain_check_at": null,
  "last_chain_check_source": null,
  "failure_class": null,
  "error_code": null,
  "error_message": null,
  "reconciliation_required": false
}
```

Rail-inapplicable fields must exist and be `null`; rail-applicable evidence
fields become mandatory once the execution crosses the related boundary. After
`SIGNED`, the status must include the signed payload hash, storage timestamp, and
nonce/seqno or equivalent rail artifact. After `BROADCASTING`, it must include
the broadcast attempt marker and provider. After `BROADCASTED`, it must include
the txid/message hash. Reconciliation and confirmation states must include the
latest chain-check timestamp and source. `state_version` must increase
monotonically so SHKeeper reconciliation cannot apply stale sidecar status over a
newer local observation. Sidecar `state_version` is independent from SHKeeper
`event_version`; SHKeeper stores the last applied sidecar version/transition and
then emits its own ordered SHKeeper state transition to Grither Pay.

### Sidecar State Machine

All payout sidecars must implement a durable state machine for client withdrawal
executions. The exact database model may differ by sidecar, but state semantics
must be consistent.

```text
RECEIVED
VALIDATED
SIGNING
SIGNED
BROADCASTING
BROADCASTED
CONFIRMING
CONFIRMED
FAILED_PRE_BROADCAST
FAILED_CHAIN_TERMINAL
RECONCILIATION_REQUIRED
```

Meaning:

- `RECEIVED`: durable sidecar execution exists; no signing or broadcast has
  started.
- `VALIDATED`: critical checks passed inside submit, not only in preflight.
- `SIGNING`: worker lease acquired and signing is in progress; no network submit
  has started.
- `SIGNED`: deterministic pre-broadcast evidence is stored. Examples: TRON txid,
  TON message hash and seqno, ETH nonce and tx hash.
- `BROADCASTING`: network/RPC submit is in progress. This is an unsafe automatic
  retry boundary.
- `BROADCASTED`: network/RPC returned a known tx/message hash or accepted result.
- `CONFIRMING`: broadcast is known and confirmation policy is pending.
- `CONFIRMED`: rail-specific confirmation and token transfer verification passed.
- `FAILED_PRE_BROADCAST`: deterministic terminal failure before any possible
  broadcast.
- `FAILED_CHAIN_TERMINAL`: chain returned a confirmed terminal failure for the
  intended transaction.
- `RECONCILIATION_REQUIRED`: sidecar cannot prove whether broadcast happened or
  cannot safely determine the final state.

Transition rules:

- State transitions must use compare-and-set updates or equivalent transactional
  guards.
- Workers must acquire a lease before moving an execution into `SIGNING`.
- Safe automatic retry/re-enqueue is allowed only from states that prove no
  broadcast could have happened, such as `RECEIVED`, `VALIDATED`, and stale
  `SIGNING` before signed evidence exists.
- Stale `SIGNING` is safe to retry only if there is no durable nonce/seqno
  reservation, no resource reservation that can lead to a later broadcast, no
  signed payload hash, and no broadcast-attempt marker. If any of those side
  effects exist, recovery must either deterministically reuse the same signed
  artifact/nonce/seqno or move to `RECONCILIATION_REQUIRED`.
- Once state reaches `SIGNED` or `BROADCASTING`, retry policy must be evidence
  driven. If status cannot be recovered by deterministic sidecar/chain evidence,
  move to `RECONCILIATION_REQUIRED`.
- Operator/manual resolution must record evidence and actor metadata before any
  business system is allowed to perform a manual payout.

Required side-effect fields:

Rail-inapplicable fields may be null, but these fields must exist in the
execution schema. The mandatory status response above exposes the
reconciliation-safe subset so tooling can use one contract across TRON, TON, and
ETH without reading raw execution rows.

- `consumer`
- `execution_id`
- `external_id`
- `contract_version`
- `asset`
- `network`
- `state`
- `state_version`
- `state_transition_id`
- `state_updated_at`
- `canonical_payload`
- `request_hash`
- `sidecar_payload_hash`
- `source_wallet`
- `token_contract`
- `jetton_master`
- `jetton_wallet`
- `chain_id_or_network_id`
- `reference_block_or_masterchain_seqno`
- `transaction_expiration_or_valid_until`
- `lease_owner`
- `lease_expires_at`
- `attempt_id`
- `nonce_or_seqno`
- `nonce_seqno_reserved_at`
- `resource_reservation_id`
- `resource_reservation_status`
- `signed_payload_storage_ref`
- `signed_payload_hash`
- `signed_payload_stored_at`
- `txid_or_message_hash`
- `broadcast_attempted_at`
- `broadcast_provider`
- `last_chain_check_at`
- `last_chain_check_source`
- `failure_class`
- `error_code`
- `error_message`
- `reconciliation_required`

## Sidecar Rail Requirements

Hot-wallet policy:

- Phase 1 keeps the current sidecar payout source wallet exactly as today's
  sidecar `/payout` implementation uses it.
- TRON and TON keep the existing `fee_deposit` source wallet name and semantics.
- Do not migrate to a dedicated payout wallet in this release.
- Every code path that can spend from the same source wallet must go through the
  same wallet-level lock, nonce/seqno/resource guard, and audit trail before that
  rail is enabled for client withdrawals.
- This requirement is per rail. Proving TRON `fee_deposit` safety does not prove
  TON or ETH wallet safety.

### TRON-USDT

Keep and normalize the current hardening:

- resource quote and resource preflight;
- ProfeeX-based destination/resource estimate for the current implementation;
- fail-closed when the payout worker is unavailable;
- dedicated `tron-usdt-payouts` worker/container consuming the catalog queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- Redis lock around fee-deposit resource provisioning and transfer;
- payout-specific transaction expiration/ref-block validity cap. The first
  production release must not use the legacy 12-hour expiration window for client
  withdrawals; use a configured short max validity window, with 10 minutes
  recommended and 30 minutes as the hard cap unless a later review proves a longer
  window is required;
- reject unactivated destinations unless a separate activation design is
  approved.

Add:

- sidecar-local execution table with primary/unique `execution_id` and unique
  `(consumer, external_id)`;
- submit/status by `execution_id`, with `(consumer, external_id)` as an
  idempotency lookup;
- normalized terminal result shape;
- chart values/schema cleanup so payout worker enablement is chart API, not only
  env-derived behavior.

### TON-USDT

Add:

- dedicated `ton-usdt-payouts` worker/container consuming the catalog queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- sidecar-local execution table with primary/unique `execution_id` and unique
  `(consumer, external_id)`;
- worker readiness check;
- preflight for Jetton USDT balance and TON fee balance;
- serialized fee-deposit wallet seqno path;
- submit/status by `execution_id`, with `(consumer, external_id)` as an
  idempotency lookup;
- normalized terminal result shape.

Keep:

- no retry for `sendBoc` or `sendBocReturnHash` after timeout.

Ambiguous TON broadcast timeout must produce `RECONCILIATION_REQUIRED`, not a
blind retry.

### ETH-USDT

Create and harden our fork.

Add:

- dedicated `eth-usdt-payouts` worker/container consuming the catalog queue;
- `concurrency=1`;
- `prefetch-multiplier=1`;
- sidecar-local execution table with primary/unique `execution_id` and unique
  `(consumer, external_id)`;
- worker readiness check;
- address validation;
- ERC20 USDT balance preflight;
- ETH gas balance preflight;
- gas estimate;
- node sync/readiness check;
- serialized nonce path through one active payout worker for the fork's current
  `/payout` source wallet, or a wallet-level nonce allocator if any other code
  path can spend from the same ETH wallet;
- submit/status by `execution_id`, with `(consumer, external_id)` as an
  idempotency lookup;
- normalized terminal result shape.

Do not add a full nonce manager in the first release if the fork's current ETH
`/payout` source wallet is used only by one active payout worker and Helm proves
that singleton topology. If the wallet is shared with deposits sweep, drain,
admin payout, or any other spend path, a nonce allocator or wallet-level lock is
mandatory before production.

Local fork implementation note:

- The first ETH implementation deliberately does not retain spendable signed raw
  transaction bytes. It persists nonce, tx hash, signed raw transaction SHA-256
  hash/ref, source-wallet address, token contract, chain id, broadcast marker,
  and receipt/log evidence.
- Because the spendable raw transaction is not retained, stale `SIGNED` and
  `BROADCASTING` states are not auto-rebroadcast. They become
  `RECONCILIATION_REQUIRED` after lease expiry and require operator evidence.
- This matches the product decision to fail critical ambiguous recovery to manual
  reconciliation rather than auto-retry when duplicate payout risk exists.
- Same-wallet legacy ETH/ETH-USDT payout and token-drain fee-deposit gas seeding
  paths must use the same fee-deposit nonce lock.

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
TRON: tron-usdt-payouts -> tron_usdt_fee_payouts
TON:  ton-usdt-payouts  -> ton_usdt_payouts
ETH:  eth-usdt-payouts  -> eth_usdt_payouts
```

Hyphenated names such as `tron-usdt-payouts` are Kubernetes container/Celery
worker names. Underscore names such as `tron_usdt_fee_payouts` are broker queue
names from the `PayoutRail` catalog. Do not use the worker/container name as the
broker queue name unless the rail catalog explicitly says so.

Worker command pattern:

```bash
celery -A celery_worker.celery worker \
  -Q <catalog.payout_queue> \
  --concurrency=1 \
  --prefetch-multiplier=1 \
  -n <rail>-usdt-payouts@%h
```

The normal `tasks` worker must not consume payout queues when dedicated payout
workers are enabled.

Same-pod dedicated worker is acceptable only if all reliability gates pass:

- durable execution state exists before enqueue;
- sidecar execution state survives pod restart and rollout;
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
must become reconciliation-required unless the txid/message hash or sidecar
execution state can be safely recovered.

## Helm/Kubernetes Design

Add first-class values for all three rails:

```yaml
payouts:
  enabled: true
  consumer: grither-pay
  sidecarRequestTimeoutSeconds: 10
  authMaxAgeSeconds: 300
  networkPolicies:
    enabled: true
  storage:
    mode: singleNodeSqlitePvc
    claimName: shkeeper-db-claim
    allowSeparateWorkerDeployments: true
    backupRestoreEvidence: "restore-drill-id"
  secrets:
    consumerKeys:
      name: shkeeper-payout-consumer-keys
      key: PAYOUT_CONSUMER_KEYS_JSON
    sidecarSigningKeys:
      name: shkeeper-payout-sidecar-signing-keys
      key: PAYOUT_SIDECAR_KEYS_JSON
    sidecarConsumerKeys:
      name: sidecar-payout-consumer-keys
      key: PAYOUT_CONSUMER_KEYS_JSON
    callbackKeys:
      name: shkeeper-payout-callback-keys
      key: PAYOUT_CALLBACK_KEYS_JSON
    callbackEndpoints:
      name: shkeeper-payout-callback-endpoints
      key: PAYOUT_CALLBACK_ENDPOINTS_JSON
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
    tonUsdt:
      enabled: false
      queue: ton_usdt_payouts
      sourceWalletRef: fee_deposit
      ownedImageRepository: nilof470/ton-shkeeper
    ethUsdt:
      enabled: false
      queue: eth_usdt_payouts
      sourceWalletRef: fee_deposit
      ownedImageRepository: nilof470/ethereum-shkeeper

tron_shkeeper:
  usdtPayoutWorker:
    enabled: false
    queue: tron_usdt_fee_payouts
    concurrency: 1
    prefetchMultiplier: 1

ton_shkeeper:
  usdtPayoutWorker:
    enabled: false
    queue: ton_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1

ethereum_shkeeper:
  usdtPayoutWorker:
    enabled: false
    queue: eth_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1
```

`hotWalletMinimumBalance` and `feeWalletMinimumBalance` are optional Prometheus
alert thresholds only; empty values render no low-balance alert and do not
change payout validation. The chart must not render SHKeeper amount/day cap
fields; payout business limits belong to the upstream consumer.

`payouts.rails.*` is the production source of truth. The
`*_shkeeper.usdtPayoutWorker.*` fields may still exist for queue/concurrency
tuning of the rail-rendered worker, but direct
`*_shkeeper.usdtPayoutWorker.enabled=true` must fail unless the matching
`payouts.rails.*.enabled=true` rail is enabled. Manual/admin `/payout` remains a
runtime SHKeeper feature; it must not become a Helm worker bypass around payout
rail production gates. Enabled client-withdrawal rails must fail rendering if
their worker queue conflicts with the rail queue. Payout-critical queue,
provisioning, and auth env vars must not be supplied through sidecar `extraEnv`.
For the first production release, enabled rails must also fail rendering if
`sourceWalletRef` is not `fee_deposit`; dedicated payout wallets require sidecar
source-override support before the chart can expose that as a production option.

Chart requirements:

- render payout worker container when enabled;
- reject direct sidecar payout worker enablement unless the matching
  `payouts.rails.*` entry is enabled;
- render SHKeeper payout execution reconciler and callback-outbox dispatcher
  workers when the payout execution API is enabled. The submit dispatcher is the
  Grither Pay submit outbox; SHKeeper submit is served by the SHKeeper API/web
  deployment;
- restrict normal `tasks` worker to the default queue;
- set payout worker defaults to concurrency 1 and prefetch 1;
- render persistent sidecar execution storage for every enabled payout rail, or
  render explicit external database configuration for that sidecar;
- render/apply sidecar execution DB migrations before payout submit becomes
  ready;
- render/apply SHKeeper payout execution migrations before SHKeeper payout API
  readiness becomes true;
- render a SHKeeper `payout-rail-sync` Job or equivalent chart-owned init path
  that upserts generic `PayoutRail` rows from values before client payout traffic
  is enabled;
- scope `payout-rail-sync` to the configured payout consumer and treat the
  payload as desired state for that consumer: rails removed from Helm values
  must be disabled in SHKeeper DB instead of remaining active from a previous
  release;
- reject duplicate desired rails for the same `(consumer, asset, network)` rather
  than allowing last-write-wins catalog behavior;
- reject unknown `PAYOUT_RAILS_JSON` rail fields so accidental business policy
  fields such as amount/day limits cannot become SHKeeper configuration;
- document and configure backup/restore posture for sidecar execution state;
- provide `values.schema.json` or Helm `required/fail` checks;
- add resources/limits for `app`, `tasks`, `payouts`, and `redis`;
- add startup, readiness, and liveness probes where supported by the sidecars;
- use owned image repositories/tags for controlled sidecar forks;
- reject placeholder production values such as `REPLACE-*`, `TODO`, `TBD`, and
  `PLACEHOLDER` whenever payout topology is enabled;
- render sidecar payout endpoint NetworkPolicy or equivalent ingress restriction
  so only SHKeeper service traffic can reach preflight, submit, and status;
- render bounded SHKeeper-to-sidecar request timeout configuration
  (`PAYOUT_SIDECAR_REQUEST_TIMEOUT`) for preflight, submit, and status calls;
- fail rendering for non-positive or non-numeric payout operational bounds:
  sidecar request timeout, sidecar HMAC max-age, SHKeeper payout worker interval,
  and SHKeeper payout worker batch limit;
- reference payout credentials, HMAC keys, RPC credentials, and hot-wallet secret
  material through Kubernetes Secret references or an external secret provider;
  do not put real secret values in committed values, ConfigMaps, or rendered
  manifests;
- fail rendering for enabled payout topology when SHKeeper or TRON/TON/ETH
  sidecar `extraEnv` contains literal secret/hot-wallet-looking keys such as
  private keys, mnemonics, seeds, passwords, API keys, auth tokens,
  `FEE_DEPOSIT_*`, `HOT_WALLET_*`, wallet secret keys, or payout auth env
  override attempts;
- expose per-rail payout enablement and pause/kill switch as chart/runtime
  configuration. SHKeeper amount/day cap fields must not render in
  `PAYOUT_RAILS_JSON`;
- include production values examples;
- include Helm template tests for worker rendering and queue isolation.

Rollout requirements with pod-local Redis:

- `replicas: 1` for sidecar deployments in Phase 1;
- rollout strategy must avoid two isolated Redis brokers serving payout submit
  traffic at the same time;
- use `Recreate` or equivalent no-surge behavior for sidecar deployments using
  pod-local Redis;
- Redis must use PVC-backed AOF or the rail must prove queued payout work is
  fully reconstructable from sidecar execution state;
- preStop/termination grace must allow in-flight worker tasks to exit cleanly;
- readiness must fail closed during startup, migration, and shutdown for payout
  submit.

If zero-downtime sidecar rollout becomes mandatory, pod-local Redis is no
longer acceptable. Move to an external broker and separate Deployments.

SHKeeper core payout workers have a separate storage constraint. The current
Helm chart mounts SHKeeper core state through `shkeeper-db-claim`, and the
application default remains SQLite under the Flask instance directory. Phase 1
may run the payout execution reconciler and callback dispatcher in separate
single-replica worker pods only as a single-VPS/single-node compromise. Before
SHKeeper core workers become HA, zero-downtime, or multi-node, move SHKeeper
execution state to a real shared database or run the workers in the same pod as
the web process so SQLite is not treated as a distributed coordination layer.

## Grither Pay Integration

Grither Pay changes:

- add withdrawal table/state machine;
- add per-rail enablement and amount validation through the existing
  wallet/business limit layer before calling SHKeeper;
- reserve `payout_amount + network_fee` before SHKeeper submit and send only
  `payout_amount` as the SHKeeper transfer amount;
- call SHKeeper with immutable `WalletWithdrawal.publicNumber` as `external_id`;
- handle duplicate submit safely;
- claim submit outbox rows with DB row lock, atomic claim update, or ShedLock plus
  row-level CAS;
- poll status after submit timeout;
- process signed callbacks;
- apply callback and status updates monotonically by SHKeeper `event_version` and
  `state_transition_id`, under row lock or optimistic version/CAS, without
  regressing withdrawal state on delayed delivery;
- use the SHKeeper-configured callback endpoint for the Grither Pay consumer; do
  not send arbitrary callback URLs in client payout requests;
- deduplicate inbound callbacks by `event_id` first, with fallback stable event
  key at minimum
  `execution_id + state + txid/message_hash + event_version`;
- map SHKeeper states to user-facing states;
- keep funds reserved for `FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`,
  `MANUAL_REVIEW`, `SAFE_FOR_MANUAL_PAYOUT`, and `MANUAL_PAYOUT_PENDING`;
- keep funds reserved and block new automatic attempts when a rail is paused
  after a withdrawal has already been submitted;
- expose operator workflow for reconciliation and manual payout decision;
- allow manual payout only after the original automatic execution is marked safe
  for manual resolution by reconciliation evidence;
- do not allow user retry with a new `external_id` while the previous attempt is
  non-terminal.

User-facing behavior:

- validation failures are immediate and do not reserve funds;
- precheck failures release or avoid reservation and show temporary
  unavailable/invalid destination messaging;
- submitted/broadcast states show pending;
- reconciliation-required states show processing and raise an operator alert;
- safe-for-manual-payout states remain internal/operator states, not a user action;
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
- executions stuck in `ENQUEUEING` or `ENQUEUED`;
- rail paused/kill-switch state;
- rail/config rejects;
- callback/status ordering conflicts.

First-release implementation status:

- SHKeeper `/metrics` exports DB-backed payout execution counts,
  non-terminal execution age, reconciliation-required count, callback outbox
  backlog count, oldest callback age, failure counts by
  `failure_class`/bounded `error_code`, dispatch backlog count/age by payout
  queue, stuck execution count/age by state threshold, broadcast-time
  confirmation SLA breach count/oldest age, ordering conflict count, and rail
  enablement. SHKeeper payout metric collection is fail-open and snapshot-safe:
  if DB collection fails, `/metrics` remains available and keeps the last
  successful payout gauge values instead of clearing
  critical alerts.
- TRON, TON, and ETH sidecar `/metrics` export rail-local payout execution
  counts, non-terminal execution age, reconciliation-required count, callback
  outbox backlog count/age, dedicated worker readiness gauges, and dedicated
  Redis broker queue depth and oldest queued item age gauges. They also expose
  hot-wallet USDT and native fee/gas/resource balance gauges for the current
  `fee_deposit` source wallet. Dedicated payout enqueue paths stamp
  `payout_enqueued_at` into Celery task headers; empty queue age is `0`, and
  Redis/unparseable age or balance collection failure is represented as `-1`
  without breaking `/metrics`. DB-backed execution/callback sidecar metrics are
  also snapshot-safe: if sidecar DB collection fails, the last successful
  execution snapshot remains while worker/queue and wallet-balance health still
  updates.
- TRON and ETH `/metrics` remain available for payout health when chain/fullnode
  metrics or external release lookup fail; TON already reports fullnode status as
  unavailable without blocking payout metric collection.
- The Helm fork renders optional chart-owned PrometheusRule alert wiring for
  SHKeeper reconciliation/stuck/dispatch/callback backlog and enabled-rail
  catalog disabled/missing plus sidecar worker/broker queue depth and age
  health, wallet-balance metric availability, confirmation SLA breach, ordering
  conflict, allocator/lock failures from bounded sidecar error codes, and
  optional low hot-wallet / fee-wallet alerts. Low-balance thresholds are empty
  by default and render only when explicitly set per rail. PrometheusRule
  manifests are disabled by default and must be enabled explicitly only for
  clusters that already run Prometheus Operator.
- Sidecars expose durable failure gauges by `state/failure_class/bounded
  error_code` plus API boundary failure counters by `operation/code` for
  auth/HMAC and payout-contract rejects on preflight/submit/status dashboards.
  Error-code labels are bounded and fall back to `OTHER`; full provider messages
  remain in execution audit/detail records, not Prometheus labels.
- Allocator conflict counters, ordering conflict alerts, confirmation SLA breach
  alerts, and final dashboard wiring remain production rollout gates.

Operator views must identify:

- consumer external withdrawal ID;
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
- rail catalog maps `(consumer, asset, network)` to the expected current
  SHKeeper crypto IDs, sidecar services, and sidecar symbols: TRON `USDT` through
  `tron-shkeeper`, TON `TON-USDT` through `ton-shkeeper`, ETH `ETH-USDT` through
  `ethereum-shkeeper`;
- rail catalog queue, sidecar enqueue config, worker queue, readiness check, and
  Helm value match for each enabled rail;
- `external_id` uniqueness is scoped to the authenticated consumer and is global
  across TRON, TON, and ETH for that consumer;
- duplicate same payload returns existing execution;
- duplicate changed payload returns `409`;
- amount canonicalization accepts equivalent 6-decimal USDT formatting as the
  same request and rejects more than 6 decimal places;
- duplicate same `external_id` with a different rail or asset returns
  `409 IDEMPOTENCY_CONFLICT`;
- consumer configuration rejects disabled rails, unsupported assets, and
  unconfigured callback endpoints before execution creation; amount/day policy is
  enforced upstream by the consumer product before submit;
- configured callback endpoint is used by default, and request-level callback
  override is rejected unless it is allowlisted for that consumer;
- execution row is created before sidecar submit;
- sidecar timeout after enqueueing path produces reconciliation state;
- status lookup after submit timeout returns authoritative state or
  `RECONCILIATION_REQUIRED`, not a new automatic execution;
- status response includes the callback-critical fields: `consumer`,
  `execution_id`, `sidecar_execution_id` when known, `contract_version`,
  `event_version`, `state_transition_id`, `occurred_at`, `updated_at`,
  `request_hash`, `sidecar_payload_hash`, failure class, txids/message hashes,
  and reconciliation flag;
- sidecar status observations are applied monotonically and conflicting
  same-version sidecar data moves the execution to `RECONCILIATION_REQUIRED`;
- callback and status ordering tests prove stale callbacks or stale polling
  responses cannot regress Grither Pay withdrawal state;
- `sidecar_execution_id` is optional correlation metadata; when present it is
  unique/immutable and never used as the idempotency/status key;
- status endpoint returns normalized states;
- generic `FAILED` is not emitted for client payouts; failure state/class is either
  `FAILED_PRE_BROADCAST` or `FAILED_CHAIN_TERMINAL`;
- manual payout resolution is rejected without recorded reconciliation evidence;
- manual payout resolution is rejected unless the evidence satisfies the
  rail-specific negative-evidence checklist;
- automatic/service legacy payout, legacy multipayout, and legacy autopayout
  paths cannot bypass `PayoutExecution` for client withdrawals; manual/admin
  payout remains available only as explicit operator action with audit metadata
  and the same wallet guard;
- inbound callbacks are idempotent by `event_id` and fallback stable event key;
- callback payload is signed and includes `event_id`, `event_version`,
  `state_transition_id`, `occurred_at`, `execution_id`, `sidecar_execution_id`,
  external ID, rail, amount, txids/message hashes, previous state, current state,
  failure class, error fields, and reconciliation flag;
- callback outbox enforces unique `event_id`, unique
  `(execution_id, event_version)`, unique `state_transition_id`, and immutable
  stored payload/signature base;
- callback retries resend the exact same payload for the same `event_id`;
- legacy payout endpoints remain compatible for admin/manual use.

### Sidecars

Shared tests:

- sidecar payout endpoints reject missing/bad scoped sidecar credentials;
- sidecar payout endpoints reject replayed/tampered signed requests;
- sidecar rejects a body `consumer` that is not allowed for the authenticated
  SHKeeper caller;
- preflight success;
- preflight worker unavailable;
- preflight insufficient USDT balance;
- preflight insufficient fee/gas/native balance;
- duplicate submit same payload;
- duplicate submit changed payload;
- submit response includes `state_version`, `state_transition_id`,
  `state_updated_at`, hashes, failure class, and reconciliation flag;
- submit rejects mismatched `sidecar_payload_hash` after sidecar canonicalization;
- sidecar stores SHKeeper `request_hash` as opaque audit metadata;
- submit stores the canonical request snapshot;
- submit repeats critical preflight checks before signing/enqueueing;
- durable state transitions use compare-and-set or equivalent transactional guard;
- sidecar execution table enforces both unique `execution_id` and unique
  `(consumer, external_id)`;
- sidecar execution table stores `state_version`, `state_transition_id`,
  `state_updated_at`, failure fields, and reconciliation flag;
- execution result normalization;
- no blind retry after broadcast timeout;
- status by `execution_id`;
- status response includes the mandatory evidence/status fields, `state_version`,
  `state_updated_at`, and `updated_at`;
- status response does not omit boundary-required evidence after `SIGNED`,
  `BROADCASTING`, `BROADCASTED`, confirmation, or reconciliation states;
- idempotency lookup by `(consumer, external_id)`;
- stale safe pre-broadcast state can be re-enqueued;
- stale `SIGNING` with nonce/seqno/resource reservation, signed payload hash, or
  broadcast-attempt marker is not blindly re-enqueued;
- stale signed/broadcasting state becomes `RECONCILIATION_REQUIRED` unless
  deterministic chain evidence resolves it;
- dedicated queue routing.

Rail-specific tests:

- TRON resource quote and provider failures;
- TRON unactivated destination rejection;
- TRON resource lock around transfer;
- TRON uses the current `fee_deposit` payout source, and every other
  same-wallet spend path is routed through the same wallet/resource lock and
  audit trail or proven unable to conflict;
- TRON client payout signed transaction expiration/ref-block validity does not
  exceed the configured payout-specific cap;
- TRON negative-evidence checklist blocks manual payout until source wallet,
  TRC20 transfer events, txid, and finalized range are checked;
- TRON negative-evidence checklist blocks manual payout until any signed
  transaction expiration/ref-block validity window has passed;
- TON seqno serialization;
- TON uses the current `fee_deposit` payout source, and every other same-wallet
  spend path is routed through the same seqno guard and audit trail or proven
  unable to conflict;
- TON no retry for broadcast timeout;
- TON Jetton balance and TON fee balance preflight;
- TON negative-evidence checklist blocks manual payout until message hash/BOC,
  seqno, source wallet history, Jetton transfer history, and masterchain range are
  checked;
- TON negative-evidence checklist blocks manual payout until signed message
  valid-until and source-wallet seqno conditions prove the original message cannot
  still execute;
- ETH gas estimate and gas balance preflight;
- ETH nonce serialization or wallet lock behavior;
- ETH uses the fork's current `/payout` source wallet in Phase 1, with one active
  payout worker or a same-wallet nonce guard for every other spend path;
- ETH negative-evidence checklist blocks manual payout until tx hash/receipt,
  source nonce/replacement status, ERC20 transfer logs, and finalized block range
  are checked;
- ETH negative-evidence checklist blocks manual payout unless the nonce is
  consumed by a finalized same-nonce tx and chain/log evidence proves no matching
  USDT `Transfer`; if a matching transfer exists manual payout is forbidden, and
  if the nonce remains unused or unfinalized the execution remains
  `RECONCILIATION_REQUIRED`.

Local sidecar verification on 2026-06-04:

- TRON sidecar in `/Users/test/PycharmProjects/tron-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 175 tests in the repository Python 3.9 `.venv` after the
  broadcast-result txid mismatch and HMAC-only v1 route boundary review fixes.
- TRON `git diff --check` passed.
- TON sidecar in `/Users/test/PycharmProjects/ton-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 67 tests after the real worker primitive, broadcast hash, preflight
  outage, valid-until evidence, and v1 `/payout-executions` route compatibility
  review fixes.
- ETH sidecar in `/Users/test/PycharmProjects/ethereum-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 66 tests after the broadcast-result hash mismatch review fix, the
  durable callback outbox parity block, manual payout safety evidence, metrics,
  and the strict execution-contract field boundary that rejects customer policy
  fields before canonicalization.

### Helm

Template tests:

- `tron-usdt-payouts`, `ton-usdt-payouts`, and `eth-usdt-payouts` render when
  enabled;
- normal `tasks` worker does not consume payout queues;
- payout workers use configured queue names;
- payout workers default to concurrency 1 and prefetch 1;
- required config validation fails for incomplete production values;
- sidecar execution DB persistence or external DB configuration is rendered for
  each enabled payout rail;
- sidecar execution DB migration job/init step is rendered or explicitly
  documented as part of the release command;
- production values define backup/restore posture for sidecar execution state;
- rendered worker queue, sidecar queue env/config, readiness checks, and
  SHKeeper rail catalog queue are identical for every enabled rail;
- rendered sidecar services match the SHKeeper rail catalog `sidecar_service` for
  every enabled rail;
- production values require Secret references or external secret references for
  payout credentials and hot-wallet material, with no real secrets in rendered
  ConfigMaps/manifests;
- NetworkPolicy or equivalent ingress restriction renders for sidecar payout
  endpoints;
- per-rail payout enablement and pause/kill switch render from values, and
  SHKeeper amount/day cap fields do not render;
- rollout strategy is safe for pod-local Redis.

Local Helm verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` passed
  31 chart tests in `/Users/test/PycharmProjects/shkeeper-helm-charts`.
- `helm lint charts/shkeeper` passed with 0 failed charts.
- `git diff --check` passed.
- Default `helm template` render passed.
- Positive TRON, TON, and ETH rail renders passed with required Secret refs,
  backup/restore evidence, owned image repository values, rail sync, migration
  jobs, dedicated workers, NetworkPolicy, probes, resources, and
  `execution_enabled=true`.
- Environment production overlays are render/staging evidence only until final
  images are built and digests are recorded. Current local audit: `shkeeper.io`
  HEAD `54fe764` vs overlay `0e4c415`, `tron-shkeeper` HEAD `7298151` vs overlay
  `5a6133b`, `ton-shkeeper` HEAD `f433e03` vs overlay `d8f5c77`; ETH overlay
  matches `ethereum-shkeeper` HEAD `977f920`.
- Positive rail renders proved sidecar Services are rendered even when the
  corresponding legacy asset flags are disabled.
- Negative render proved enabled TRON payout fails if queue is supplied through
  `tron_shkeeper.extraEnv.TRON_USDT_PAYOUT_QUEUE` instead of the chart-owned
  `payouts.rails.tronUsdt.queue`.
- Negative render proved enabled payout topology rejects literal
  hot-wallet/secret-looking `extraEnv` values for SHKeeper and TRON/TON/ETH
  sidecars; production secret material must be supplied through Secret or
  external-secret references.
- Negative render proved enabled rail images cannot pass validation by embedding
  an owned image repository as a substring inside an untrusted image name.
- Disabled-rail render proved an enabled legacy TRON sidecar does not receive
  payout auth/auto-enqueue env when only the ETH rail is enabled.

### End-To-End

For each rail:

- crash after Grither Pay reservation but before dispatcher send recovers from
  the committed submit outbox event;
- testnet or low-value mainnet smoke payout;
- submit timeout simulation followed by status lookup;
- worker unavailable simulation;
- sidecar restart during non-broadcast state;
- sidecar restart during broadcast ambiguity;
- callback retry simulation;
- duplicate callback delivery simulation;
- missed callback followed by status polling simulation;
- confirmation polling.

## Implementation Phases

### Phase 0: Fork And Control Plane

- Create `nilof470/ethereum-shkeeper`.
- Checkout `/Users/test/PycharmProjects/ethereum-shkeeper`.
- Configure `origin` and `fork` remotes consistently with TON/TRON.
- Ensure Helm values reference owned images for TRON/TON/ETH.
- Document fork ownership and upstream sync process.
- Add a rail ownership matrix that maps each production rail to the exact repo,
  image tag, Helm values block, and sidecar execution contract version.

Gate: all payout rails are under code ownership.

### Phase 1: SHKeeper Execution API

- Add service-to-service execution API separate from legacy admin payout.
- Add scoped auth/HMAC for payout consumer submit/status and callbacks.
- Add `PayoutRail` catalog and route by `(consumer, asset, network)` to explicit
  `crypto_id`, sidecar symbol/service, token metadata, queue, wallet, callback
  endpoint, operational contract metadata, and contract version.
- Add durable `PayoutExecution` table and normalized state machine. Do not depend
  on legacy `Payout.task_id` as the source of truth for client withdrawals.
- Add canonical request hashing over rail, network, asset, destination, amount,
  consumer, external id, configured callback endpoint id, and contract version.
- Add canonical USDT amount handling: 6-decimal normalization for hashing and
  callbacks, and fail-closed rejection for amounts with higher precision.
- Add sidecar payload hashing over sidecar-visible fields and store both
  `request_hash` and `sidecar_payload_hash`.
- Add idempotent submit/status by `(consumer, external_id)`, where
  `external_id` is unique across rails/assets for that consumer, and reject
  duplicate external ids with a different request hash.
- Add configured callback endpoints per consumer with request-level override
  allowlisting only when explicitly needed.
- Add consumer rail/asset allowlists so disabled rails fail before execution
  creation. SHKeeper must not own per-withdrawal or daily business limits.
- Add runtime guard so automatic/service client withdrawals cannot bypass
  `PayoutExecution` through legacy payout, legacy multipayout, autopayout, direct
  `crypto.mkpayout`, or direct `crypto.multipayout` paths. Manual/admin payout
  remains available as explicit operator action with audit metadata and the same
  wallet guard.
- Split failure state/class into `FAILED_PRE_BROADCAST` and
  `FAILED_CHAIN_TERMINAL`; do not expose generic `FAILED` for client payouts.
- Add `sidecar_execution_id`, `reconciliation_required`, `last_error`,
  `attempt_count`, `submitted_at`, `broadcasted_at`, and `confirmed_at`.
- Add manual resolution fields and audit trail for reconciliation evidence.
- Add `FAILED_CHAIN_TERMINAL` accounting policy: first release defaults to
  `MANUAL_REVIEW` with funds reserved until operator resolution.
- Add durable `PayoutCallbackEvent` outbox for service-consumer callbacks instead
  of relying only on inline callback attempts, with unique `event_id`, unique
  `(execution_id, event_version)`, unique `state_transition_id`, immutable
  payload/signature data, and bounded observable dispatch retries.
- Add shared SHKeeper callback/status ordering metadata: SHKeeper
  `state_version` is exposed as client-facing `event_version`, alongside
  `last_state_transition_id`, `last_state_occurred_at`, and conflict handling
  when two sources disagree for the same ordering key.
- Add sidecar status observation fields and monotonic application rules:
  `last_sidecar_state`, `last_sidecar_state_version`,
  `last_sidecar_state_transition_id`, `last_sidecar_status_hash`, and
  `last_sidecar_status_observed_at`.
- Add a SHKeeper reconciler worker for execution states. The first SHKeeper
  worker entrypoint is `flask payout-execution-reconciler`; Helm must run it in a
  dedicated worker container when payout execution is enabled. APScheduler in the
  web process is not sufficient for the production withdrawal path.
- Add a SHKeeper callback outbox dispatcher worker. The first callback worker
  entrypoint is `flask payout-callback-dispatcher`; Helm must run it in a
  dedicated worker container when payout execution callbacks are enabled.
- Keep legacy payout API intact.

Gate: Grither Pay can submit/retry/status safely at the SHKeeper API boundary,
but rail production is still blocked until the matching sidecar has durable
execution and pre-broadcast evidence.

### Phase 2: Sidecar Hardening

- Implement shared preflight/submit/status contract in TRON, TON, and ETH.
- Add sidecar-local execution records.
- Implement the shared sidecar execution state machine.
- Add scoped SHKeeper-to-sidecar payout auth for preflight, submit, and status.
- Use a database-backed execution table with migrations and both required unique
  constraints: `execution_id` and `(consumer, external_id)`.
- Store sidecar `state_version`, `state_transition_id`, state timestamps, failure
  fields, and reconciliation flag in the sidecar execution table.
- Implement compare-and-set state transitions and worker lease fields:
  `lease_owner`, `lease_expires_at`, `attempt_id`, and heartbeat.
- Persist deterministic pre-broadcast evidence before sending to the chain:
  signed TRON raw transaction + txid, signed TON BOC + message hash + seqno,
  signed ETH raw transaction + nonce + tx hash + chain id.
- Persist side-effect markers for nonce/seqno/resource reservation and broadcast
  attempts so stale `SIGNING` can be safely classified as retryable or
  reconciliation-required.
- Canonicalize and verify `sidecar_payload_hash` inside each sidecar before
  creating or reusing an execution; store SHKeeper `request_hash` as opaque audit
  metadata.
- Implement the mandatory sidecar status response schema with `state_version`,
  state timestamps, hashes, rail evidence fields, error fields, and reconciliation
  flag.
- Persist source wallet, token contract or Jetton master/wallet, chain/network id,
  reference block/masterchain seqno, expiration/valid-until data, txid/message
  hash, and chain check metadata needed for negative-evidence reconciliation.
- Add dedicated payout queues and worker readiness.
- Add TRON payout-specific transaction expiration/ref-block validity cap and
  persist the configured expiration evidence.
- Normalize execution results.
- Protect broadcast windows and ambiguous timeouts.
- Replace infinite sidecar `post_payout_results` loops with durable sidecar
  outbox or bounded retry, and make SHKeeper status polling authoritative.
- Fix TON multipayout result mapping before enabling TON client withdrawals.
- Audit all hot-wallet spend paths per rail and either route them through the
  same wallet lock/nonce/seqno allocator or prove they cannot spend from the
  payout hot wallet.
- Phase 1 keeps the current sidecar payout source wallet. If that source wallet
  is shared with manual/admin, sweep, fee, staking, AML, or drain paths, those
  same-wallet spend paths must use the same wallet lock/nonce/seqno/resource
  guard or be proven unable to conflict with client withdrawals.

Gate: each sidecar protects its own network-specific broadcast path.

### Phase 3: Helm/Kubernetes

- Add first-class payout worker values for TON and ETH.
- Clean up TRON payout worker chart API.
- Add schema/required validation.
- Add resources, readiness probes, liveness probes, preStop quiesce, and
  termination grace for sidecar app/tasks/payout workers.
- Add sidecar execution DB persistence or external DB configuration for TRON,
  TON, and ETH payout execution state.
- Add sidecar execution DB migrations and make payout submit readiness depend on
  successful migration completion.
- Add sidecar execution state backup/restore guidance for production.
- Add Redis persistence/AOF parity for TON and ETH if Redis remains pod-local.
- Add rollout strategy that prevents two active payout workers from consuming the
  same rail/hot-wallet queue during upgrades. Prefer explicit quiesce over
  wrapper-only verification.
- Add NetworkPolicy or equivalent ingress restriction for sidecar payout
  endpoints.
- Add Secret/external-secret references for payout credentials, callback signing
  keys, RPC credentials, and hot-wallet material; production chart rendering must
  not expose real secrets through values or ConfigMaps.
- Add per-rail payout enablement and pause/kill switch as chart/runtime
  configuration. Do not add SHKeeper-side amount/day cap fields to the chart
  values or rail sync payload.
- Add chart tests and production values examples.

Gate: production topology is chart-rendered and verified, not patched by
runbook scripts.

### Phase 4: Grither Pay Integration

- Add withdrawal state machine and reservation flow.
- Add per-rail enablement and the existing Grither wallet/business amount
  validation before submit. Do not add SHKeeper-side amount/day caps.
- Create withdrawal row, ledger reservation entry, and SHKeeper submit outbox
  event atomically in one database transaction.
- Add durable outbox/dispatcher for SHKeeper submit and callback handling.
- Call SHKeeper execution API with immutable `WalletWithdrawal.publicNumber` as
  `external_id`.
- Handle callbacks and polling through one monotonic state-application path keyed
  by `event_version` and `state_transition_id`; stale updates are ignored and
  conflicting same-version updates go to operator reconciliation.
- Implement operator reconciliation flow with constrained actions and audit log.
- Implement manual payout resolution only for reconciliation-verified safe cases;
  critical ambiguous cases remain reserved and blocked until evidence is recorded.
- Implement `MANUAL_REVIEW`, `SAFE_FOR_MANUAL_PAYOUT`, `MANUAL_PAYOUT_PENDING`,
  and `MANUAL_PAYOUT_COMPLETED` states.
- Release funds automatically only for failures known to be pre-broadcast; handle
  chain-terminal failures through `MANUAL_REVIEW` and explicit operator
  accounting policy.
- Add user-facing mapping that distinguishes submitted, broadcasted, confirming,
  completed, failed, and reconciliation-required states.

Gate: Grither Pay never loses a withdrawal and never releases reserved funds
without a terminal state.

### Phase 5: Observability And Ops

- Add metrics and alerts.
- First-release SHKeeper and sidecar DB-backed payout metrics cover execution
  counts, non-terminal age, reconciliation-required count, callback outbox
  backlog, dedicated worker readiness, broker queue depth/age, rail disabled
  state, sidecar hot-wallet and native fee/gas/resource balances,
  wallet-balance availability, optional low-balance alerts, sidecar
  preflight/submit/status failure-rate dashboard metrics, allocator/lock
  failures, ordering conflicts, and broadcast-time confirmation SLA breach.
- Add operator status views.
- Add runbooks for reconciliation, worker unavailable, low balance, and provider
  failures.
- Add alerts for stuck state age, ambiguous broadcast, callback backlog, sidecar
  outbox backlog, wallet-balance metric availability, optional low hot-wallet
  balance, optional low fee balance, nonce/seqno allocator errors, rail paused
  state, rail/config rejects, ordering conflicts, and confirmation SLA breach.
- Grither Pay payout schedulers must expose first-release Micrometer metrics:
  constant-cardinality scheduler run counters tagged by operation/result and
  processed-row summaries tagged by operation. Metric recording must fail open
  and must not interrupt payout processing.
- In Grither Pay, callback backlog is a monitor-only detector over stale payout
  callback event rows with `applied_at is null`. It must alert operators but
  must not replay callbacks or mutate payout/wallet state; normal callback
  processing remains a single transaction that inserts the event, applies
  provider state, and applies wallet state atomically.
- Add structured audit trail for every state transition and every operator action.

Gate: operators can detect and act on stuck or ambiguous withdrawals before
users are affected at scale.

### Phase 6: Production Rollout

- Roll out one rail at a time.
- Run smoke payout per rail.
- Observe metrics for a defined stability window.

Gate: real client withdrawals are allowed only after all reliability gates for
that rail pass.

## Acceptance Criteria

The system is production-ready for a rail only when:

- Grither Pay stores the withdrawal and reserves funds before SHKeeper submit.
- Grither Pay creates the withdrawal row, ledger reservation entry, and
  SHKeeper-submit outbox event atomically in one database transaction.
- Grither Pay enforces per-rail enablement and its existing wallet/business
  amount limits before submit.
- Grither Pay has a durable outbox/dispatcher for submit retries and callback
  processing.
- SHKeeper creates durable execution state before sidecar submit.
- Submit is idempotent by consumer external withdrawal ID.
- SHKeeper has an explicit `PayoutRail` catalog for every enabled rail; TRON maps
  to `USDT` through `tron-shkeeper`, TON maps to `TON-USDT` through
  `ton-shkeeper`, ETH maps to `ETH-USDT` through `ethereum-shkeeper`, and catalog
  queue matches sidecar enqueue config, worker `-Q`, readiness check, and Helm
  values.
- USDT amount is canonicalized to a 6-decimal string before hashing/storage and
  higher-precision payout amounts are rejected.
- Consumer external withdrawal ID is unique per consumer across all supported
  payout rails/assets.
- Duplicate submits with the same idempotency key and same request hash return
  the existing execution; duplicate submits with a different request hash fail.
- Callback target is configured/allowlisted per consumer and is not arbitrary
  request input.
- When a rail is enabled for service-consumer client withdrawals, automatic or
  service-originated legacy payout, multipayout, and autopayout paths cannot
  bypass `PayoutExecution`. Manual/admin payout remains available only as
  explicit operator action with audit metadata and the same wallet guard.
- SHKeeper consumer configuration enforces allowed rails/assets and callback
  endpoint configuration before execution creation. Client amount/day limits are
  upstream product policy and must not be SHKeeper rail fields.
- SHKeeper status response includes callback-critical fields: `consumer`,
  `execution_id`, `sidecar_execution_id` when known, `contract_version`,
  `event_version`, `state_transition_id`, `occurred_at`, `updated_at`,
  `request_hash`, `sidecar_payload_hash`, failure class, txids/message hashes,
  and reconciliation flag.
- Callback events include `event_id`, `event_version`, `state_transition_id`,
  `occurred_at`, `execution_id`, and nullable `sidecar_execution_id`; callbacks
  are idempotent by `event_id`.
- Callback events are stored in a durable `PayoutCallbackEvent` outbox with unique
  `event_id`, unique `(execution_id, event_version)`, unique
  `state_transition_id`, immutable payload/signature data, and bounded observable
  dispatch retries.
- Grither Pay applies callback and status updates monotonically; stale delivery
  cannot regress withdrawal state, and conflicting same-version data keeps funds
  reserved for reconciliation.
- `sidecar_execution_id` is optional correlation metadata only; if present it is
  unique/immutable and never used as the idempotency or status lookup key.
- SHKeeper and sidecar callbacks/status expose separate pre-broadcast and
  chain-terminal failure classes.
- `FAILED_CHAIN_TERMINAL` moves Grither Pay to `MANUAL_REVIEW` with funds
  reserved until an operator records the accounting resolution.
- Sidecar payout endpoints use scoped SHKeeper-to-sidecar auth and reject
  unauthenticated, replayed, tampered, or unauthorized-consumer requests.
- Sidecar execution storage enforces both unique `execution_id` and unique
  `(consumer, external_id)`.
- Sidecar execution storage includes monotonic `state_version`,
  `state_transition_id`, state timestamps, failure fields, and reconciliation
  flag.
- Sidecar submit is idempotent by unique `execution_id` and unique
  `(consumer, external_id)`; it does not use Celery `task_id` as the durable
  execution identity.
- Sidecar submit verifies `sidecar_payload_hash` instead of trusting the
  caller-provided value, and stores SHKeeper `request_hash` as opaque audit
  metadata.
- Sidecar submit repeats critical checks before signing/enqueueing.
- Sidecar submit response includes sidecar ordering metadata and duplicate
  same-payload submit returns the current execution/status, not a weaker
  task-only response.
- Sidecar state transitions are durable and guarded by compare-and-set or an
  equivalent transactional mechanism.
- Sidecar stores pre-broadcast evidence before network submit.
- Sidecar stores source wallet, token contract/Jetton metadata, chain/ref-block
  metadata, signed payload reference/hash, txid/message hash, and chain check
  metadata required by manual negative-evidence review.
- Sidecar status returns the mandatory evidence/status schema, including
  `state_version`, `state_transition_id`, state timestamps, request hashes,
  source wallet, rail metadata, signed artifact metadata, txid/message hash,
  chain-check metadata, error fields, and reconciliation flag.
- SHKeeper applies sidecar status monotonically; stale sidecar status cannot
  overwrite newer execution state, and conflicting same-version sidecar data moves
  the execution to `RECONCILIATION_REQUIRED`.
- Manual negative-evidence review proves any signed artifact cannot still
  complete: TRON expiration/ref-block validity has passed, TON valid-until/seqno
  conditions are resolved, and ETH nonce is consumed by a finalized same-nonce tx
  with no matching USDT `Transfer`.
- TRON client payout signed artifacts use a configured payout-specific expiration
  cap and do not use the legacy 12-hour validity window.
- Stale `SIGNING` can be automatically retried only when durable state proves no
  nonce/seqno/resource reservation, signed payload, or broadcast attempt exists.
- Dedicated payout worker and queue are enabled.
- Sidecar execution DB state is persisted or externalized, migrated before
  readiness, and covered by production backup/restore posture.
- Broadcast path and all other hot-wallet spend paths are serialized or protected
  by a shared wallet-level nonce/seqno/lock policy for that rail.
- Production rail keeps the Phase 1 sidecar payout source wallet; all same-wallet
  spend paths are proven to use the same nonce/seqno/resource guard and audit
  trail or proven unable to conflict.
- Kubernetes restricts sidecar payout endpoints to SHKeeper service traffic with
  NetworkPolicy or equivalent ingress controls.
- Production secrets for payout credentials, signing keys, RPC credentials, and
  hot-wallet material are referenced from Kubernetes Secrets or an external secret
  provider and are not committed or rendered into ConfigMaps.
- No blind retry can occur after ambiguous broadcast.
- Status lookup after Grither Pay submit timeout returns authoritative state,
  including `RECONCILIATION_REQUIRED` when safe recovery is impossible.
- `RECONCILIATION_REQUIRED` is visible and alerted.
- Manual payout resolution is blocked until reconciliation records evidence that
  the automatic execution did not complete the requested USDT transfer and cannot
  still complete later, using the rail-specific negative-evidence checklist.
- Callback delivery retries are bounded and observable.
- Helm chart renders the production topology from values.
- Helm chart renders and validates per-rail payout enablement, pause/kill switch,
  technical worker bounds, worker topology, storage, migrations,
  NetworkPolicy/ingress restriction, and Secret/external-secret references.
- Rollout cannot split payout traffic across two isolated pod-local Redis
  brokers.
- Confirmation checks verify rail-specific token transfer details, not only a
  generic confirmation count.
- End-to-end smoke payout passes on that rail.

## Known Risks And Mitigations

Pod-local Redis risk:

- Mitigation: durable execution records are authoritative; use safe rollout;
  move to external broker if reliability gates cannot be met.

Ambiguous broadcast timeout:

- Mitigation: no blind retry; mark reconciliation-required; recover only by
  deterministic sidecar/chain evidence or operator workflow. Manual payout is
  allowed only after evidence shows the original execution did not complete the
  requested USDT transfer and cannot still complete later.

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

Sidecar callback loop risk:

- Mitigation: remove infinite in-task notification loops from payout-critical
  workers; use durable outbox or bounded retry plus SHKeeper polling.

TON result mapping risk:

- Mitigation: fix and test result-to-destination mapping before TON-USDT is
  exposed to Grither Pay, even if client withdrawals use single payout first.

ETH ownership risk:

- Mitigation: ETH-USDT cannot pass Phase 0 until the fork is checked out locally,
  image publishing is owned, and Helm references owned image tags.

## Non-Goals For This Release

- Native TRX/TON/ETH withdrawals.
- Multipayout for client withdrawals.
- High-volume parallel payout engine.
- Full zero-downtime sidecar rollout with pod-local Redis.
- New standalone payout microservice between Grither Pay and SHKeeper.
- User-facing withdrawal UI redesign beyond required status mapping.
- Rewriting SHKeeper invoice/deposit processing, `walletnotify`, or the legacy
  admin payout UI.
