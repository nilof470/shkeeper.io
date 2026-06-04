# USDT Withdrawals Production Readiness Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make Grither Pay client withdrawals production-ready through SHKeeper
for USDT on TRON, TON, and Ethereum, without using legacy admin payout behavior
as the client withdrawal contract. Legacy manual/admin payout remains available
as an operator flow.

**Architecture:** Grither Pay remains the withdrawal ledger and customer state
owner; SHKeeper becomes the service-to-service payout execution/control plane;
TRON, TON, and ETH sidecars own network-specific signing, broadcast evidence,
confirmation, and reconciliation data. Kubernetes/Helm only renders this topology
and safety gates; it must not hide payout correctness in wrapper scripts.

**Tech Stack:** Python Flask, SQLAlchemy, Alembic, Celery, Redis, pytest/unittest
for SHKeeper and sidecars; Helm chart templates and Python unittest chart tests;
Java/Spring Boot, Maven, JPA/Liquibase-style changelogs, and JUnit for Grither
Pay.

**Source spec:**
`docs/superpowers/specs/2026-06-03-usdt-withdrawals-production-readiness-design.md`

**Grither Pay integration spec:**
`docs/superpowers/specs/2026-06-03-grither-pay-shkeeper-payout-integration-design.md`

**Safety priority:** correctness > reliability > operational clarity >
simplicity > speed. Avoid new infrastructure unless it directly reduces payout
loss, duplicate payout, ambiguous broadcast, or recovery risk.

**Non-goal:** do not add a new standalone payout microservice, Kafka, Temporal,
or a distributed workflow engine in the first release.

---

## Superpowers Scope Decision

This is a master orchestration and handoff plan. The source spec spans
independent systems: SHKeeper API/state, three sidecars, Helm/Kubernetes, and
Grither Pay. Per `Superpowers:writing-plans`, implementation must be split into
repo-scoped task plans before coding. This file controls sequencing, gates, and
cross-repo contracts. A subsystem is ready for implementation only when its
repo-scoped plan has exact file paths, failing tests, commands, and review gates.
If a subsystem document still reads as an architectural outline, the executing
agent must first expand it inside the target repo before touching code.

Plan suite:

- SHKeeper execution API task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-shkeeper-execution-api.md`
- TRON sidecar task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-tron-sidecar.md`
- TON sidecar task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-ton-sidecar.md`
- ETH sidecar task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-eth-sidecar.md`
- Helm/Kubernetes task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-helm-kubernetes.md`
- Grither Pay integration task plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-grither-pay.md`
  This plan has been refreshed against the local Grither Pay codebase and should
  be used together with the Grither Pay integration spec above.

Execution order:

1. SHKeeper execution API and durable callback/status contract.
2. Shared sidecar contract, then TRON because it already has the payout worker
   baseline.
3. TON sidecar after TRON contract patterns are reviewed.
4. ETH fork ownership, then ETH sidecar.
5. Helm production topology after at least TRON sidecar contract is stable.
6. Grither Pay integration after SHKeeper status/callback contract is stable.
7. Rail-by-rail smoke rollout with per-rail kill switch. SHKeeper does not own
   customer withdrawal policy; client withdrawal amount policy remains in the
   upstream product ledger/risk layer.

Review rule: after each subsystem task plan is written, request an independent
review focused on payout correctness, duplicate-payout prevention, ambiguous
broadcast handling, and manual payout boundaries. Validate every finding against
code/spec before patching the plan.

## Implementation Rules

