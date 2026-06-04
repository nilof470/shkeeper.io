# USDT Withdrawals ETH Sidecar Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fork and harden an owned Ethereum sidecar for ETH-USDT client payouts.

**Architecture:** ETH payouts must use an owned `nilof470/ethereum-shkeeper`
fork. SHKeeper calls scoped HMAC preflight/submit/status endpoints. The sidecar
keeps the fork's existing `/ETH-USDT/payout` transfer source wallet as the Phase
1 payout source, then wraps that behavior with durable execution state, nonce,
signed raw transaction, tx hash, chain ID, ERC20 USDT contract, source wallet,
broadcast marker, and receipt/log evidence before SHKeeper can expose a terminal
state to any payout API consumer. Ambiguous RPC broadcast outcomes become
reconciliation-required and are never blindly retried.

**Tech Stack:** Python sidecar stack inherited from upstream Ethereum SHKeeper after fork inspection; Flask/Celery/Redis expected from the chart baseline; web3/JSON-RPC expected for ETH execution.

---

## Precondition Gate

The repository was not present at `/Users/test/PycharmProjects/ethereum-shkeeper`
during planning. Do not implement ETH payout code from assumptions. First create
or clone the owned fork and verify its actual framework/files.

### Fork Validation Notes

Validated on 2026-06-03:

- Owned fork created: `https://github.com/nilof470/ethereum-shkeeper`
- Local checkout: `/Users/test/PycharmProjects/ethereum-shkeeper`
- Remotes:
  - `origin`: `https://github.com/nilof470/ethereum-shkeeper.git`
  - `upstream`: `https://github.com/vsys-host/ethereum-shkeeper.git`
- Default upstream branch: `main`
- Framework: Flask/Celery/Redis/SQLAlchemy sidecar.
- There is no upstream `tests/` directory in the fork at validation time; payout
  execution tests must be created in this fork.

Validated payout entrypoints and source-wallet paths:

- Legacy payout route:
  `/Users/test/PycharmProjects/ethereum-shkeeper/app/api/payout.py`
  - `POST /<symbol>/payout/<to>/<amount>`
  - `POST /<symbol>/multipayout`
  - both enqueue `app.tasks.make_multipayout`
- Legacy payout task:
  `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`
  - `make_multipayout(symbol, payout_list, fee)`
  - calls `Coin.make_multipayout_eth(...)` for native ETH
  - calls `Token.make_token_multipayout(...)` for ERC20 rails such as `ETH-USDT`
- Native ETH payout source:
  `/Users/test/PycharmProjects/ethereum-shkeeper/app/token.py`
  - `Coin.make_multipayout_eth(...)`
  - source wallet is `Coin.get_fee_deposit_account()`
  - nonce is read with `provider.eth.get_transaction_count(fee_deposit)`
  - signed transaction is sent immediately with `send_raw_transaction(...)`
- ETH-USDT payout source:
  `/Users/test/PycharmProjects/ethereum-shkeeper/app/token.py`
  - `Token.make_token_multipayout(...)`
  - source wallet is `Token.get_fee_deposit_account()`
  - token balance is checked with `get_fee_deposit_token_balance()`
  - gas funding is checked with `get_fee_deposit_account_balance()`
  - nonce is read with `provider.eth.get_transaction_count(payout_account)`
  - signed raw ERC20 transaction is sent immediately with `sendRawTransaction(...)`

Phase 1 source-wallet decision:

- Keep the existing fork behavior: `ETH-USDT` client payouts spend from the
  existing `fee_deposit` account.
- Do not introduce a dedicated payout wallet migration in the first production
  release.
- Wrap the current transfer primitive with durable execution state, nonce guard,
  signed raw transaction evidence, broadcast marker, tx hash, and ERC20 receipt
  evidence before enabling `ETH-USDT` in SHKeeper/Helm.

Implementation status on 2026-06-04:

- Local fork path: `/Users/test/PycharmProjects/ethereum-shkeeper`.
- Implemented scoped HMAC auth, replay protection, canonical payload hash,
  `/ETH-USDT/payout/preflight`, `/ETH-USDT/payout/submit`, and
  `/ETH-USDT/payout/status/<execution_id>`.
- Implemented strict execution-contract field validation on the ETH sidecar. The
  sidecar now rejects unsupported request fields instead of silently ignoring
  them. It accepts only execution-contract fields and does not expose
  customer/business amount policy fields.
- Implemented sidecar execution state with CAS transitions, lease/attempt
  fields, nonce, tx hash, signed raw transaction hash/ref, source-wallet address
  evidence, broadcast marker, ERC20 receipt/log evidence, failure fields, and
  reconciliation flag.
- Implemented safe auto-retry only for stale `SIGNING` with no nonce/signed/broadcast
  evidence. Stale `SIGNED`/`BROADCASTING` and ambiguous broadcast failures become
  `RECONCILIATION_REQUIRED`.
