# USDT Withdrawals TRON Sidecar Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make `tron-shkeeper` a durable, idempotent TRON-USDT payout executor for SHKeeper service-consumer client withdrawals.

**Architecture:** SHKeeper calls new TRON preflight/submit/status endpoints with scoped HMAC auth. TRON stores sidecar execution state before enqueue/sign/broadcast, keeps the existing broker queue `tron_usdt_fee_payouts`, keeps the existing `/USDT/payout/<to>/<amount>` transfer source wallet (`fee_deposit`), and confirms only verified TRC20 USDT transfers.

**Tech Stack:** Python, Flask Blueprint, Celery, Redis, SQLModel/sqlite, tronpy, pytest/unittest.

**Current Status, 2026-06-04:** TRON sidecar implementation is present in
`/Users/test/PycharmProjects/tron-shkeeper` and has passed the TRON verification
gate in the repository's existing Python 3.9 `.venv`. Runtime-evaluated
Pydantic/SQLModel annotations were normalized away from Python 3.10-only union
syntax so the real sidecar environment can execute the suite.

Validated evidence after the 2026-06-04 TRON independent-review fix pass:

- Review-fix focused payout suite:
  `tests/test_payout_execution_boundaries.py`,
  `tests/test_payout_status_confirmation.py`,
  `tests/test_payout_task_resource_provisioning.py`, and
  `tests/test_payout_callback_outbox.py`: `59 passed`.
- Original focused payout suite from the earlier review pass:
  `tests/test_payout_execution_contract.py`,
  `tests/test_payout_execution_boundaries.py`,
  `tests/test_payout_status_confirmation.py`,
  `tests/test_payout_callback_outbox.py`,
  `tests/test_payout_task_resource_provisioning.py`, and
  `tests/test_celery_readiness.py`: `63 passed`.
- Full TRON suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 175 tests after the HMAC-only v1 route boundary fix.
- `git diff --check`: clean.
- Review fix already applied during verification: non-finite payout amounts
  (`NaN`, `Infinity`, `-Infinity`) now fail as controlled `INVALID_AMOUNT`
  responses instead of raw Decimal/TypeError exceptions.
- Independent review fixes applied and verified:
  stale `SIGNED`/`BROADCASTING` executions recover to manual reconciliation
  after lease expiry; stale workers cannot downgrade `BROADCASTED`/terminal
  executions; `execute()` reloads execution state after the resource lock before
  side effects; worker-time USDT balance is rechecked before resource/signing;
  legacy `/USDT/multipayout` uses the same dedicated queue/readiness/resource
  path as single payout; TRON status requires configured minimum confirmations;
  confirmed transactions without the expected USDT transfer become terminal
  chain failures; status polling tolerates CAS refresh races; callback outbox
  write failure after a successful transfer no longer fails the payout task;
  duplicate worker CAS before lock returns the current execution state instead
  of failing the Celery task.
- Subagent review follow-up fixes applied and verified: legacy `/USDT/multipayout`
  rejects underfunded batches before enqueue; partial successful legacy batch
  results are queued to callback outbox before a later transfer failure is
  re-raised; confirmation uses persisted `source_wallet_address` from signed
  evidence instead of the live `fee_deposit` key after rotation; resource-lock
  acquisition timeout before unsafe side effects resets the execution to
  retryable `RECEIVED` with `failure_class=TRANSIENT` instead of terminal
  pre-broadcast failure.
- Additional review fix applied and verified: TRON payout execution now verifies
  broadcast-result transaction IDs against the signed transaction ID before
  moving to `BROADCASTED`; mismatches fail closed to
  `RECONCILIATION_REQUIRED` with `BROADCAST_TXID_MISMATCH`. The real
  `Wallet.broadcast_signed_transfer()` preserves the `tronpy.TransactionRet`
  txid after `wait()` so production responses carry comparable broadcast
  evidence without changing legacy `/payout` return semantics.
- Helm/SHKeeper integration review fix applied and verified: v1
  `/USDT/payout-executions/<execution_id>` preflight/submit/status endpoints are
  protected by scoped payout HMAC and no longer require legacy Basic Auth. Legacy
  `/USDT/multipayout` still requires legacy Basic Auth. Contract tests passed 22
  tests; focused payout group passed 81 tests.
