# USDT Withdrawals TON Sidecar Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make `ton-shkeeper` a durable, idempotent TON-USDT payout executor for SHKeeper service-consumer client withdrawals.

**Architecture:** SHKeeper calls new TON preflight/submit/status endpoints with
scoped HMAC auth. TON stores a durable sidecar execution row, seqno, signed BOC,
message hash, valid-until, Jetton master/wallet evidence, and broadcast markers
before unsafe side effects. Phase 1 keeps the existing `/TON-USDT/payout` transfer
source wallet (`fee_deposit`) and wraps the current payout behavior with durable
execution rather than migrating to a dedicated payout wallet. Ambiguous `sendBoc`
or `sendBocReturnHash` outcomes become reconciliation-required and are never
blindly retried.

**Tech Stack:** Python, Flask Blueprint, Celery, Redis, Flask-SQLAlchemy, TON SDK/client libraries, pytest/unittest.

**Current Status, 2026-06-04:** TON sidecar implementation is present in
`/Users/test/PycharmProjects/ton-shkeeper` and has passed verification in the
repository's Python 3.12 `.venv` after installing `pytest`.

Validated evidence after the 2026-06-04 TON reliability review-fix pass:

- Original focused payout suite:
  `tests/test_payout_execution_contract.py`,
  `tests/test_payout_execution_boundaries.py`,
  `tests/test_payout_status_confirmation.py`, and
  `tests/test_payout_callback_outbox.py`: `38 passed`.
- Review-fix focused payout suite:
  `tests/test_payout_execution_boundaries.py`,
  `tests/test_payout_status_confirmation.py`, and
  `tests/test_payout_callback_outbox.py`: `35 passed`.
- Full TON suite: `67 passed` after adding SHKeeper v1
  `/payout-executions/<execution_id>` route compatibility.
- `compileall -q app tests`: clean when pycache is redirected to `/private/tmp`.
- `git diff --check`: clean.
- Review fix already applied during verification: non-finite payout amounts
  (`NaN`, `Infinity`, `-Infinity`) now fail as controlled `INVALID_AMOUNT`
  responses instead of raw Decimal exceptions.
- Local review fixes applied and verified: stale `SIGNED`/`BROADCASTING`
  executions recover to manual reconciliation after lease expiry; stale workers
  cannot downgrade `BROADCASTED`/terminal executions; `execute()` reloads state
  after the seqno lock before side effects; duplicate-worker CAS before lock
  returns the current execution state; status polling tolerates CAS during
  recovery/refresh; legacy `/TON-USDT/payout` and `/TON-USDT/multipayout` route
  to `ton_usdt_payouts` and fail closed when the dedicated worker is missing;
  TON status waits for configured masterchain confirmations and fails
  terminally on indexed Jetton transfer mismatch; callback outbox write failure
  after payout no longer fails the payout task.
- Additional review fixes applied and verified: the real `Coin` class now
  implements the client-payout signed BOC, immutable signed BOC evidence, and
  broadcast primitive used by `execute_payout_execution`; broadcast result hash
  must match the persisted signed message hash before `BROADCASTED`; provider
  errors during TON-USDT preflight become controlled 503 fail-closed responses;
  `valid_until` evidence is aligned with the tonsdk wallet v4r2 60-second
  signing-message expiry behavior instead of a misleading 600-second value.
- Helm/SHKeeper integration review fix applied and verified: v1
  `/TON-USDT/payout-executions/<execution_id>` preflight/submit/status endpoints
  are now supported, path/body execution-id mismatches fail closed, and these
  HMAC-protected payout execution endpoints do not require legacy Basic Auth.
  Contract tests passed 16 tests.