- The fork deliberately does not retain spendable signed raw transaction bytes in
  Phase 1. It persists `signed_raw_tx_hash`, `signed_raw_tx_ref`,
  `source_wallet_address`, nonce, tx hash, token contract, and chain evidence, and
  forbids automatic rebroadcast from unsafe states.
- Legacy `/ETH-USDT/payout` and `/ETH-USDT/multipayout` remain available but route
  through the dedicated `eth_usdt_payouts` queue and fail closed when that worker
  is unavailable.
- Legacy ETH-USDT endpoints are intentionally not blocked at sidecar code level
  because manual/admin SHKeeper payouts must remain available. They are not part
  of the Grither Pay/client withdrawal API. Production safety depends on SHKeeper
  service-consumer legacy-spend guards, sidecar NetworkPolicy/basic auth, explicit
  operator audit, and the shared fee-deposit nonce lock.
- Legacy fee-deposit spend paths now share the fee-deposit nonce lock, including
  ETH/ETH-USDT multipayout and token-drain gas seeding from `fee_deposit`.
- Legacy sidecar payoutnotify delivery now uses a durable
  `PayoutCallbackOutbox` with bounded retry, claim TTL, due-row sweeper, and no
  in-task sleep loop. If task enqueue fails after the outbox row is written, the
  sweeper can recover delivery; if the outbox write itself fails after a
  completed legacy payout, the payout result is preserved and the error is
  logged for operator reconciliation.
- Independent review fixes validated on code and patched: fee-deposit nonce reads
  use pending nonce, exact source wallet address is persisted at nonce
  reservation before signing, `execution_id` is required, submit fails closed if
  auto enqueue is disabled without explicit manual-dispatch test/operator mode,
  worker rechecks execution ownership before broadcast, and RPC broadcast result
  tx hash must match the signed transaction hash before `BROADCASTED`.
- Manual payout safety review fix validated on code and patched: sidecar status
  now returns `manual_payout_allowed`, `manual_payout_block_reason`, and
  `manual_payout_evidence`. ETH manual payout stays blocked until finalized
  same-nonce transaction evidence exists with no matching ETH-USDT `Transfer`
  log; confirmed automatic transfers and unresolved `RECONCILIATION_REQUIRED`
  states explicitly block manual payout.
- Local verification:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 66 tests in 2.802s OK`.
- Compile verification:
  `PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  -> clean.
- Diff verification: `git diff --check` -> clean.
- Production enablement remains blocked until the owned ETH image is published
  and Helm renders `eth-usdt-payouts` with concurrency/prefetch, storage/migration
  readiness, and backup/restore posture.

## Task 1: Own The Fork

- [x] Create or confirm `nilof470/ethereum-shkeeper`.
- [x] Checkout the fork to `/Users/test/PycharmProjects/ethereum-shkeeper`.
- [x] Configure remotes:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
git remote -v
```

Expected: `origin` points to the owned fork and any upstream remote is read-only.

- [x] Inspect actual files:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
find app -maxdepth 3 -type f
find tests -maxdepth 3 -type f
```

- [x] Patch this plan with exact ETH file paths before coding.
- [x] Prove and document the fork's current `/ETH-USDT/payout/<to>/<amount>`
  source wallet. Keep that source as-is for Phase 1; do not introduce a dedicated
  payout wallet migration unless the fork already uses one.