- Same-wallet `fee_deposit` spend-path review applied and verified:
  `app/fee_deposit_spend_guard.py` provides the shared reentrant Redis lock used
  by client payout execution, legacy `/USDT/payout`, legacy `/USDT/multipayout`,
  default `Wallet.transfer()` spends, AML TRX top-ups through `Wallet.transfer()`,
  TRC20 sweep fee funding, TRC20 account activation funding, staking-provider
  energy delegation, staking API freeze/unfreeze/withdraw/reward/delegate paths,
  `undelegate_energy()`, and SR voting when the configured energy delegator is
  the same `fee_deposit` wallet. Onetime-account sweeps and separate energy
  account paths were reviewed as non-conflicting because they do not sign from
  the payout source wallet. The guard is active when
  `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true`; production TRON payout
  enablement must keep that flag tied to the dedicated payout queue/readiness
  rollout.
- Same-wallet review tests passed:
  `tests/test_fee_deposit_spend_guard.py`: 6 tests; focused payout group
  including callback/boundary/contract/status/readiness/resource tests: 90 tests;
  full TRON suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  passed: 181 tests. `py_compile` for edited TRON files passed and
  `git diff --check` stayed clean.
- Residual accepted risk: if the local DB is unavailable after a legacy transfer
  has already completed, the callback outbox row cannot be durably created.
  The payout task must not fail after spend; the code logs the exception for
  manual recovery. Sidecar execution status remains the authoritative recovery
  path for client withdrawals.

---

## Files

Repository: `/Users/test/PycharmProjects/tron-shkeeper`

Modify:

- `app/api/__init__.py`
- `app/api/payout.py`
- `app/tasks.py`
- `app/wallet.py`
- `app/config.py`
- `app/celery_readiness.py`
- `app/schema.sql`

Create:

- `app/payout_execution.py`
- `app/payout_auth.py`
- `app/payout_status.py`
- `app/payout_migrations.py`
- `tests/test_payout_execution_contract.py`
- `tests/test_payout_execution_boundaries.py`
- `tests/test_payout_status_confirmation.py`
- `tests/test_payout_callback_outbox.py`

Keep:

- legacy `/USDT/payout/<to>/<amount>` and `/USDT/task/<id>` behavior for legacy
  callers.
- the Phase 1 transfer primitive and source wallet as-is: client-withdrawal
  execution wraps the same `Wallet.transfer(...)` behavior that currently sends
  from the `fee_deposit` key when no source override is provided.
- the `fee_deposit` name and current manual/admin payout semantics.
- queue value `tron_usdt_fee_payouts` unless sidecar, Helm, readiness, and tests
  are migrated together.

## Task 1: Scoped Auth And Contract Endpoints

- [x] Write failing tests in `tests/test_payout_execution_contract.py` for:
  `POST /USDT/payout/preflight`, `POST /USDT/payout/submit`,
  `GET /USDT/payout/status/<execution_id>`, missing HMAC auth, replayed nonce,
  tampered body, wrong consumer, duplicate same payload, duplicate changed payload,
  and submit response fields.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_execution_contract.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Implement `app/payout_auth.py` with the same header semantics as SHKeeper:
  `X-Payout-Consumer`, `X-Payout-Key-Id`, `X-Payout-Timestamp`,
  `X-Payout-Nonce`, `X-Payout-Signature`.
- [x] Use the shared sidecar signature base:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- [x] Persist replay nonces per key id until timestamp tolerance expires and
  reject body `consumer` values not authorized for the authenticated key/rail.
- [x] Implement routes in `app/api/payout.py`:
  `/USDT/payout/preflight`, `/USDT/payout/submit`,
  `/USDT/payout/status/<execution_id>`.
- [x] Add SHKeeper v1-compatible
  `/USDT/payout-executions/<execution_id>` preflight, submit, and status routes
  with scoped HMAC auth and no legacy Basic Auth dependency.
- [x] Verify `sidecar_payload_hash` after TRON-side canonicalization before
  creating or reusing an execution.
- [x] Reject body `asset`/`network`/symbol that does not match the `/USDT`
  endpoint rail before execution creation.
- [x] Run the same pytest command and require all tests to pass.
- [ ] Commit:

```bash
git add app/api/__init__.py app/api/payout.py app/payout_auth.py tests/test_payout_execution_contract.py
git commit -m "feat: add tron payout execution contract"
```

## Task 2: Durable Execution State And Unsafe Boundaries