- Same-wallet `fee_deposit` seqno review applied and verified: a shared
  reentrant `fee_deposit_seqno_lock` now backs client payout execution, native
  fee-deposit multipayout, Jetton fee-deposit multipayout, and any defensive
  drain path that would sign from the `fee_deposit` wallet. Normal onetime-account
  drain paths were reviewed as non-conflicting because they sign from the onetime
  wallet, not the payout source wallet. Legacy single payout still flows through
  legacy multipayout, so it uses the same guarded `Coin` spend path.
- TON multipayout result mapping fixed and verified: native TON and Jetton
  multipayout batches now keep each `(payout, transaction)` pair together, so
  callback results do not reuse the last destination/amount for every transaction.
- Same-wallet/mapping review tests passed:
  `tests/test_fee_deposit_seqno_guard.py`: 3 tests; schema regression:
  2 tests; focused payout group with callback/boundary/contract/status tests:
  59 tests; full TON suite:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q` passed:
  72 tests. `compileall -q app tests` with pycache redirected to `/private/tmp`
  passed and `git diff --check` stayed clean.

---

## Files

Repository: `/Users/test/PycharmProjects/ton-shkeeper`

Modify:

- `app/api/__init__.py`
- `app/api/payout.py`
- `app/tasks.py`
- `app/coin.py`
- `app/config.py`
- `app/models.py`
- migration/schema initialization files identified in the fork

Create:

- `app/payout_execution.py`
- `app/payout_auth.py`
- `app/payout_status.py`
- `app/payout_migrations.py` or the fork's equivalent migration hook
- `tests/test_payout_execution_contract.py`
- `tests/test_payout_execution_boundaries.py`
- `tests/test_payout_status_confirmation.py`
- `tests/test_payout_callback_outbox.py`
- `tests/test_payout_migrations.py`

Keep:

- legacy payout/multipayout endpoints for legacy/manual callers.
- the Phase 1 transfer primitive and source wallet as-is: client-withdrawal
  execution wraps the same fee-deposit account/mnemonic/seqno behavior that the
  current `/payout` path uses.
- the `fee_deposit` name and current manual/admin payout semantics.

## Task 1: Client Payout Contract

- [x] Write failing tests for preflight/submit/status, HMAC auth, replay/tamper
  rejection, method/path/query signature mismatch, wrong consumer rejection,
  duplicate same payload, duplicate changed payload, and status response fields.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests/test_payout_execution_contract.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Implement scoped auth in `app/payout_auth.py` with headers:
  `X-Payout-Consumer`, `X-Payout-Key-Id`, `X-Payout-Timestamp`,
  `X-Payout-Nonce`, `X-Payout-Signature`.
- [x] Use the shared sidecar signature base:
  `<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>`.
- [x] Persist replay nonces per key id until timestamp tolerance expires and
  reject body `consumer` values not authorized for the authenticated key/rail.
- [x] Add `/TON-USDT/payout/preflight`, `/TON-USDT/payout/submit`, and
  `/TON-USDT/payout/status/<execution_id>` in `app/api/payout.py`.
- [x] Add SHKeeper v1-compatible
  `/TON-USDT/payout-executions/<execution_id>` preflight, submit, and status
  routes with scoped HMAC auth and no legacy Basic Auth dependency.
- [x] Verify `sidecar_payload_hash` after TON-side canonicalization before creating
  or reusing an execution.
- [x] Reject body `asset`/`network`/symbol that does not match the `/TON-USDT`
  endpoint rail before execution creation.
- [x] Run the contract tests and require all to pass.
- [ ] Commit:

```bash
git add app/api/__init__.py app/api/payout.py app/payout_auth.py tests/test_payout_execution_contract.py
git commit -m "feat: add ton payout execution contract"
```

## Task 2: Durable Seqno, BOC, And Broadcast Boundaries

- [x] Write failing tests for seqno serialization, stale safe pre-signing retry,
  stale `SIGNING` with seqno reservation, stale `SIGNING` with signed BOC, stale
  `SIGNING` with broadcast marker, ambiguous broadcast timeout, mismatched
  `sidecar_payload_hash`, and wrong-rail body payload.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests/test_payout_execution_boundaries.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Implement `app/payout_execution.py` with fields:
  `execution_id`, `consumer`, `external_id`, `request_hash`,
  `sidecar_payload_hash`, `state`, `state_version`, `state_transition_id`,
  `state_updated_at`, `lease_owner`, `lease_expires_at`, `attempt_id`,
  `source_wallet`, `jetton_master`, `jetton_wallet`, `chain_id_or_network_id`,
  `masterchain_seqno`, `source_seqno`, `valid_until`,
  `canonical_payload_json`, immutable signed artifact evidence/ref,
  `signed_boc_ref`, `signed_boc_hash`, `signed_boc_stored_at`, `message_hash`,
  `broadcast_provider`, `broadcast_attempted_at`, `chain_check_metadata`,
  `failure_class`, `error_code`, `error_message`, and
  `reconciliation_required`.
- [x] Add DB constraints/schema initialization: primary `execution_id`, unique
  `(consumer, external_id)`, indexed non-terminal states, required evidence
  fields, and service-level request-hash immutability through conflict checks.
- [x] Use compare-and-set state transitions for every state change.
- [x] Add source-wallet seqno allocator or worker lock for client payouts.
- [x] Persist seqno reservation before signing.
- [x] Persist immutable signed artifact evidence/ref plus hash, timestamp,
  source wallet, Jetton metadata, seqno, valid-until, and message hash before
  network submit. Do not retain a spendable signed BOC in the DB in Phase 1.
- [x] Mark stale `SIGNING` retryable only when DB state proves no seqno
  reservation, no signed artifact, and no broadcast marker.
- [x] Convert ambiguous `sendBoc`/`sendBocReturnHash` timeout to
  `RECONCILIATION_REQUIRED`; do not re-sign, re-enqueue, or blind retry after
  broadcast ambiguity. Status must expose persisted BOC/message evidence.
- [x] Run boundary tests and require all to pass.
- [ ] Commit:

```bash
git add app/models.py app/payout_execution.py app/tasks.py app/coin.py tests/test_payout_execution_boundaries.py
git commit -m "feat: persist ton payout execution boundaries"
```

## Task 3: TON Preflight And Jetton Confirmation Evidence

- [x] Write failing tests for invalid address, insufficient Jetton balance,
  insufficient TON fee balance, provider unreadiness, worker unavailable,
  valid-until evidence, generic message confirmation without Jetton transfer, and
  positive confirmation with matching Jetton master, source, destination, amount,
  and network.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests/test_payout_status_confirmation.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Add config keys for `TON_USDT_PAYOUT_QUEUE` defaulting to
  `ton_usdt_payouts`, worker readiness timeout, and payout message valid-until
  cap.
- [x] Add dedicated `ton_usdt_payouts` queue routing for client payouts.
- [x] Preflight fails closed when the dedicated `ton-usdt-payouts` worker/queue
  is unavailable before signing or broadcast side effects.
- [x] Fix multipayout result mapping in `app/coin.py` before enabling client
  withdrawals; every result row must map to its own payout destination/amount.
- [x] Confirmation requires Jetton transfer evidence and masterchain range evidence
  before `CONFIRMED`.
- [x] Return the mandatory sidecar status schema on every status response:
  `execution_id`, `consumer`, `external_id`, `request_hash`,
  `sidecar_payload_hash`, `state`, `state_version`, `state_transition_id`,
  `state_updated_at`, source wallet, Jetton master/wallet, chain/network id,
  signed-artifact metadata/ref/hash, broadcast provider/timestamp, message hash,
  chain check metadata, failure class, error fields, and reconciliation flag.
- [x] Run status confirmation tests and require all to pass.
- [ ] Commit:

```bash
git add app/config.py app/api/payout.py app/tasks.py app/coin.py app/payout_status.py tests/test_payout_status_confirmation.py
git commit -m "feat: verify ton payout transfer evidence"
```

## Task 4: Remove Infinite Callback Loop

- [x] Write failing tests proving SHKeeper unavailable after broadcast does not keep
  the payout worker in an infinite notification loop.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests/test_payout_callback_outbox.py -q
```