- [x] Keep invoice/deposit processing, `walletnotify`, and the legacy admin UI
  behavior unchanged unless a task explicitly says otherwise.
  Verification on 2026-06-04: current SHKeeper diff touches payout execution API,
  payout services/models/tasks, payout sidecar adapters, and metrics only; no
  template/static admin UI files are modified. Full SHKeeper suite passed
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 209 tests in 2.980s OK`, including AML/deposit callback coverage and
  legacy admin single/multipayout endpoint coverage. A review fix changed
  legacy admin payout operator lookup to `_current_operator_id()` so missing
  `g.user` cannot break the existing endpoint behavior.
- [ ] Do not enable a rail for Grither Pay until every acceptance gate for that
  rail passes.
- [ ] Roll out one rail at a time behind a per-rail enablement flag and operator
  pause/kill switch.
- [x] Use SHKeeper `execution_id` as the required cross-service sidecar key.
  `sidecar_execution_id` is nullable correlation metadata only.
- [x] Use the `PayoutRail` catalog as the only routing source for `(consumer,
  asset, network)`.
- [x] Keep TRON Phase 1 queue name `tron_usdt_fee_payouts` unless sidecar, Helm,
  readiness, docs, and tests are migrated together.
- [x] Keep the Phase 1 sidecar payout source wallet exactly as today's sidecar
  `/payout` implementation uses it. Do not rename `fee_deposit` or migrate to a
  dedicated payout wallet in this release. If a wallet is shared, every
  same-wallet spend path must use the same allocator/lock and audit trail.
- [x] Runtime sidecar routing for client withdrawals must use `PayoutRail` /
  `PayoutExecution.sidecar_service`, not legacy `Crypto.instances`. Legacy
  crypto modules may remain as a compatibility fallback only.

## Initial Validated Baseline

These checks record the pre-implementation baseline that drove the architecture.
They are kept as historical evidence; Phase 1 and Phase 2 below describe the new
production payout execution path.

- [x] SHKeeper initially had legacy `Payout` with flat `IN_PROGRESS`, `SUCCESS`,
  `FAIL`; no payout execution table, event version, sidecar evidence, or callback
  outbox.
- [x] SHKeeper legacy status is `GET /api/v1/<crypto>/payout/status?external_id=`.
- [x] Initial legacy external ID uniqueness was `(crypto, external_id)`, not
  `(consumer, external_id)`.
- [x] Legacy `payoutnotify` logs notifications but does not drive payout
  execution state.
- [x] TRON sidecar has a dedicated `tron-usdt-payouts` worker container, but its
  broker queue is `tron_usdt_fee_payouts`.
- [x] TRON sidecar initially exposed legacy `/USDT/payout/<to>/<amount>` and
  returned only a
  Celery `task_id`.
- [x] TON sidecar initially exposed legacy payout/multipayout endpoints and returned only a
  Celery `task_id`.
- [x] ETH sidecar is under owned fork checkout at
  `/Users/test/PycharmProjects/ethereum-shkeeper`; local payout execution code is
  implemented and verified, but production enablement still waits for a concrete
  published owned image tag, real production Secret bindings, restore-drill
  evidence, and staging smoke evidence.
- [x] Helm fork now renders first-class TRON, TON, and ETH payout workers,
  SHKeeper payout execution workers, rail sync, migration jobs, Redis AOF/PVC
  posture, NetworkPolicy, Secret refs, and fail-closed chart validation.

## Phase 0: Repo Ownership And Control Plane

### 0.1 Own ETH Sidecar Fork

- [x] Create or confirm `nilof470/ethereum-shkeeper`.
- [x] Checkout it at `/Users/test/PycharmProjects/ethereum-shkeeper`.
- [x] Configure `origin` and `fork` remotes consistently with TRON/TON.
- [ ] Confirm branch protection/release process for owned image publishing.
  Release-image audit on 2026-06-04 found that current environment overlay image
  refs are not proof of production-ready images: local WIP exists in SHKeeper,
  TRON, TON, ETH, Helm, and Grither repos, and some overlay tags do not match
  current repository HEADs (`shkeeper.io` HEAD `54fe764` vs overlay `0e4c415`,
  `tron-shkeeper` HEAD `7298151` vs overlay `5a6133b`, `ton-shkeeper` HEAD
  `f433e03` vs overlay `d8f5c77`; ETH overlay matches HEAD `977f920`). Treat
  all existing image refs as render/staging evidence only until final commits
  are reviewed, images are built/pushed, registry digests are recorded, and
  values reference those final image tags or digests.
- [x] Update Helm values to require owned image repositories for enabled TRON,
  TON, and ETH payout rails. Production still needs concrete published image
  tags in environment values.
- [x] Keep the ETH payout rail disabled in SHKeeper and Helm until the owned fork
  proves its current `/payout` source wallet, nonce model, signed raw transaction
  persistence, and broadcast evidence from code. The fork evidence is now
  present; production enablement still waits for published owned image,
  restore-drill, Secret binding, and smoke payout gates.

Acceptance:

- [x] No enabled payout rail uses an upstream-only image tag.
- [x] ETH-USDT remains disabled until the owned fork acceptance gates pass.
- [x] Rail ownership matrix lists repo, branch, image tag, chart values key, and
  payout contract version.

### 0.2 Add Rail Ownership Matrix

- [x] Add docs table mapping each rail to repo, image, chart values key,
  `crypto_id`, sidecar service, sidecar symbol, queue, wallet policy, and
  contract version.
- [x] Keep first-release mapping:
  - TRON: `USDT`, `tron-shkeeper`, `USDT`, `tron_usdt_fee_payouts`.
  - TON: `TON-USDT`, `ton-shkeeper`, `TON-USDT`, `ton_usdt_payouts`.
  - ETH: `ETH-USDT`, `ethereum-shkeeper`, `ETH-USDT`, `eth_usdt_payouts`.

Rail ownership/source matrix, validated against SHKeeper `PayoutRail` fields and
Helm values on 2026-06-04:

| Rail | Repo | Branch | Production image tag | Chart key | `crypto_id` | Sidecar service | Sidecar symbol | Queue | Source wallet ref | Wallet policy | Contract |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| USDT/TRON | `/Users/test/PycharmProjects/tron-shkeeper` | `profeex-bandwidth-provider` | owned tag required before enablement | `payouts.rails.tronUsdt` | `USDT` | `tron-shkeeper` | `USDT` | `tron_usdt_fee_payouts` | `fee_deposit` | `CURRENT_SIDECAR_SOURCE_WALLET` | `usdt-payout-execution-v1` |
| USDT/TON | `/Users/test/PycharmProjects/ton-shkeeper` | `fix/ton-scanner-indexer-404-resilience` | owned tag required before enablement | `payouts.rails.tonUsdt` | `TON-USDT` | `ton-shkeeper` | `TON-USDT` | `ton_usdt_payouts` | `fee_deposit` | `CURRENT_SIDECAR_SOURCE_WALLET` | `usdt-payout-execution-v1` |
| USDT/ETH | `/Users/test/PycharmProjects/ethereum-shkeeper` | `main` | owned tag required before enablement | `payouts.rails.ethUsdt` | `ETH-USDT` | `ethereum-shkeeper` | `ETH-USDT` | `eth_usdt_payouts` | `fee_deposit` | `CURRENT_SIDECAR_SOURCE_WALLET` | `usdt-payout-execution-v1` |

Acceptance:

- [x] Matrix matches the SHKeeper `PayoutRail` config and Helm values.

## Phase 1: SHKeeper Execution API

**Current Status, 2026-06-04:** Phase 1 implementation is present in the
current SHKeeper worktree and has passed the SHKeeper block verification gate in
`2026-06-03-usdt-withdrawals-shkeeper-execution-api.md`.

Validated evidence:

- Expanded payout suite: `109 passed`.
- Rail-sync hardening review: focused payout execution suite passed 95 tests;
  full SHKeeper unittest discovery passed 181 tests.
- Helm/SHKeeper rail-only review: `HttpPayoutSidecarClient` now builds sidecar
  URLs from `PayoutExecution.sidecar_service`, defaults bare Kubernetes service
  names to port 6000, and no longer requires legacy `Crypto.instances` for
  rail-only payout deployments. Focused client tests passed 12 tests; full
  SHKeeper unittest discovery passed 184 tests.
- Direct legacy adapter guard review: TRON/ETH/TON adapter payout methods require
  a `PayoutService` guard context, so direct `crypto.mkpayout` and
  `crypto.multipayout` calls cannot bypass `PayoutExecution` for enabled
  client-withdrawal rails. API-level regressions also verify that legacy admin
  `/payout` and `/multipayout` remain available through the authenticated admin
  context. Focused API tests passed 31 tests; focused payout suite passed 125
  tests; full SHKeeper unittest discovery passed 191 tests.
- Service-auth boundary review: payout API auth regressions verify missing
  signature 401, tampered body 403, expired timestamp 403, replay nonce 403, and
  method/path/query-bound signatures. Focused API tests passed 35 tests; focused
  payout suite passed 129 tests; full SHKeeper unittest discovery passed 195
  tests.
- `py_compile` on the payout execution modules, API, models, and migration:
  clean.
- `git diff --check`: clean.
- Isolated Flask/Alembic migration smoke against temporary SQLite: clean.
- Independent review findings around scoped HMAC rail access, callback outbox
  double-delivery/order, sidecar ordering metadata, non-finite amounts, malformed
  JSON, sidecar timeouts, migration bootstrap, and shared-wallet guard policy
  have been validated against code and fixed.
- Product-policy boundary revalidation after the 2026-06-04 architecture
  clarification: SHKeeper runtime, migration, OpenAPI, sidecars, and Helm rail
  config do not implement customer withdrawal limits. SHKeeper accepts only
  technical execution/routing contract fields at the execution and rail-config
  boundary; customer withdrawal policy remains a Grither-side product ledger
  responsibility.
- Sidecar-status error-field review: stale transient dispatcher/status errors are
  now cleared when later sidecar progress reaches active/successful states
  without current error fields, preventing `PAYOUT_DISPATCH_EXCEPTION` from
  leaking into successful status responses or callbacks. Focused reconciler tests
  passed 23 tests; broader payout suite passed 111 tests; full SHKeeper unittest
  discovery passed 216 tests; `py_compile` and `git diff --check` were clean.
- SHKeeper product-policy cleanup and callback HMAC freshness review:
  runtime/API/rail sync now use generic strict execution/routing contract errors
  for unsupported fields and do not name product policy fields. Callback retries
  keep raw payload and `event_id` nonce stable, but refresh timestamp/signature
  metadata per delivery attempt. Focused tests passed 71 tests; broad payout
  suite passed 112 tests; full SHKeeper unittest discovery passed 217 tests.
- SHKeeper no-business-logic boundary follow-up: payout execution request tests,
  rail-sync tests, generated OpenAPI payout docs, and production-readiness notes
  no longer enumerate concrete customer policy fields. SHKeeper accepts only
  generic execution-contract fields and rail routing/config fields; customer
  withdrawal eligibility and amount policy remain upstream application concerns.
  Focused SHKeeper tests passed 62 tests; OpenAPI JSON parsed; `git diff
  --check` was clean. The boundary is enforced by strict execution/routing
  allowlists and unknown-field rejection, not by SHKeeper-side customer limit
  fields. Broad payout unittest discovery passed 137 tests.
- Grither callback HMAC retry compatibility review: targeted
  `ShKeeperPayoutSignatureServiceTest` passed and now verifies that the same
  callback `event_id` nonce with refreshed timestamp/signature is accepted as
  replay/idempotency evidence rather than rejected as an auth failure.
- ETH sidecar execution-contract boundary review: validated that the ETH sidecar
  accepted extra request fields and patched it to reject unsupported fields before
  canonicalization. `tests.test_payout_execution_contract` now proves fields
  outside the execution contract are rejected with
  `PAYOUT_EXECUTION_BAD_REQUEST`; focused ETH sidecar tests passed 48 tests and
  the full ETH sidecar suite passed 66 tests.
- SHKeeper submit-window safety review: `ENQUEUEING` no longer auto-resubmits
  when sidecar status returns `NO_EXECUTION_CREATED`/404. That window can mean
  either "not submitted yet" or "submitted but sidecar state is missing", so the
  MVP fails closed to `RECONCILIATION_REQUIRED` with
  `SIDECAR_EXECUTION_NOT_FOUND_AFTER_SUBMIT_WINDOW` instead of risking duplicate
  broadcast. Focused SHKeeper payout/reconciler/API/model tests passed 85 tests.
- TON/TRON sidecar execution-contract boundary review: validated that TON and
  TRON sidecars could silently ignore extra request fields, then patched both to
  reject unsupported fields with `PAYOUT_EXECUTION_BAD_REQUEST`. ETH test wording
  was also normalized to generic unsupported fields so payout contracts do not
  advertise customer/business policy names. Focused sidecar payout tests passed:
  ETH 48, TON 52, TRON 65. `compileall`, `git diff --check`, and forbidden
  payout business-limit field grep were clean across SHKeeper, ETH, TON, and
  TRON.
- Customer-policy boundary hardening: SHKeeper payout execution still has no
  min/max/day/tier/customer withdrawal policy runtime. The release gate now
  fails if payout execution/routing paths in SHKeeper or ETH/TON/TRON sidecars
  introduce common business policy field names such as amount bounds, daily
  withdrawal caps, tier, KYC, or customer limit fields. SHKeeper remains an
  execution and routing layer; upstream products must decide whether a customer
  may withdraw before calling SHKeeper.

Expected SHKeeper write scope:

- `shkeeper/models.py`
- `shkeeper/api_v1.py` or a new payout execution blueprint registered from the
  existing app
- `shkeeper/services/payout_service.py` only for compatibility boundaries
- new service modules for payout execution, rail catalog, callback outbox,
  reconciliation, and canonical hashing
- `migrations/versions/*`
- focused tests under `tests/`

### 1.1 Add Data Model

- [x] Add `PayoutExecution` table separate from legacy `Payout` unless migration
  review proves a bounded extension is safer.
- [x] Required unique key: `(consumer, external_id)`.
- [x] Store canonical request, request hash, sidecar payload hash, contract
  version, consumer, asset, network, destination, amount, callback endpoint, and
  callback URL snapshot.
- [x] Store normalized state, failure class, state version, state transition ID,
  occurred timestamp, sidecar state observation fields, txids/message hashes, and
  manual resolution evidence.
- [x] Store lease/claim fields used by dispatcher/reconciler:
  `lease_owner`, `lease_acquired_at`, `lease_expires_at`, `attempt_id`, and
  `claim_token`, or implement equivalent row-lock/CAS fields with the same
  duplicate-submit protection.
- [x] Add additive migration. Do not mutate legacy payout rows except for
  compatibility-safe indexes already present.

Acceptance:

- [x] `PayoutExecution` can be created before sidecar submit.
- [x] Duplicate `(consumer, external_id)` cannot race through.
- [x] Legacy admin payout rows and legacy status endpoint still work.

### 1.2 Add PayoutRail Catalog

- [x] Add `PayoutRail` config/storage for `(consumer, asset, network)` routing.
- [x] Required fields: `crypto_id`, `sidecar_service`, `sidecar_symbol`,
  token/Jetton metadata, chain id/network id, decimals, source wallet reference,
  `payout_queue`, enabled flag, callback endpoint, hot wallet policy, and
  contract version. SHKeeper must not own per-withdrawal or daily business
  limits; those remain upstream product policy.
- [x] Add generic `flask payout-rail-sync` that upserts `PayoutRail` rows from
  `PAYOUT_RAILS_JSON`, and wire Helm to run it so rail creation is not a manual
  SQL/runbook step.
- [x] Treat Helm-generated `PAYOUT_RAILS_JSON` as desired state when it includes
  a top-level `consumer`: rails absent from the desired catalog are preserved for
  history but disabled for client withdrawals.
- [x] Make `payout-rail-sync` fail closed for invalid booleans, missing callback
  endpoint on enabled rails, non-USDT decimals, duplicate desired rails,
  unknown rail config fields, and partially invalid batches. SHKeeper does not
  store or apply business amount/day limit fields; those controls stay upstream.
- [x] Record Phase 1 source wallets explicitly: TRON `fee_deposit`, TON
  `fee_deposit`, and ETH existing `fee_deposit`; ETH production enablement stays
  disabled until owned image and Helm runtime gates pass.
- [x] Reject unknown, disabled, or unsupported rails before creating a
  `PayoutExecution`; consumer amount/day limits are not part of SHKeeper.
- [x] Do not route by string concatenating `asset` and `network`.

Acceptance:

- [x] TRON `USDT` maps to `tron-shkeeper`.
- [x] TON `TON-USDT` maps to `ton-shkeeper`.
- [x] ETH `ETH-USDT` maps to `ethereum-shkeeper`.
- [x] Catalog queue matches sidecar queue env/config and Helm worker `-Q`.

### 1.3 Add Service-To-Service API

- [x] Add `POST /api/v1/payout-executions`.
- [x] Add `GET /api/v1/payout-executions/{external_id}` scoped by authenticated
  consumer.
- [x] Return the full client-facing status schema: `consumer`, `execution_id`,
  nullable `sidecar_execution_id`, `contract_version`, `event_version`,
  `state_transition_id`, timestamps, canonical amount, destination,
  `request_hash`, `sidecar_payload_hash`, failure class, txids/message hashes,
  sidecar state evidence, error fields, and reconciliation flag.
- [x] Use scoped service auth, HMAC body signature, timestamp tolerance, replay
  protection, and key rotation.
- [x] Document stable auth header names and key IDs for submit/status and callback
  signing. Signature base is:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- [x] Do not reuse admin Basic Auth.
- [x] Normalize USDT amounts to exactly 6 decimals before hashing/storage.
- [x] Reject higher precision instead of rounding.
- [x] Resolve callback endpoint from consumer config; reject arbitrary callback
  URL unless explicitly allowlisted.

Acceptance:

- [x] Equivalent amount strings such as `25`, `25.0`, and `25.000000` produce the
  same canonical request hash.
- [x] `25.0000001` is rejected.
- [x] Duplicate same canonical payload returns existing execution.
- [x] Duplicate changed payload returns `409 IDEMPOTENCY_CONFLICT`.
- [x] Missed callback followed by status polling gives the API consumer enough
  ordering metadata to apply the state monotonically.

### 1.4 Add State Machine And Reconciler

- [x] Implement SHKeeper states: `CREATED`, `PREFLIGHTED`, `ENQUEUEING`,
  `ENQUEUED`, `BROADCAST`, `CONFIRMED`, `FAILED_PRE_BROADCAST`,
  `FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`.
- [x] Increment `state_version` transactionally on every state transition.
- [x] Persist immutable `state_transition_id` and `occurred_at`.
- [x] Add SHKeeper reconciler worker. APScheduler in the web process is not the
  production withdrawal reconciler.
- [x] Recover `CREATED` and `PREFLIGHTED` through durable submit dispatch or an
  equivalent DB-backed job path. Do not make sidecar submit recoverability depend
  only on the original HTTP request process.
- [x] Recover stale `ENQUEUEING` only after authenticated sidecar status lookup by
  SHKeeper `execution_id`. Retry submit only when sidecar durably returns
  `NOT_FOUND`/`NO_EXECUTION_CREATED`; otherwise move ambiguous or unavailable
  status to `RECONCILIATION_REQUIRED`.
- [x] Apply sidecar statuses monotonically by sidecar `state_version` and
  `state_transition_id`.
- [x] Move conflicting same-version sidecar data to `RECONCILIATION_REQUIRED`.

Acceptance:

- [x] Stale sidecar status cannot overwrite newer SHKeeper execution state.
- [x] Same-version conflicting status produces reconciliation.
- [x] Submit timeout never creates a second execution.
- [x] Crash after `PayoutExecution` creation but before sidecar submit is recovered
  by the dispatcher/reconciler and remains idempotent by `(consumer, external_id)`.
- [x] Timeout while calling sidecar submit returns status as authoritative state or
  `RECONCILIATION_REQUIRED`, never a new automatic execution.

### 1.5 Add Callback Event Outbox

- [x] Add `PayoutCallbackEvent` table.
- [x] Unique keys: `event_id`, `(execution_id, event_version)`,
  `state_transition_id`.
- [x] Store immutable payload and payload hash, plus delivery signature metadata,
  callback endpoint ID, dispatch status, attempt count, next attempt, last error.
- [x] Insert callback outbox events in the same transaction as the state
  transition they represent. A committed payout state must not rely on inline
  best-effort callback generation.
- [x] Payload includes previous state, current state, event ordering, rail, amount,
  destination, txids/message hashes, failure class, error fields, and
  reconciliation flag.
- [x] Dispatcher retries are bounded and observable.
- [x] Retries resend the stored payload. `event_id` remains the callback nonce and
  idempotency key; timestamp/signature metadata is refreshed for each delivery
  attempt.

Acceptance:

- [x] Duplicate callback delivery is idempotent by `event_id`.
- [x] Callback payload includes nullable `sidecar_execution_id`.
- [x] Callback and status payloads share SHKeeper `event_version` ordering.

### 1.6 Guard Legacy Spend Paths

- [x] Identify legacy admin/manual payout, legacy multipayout, legacy autopayout,
  direct `crypto.mkpayout`, and direct `crypto.multipayout` paths for TRON, TON,
  ETH.
- [x] For any execution-enabled rail, ensure automatic/service
  client-withdrawal traffic cannot bypass `PayoutExecution` through legacy paths.
- [x] Keep manual/admin payout available as explicit operator action, but require
  audit metadata and the same wallet lock/nonce/seqno/resource guard when it
  spends from the current client-withdrawal source wallet.

Acceptance:

- [x] Legacy autopayout, legacy multipayout, service-originated legacy calls, and
  direct TRON/TON/ETH adapter payout calls cannot bypass client payout
  reconciliation for an enabled rail; manual/admin payout remains an explicit
  audited operator flow.

## Phase 2: Shared Sidecar Contract

Expected shared sidecar write scope per sidecar:

- `app/api/payout.py`
- `app/api/__init__.py` or a new payout-auth wrapper module
- `app/models.py` / schema or migration files
- `app/tasks.py`
- `app/wallet.py` / `app/coin.py` / ETH equivalent signing module
- readiness/auth/status helpers
- focused tests under each sidecar `tests/`

### 2.1 Add Sidecar Auth

- [x] Add scoped SHKeeper-to-sidecar payout credentials.
- [x] Sign request body with timestamp/replay protection using:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- [x] Reject tampered, replayed, or wrong-consumer requests.
- [x] Keep legacy Basic Auth endpoints available for legacy behavior, but do not
  use them for Grither client withdrawals.
- [x] Sidecar `/payout-executions/<execution_id>` endpoints must be callable with
  scoped payout HMAC auth without requiring legacy Basic Auth. Legacy
  `/payout`, `/multipayout`, and admin/manual endpoints stay under existing
  legacy authentication.

Acceptance:

- [x] Missing/bad payout credential returns 401/403.
- [x] Body consumer not allowed for caller returns 403.

### 2.2 Add Sidecar Execution Table

- [x] Add execution table in each sidecar.
- [x] Unique keys: `execution_id`, `(consumer, external_id)`.
- [x] Store canonical payload, request hash, sidecar payload hash, state,
  `state_version`, `state_transition_id`, state timestamps, lease fields,
  source wallet, token/Jetton metadata, chain id, reference block/masterchain
  seqno, expiration/valid-until, nonce/seqno, signed payload hash/storage ref,
  txid/message hash, broadcast attempt marker, chain check metadata, failure
  fields, and reconciliation flag.
- [x] Use compare-and-set state transitions.
- [x] Store encrypted signed artifact bytes or an immutable artifact reference
  plus hash, stored-at timestamp, source wallet, token metadata, nonce/seqno,
  and chain/network metadata before network submit.
- [x] Stale `SIGNING` is retryable only when durable state proves no
  nonce/seqno/resource reservation, no signed payload, and no broadcast attempt
  marker. Otherwise move to `RECONCILIATION_REQUIRED`.

Acceptance:

- [x] Duplicate same payload returns current execution/status.
- [x] Duplicate changed payload returns 409.
- [x] Worker crash before unsafe boundary can be safely retried.
- [x] Worker crash at/after unsafe boundary becomes `RECONCILIATION_REQUIRED`
  unless deterministic evidence resolves it.
- [x] Tests cover stale `SIGNING` with and without nonce/seqno/resource
  reservation, signed artifact, and broadcast marker.

### 2.3 Add Preflight/Submit/Status Endpoints

- [x] Add `POST /USDT/payout/preflight`.
- [x] Add `POST /USDT/payout/submit`.
- [x] Add `GET /USDT/payout/status/{execution_id}`.
- [x] Submit creates durable local execution before enqueue.
- [x] Submit repeats critical preflight immediately before enqueue/signing.
- [x] Preflight and submit fail closed when the dedicated payout worker/queue is
  unavailable before signing or broadcast-side effects.
- [x] Submit response includes sidecar `state_version`, `state_transition_id`,
  `state_updated_at`, hashes, failure class, and reconciliation flag.
- [x] Status response includes the mandatory evidence/status schema from the spec.
- [x] Verify `sidecar_payload_hash` after sidecar canonicalization before creating
  or reusing an execution; store SHKeeper `request_hash` as opaque audit metadata.
- [x] Validate body `asset`/`network`/symbol against the endpoint rail. A
  `/USDT` or `/TON-USDT` endpoint must reject a payload for a different rail even
  when the caller is authenticated.
- [x] Preflight checks destination validity, token/Jetton balance, native
  fee/gas/resource balance, node/provider readiness, and dedicated worker/queue
  readiness.
- [x] Confirmation logic verifies rail-specific token transfer details before
  `CONFIRMED`: token contract/Jetton master, source wallet, destination, amount,
  and chain/network. Do not confirm from generic tx confirmation count alone.
- [x] Remove infinite SHKeeper notification loops from payout-critical workers.
  Use durable sidecar callback outbox or bounded retry outside the payout worker;
  SHKeeper status polling remains authoritative. Verified in TRON, TON, and ETH
  sidecar worktrees through sidecar callback outbox tests and full sidecar test
  suites.

Acceptance:

- [x] No endpoint returns only Celery `task_id` for client withdrawal flow.
- [x] Submit rejects mismatched `sidecar_payload_hash`.
- [x] Submit rejects wrong-rail body payloads before execution creation.
- [x] Worker-unavailable preflight is deterministic and safe pre-broadcast failure.
- [x] Insufficient USDT/Jetton balance and insufficient fee/gas/resource balance are
  deterministic pre-broadcast failures.
- [x] Node/provider unreadiness is fail-closed before signing.
- [x] Status does not omit boundary-required evidence after `SIGNED`,
  `BROADCASTING`, `BROADCASTED`, confirmation, or reconciliation states.
- [x] `CONFIRMED` is impossible without verified USDT transfer details.
- [x] SHKeeper unavailable after broadcast does not block the payout worker
  indefinitely, and execution status remains recoverable by polling.

## Phase 3: Rail Sidecar Hardening

### 3.1 TRON-USDT

Expected TRON write scope:

- `/Users/test/PycharmProjects/tron-shkeeper/app/api/payout.py`
- `/Users/test/PycharmProjects/tron-shkeeper/app/tasks.py`
- `/Users/test/PycharmProjects/tron-shkeeper/app/wallet.py`
- `/Users/test/PycharmProjects/tron-shkeeper/app/models.py`
- `/Users/test/PycharmProjects/tron-shkeeper/app/config.py`
- `/Users/test/PycharmProjects/tron-shkeeper/app/celery_readiness.py`
- `/Users/test/PycharmProjects/tron-shkeeper/tests/`

Current TRON implementation status, 2026-06-04:

- TRON sidecar implementation is present in `/Users/test/PycharmProjects/tron-shkeeper`.
- Review-fix focused payout suite: `59 passed`.
- Full TRON suite in the repository Python 3.9 `.venv`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 175 tests after the v1 HMAC-only route boundary fix.
- `git diff --check`: clean.
- Independent review fixes validated on code and patched: unsafe-state recovery
  for `SIGNED`/`BROADCASTING`, stale-worker no-downgrade guard, post-lock state
  reload before side effects, worker-time USDT balance check, legacy
  `/USDT/multipayout` queue/readiness/resource guard, minimum confirmation
  handling, confirmed-no-transfer terminal failure, CAS-safe status polling, and
  payout callback outbox write failure handling after transfer. A local review
  pass also fixed duplicate-worker CAS before lock so a redundant worker returns
  current execution state instead of failing the Celery task. Subagent review
  follow-up also fixed underfunded legacy multipayout enqueue, partial-success
  callback loss on later batch failure, confirmation source drift after
  `fee_deposit` key rotation, and transient resource-lock timeout terminalization.
  Additional review follow-up validates broadcast-result txid evidence against
  the signed transaction txid before `BROADCASTED`; mismatches now fail closed to
  manual reconciliation and the real tronpy wallet wrapper preserves the
  broadcast txid after `wait()`.
  Helm/SHKeeper integration review follow-up also verified that TRON
  `/payout-executions/<execution_id>` endpoints accept scoped HMAC without
  requiring legacy Basic Auth, while legacy `/USDT/multipayout` remains protected
  by legacy Basic Auth. Contract tests passed 22 tests; focused payout group
  passed 81 tests.
  Same-wallet `fee_deposit` spend-path review is now applied: the shared
  reentrant Redis guard covers client payout execution, legacy single payout,
  legacy multipayout, default `Wallet.transfer()` spends, AML TRX top-ups via
  `Wallet.transfer()`, TRC20 sweep fee funding, TRC20 account activation funding,
  staking-provider energy delegation, staking API
  freeze/unfreeze/withdraw/reward/delegate paths, `undelegate_energy()`, and SR
  voting when the energy delegator is the `fee_deposit` wallet. Onetime-account
  sweeps and separate energy-account paths were reviewed as non-conflicting
  because they do not sign from the payout source wallet. Verification passed:
  `tests/test_fee_deposit_spend_guard.py` 6 tests, focused payout group 90
  tests, full TRON suite 181 tests, edited-file `py_compile`, and
  `git diff --check`.
  Residual accepted risk: if local DB is unavailable after a legacy transfer,
  no durable callback outbox row can be created; the task logs for manual
  recovery and client withdrawals rely on authoritative sidecar status.

- [x] Preserve broker queue `tron_usdt_fee_payouts` unless coordinated rename is
  implemented.
- [x] Add client payout preflight/submit/status contract.
- [x] Store signed raw transaction and txid before broadcast.
- [x] Add payout-specific transaction expiration/ref-block validity cap; do not
  use legacy 12-hour expiration for client withdrawals.
- [x] Keep resource quote/resource lock behavior.
- [x] Reject unactivated or otherwise non-transferable TRON destinations before
  signing.
- [x] Preflight TRC20 USDT balance, TRX/resource availability, and provider
  readiness.
- [x] Verify current `fee_deposit` wallet/resource lock coverage for every other
  TRON spend path.

Acceptance:

- [x] Worker/container `tron-usdt-payouts` consumes `tron_usdt_fee_payouts`.
- [x] Queue readiness checks the same queue.
- [x] Signed client payout expiration is within configured cap.
- [x] Negative evidence requires source wallet, TRC20 events, txid, finalized
  range, and expiration/ref-block validity proof.
- [x] Tests cover TRON resource quote/provider failure and unactivated destination
  rejection.

### 3.2 TON-USDT

Expected TON write scope:

- `/Users/test/PycharmProjects/ton-shkeeper/app/api/payout.py`
- `/Users/test/PycharmProjects/ton-shkeeper/app/tasks.py`
- `/Users/test/PycharmProjects/ton-shkeeper/app/coin.py`
- `/Users/test/PycharmProjects/ton-shkeeper/app/models.py`
- `/Users/test/PycharmProjects/ton-shkeeper/app/config.py`
- `/Users/test/PycharmProjects/ton-shkeeper/tests/`

Current TON implementation status, 2026-06-04:

- TON sidecar implementation is present in `/Users/test/PycharmProjects/ton-shkeeper`.
- Review-fix focused payout suite: `35 passed`.
- Full TON suite: `67 passed` after adding SHKeeper v1
  `/payout-executions/<execution_id>` routes.
- `compileall -q app tests`: clean with pycache redirected to `/private/tmp`.
- `git diff --check`: clean.
- Local review fixes validated on code and patched: unsafe-state recovery for
  `SIGNED`/`BROADCASTING`, stale-worker no-downgrade guard, post-lock state
  reload before seqno/sign/broadcast side effects, duplicate-worker CAS before
  lock, CAS-safe status polling, legacy `/TON-USDT/payout` and
  `/TON-USDT/multipayout` queue/readiness guard, minimum masterchain
  confirmation handling, indexed Jetton transfer mismatch terminal failure, and
  callback outbox write failure handling after payout.
- Additional review fixes validated on code and patched: real TON `Coin`
  client-payout signing/broadcast primitives are implemented, broadcast result
  hash is checked against persisted signed message hash before `BROADCASTED`,
  provider-side preflight failures return controlled 503 errors, and
  `valid_until` evidence now matches tonsdk wallet v4r2 60-second expiry
  behavior.
- Helm/SHKeeper integration review follow-up added TON
  `/payout-executions/<execution_id>` preflight/submit/status routes, validates
  path/body execution-id mismatches, and exempts only HMAC-protected payout
  execution endpoints from legacy Basic Auth. Contract tests passed 16 tests.
- Same-wallet `fee_deposit` seqno review is now applied: a shared reentrant
  `fee_deposit_seqno_lock` covers client payout execution, native fee-deposit
  multipayout, Jetton fee-deposit multipayout, and defensive drain paths that
  would sign from `fee_deposit`. Normal onetime-account drains were reviewed as
  non-conflicting because they do not sign from the payout source wallet. Legacy
  single payout flows through legacy multipayout and therefore uses the same
  guarded `Coin` spend path.
- TON multipayout result mapping is fixed: native TON and Jetton batches keep
  each `(payout, transaction)` pair together before callback result creation, so
  multi-destination batches do not report every tx with the last destination and
  amount. Verification passed: `tests/test_fee_deposit_seqno_guard.py` 3 tests,
  schema regression 2 tests, focused payout group 59 tests, full TON suite
  72 tests, `compileall -q app tests`, and `git diff --check`.

- [x] Add dedicated `ton-usdt-payouts` worker consuming `ton_usdt_payouts`.
- [x] Add worker readiness.
- [x] Add client payout execution table and contract endpoints.
- [x] Serialize TON source-wallet seqno for client payouts.
- [x] Preflight Jetton balance, TON fee balance, worker readiness, and provider
  outage fail-closed behavior.
- [x] Persist immutable signed artifact evidence/ref, message hash, seqno,
  valid-until, source wallet, and Jetton wallet/master evidence before
  broadcast. Phase 1 deliberately stores non-spendable evidence/hash, not a
  reusable signed BOC payload.
- [x] Preserve no-blind-retry behavior for `sendBoc`/`sendBocReturnHash` timeout.
- [x] Fix multipayout result mapping before enabling TON client withdrawals.
- [x] Verify current TON `fee_deposit` seqno guard coverage for every other TON
  spend path.

Acceptance:

- [x] Ambiguous TON broadcast timeout becomes `RECONCILIATION_REQUIRED`.
- [x] Negative evidence requires BOC/message hash, seqno, source wallet history,
  Jetton transfer history, masterchain range, and valid-until proof.
  Current status responses expose the persisted execution evidence and Jetton
  confirmation metadata. `docs/runbooks/usdt-payout-operations.md` now documents
  TON source-wallet history, Jetton transfer history, masterchain range,
  valid-until, and manual-payout blocking rules, and Grither manual-resolution
  validation requires the same structured TON evidence before
  `SAFE_FOR_MANUAL_PAYOUT`.
- [x] Tests cover seqno serialization and Jetton/TON fee preflight failures.

### 3.3 ETH-USDT

Expected ETH write scope:

- `/Users/test/PycharmProjects/ethereum-shkeeper/app/api/payout.py`
- `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`
- `/Users/test/PycharmProjects/ethereum-shkeeper/app/token.py`
- `/Users/test/PycharmProjects/ethereum-shkeeper/app/models.py`
- `/Users/test/PycharmProjects/ethereum-shkeeper/app/config.py`
- `/Users/test/PycharmProjects/ethereum-shkeeper/tests/`

- [x] Fork and own Ethereum sidecar first.
- [x] Add dedicated `eth-usdt-payouts` queue contract consuming
  `eth_usdt_payouts` from sidecar code. Helm worker rendering remains in Phase 4.
- [x] Add `ETH_USDT_PAYOUT_QUEUE=eth_usdt_payouts` and fail-closed worker/queue
  readiness for preflight/submit.
- [x] Add client payout execution table and contract endpoints.
- [x] Reject unknown sidecar execution fields before canonicalization so
  customer/business payout policy fields cannot be silently accepted by the ETH
  sidecar.
- [x] Add address validation, ERC20 USDT balance preflight, ETH gas balance
  preflight, gas estimate, node sync/readiness.
- [x] Serialize nonce through a fee-deposit wallet-level nonce lock shared by
  payout and same-wallet drain gas-seeding paths.
- [x] Persist nonce, tx hash, signed transaction hash/ref, chain id, source wallet
  address, token contract, and broadcast marker before network submit. Phase 1
  deliberately does not retain spendable signed raw transaction bytes.
- [x] Ambiguous ETH RPC broadcast timeout after signed artifact or broadcast
  marker becomes `RECONCILIATION_REQUIRED`; never re-sign, re-enqueue, or blind
  retry from that boundary.
- [x] Validate ETH RPC broadcast result tx hash against the signed transaction
  hash before marking `BROADCASTED`; mismatches become
  `RECONCILIATION_REQUIRED`.
- [x] Use the fork's current `/payout` source wallet with one active payout
  worker, or add a wallet-level nonce allocator if any other path spends from the
  same wallet.

Acceptance:

- [x] Negative evidence does not treat txpool disappearance as proof.
- [x] Manual payout is blocked unless nonce is consumed by finalized same-nonce tx
  and chain/log evidence proves no matching USDT `Transfer`.

ETH local verification on 2026-06-04:

- Focused sidecar suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract tests.test_payout_execution_boundaries tests.test_payout_status_confirmation -v`
  -> `Ran 48 tests in 2.347s OK`.
- Manual payout safety focused suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract tests.test_payout_status_confirmation -v`
  -> `Ran 34 tests in 1.495s OK`.
- Full sidecar suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 66 tests in 2.802s OK`.
- Compile:
  `PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  -> clean.
- Diff hygiene: `git diff --check` -> clean.
- Review follow-up validated on code: stale `SIGNED`/`BROADCASTING` recovery now
  fails to `RECONCILIATION_REQUIRED`, post-lock row reload prevents stale workers
  from reserving nonce after a competing state transition, ERC20 confirmation uses
  persisted source wallet address, receipt without matching USDT `Transfer`
  becomes `FAILED_CHAIN_TERMINAL` after required confirmations, non-finite amounts
  are rejected, legacy ETH-USDT payout routes fail closed/route to the dedicated
  queue, and same-wallet fee-deposit spend paths share the nonce lock. Independent
  review follow-up also changed fee-deposit nonce reads to pending nonce, persists
  exact source wallet address at nonce reservation before signing, requires
  SHKeeper-provided `execution_id`, fails submit closed when dispatch is disabled
  without explicit manual-dispatch mode, and rechecks execution ownership before
  broadcast. Additional review follow-up validates the RPC broadcast result tx
  hash against the signed transaction hash before marking `BROADCASTED`.
  Additional parity block replaces the legacy ETH payoutnotify infinite retry
  loop with a durable `PayoutCallbackOutbox`, bounded retry/claim semantics, and
  a due-row sweeper while preserving legacy payout return behavior after a
  completed transfer. Manual payout safety follow-up adds machine-readable
  `manual_payout_allowed`, `manual_payout_block_reason`, and
  `manual_payout_evidence` status fields, and keeps manual payout blocked until
  finalized same-nonce evidence proves no matching ETH-USDT `Transfer`.
  Additional contract-boundary follow-up rejects unsupported ETH sidecar request
  fields before canonicalization.
- Remaining production blocker: ETH chart topology now renders; do not enable
  ETH-USDT in production until the owned fork image is published with a concrete
  tag/digest, production Secret bindings are created, restore-drill evidence is
  recorded, and a low-value staging smoke payout passes.

## Phase 4: Helm/Kubernetes

Expected Helm write scope:

- `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/values.yaml`
- `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/values.schema.json`
  or required/fail validation templates
- `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/templates/deployments/*-shkeeper.yaml`
- new templates for NetworkPolicy, migration jobs/init steps, and secret refs
- `/Users/test/PycharmProjects/shkeeper-helm-charts/tests/`

- [x] Add first-class payout worker values for TRON, TON, ETH.
- [x] Render SHKeeper payout execution reconciler and callback dispatcher workers.
  The Grither Pay submit dispatcher remains in the Grither Pay outbox; SHKeeper
  submit is served by the SHKeeper API/web deployment.
- [x] Clean up TRON payout worker chart API: worker enablement, queue isolation,
  and default worker restriction are controlled by explicit chart values and
  Helm fail checks, not inferred from `extraEnv`.
- [x] Render worker containers from values.
- [x] Restrict normal `tasks` worker to default queue when payout worker enabled.
- [x] Payout workers default to concurrency 1 and prefetch 1, and Helm rendering
  fails for enabled TRON/TON/ETH payout workers if concurrency or prefetch is
  raised above 1 before a rail-specific wallet allocator proves it safe.
- [x] Render or reference persistent sidecar execution DB for every enabled rail.
- [x] Render/apply sidecar DB init/migration jobs before payout workers are
  deployed by Helm hook ordering.
- [x] Fail Helm rendering if SHKeeper payout reconciler/dispatcher workers are
  explicitly enabled while `payouts.enabled=false`; payout workers must not run
  without the payout API contract, secrets, and rail config.
- [x] Render backup/restore posture for sidecar execution state, or require an
  external DB backup policy for every enabled payout rail.
- [x] If Redis remains pod-local, render persistence/AOF parity for payout queues or
  prove queued payout work is fully recoverable from sidecar execution state.
- [x] If Redis remains pod-local for a payout sidecar, force `replicas: 1`,
  `strategy.type: Recreate`, no surge/second broker window, Redis PVC-backed AOF,
  `preStop`, startup/readiness probes, termination grace, and fail-closed payout
  readiness during startup, migration, and shutdown.
- [x] If SHKeeper core payout reconciler/callback workers run as separate pods
  while core state is still SQLite on `shkeeper-db-claim`, treat that as a
  single-node Phase 1 compromise. Before HA, zero-downtime, or multi-node core
  workers, move SHKeeper payout execution state to a shared DB or keep those
  workers in the same pod as the web process.
- [ ] Run a restore drill before enabling a rail for client withdrawals.
- [x] Add resources/limits, readiness probes, liveness probes, preStop, and
  termination grace for app/tasks/payouts/redis containers.
- [x] Fail Helm rendering for invalid payout operational bounds:
  `payouts.sidecarRequestTimeoutSeconds`, `payouts.authMaxAgeSeconds`,
  `payouts.shkeeperWorkers.intervalSeconds`, and
  `payouts.shkeeperWorkers.limit` must be positive integers when payouts are
  enabled. These are request/auth/loop bounds, not payout amount limits.
- [x] Add NetworkPolicy or equivalent ingress restriction so only SHKeeper service
  traffic reaches payout preflight/submit/status.
- [x] Add Secret/external-secret references for payout credentials and callback
  keys, and preserve existing RPC/backend Secret references.
- [x] Document and verify hot-wallet material secret/storage handling per rail
  before enabling that rail in production. Do not commit real hot-wallet secrets
  or render them through ConfigMaps. Helm now rejects enabled payout topology
  when SHKeeper or TRON/TON/ETH sidecar `extraEnv` supplies literal
  secret/hot-wallet-looking keys such as private keys, mnemonics, seeds,
  passwords, API keys, auth tokens, `FEE_DEPOSIT_*`, or `HOT_WALLET_*`;
  production values must use Secret/external-secret references for secret
  material.
- [x] Add per-rail enablement, pause/kill switch, callback endpoint, source
  wallet reference, and worker routing as runtime/chart configuration.
- [x] Make SHKeeper rail catalog sync desired-state for a configured consumer:
  enabled rails stay/update, paused/killed rails sync as
  `execution_enabled=false`, and rails removed from Helm values are
  disabled in the DB instead of remaining silently active from an older release.
- [x] Add environment-specific production values examples with concrete Secret
  object names, owned image tags, and restore-drill evidence references. Do not
  commit real secret values. Evidence on 2026-06-04: Helm chart now includes
  TRON, TON, and ETH production overlay examples with concrete Kubernetes Secret
  object names, owned image tags (`ghcr.io/nilof470/tron-shkeeper:5a6133b`,
  `ghcr.io/nilof470/ton-shkeeper:d8f5c77`, and
  `ghcr.io/nilof470/ethereum-shkeeper:977f920`), one kill-switched rail each,
  and restore-evidence reference fields. The overlays keep chart-owned
  `PrometheusRule` rendering disabled by default so Prometheus Operator is not a
  hidden rollout dependency; alert manifests remain explicit opt-in. Actual
  registry publication and cluster restore-drill evidence remain production
  rollout gates before client withdrawals are enabled.
- [x] Add an ETH-USDT production values overlay scaffold that renders the owned
  fork repository, `eth-usdt-payouts` worker, sidecar migration job, SHKeeper
  payout reconciler, Secret refs, source-wallet ref, and backup/restore evidence
  placeholders without committing hot-wallet material or secret values.
- [x] Add required validation through `values.schema.json` or Helm
  `required`/`fail`; enabled rails must fail rendering when required production
  values are missing.

Acceptance:

- [x] Helm template tests prove worker rendering, queue isolation, queue value
  matching, service mapping, storage, migrations, NetworkPolicy/ingress
  restriction, and Secret references.
- [x] Negative chart tests prove TRON `extraEnv` resource-provisioning flags cannot
  silently enable payout worker topology without explicit payout rail values and
  production gate config. Payout-critical `extraEnv` keys now fail rendering when
  the rail is enabled.
- [x] Negative chart tests prove enabled payout topology rejects literal
  hot-wallet/secret-looking `extraEnv` values for SHKeeper and TRON/TON/ETH
  sidecars, including payout auth env override attempts.
- [x] Negative chart tests fail for enabled rails missing owned image tag, queue
  match, storage/migration config, backup posture, NetworkPolicy/ingress
  restriction, Secret refs, pause/kill-switch config, or safe rollout strategy.
- [x] Environment-specific TRON/TON/ETH production overlay tests prove concrete
  Secret names and owned image tags render, only one rail worker is present, and
  the rail catalog remains kill-switched (`execution_enabled=false`).
  They also prove `PrometheusRule` is not rendered unless monitoring is enabled
  explicitly.
- [x] Chart tests prove payout worker concurrency/prefetch defaults and pod-local
  Redis persistence/recovery posture. Follow-up chart tests now also prove
  unsafe payout worker concurrency/prefetch overrides fail rendering.
- [x] Rendered manifests never place real payout credentials, signing keys, RPC
  credentials, or hot-wallet material in ConfigMaps.
- [x] Rollout cannot run two active pod-local Redis brokers serving payout submit
  traffic for the same rail.
- [ ] Restore drill evidence exists before a rail is enabled.

## Phase 5: Grither Pay Integration

The Grither Pay repository has been validated separately for integration
handoff. Use the Grither Pay integration spec and task plan for exact package
names, existing classes, provider-specific state gaps, and implementation order.

Current Grither Pay implementation status, 2026-06-04:

- First implementation pass is present in `/Users/test/IdeaProjects/grither-pay`.
- Targeted payout integration gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='ShKeeperPayout*Test,WalletShKeeperWithdrawalCreationServiceTest,WalletCryptoWithdrawalPayoutStateServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,AdminShKeeperPayoutResolutionControllerTest,ShKeeperConfigTest'`
  returned 63 tests, 0 failures, 0 errors.
- Additional wallet/provider identity regression gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='AltynWalletStatusMapperTest,AltynWalletStatusPollingAdapterTest,WalletDepositServiceTest,WalletDepositIntegrationTest,WalletCrossSystemIntegrationTest,WalletControllerIntegrationTest,WalletWebhookRoutingTest'`
  returned 187 tests, 0 failures, 0 errors.
- Wallet concurrency regression passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=WalletConcurrencyIntegrationTest`
  returned 8 tests, 0 failures, 0 errors.
- Fresh full Grither backend suite passed:
  `./mvnw -q -pl apps/backend clean test -Djacoco.skip=true`
  produced surefire reports with 1973 tests, 0 failures, 0 errors, 0 skipped.
- The targeted Spring tests exercised PostgreSQL via Testcontainers and applied
  Liquibase through `V089_create_shkeeper_payouts.sql`.
- `git diff --check` passed in `/Users/test/IdeaProjects/grither-pay`.
- Provider payment reference and provider transaction id are now separated in
  Grither wallet provider status handling. Expired unbound USDT deposits without
  provider external id or prior marker stay fail-closed.
- Rail-specific manual negative-evidence gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualResolutionServiceTest`
  returned 8 tests, 0 failures, 0 errors, and the adjacent
  accounting/state/controller set returned 30 tests, 0 failures, 0 errors.
  `SAFE_FOR_MANUAL_PAYOUT` now requires structured common and TRON/TON/ETH
  evidence, not just a free-text operator note or
  `negativeEvidenceConfirmed=true`.
- Grither scheduler/config recovery gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest`
  returned 13 tests, 0 failures, 0 errors after adding callback-backlog
  monitoring. `ShKeeperPayoutMetricsTest` plus the scheduler test returned 15
  tests, 0 failures, 0 errors after adding fail-open Micrometer scheduler
  metrics. The adjacent payout/config/state set returned 68 tests, 0 failures,
  0 errors. The scheduled submit dispatcher, status sync, manual-review monitor,
  and callback-backlog monitor now skip when payouts are disabled and emit
  `SCHEDULER_FAILURE` without terminating the scheduled loop when an unexpected
  scheduler-level exception occurs.
- Grither submit retry exhaustion alert gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSubmitDispatcherTest`
  returned 10 tests, 0 failures, 0 errors. When submit retries are exhausted,
  the outbox becomes `FAILED_FINAL`, the execution becomes
  `RECONCILIATION_REQUIRED`, the withdrawal remains `PROCESSING`, funds stay
  reserved, and operator alert delivery failure does not fail the dispatcher.
- Grither stale manual-review alert gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualReviewMonitorTest`
  returned 2 tests, 0 failures, 0 errors. The payout-local monitor scans stale
  open manual resolution states with a DB-relative timestamp query, masks
  external id and destination in alert context, and does not move payout state.
- Grither status-sync/callback-backlog alert gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperConfigTest,ShKeeperPayoutSchedulerTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutCallbackBacklogMonitorTest`
  returned 32 tests, 0 failures, 0 errors. Status-sync reconciliation now has a
  direct path test, and callback backlog is a monitor-only alert over stale
  `shkeeper_payout_callback_events.applied_at is null` rows. It does not replay
  callbacks or mutate payout/wallet state. A masking follow-up over submit and
  status-sync alerts returned 13 tests, 0 failures, 0 errors.
- Grither scheduler-level Micrometer metrics gate passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest,ShKeeperPayoutMetricsTest`
  returned 15 tests, 0 failures, 0 errors. Metrics use constant operation/result
  tags and fail open so observability cannot break payout processing.
- Grither ShKeeper amount-cap correction gate passed:
  follow-up review found that `shkeeper.payouts.networks.*.max-single-amount`
  and `daily-limit` would create a second provider-specific product policy layer
  on top of Grither's existing wallet limits. The Grither implementation no
  longer exposes or enforces provider-owned amount policy for SHKeeper payouts.
  Amount validation remains owned by the existing wallet/business limit layer;
  SHKeeper payout code validates rail enablement, canonical USDT precision,
  ledger reservation, idempotent outbox, callbacks, status sync, and manual
  review.
- Grither payout/config verification after amount-cap removal passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperConfigTest,WalletShKeeperWithdrawalCreationServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,ShKeeperPayoutMetricsTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutWebhookControllerTest,AdminShKeeperPayoutResolutionControllerTest`
  returned 50 tests, 0 failures, 0 errors.
- Grither full backend regression after amount-cap removal passed:
  `./mvnw -q -pl apps/backend clean test -Djacoco.skip=true` produced fresh
  surefire reports with 1973 tests, 0 failures, 0 errors, 0 skipped.
- Grither architecture boundary verification passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ArchitectureRulesTest`
  returned 362 tests, 0 failures, 0 errors after routing limit decimal parsing
  through `ShKeeperProviderValueParser`.
- Final adjacent Grither payout/config/state regression passed:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest,ShKeeperPayoutMetricsTest,ShKeeperConfigTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutManualResolutionServiceTest,ShKeeperPayoutManualReviewMonitorTest,ShKeeperPayoutCallbackBacklogMonitorTest,AdminShKeeperPayoutResolutionControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest,WalletShKeeperWithdrawalCreationServiceTest`
  returned 76 tests, 0 failures, 0 errors.

- [x] Add or confirm withdrawal state machine.
- [x] Create withdrawal row, ledger reservation entry, and SHKeeper-submit outbox
  event atomically in one DB transaction.
- [x] Enforce per-rail enablement and existing Grither wallet/business amount
  validation before submit. Do not add SHKeeper-side amount/day caps.
- [x] Use the immutable Grither withdrawal public number as SHKeeper
  `external_id`, unless the Grither integration spec is explicitly amended to
  use another immutable unique withdrawal identifier.
- [x] Add submit dispatcher with idempotent retry.
- [x] Submit dispatcher claims outbox rows with DB row lock, atomic claim update,
  or ShedLock plus row-level CAS; no in-memory-only locks.
- [x] Process SHKeeper callbacks with HMAC verification.
- [x] Deduplicate callbacks by `event_id`, with a stable fallback key only for
  compatibility.
- [x] Poll SHKeeper status after submit timeout or missed callback.
- [x] Apply callback/status through one monotonic state-application path by
  `event_version` and `state_transition_id`.
- [x] Apply callback/status under row lock or inherited optimistic `@Version` plus
  retry-limited CAS; callback event insert, provider state update, wallet status
  update, and ledger effect commit atomically.
- [x] Preserve Grither fee accounting: reserve `payout_amount + network_fee`, send
  only `payout_amount` to SHKeeper, refund full reserved amount only for
  `FAILED_PRE_BROADCAST`, and require explicit operator accounting for manual fee
  deltas.
- [x] Keep funds reserved for `RECONCILIATION_REQUIRED`, `MANUAL_REVIEW`,
  `SAFE_FOR_MANUAL_PAYOUT`, and `MANUAL_PAYOUT_PENDING`.
- [x] Map any `FAILED_CHAIN_TERMINAL` callback/status to `MANUAL_REVIEW`, keep
  funds reserved, and block user retry/manual payout until operator accounting
  resolution is recorded.
- [x] Implement operator reconciliation and manual payout completion evidence.
- [x] Block manual payout completion until operator evidence satisfies the
  rail-specific negative-evidence checklist.

Acceptance:

- [x] Crash after DB commit but before dispatcher send is recovered by outbox.
- [x] Crash before DB commit does not create a payout without reservation.
- [x] Duplicate callback delivery is idempotent by `event_id`.
- [x] Delayed callback cannot regress withdrawal state.
- [x] Conflicting same-version callback/status keeps funds reserved and alerts.
- [x] Delayed `FAILED_CHAIN_TERMINAL` callback/status cannot release funds or
  create an automatic retry.
- [x] Manual payout is forbidden until reconciliation proves the original automatic
  execution cannot still complete.

## Phase 6: Observability And Ops

- [x] Add initial Grither scheduler-level Micrometer metrics for payout
  operations: scheduler run counters with `operation`/`result` tags and
  processed-row summaries.
- [x] Add SHKeeper DB-backed payout metrics to `/metrics` for execution counts,
  oldest non-terminal execution age, reconciliation-required count, callback
  outbox backlog count, and oldest callback backlog age. Metrics collection is
  fail-open so `/metrics` remains available if payout DB collection fails.
  Collection is snapshot-safe: a failed DB collection leaves the last successful
  payout gauge values in the registry instead of clearing critical alerts.

  Verification on 2026-06-04:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  -> `Ran 2 tests in 0.044s OK`.

  Metric names:
  `shkeeper_payout_execution_count`,
  `shkeeper_payout_non_terminal_oldest_age_seconds`,
  `shkeeper_payout_reconciliation_required_count`,
  `shkeeper_payout_callback_outbox_backlog_count`,
  `shkeeper_payout_callback_outbox_oldest_age_seconds`.
- [x] Add sidecar DB-backed payout metrics to TRON/TON/ETH `/metrics` for
  execution counts by state, oldest non-terminal execution age,
  reconciliation-required count, callback outbox backlog count/age, and
  dedicated payout worker readiness. Add sidecar Redis broker queue depth and
  oldest-age gauges for each dedicated payout queue, plus hot-wallet USDT and
  native fee/gas/resource balance gauges for the current `fee_deposit` source
  wallet. Add DB-backed sidecar failure gauges by
  `state/failure_class/bounded error_code` and process-local API boundary reject
  counters by `operation/code` for auth/HMAC and payout-contract rejects on
  `preflight`, `submit`, and `status` failure-rate dashboards. Redis read
  failure, unparseable queued task age, or balance collection failure is
  reported as `-1` and does not break `/metrics`.
  Sidecar payout metric collection is fail-open; TRON/ETH chain metric failures
  and external release lookups also fail open so payout observability remains
  available during node or GitHub failures.

  Verification on 2026-06-04:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  in `/Users/test/PycharmProjects/tron-shkeeper`
  -> `Ran 190 tests in 1.763s OK`.
  Same command in `/Users/test/PycharmProjects/ton-shkeeper`
  -> `Ran 80 tests in 2.110s OK`.
  Same command in `/Users/test/PycharmProjects/ethereum-shkeeper`
  -> `Ran 66 tests in 2.802s OK`.

  Metric names:
  `tron_payout_execution_count`,
  `tron_payout_non_terminal_oldest_age_seconds`,
  `tron_payout_reconciliation_required_count`,
  `tron_payout_callback_outbox_backlog_count`,
  `tron_payout_callback_outbox_oldest_age_seconds`,
  `tron_payout_worker_ready`,
  `tron_payout_broker_queue_depth`,
  `tron_payout_broker_queue_oldest_age_seconds`,
  `tron_payout_hot_wallet_balance`,
  `tron_payout_fee_wallet_balance`,
  `tron_payout_failure_count`,
  `tron_payout_request_failed_total`,
  and matching `ton_payout_*` / `ethereum_payout_*` names.

  Sidecar collection is also snapshot-safe for DB-backed execution/callback
  gauges: a DB collection failure preserves the last successful execution
  snapshot while still updating worker readiness, Redis queue depth/age, and
  wallet balance gauges. Sidecar `error_code` labels are bounded to
  machine-readable values and fall back to `OTHER`, so provider messages,
  destination addresses, and secrets do not become Prometheus labels.
- [x] Add SHKeeper runtime payout metrics for current failure counts by
  `failure_class`/bounded `error_code`, DB-backed dispatch backlog count/age by
  `payout_queue`, stuck execution count/age by state threshold, and rail
  enablement. SHKeeper does not export amount/day cap metrics because client
  payout limits are upstream business policy, not SHKeeper execution policy.

  Verification on 2026-06-04:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  -> `Ran 7 tests in 0.126s OK`.

  Metric names:
  `shkeeper_payout_failure_count`,
  `shkeeper_payout_dispatch_backlog_count`,
  `shkeeper_payout_dispatch_backlog_oldest_age_seconds`,
  `shkeeper_payout_stuck_execution_count`,
  `shkeeper_payout_stuck_execution_oldest_age_seconds`,
  `shkeeper_payout_rail_enabled`.
- [x] Add chart-owned optional PrometheusRule for first-release payout alerts:
  SHKeeper reconciliation required, stuck execution, SHKeeper dispatch backlog,
  SHKeeper callback backlog, enabled-rail catalog disabled/missing, enabled-rail
  sidecar worker unavailable, sidecar broker queue depth unavailable, sidecar
  broker queue backlog/age, wallet-balance metric unavailable, and optional low
  hot-wallet/fee-wallet balance alerts. Low-balance thresholds default to empty
  and render only when an operator explicitly sets `hotWalletMinimumBalance` or
  `feeWalletMinimumBalance`; they are alert thresholds, not mandatory payout
  amount validation.

  Verification on 2026-06-04 in
  `/Users/test/PycharmProjects/shkeeper-helm-charts`:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_payout_prometheus_rule_is_disabled_by_default tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_payout_prometheus_rule_renders_enabled_rail_alerts tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_values_expose_usdt_payout_worker_settings -v`
  -> `Ran 3 tests in 0.194s OK`.
  Full chart suite:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  -> `Ran 32 tests in 1.809s OK`.
- [x] Add remaining production runtime metrics and alert rules. Completed in
  this bucket: rail paused/kill-switch alert wiring through rail enablement
  drift, sidecar broker age/availability, wallet-balance availability and
  optional low-balance alerts, sidecar preflight/submit/status failure-rate
  dashboard metrics, nonce/seqno/resource allocator error alerts, ordering
  conflict alerts, and broadcast-time confirmation SLA breach alerts.
  - [x] Keep client/business amount-limit alerting on the upstream product side;
    SHKeeper must not expose amount/day cap reject counters.
  - [x] Add chart-owned enabled-rail alert for missing/disabled
    `shkeeper_payout_rail_enabled`, covering rail-sync drift, pause, and
    kill-switch states for rails that Helm values declare enabled.
  - [x] Add broker-level queue age metrics and Helm alerts. TRON, TON, and ETH
    sidecars stamp `payout_enqueued_at` into dedicated payout Celery task
    headers and expose `*_payout_broker_queue_oldest_age_seconds`; empty queue is
    `0`, Redis/unparseable age is `-1`. Helm alert wiring treats missing/negative
    queue age as broker metric unavailable and adds per-rail old-queue alerts.
  - [x] Add sidecar hot-wallet and native fee/gas/resource balance gauges plus
    Helm balance alert wiring. TRON exports USDT/TRX for `fee_deposit`, TON
    exports USDT/TON for `fee_deposit`, and ETH exports USDT/ETH for
    `fee_deposit`. Missing or failed balance collection is `-1` and is covered by
    wallet-balance-unavailable alerts. Low-balance alerts render only when
    explicit per-rail thresholds are configured; no production amount threshold
    is hardcoded. ETH balance collection was review-fixed to read the existing
    `fee_deposit` address directly instead of calling the auto-create
    `get_fee_deposit_account()` path from metrics.
  - [x] Add sidecar preflight/submit/status failure-rate dashboard metrics.
    TRON, TON, and ETH expose `*_payout_failure_count` from durable sidecar DB
    failure metadata and `*_payout_request_failed_total{operation,code}` for
    auth/HMAC and payout-contract rejects. Error-code labels are bounded;
    non-machine-readable values are exported as `OTHER`. These are
    dashboard/triage metrics, not new spend controls and not
    per-failed-execution alert rules.
  - [x] Add allocator/ordering/confirmation SLA alert wiring. SHKeeper exports
    `shkeeper_payout_confirmation_sla_breach_count`,
    `shkeeper_payout_confirmation_sla_breach_oldest_age_seconds`, and
    `shkeeper_payout_ordering_conflict_count`; confirmation SLA is based on
    `broadcasted_at`, not mutable status-poll `updated_at`. Helm renders
    confirmation SLA, ordering conflict, and bounded sidecar allocator/lock
    alerts for TRON, TON, and ETH.

  Follow-up correction on 2026-06-04 removed SHKeeper-side amount/day limit
  tests and rail-sync awareness entirely. SHKeeper now validates technical
  payout invariants only: positive canonical 6-decimal USDT amount, rail
  enablement, auth, idempotency, destination, callback endpoint, and sidecar
  execution contract. Focused verification confirmed no SHKeeper-owned payout
  amount/day limit identifiers remained in code/docs, and
  `.venv/bin/python -m unittest tests.test_payout_rail_sync`
  -> `Ran 13 tests in 0.102s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  in `/Users/test/PycharmProjects/tron-shkeeper`
  -> `Ran 9 tests in 0.650s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract.PayoutExecutionContractTests.test_wrong_rail_body_is_rejected -v`
  in `/Users/test/PycharmProjects/tron-shkeeper`
  -> `Ran 1 test in 0.647s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  in `/Users/test/PycharmProjects/ton-shkeeper`
  -> `Ran 7 tests in 0.539s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract.TonPayoutExecutionContractTests.test_wrong_rail_body_is_rejected_before_creation -v`
  in `/Users/test/PycharmProjects/ton-shkeeper`
  -> `Ran 1 test in 0.451s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  in `/Users/test/PycharmProjects/ethereum-shkeeper`
  -> `Ran 10 tests in 0.825s OK`.
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract.EthPayoutExecutionContractTests.test_wrong_rail_body_is_rejected_before_creation -v`
  in `/Users/test/PycharmProjects/ethereum-shkeeper`
  -> `Ran 1 test in 0.619s OK`.
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_payout_prometheus_rule_renders_enabled_rail_alerts tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_payout_prometheus_rule_renders_optional_low_balance_alerts tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_enabled_eth_payout_renders_production_runtime_contract tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_values_expose_usdt_payout_worker_settings -v`
  in `/Users/test/PycharmProjects/shkeeper-helm-charts`
  -> `Ran 4 tests in 0.217s OK`.
  Full verification after this gate:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  in `/Users/test/PycharmProjects/shkeeper.io`
  -> `Ran 209 tests in 2.980s OK`.
  Same command in `/Users/test/PycharmProjects/tron-shkeeper`
  -> `Ran 190 tests in 1.763s OK`.
  Same command in `/Users/test/PycharmProjects/ton-shkeeper`
  -> `Ran 80 tests in 2.110s OK`.
  Same command in `/Users/test/PycharmProjects/ethereum-shkeeper`
  -> `Ran 66 tests in 2.802s OK`.
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  in `/Users/test/PycharmProjects/shkeeper-helm-charts`
  -> `Ran 32 tests in 1.809s OK`.
- [x] Add operator views showing `WalletWithdrawal.publicNumber`, SHKeeper
  execution ID, optional sidecar correlation ID, rail, amount, destination,
  current state, txids/message hashes, last error, and next safe action.
  Evidence on 2026-06-04: Grither admin endpoint
  `/api/admin/wallet/shkeeper-payouts/{payoutExecutionId}` returns explicit
  `publicNumber`, `walletWithdrawalId`, `externalId`, SHKeeper `executionId`,
  optional `sidecarExecutionId`, rail, fee split, destination, provider/manual
  state, public withdrawal status, txids/message hashes, error/failure fields,
  and `nextSafeAction`.
- [x] Add runbooks for reconciliation, worker unavailable, low balance, provider
  failures, ambiguous broadcast, paused rail, and manual payout evidence.
  Evidence on 2026-06-04: `docs/runbooks/usdt-payout-operations.md` covers the
  operator entry points, first triage, `RECONCILIATION_REQUIRED`, worker
  unavailable, low balance/gas, provider/callback failure, ambiguous broadcast,
  paused rail, manual payout evidence, and audit expectations.
- [x] Add structured audit trail for every state transition and operator action.
  Evidence on 2026-06-04: SHKeeper `PayoutCallbackEvent` stores durable raw
  state-transition payloads keyed by `event_version` and
  `state_transition_id`; Grither migration
  `V090_create_shkeeper_payout_manual_resolution_audit.sql` adds append-only
  `shkeeper_payout_manual_resolution_audit` rows for manual-resolution operator
  actions. `ShKeeperPayoutManualResolutionAuditRecorder` records actor, previous
  and new provider/manual states, reason, evidence, tx/message hash, request
  hash, sidecar payload hash, and transition metadata.

  Verification on 2026-06-04:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualResolutionServiceTest,AdminShKeeperPayoutResolutionControllerTest,ArchitectureRulesTest#shkeeper_payout_manual_resolution_audit_must_live_in_dedicated_recorder`
  -> `Tests run: 10, Failures: 0, Errors: 0, Skipped: 0`.

Acceptance:

- [x] Operators can identify whether manual payout is allowed, forbidden, or
  still blocked by missing evidence.
  Evidence on 2026-06-04: Grither admin view exposes `nextSafeAction`, manual
  resolution state, reconciliation flag, and structured evidence; manual payout
  remains blocked until rail-specific negative evidence is recorded.
- [x] `RECONCILIATION_REQUIRED` is visible and alerted.
  Evidence on 2026-06-04: SHKeeper `/metrics` exports reconciliation count and
  non-terminal age; Grither `ShKeeperPayoutReconciliationAlertService` and
  `ShKeeperPayoutManualReviewMonitor` emit
  `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED` alerts for reconciliation and stale
  manual-review rows.

## Phase 7: Production Rollout

- [x] Keep all rails disabled by default. Chart defaults and the production
  overlay render no payout workers/secrets by default, and SHKeeper
  `payout-rail-sync` now receives a top-level consumer so an empty or reduced
  desired catalog disables stale enabled rails for that consumer.
- [ ] Enable one rail at a time.
- [ ] Run testnet or low-value mainnet smoke payout.
- [ ] Simulate submit timeout, worker unavailable, sidecar restart before
  broadcast, sidecar restart during broadcast ambiguity, duplicate callback,
  missed callback followed by polling, and confirmation polling.
- [ ] Observe metrics for a defined stability window.

Acceptance:

- [ ] A rail is enabled for real client withdrawals only after every gate for that
  rail passes.

## Verification Commands

SHKeeper:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
```

SHKeeper local verification on 2026-06-04:

- `.venv/bin/python -m pytest tests` is not a runnable entry point in this
  checkout because pytest is not installed in `.venv`.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 201 tests after rail validation, balance metrics, and rail alert
  fixes.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_rail_sync tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_callback_outbox tests.test_payout_sidecar_client tests.test_payout_service_external_id tests.test_healthz -v`
  passed: 95 tests.
- Current SHKeeper model/import review after declaring the dedicated
  `PayoutExecutionState` enum passed:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_metrics tests.test_payout_rail_sync tests.test_payout_sidecar_client tests.test_payout_service_external_id tests.test_payout_status_response tests.test_payout_tron_template tests.test_tron_token_payout_preflight tests.test_healthz -v`
  -> 143 tests OK; and
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> 209 tests OK. `git diff --check` was clean.
- Current SHKeeper product-policy boundary review passed:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_rail_sync tests.test_payout_execution_models tests.test_payout_execution_api -v`
  -> 60 tests OK; and
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> 215 tests OK. `jq empty docs/openapi-3.json`, `git diff --check`, and
  runtime/OpenAPI/migration grep gates for Grither-specific names and customer
  withdrawal amount-limit fields were clean. SHKeeper rejects unknown rail config
  fields and unsupported `/api/v1/payout-executions` request fields through a
  strict technical contract; upstream product code owns those withdrawal
  policies.

TRON sidecar:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
```

TRON local verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 190 tests.
- `git diff --check` passed.

TON sidecar:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
```

TON local verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 80 tests.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract -v`
  passed: 16 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/ton-shkeeper-pycache .venv/bin/python -m py_compile app/api/__init__.py app/api/payout.py tests/test_payout_execution_contract.py`
  passed.
- `git diff --check` passed.
- Same-wallet/multipayout mapping verification:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_fee_deposit_seqno_guard.py tests/test_payout_execution_schema.py tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_status_confirmation.py tests/test_payout_callback_outbox.py -q`
  passed: 59 tests; full `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q`
  passed: 72 tests; `PYTHONPYCACHEPREFIX=/private/tmp/ton-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  and `git diff --check` passed.

ETH sidecar:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache PYTHONDONTWRITEBYTECODE=0 .venv/bin/python -m compileall -q app tests
git diff --check
```

ETH local verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 65 tests.
- `git diff --check` passed.

Helm chart:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
helm lint charts/shkeeper
git diff --check
```

Helm local verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` passed:
  32 tests.
- `helm lint charts/shkeeper` passed: 1 chart linted, 0 failed. Only the
  optional chart icon info message remained.
- `git diff --check` passed.
- `helm template shkeeper charts/shkeeper` and
  `helm template shkeeper charts/shkeeper -f charts/shkeeper/values-payouts-production-example.yaml`
  rendered successfully.
- `helm template shkeeper charts/shkeeper --output-dir /private/tmp/shkeeper-helm-render-default-20260604`
  rendered the default chart.
- `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-tron-payout.yaml`,
  `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-ton-payout.yaml`,
  and `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-eth-payout.yaml`
  rendered successfully; rendered manifests include kill-switched catalog rows,
  exactly one dedicated sequential payout worker for the selected rail, sidecar
  migration jobs, NetworkPolicy, and no `PrometheusRule` manifests by default.
- `helm template` rendered positive TRON, TON, and ETH payout rail manifests with
  required Secret refs, backup evidence values, owned image repositories, rail
  sync, migration jobs, workers, NetworkPolicy, resources, probes, and
  `execution_enabled=true`.
- Rail-only positive render proved sidecar Services are rendered even when legacy
  asset flags are disabled.
- Negative render proved enabled TRON payout rail fails if
  `tron_shkeeper.extraEnv.TRON_USDT_PAYOUT_QUEUE` is used as a bypass instead of
  `payouts.rails.tronUsdt.queue`.
- Negative render proved enabled payout topology rejects literal
  hot-wallet/secret-looking `extraEnv` values for SHKeeper and TRON/TON/ETH
  sidecars; payout credentials and wallet material must be supplied through
  Secret/external-secret refs.
- Negative render proved enabled rail images cannot pass validation by embedding
  an owned image repository as a substring inside an untrusted image name.
- ETH production overlay render proved `eth-usdt-payouts`, ETH sidecar
  migration job, SHKeeper payout reconciler, and ETH payout NetworkPolicy render
  from environment values with owned image tag
  `ghcr.io/nilof470/ethereum-shkeeper:977f920` and without committed hot-wallet
  material or secret values.
- Render check proved a sidecar with its legacy wallet enabled but rail disabled
  does not receive payout auth/auto-enqueue env vars.

Current full local verification after the release-image gate clarification on
2026-06-04:

- `/Users/test/PycharmProjects/shkeeper.io`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 219 tests; `PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m compileall -q shkeeper tests`
  and `git diff --check` passed.
- `/Users/test/PycharmProjects/tron-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 /tmp/tron-shkeeper-py312-venv/bin/python -m unittest discover -s tests -v`
  passed 191 tests; `PYTHONPYCACHEPREFIX=/private/tmp/tron-shkeeper-pycache /tmp/tron-shkeeper-py312-venv/bin/python -m compileall -q app tests`
  and `git diff --check` passed.
- `/Users/test/PycharmProjects/ton-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 81 tests; `PYTHONPYCACHEPREFIX=/private/tmp/ton-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  and `git diff --check` passed.
- `/Users/test/PycharmProjects/ethereum-shkeeper`:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed 66 tests; `PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  and `git diff --check` passed.
- `/Users/test/PycharmProjects/shkeeper-helm-charts`:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` passed
  32 tests; `helm lint charts/shkeeper`, `git diff --check`, and TRON/TON/ETH
  production overlay `helm template` renders passed.
- Payout execution/routing allowlist tests are the boundary for customer policy:
  SHKeeper, sidecars, and Helm accept only execution/routing fields and reject
  unknown fields instead of defining SHKeeper-side customer limit fields.
  Product-specific runtime grep was clean in SHKeeper runtime, sidecar `app/`,
  and Helm templates.
- `python3 scripts/verify_payout_release_gate.py` passed end-to-end after the
  rail contract was narrowed to `execution_enabled` and SHKeeper-side product
  policy fields were removed from the runtime/API contract.

Current release-readiness audit on 2026-06-04:

- `python3 scripts/verify_payout_release_gate.py` passed end-to-end again after
  the Helm environment-overlay tests were narrowed to owned image repository
  shape instead of hardcoded historical image tags. Exact overlay image tag to
  clean commit matching belongs to `--require-clean`, not ordinary chart unit
  tests.
- `/Users/test/PycharmProjects/shkeeper-helm-charts`:
  `python3 -m unittest tests/test_shkeeper_fork_chart.py -v` passed 34 tests;
  `helm lint charts/shkeeper` passed; `git diff --check` passed.
- `python3 scripts/verify_payout_release_gate.py --require-clean` currently
  fails because the participating worktrees still contain uncommitted payout
  changes. This is the remaining release blocker before image build/push and
  deploy commands can be treated as production-ready.
- Production overlay image tags are intentionally not final while the repos are
  dirty. After committing SHKeeper, TRON, TON, ETH, and Helm changes, update the
  Helm `charts/shkeeper/environments/values-prod-*-payout.yaml` `image:` fields
  to those clean commit short SHA tags, commit that overlay update, and rerun
  `python3 scripts/verify_payout_release_gate.py --require-clean`.
- `docs/DEPLOYMENT.md` now includes explicit `git add`, `git commit`, and
  `git push` commands after the overlay image-tag rewrite step, so the documented
  sequence no longer asks the operator to run the clean gate against a dirty Helm
  checkout.
- Runtime Docker build contexts in SHKeeper, TRON, TON, and ETH now explicitly
  ignore `.env*`, PEM/key files, logs, and SQLite files in `.dockerignore`.
  Current local scan found no such files in the four build contexts, and
  `git diff --check` passed in all four runtime repos after the ignore hardening.
- `scripts/verify_payout_release_gate.py --require-clean` now aggregates dirty
  state across all five participating checkouts before failing, so release
  operators see the complete commit/cleanup list in one run instead of fixing
  one repo at a time. The expected dirty-repo failure listed SHKeeper, ETH, TON,
  TRON, and Helm; the ordinary full gate passed again after this diagnostic
  change.

Add focused tests as implementation progresses; these commands are the minimum
existing suite entry points, not the full final gate.

## Review Gates Applied

- [x] Review this plan before implementation starts.
- [x] After plan review, validate every finding against code/spec and patch this
  plan.
- [x] Before implementation, split work into PR-sized chunks by repo:
  SHKeeper API/state, TRON sidecar, TON sidecar, ETH sidecar, Helm, Grither Pay.
- [x] Each implementation block or PR must include tests for the acceptance
  criteria it claims.