- [x] Write failing tests in `tests/test_payout_execution_boundaries.py` for:
  safe retry before nonce/resource reservation, stale `SIGNING` with resource
  reservation, stale `SIGNING` with signed raw tx, stale `SIGNING` with broadcast
  marker, crash after broadcast timeout, duplicate submit after a worker crash,
  mismatched `sidecar_payload_hash`, and wrong-rail body payload.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_execution_boundaries.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Implement `app/payout_execution.py` with fields:
  `execution_id`, `consumer`, `external_id`, `request_hash`,
  `sidecar_payload_hash`, `state`, `state_version`, `state_transition_id`,
  `state_updated_at`,
  `lease_owner`, `lease_expires_at`, `attempt_id`, `source_wallet`,
  `token_contract`, `resource_reservation_id`, `reference_block`,
  `chain_id_or_network_id`, `expiration_at`, `canonical_payload_json`,
  `signed_raw_tx_encrypted` or immutable `signed_raw_tx_ref`,
  `signed_raw_tx_hash`, `signed_raw_tx_stored_at`, `txid`, `broadcast_provider`,
  `broadcast_attempted_at`, `chain_check_metadata`, `failure_class`,
  `error_code`, `error_message`, `reconciliation_required`.
- [x] Add DB constraints in `app/schema.sql` and migration/init code:
  unique `execution_id`, unique `(consumer, external_id)`, immutable request hash
  fields, indexed non-terminal states, and explicit nullable fields for
  rail-inapplicable status evidence.
- [x] Use compare-and-set state transitions for every state change.
- [x] Persist resource reservation marker before resource provider side effects.
- [x] Persist signed raw transaction bytes encrypted, or an immutable storage
  reference plus hash, timestamp, source wallet, token contract, reference block,
  expiration, and txid before network submit.
- [x] Mark stale `SIGNING` retryable only when DB state proves no resource
  reservation, no signed artifact, and no broadcast marker.
- [x] Ambiguous broadcast timeout after signed artifact or broadcast marker moves
  to `RECONCILIATION_REQUIRED`; never re-sign, re-enqueue, or blind retry from
  that state. Status must expose persisted signed artifact metadata and tx
  evidence.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_execution_boundaries.py -q
```

Expected: all boundary tests pass.
- [ ] Commit:

```bash
git add app/payout_execution.py app/tasks.py app/wallet.py tests/test_payout_execution_boundaries.py
git commit -m "feat: persist tron payout execution boundaries"
```

## Task 3: TRON Preflight And Confirmation Evidence

- [x] Write failing tests in `tests/test_payout_status_confirmation.py` for:
  unactivated destination rejection, invalid address rejection, insufficient USDT,
  insufficient TRX/resource, provider unreadiness, payout worker unavailable,
  configured transaction expiration cap, generic transaction confirmation without
  TRC20 Transfer event, and positive confirmation with matching contract, source,
  destination, amount, and network.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_status_confirmation.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Add config keys in `app/config.py`:
  `TRON_USDT_PAYOUT_TX_EXPIRATION_CAP_SEC`, default `600`; hard reject values above
  `1800` unless a named override is set for a reviewed deployment.
- [x] In `app/wallet.py`, keep legacy 12-hour expiration for legacy calls and add a
  client-payout signing path that uses the payout-specific cap.
- [x] In preflight, check destination, TRC20 USDT balance, TRX/resource availability,
  resource provider readiness, and `usdt_payout_worker_ready()`.
- [x] In status/confirmation, require TRC20 Transfer evidence for token contract,
  source wallet, destination, canonical amount, and network before `CONFIRMED`.
- [x] Return the mandatory sidecar status schema on every status response:
  `execution_id`, `consumer`, `external_id`, `request_hash`,
  `sidecar_payload_hash`, `state`, `state_version`, `state_transition_id`,
  `state_updated_at`, source wallet, token contract, chain/network id,
  signed-artifact metadata/ref/hash, broadcast provider/timestamp, txid, chain
  check metadata, failure class, error fields, and reconciliation flag.
- [x] Run the status confirmation tests and require all to pass.
- [ ] Commit:

```bash
git add app/config.py app/wallet.py app/api/payout.py app/payout_status.py tests/test_payout_status_confirmation.py
git commit -m "feat: verify tron payout transfer evidence"
```

## Task 4: Remove Payout-Critical Infinite Callback Loop

- [x] Write failing tests in `tests/test_payout_callback_outbox.py` proving SHKeeper
  unavailable after broadcast does not block the payout worker indefinitely and
  status remains recoverable by polling.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_callback_outbox.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Move SHKeeper notification retry outside the payout-critical worker path using
  a durable outbox or bounded retry. The payout worker must finish after durable
  local status is stored.
- [x] Keep SHKeeper status polling authoritative.
- [x] Add schema migration tests proving `app/schema.sql`/migration init creates
  payout execution constraints before submit readiness can pass.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_callback_outbox.py tests/test_payout_task_resource_provisioning.py tests/test_celery_readiness.py -q
```