Initial TDD expectation: failed before implementation. Current gate: passes.

- [x] Replace the infinite loop in `app/tasks.py` with durable outbox or bounded
  retry outside the payout-critical worker.
- [x] Keep SHKeeper status polling authoritative.
- [x] Add migration/schema tests proving payout execution constraints are applied
  before submit readiness can pass.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests/test_payout_callback_outbox.py tests/test_payout_execution_boundaries.py -q
```

Expected: selected tests pass.
- [ ] Commit:

```bash
git add app/tasks.py app/payout_execution.py tests/test_payout_callback_outbox.py
git commit -m "fix: decouple ton payout callbacks from worker completion"
```

## Task 5: Sidecar Payout Metrics

- [x] Add TON sidecar payout gauges to `/metrics`:
  `ton_payout_execution_count`,
  `ton_payout_non_terminal_oldest_age_seconds`,
  `ton_payout_reconciliation_required_count`,
  `ton_payout_callback_outbox_backlog_count`,
  `ton_payout_callback_outbox_oldest_age_seconds`, and
  `ton_payout_worker_ready`.
- [x] Add `ton_payout_broker_queue_depth` for Redis `LLEN` of the dedicated
  payout queue. Redis read failure is exposed as `-1` and remains fail-open.
- [x] Add `ton_payout_broker_queue_oldest_age_seconds` from sidecar-owned
  `payout_enqueued_at` Celery task headers. Empty queue is `0`; Redis or
  unparseable queued task age is `-1`; if Redis depth is readable but task age is
  malformed, depth is preserved and age is `-1`.
- [x] Add source-wallet balance gauges:
  `ton_payout_hot_wallet_balance{asset="USDT",source_wallet="fee_deposit"}` and
  `ton_payout_fee_wallet_balance{asset="TON",source_wallet="fee_deposit"}`.
  Balance collection failure or missing fee-deposit account row is exposed as
  `-1` and remains fail-open.
- [x] Add sidecar failure dashboard metrics:
  `ton_payout_failure_count{state,failure_class,error_code}` from durable
  execution failure metadata and
  `ton_payout_request_failed_total{operation,code}` from auth/HMAC and
  payout-contract rejects. Error-code labels are bounded and fall back to
  `OTHER` for non-machine-readable values.
- [x] Keep payout metric collection fail-open so DB/worker-readiness collection
  cannot break the full `/metrics` endpoint. DB-backed payout
  execution/callback gauges preserve the last successful snapshot if DB
  collection fails; worker readiness and Redis queue depth/age still refresh.
- [x] Make Prometheus collector unregister idempotent for repeated imports/tests.
- [x] Add tests for DB-backed payout metrics.

Verification on 2026-06-04:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_metrics -v`
  -> `Ran 7 tests in 0.539s OK`.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v`
  -> `Ran 80 tests in 2.110s OK`.
- `git diff --check` -> clean.

## Verification Gate

- [x] Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
pytest tests -q
```

- [x] Request independent review focused on seqno safety, signed BOC persistence,
  broadcast ambiguity, Jetton transfer confirmation, and callback loop removal.
- [x] Do not enable TON until Helm renders `ton-usdt-payouts` consuming
  `ton_usdt_payouts` with storage/backup posture.
- [x] Enumerate every same-wallet spend path in `app/coin.py`, payout,
  multipayout, sweep/drain, and fee-wallet code. Keep the current `fee_deposit`
  source wallet in Phase 1 and prove every same-wallet path uses the same seqno
  guard and audit trail, or is unable to conflict with client withdrawals.