- [ ] Commit the plan patch in `shkeeper.io`:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git add docs/superpowers/plans/2026-06-03-usdt-withdrawals-eth-sidecar.md
git commit -m "docs: record eth sidecar implementation plan paths"
```

## Task 2: Client Payout Contract

- [x] After Task 1 path validation, write failing tests for preflight/submit/status,
  HMAC auth, replay/tamper rejection, method/path/query signature mismatch,
  duplicate same payload, duplicate changed payload, worker unavailable, and
  status response fields.
- [x] Implement scoped auth with headers: `X-Payout-Consumer`,
  `X-Payout-Key-Id`, `X-Payout-Timestamp`, `X-Payout-Nonce`,
  `X-Payout-Signature`.
- [x] Use the shared sidecar signature base:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- [x] Persist replay nonces per key id until timestamp tolerance expires and
  reject body `consumer` values not authorized for the authenticated key/rail.
- [x] Implement scoped auth, `sidecar_payload_hash` verification, and endpoints:
  `/ETH-USDT/payout/preflight`, `/ETH-USDT/payout/submit`,
  `/ETH-USDT/payout/status/<execution_id>`.
- [x] Reject body `asset`/`network`/symbol that does not match the `/ETH-USDT`
  endpoint rail before execution creation.
- [x] Reject unknown sidecar execution fields so customer/business payout policy
  does not enter the ETH sidecar contract.
- [x] Add `ETH_USDT_PAYOUT_QUEUE` defaulting to `eth_usdt_payouts`, dedicated
  `eth-usdt-payouts` worker readiness, and a fail-closed preflight result when
  the worker/queue is unavailable before signing or broadcast side effects.
- [x] Status must return the mandatory sidecar schema, not only a Celery task id:
  `execution_id`, `consumer`, `external_id`, `request_hash`,
  `sidecar_payload_hash`, `state`, `state_version`, `state_transition_id`,
  `state_updated_at`, source wallet, token contract, chain id, signed-artifact
  metadata/ref/hash, broadcast provider/timestamp, tx hash, receipt/log evidence,
  failure class, error fields, and reconciliation flag.
- [x] Keep legacy Ethereum payout endpoints available for legacy callers.
- [ ] Commit with message:

```bash
git commit -m "feat: add eth payout execution contract"
```

## Task 3: Nonce, Signed Raw Tx, And Broadcast Boundaries

- [x] Write failing tests for nonce serialization, stale safe retry, stale `SIGNING`
  with nonce reservation, stale `SIGNING` with signed raw tx, stale `SIGNING` with
  broadcast marker, ambiguous RPC broadcast timeout, mismatched
  `sidecar_payload_hash`, and wrong-rail body payload.
- [x] Implement execution storage with `execution_id`, `consumer`, `external_id`,
  request hashes, canonical payload JSON, state, state version, transition ID,
  `state_updated_at`, lease fields, `attempt_id`, source wallet, token contract,
  chain ID, nonce, signed raw transaction bytes encrypted or immutable
  `signed_raw_tx_ref`, `signed_raw_tx_hash`, `signed_raw_tx_stored_at`, tx hash,
  broadcast provider/timestamp marker, receipt/log evidence, failure class,
  error fields, and reconciliation flag.
- [x] Add DB constraints in migration/schema initialization: unique
  `execution_id`, unique `(consumer, external_id)`, immutable request hash
  fields, indexed non-terminal states, and explicit nullable fields for
  rail-inapplicable evidence.
- [x] Use compare-and-set state transitions for every state change.
- [x] Use the fork's existing `/payout` source wallet as-is with one active payout
  worker, or add a wallet-level nonce allocator for every same-wallet spend path.
- [x] Persist nonce reservation before signing.
- [x] Persist signed transaction hash/ref metadata, timestamp, source wallet,
  token contract, chain ID, nonce, and tx hash before network submit. Phase 1
  intentionally does not retain spendable signed raw transaction bytes; unsafe
  states fail to reconciliation rather than rebroadcast.
- [x] Mark stale `SIGNING` retryable only when DB state proves no nonce
  reservation, no signed artifact, and no broadcast marker.
- [x] Convert ambiguous RPC broadcast timeout after signed artifact or broadcast
  marker to `RECONCILIATION_REQUIRED`; do not re-sign, re-enqueue, or blind retry.
  Status must expose persisted signed transaction metadata and tx evidence.
- [x] Validate RPC broadcast result hash against the signed transaction hash
  before marking `BROADCASTED`; mismatches become `RECONCILIATION_REQUIRED`.
- [ ] Commit with message:

```bash
git commit -m "feat: persist eth payout execution boundaries"
```

## Task 4: ERC20 Confirmation And Manual Negative Evidence

- [x] Write failing tests for invalid address, insufficient USDT, insufficient ETH
  gas, gas estimate failure, node sync failure, generic receipt confirmation
  without ERC20 `Transfer`, matching ERC20 `Transfer`, and unsafe status
  boundaries while nonce/tx evidence remains unresolved.
- [x] Confirmation requires ERC20 `Transfer` log matching token contract, source,
  destination, amount, chain ID, and finalized block range.
- [x] Negative evidence must not treat txpool disappearance as proof.
- [x] Manual payout is blocked unless nonce is consumed by a finalized same-nonce
  transaction and chain/log evidence proves no matching USDT `Transfer`.
- [x] Enumerate every same-wallet spend path in the fork's wallet, payout,
  multipayout, sweep/drain, and gas-management code. Keep the fork's current
  `/payout` source wallet in Phase 1 and prove every same-wallet path uses the
  same wallet-level nonce guard and audit trail, or is unable to conflict with
  client withdrawals.
- [ ] Commit with message:

```bash
git commit -m "feat: verify eth payout transfer evidence"
```

## Task 4.5: Durable Legacy Payout Callback Outbox

- [x] Write failing tests proving `post_payout_results` records bounded retry
  state without sleeping forever inside the Celery task.
- [x] Add `PayoutCallbackOutbox` storage with `PENDING`, `DISPATCHING`, `SENT`,
  and `FAILED` states, attempts, next-attempt time, claim token, claim TTL,
  HTTP/error evidence, and sent timestamp.
- [x] Replace direct `post_payout_results.delay(payload, symbol)` with
  `queue_payout_callback(payload, symbol)`, preserving legacy payout return
  behavior after a completed transfer.
- [x] Add `dispatch_due_payout_callbacks` sweeper so pending rows recover if the
  immediate Celery enqueue fails.
- [x] Keep callback delivery best-effort and bounded; SHKeeper status polling
  remains the authoritative client-withdrawal recovery path.

Verification on 2026-06-04:

- Focused outbox suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox -v`
  -> `Ran 8 tests in 0.605s OK`.