Expected: all selected tests pass.

Verification on 2026-06-04:

- Focused TRON payout group:
  `PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS="-o cache_dir=/private/tmp/tron-shkeeper-pytest-cache" .venv/bin/python -m pytest tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_status_confirmation.py tests/test_payout_callback_outbox.py tests/test_fee_deposit_spend_guard.py -q`
  -> `72 passed, 1 warning in 2.03s`.
- [ ] Commit:

```bash
git add app/tasks.py app/payout_execution.py tests/test_payout_callback_outbox.py
git commit -m "fix: decouple tron payout callbacks from worker completion"
```

## Task 5: Sidecar Payout Metrics

- [x] Add TRON sidecar payout gauges to `/metrics`:
  `tron_payout_execution_count`,
  `tron_payout_non_terminal_oldest_age_seconds`,
  `tron_payout_reconciliation_required_count`,
  `tron_payout_callback_outbox_backlog_count`,
  `tron_payout_callback_outbox_oldest_age_seconds`, and
  `tron_payout_worker_ready`.
- [x] Add `tron_payout_broker_queue_depth` for Redis `LLEN` of the dedicated
  payout queue. Redis read failure is exposed as `-1` and remains fail-open.
- [x] Add `tron_payout_broker_queue_oldest_age_seconds` from sidecar-owned
  `payout_enqueued_at` Celery task headers. Empty queue is `0`; Redis or
  unparseable queued task age is `-1`; if Redis depth is readable but task age is
  malformed, depth is preserved and age is `-1`.
- [x] Add source-wallet balance gauges:
  `tron_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}` and
  `tron_payout_fee_wallet_balance{asset="TRX",source_wallet="fee_deposit"}`.
  Balance collection failure is exposed as `-1` and remains fail-open.
- [x] Add sidecar failure dashboard metrics:
  `tron_payout_failure_count{state,failure_class,error_code}` from durable
  execution failure metadata and
  `tron_payout_request_failed_total{operation,code}` from auth/HMAC and
  payout-contract rejects. Error-code labels are bounded and fall back to
  `OTHER` for non-machine-readable values.
- [x] Keep payout metric collection fail-open so DB/worker-readiness collection
  cannot break the full `/metrics` endpoint. DB-backed payout
  execution/callback gauges preserve the last successful snapshot if DB
  collection fails; worker readiness and Redis queue depth/age still refresh.
- [x] Keep the `/metrics` endpoint fail-open when chain/block-scanner metrics fail
  so payout health remains visible during fullnode incidents.
- [x] Make TRON GitHub release lookup bounded/fail-open so external release
  metadata cannot hide payout health metrics.
- [x] Add tests for DB-backed payout metrics, release lookup failure, and
  endpoint availability when chain metrics fail.

Verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  -> `Ran 9 tests in 0.650s OK`.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 190 tests in 1.763s OK`.
- `git diff --check` -> clean.

## Verification Gate

- [x] Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests -q
```

- [x] Request independent review focused on duplicate payout prevention, resource
  reservation boundaries, signed transaction persistence, broadcast ambiguity,
  TRC20 Transfer confirmation, and callback loop removal.
- [x] Do not enable the TRON rail until the Helm plan proves the worker consumes
  `tron_usdt_fee_payouts` with concurrency 1 and prefetch 1.
- [x] Enumerate every same-wallet spend path in `app/wallet.py`,
  `app/payout_resources.py`, sweep/drain/staking/resource code, and legacy payout
  endpoints. Keep the current `fee_deposit` source wallet in Phase 1 and prove
  every same-wallet path uses the same wallet-level resource/nonce lock and audit
  trail, or is unable to conflict with client withdrawals.