- Focused ETH payout group:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract tests.test_payout_execution_boundaries tests.test_payout_status_confirmation -v`
  -> `Ran 48 tests in 2.347s OK`.
- Manual payout safety focused suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_contract tests.test_payout_status_confirmation -v`
  -> `Ran 34 tests in 1.495s OK`.
- Full ETH sidecar suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 66 tests in 2.802s OK`.
- Additional contract-boundary follow-up: `tests.test_payout_execution_contract`
  now verifies that generic unsupported request fields are rejected with
  `PAYOUT_EXECUTION_BAD_REQUEST`.
- Compile:
  `PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache .venv/bin/python -m compileall -q app tests`
  -> clean.
- Diff hygiene: `git diff --check` -> clean.

## Task 4.6: Sidecar Payout Metrics

- [x] Add ETH sidecar payout gauges to `/metrics`:
  `ethereum_payout_execution_count`,
  `ethereum_payout_non_terminal_oldest_age_seconds`,
  `ethereum_payout_reconciliation_required_count`,
  `ethereum_payout_callback_outbox_backlog_count`,
  `ethereum_payout_callback_outbox_oldest_age_seconds`, and
  `ethereum_payout_worker_ready`.
- [x] Add `ethereum_payout_broker_queue_depth` for Redis `LLEN` of the dedicated
  payout queue. Redis read failure is exposed as `-1` and remains fail-open.
- [x] Add `ethereum_payout_broker_queue_oldest_age_seconds` from sidecar-owned
  `payout_enqueued_at` Celery task headers. Empty queue is `0`; Redis or
  unparseable queued task age is `-1`; if Redis depth is readable but task age is
  malformed, depth is preserved and age is `-1`.
- [x] Add source-wallet balance gauges:
  `ethereum_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}`
  and
  `ethereum_payout_fee_wallet_balance{asset="ETH",source_wallet="fee_deposit"}`.
  Balance collection failure or missing fee-deposit account row is exposed as
  `-1` and remains fail-open. Balance collection reads the already existing
  `fee_deposit` address directly and must not call the auto-create
  `get_fee_deposit_account()` path.
- [x] Add sidecar failure dashboard metrics:
  `ethereum_payout_failure_count{state,failure_class,error_code}` from durable
  execution failure metadata and
  `ethereum_payout_request_failed_total{operation,code}` for API-boundary
  auth/HMAC and payout-contract rejects. Error-code labels are bounded and fall
  back to `OTHER` for non-machine-readable values.
- [x] Keep payout metric collection fail-open so DB/worker-readiness collection
  cannot break the full `/metrics` endpoint. DB-backed payout
  execution/callback gauges preserve the last successful snapshot if DB
  collection fails; worker readiness and Redis queue depth/age still refresh.
- [x] Keep the `/metrics` endpoint fail-open when chain/fullnode metrics fail so
  payout health remains visible during node incidents.
- [x] Keep GitHub release lookup bounded/fail-open so external release metadata
  cannot hide payout health metrics.
- [x] Add tests for DB-backed payout metrics, release lookup failure, and
  endpoint availability when chain metrics fail.

Verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  -> `Ran 10 tests in 0.825s OK`.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 66 tests in 2.802s OK`.
- `git diff --check` -> clean.

## Verification Gate

- [x] Run the ETH sidecar test suite from the fork after Task 1 identifies the exact
  command.
- [x] Request independent review focused on nonce safety, signed transaction
  persistence, RPC ambiguity, ERC20 log confirmation, and manual negative evidence.
- [ ] Do not enable ETH in Helm until the owned image is published and chart values
  reference the owned image tag.
- [x] Do not enable ETH until Helm renders `eth-usdt-payouts` consuming
  `eth_usdt_payouts` with concurrency 1, prefetch 1, storage/migration readiness,
  and backup/restore posture. Verified on 2026-06-04:
  `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-eth-payout.yaml`
  rendered the ETH sidecar migration job, SHKeeper payout workers, ETH
  NetworkPolicy, kill-switched rail sync payload, and dedicated `eth-usdt-payouts`
  worker using `ghcr.io/nilof470/ethereum-shkeeper:977f920`.
