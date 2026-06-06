# Payout UAT Edge Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build practical cross-repository UAT and edge-case coverage for SHKeeper/Grither Pay USDT payouts so retries, concurrency, callbacks, worker failures, and manual-resolution paths cannot create duplicate payouts or unsafe refunds.

**Architecture:** Keep runtime logic unchanged unless a test exposes a real defect. Add gap-focused tests in the existing SHKeeper, sidecar, Helm, and Grither Pay suites, then add one lightweight dev UAT runner that talks to live dev endpoints through the documented payout execution contract. Treat ambiguous payout states as reserved/manual, never as automatic retry/refund.

**Tech Stack:** Python unittest/Flask test client, Java 21/Spring Boot/JUnit 5/Testcontainers, Helm unittest/render checks, Kubernetes `kubectl`, signed `X-Payout-*` HMAC requests.

---

## Scope

This plan covers payout execution testing only:

- Grither Pay wallet withdrawal creation, balance reserve, submit outbox, callback webhook, status sync, manual resolution.
- SHKeeper payout execution API, idempotency, reconciler, callback outbox, metrics.
- TRON/TON/ETH sidecar payout contract and rail-specific signing/resource/nonce or seqno boundaries.
- Helm topology for staged and active payout workers.
- Live dev UAT runner for controlled low-value scenarios.

This plan intentionally excludes admin UI visual testing except manual-resolution API checks. The admin UI can be tested later after the backend payout safety gates pass.

## Existing Coverage Confirmed

Grither Pay already has relevant tests in `/Users/test/IdeaProjects/grither-pay`:

- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcherTest.java`
  covers dispatcher claim locking, timeout status lookup, stale worker commit protection, retry exhaustion, 409 conflict handling.
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
  covers duplicate callback idempotency, stale event versions, same-version conflicts, consumer mismatch, unknown external id replay, terminal state handling.
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`
  covers signed callback acceptance, replay nonce idempotency, nonce/event mismatch rejection, bad signature rejection.
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationServiceTest.java`
  covers atomic withdrawal/ledger/execution/outbox creation, no Altyn KYC requirement, rollback on outbox conflict, fee/precision/network validation.

SHKeeper and sidecars already have contract, outbox, metrics, reconciler, and rail boundary tests. The missing layer is a consistent cross-repo edge/UAT matrix and live dev runner.

## File Structure

Create or modify:

- `shkeeper.io/scripts/payout_dev_uat.py`: live dev UAT runner for signed payout execution calls, concurrent submit, status polling, and optional metrics snapshots.
- `shkeeper.io/tests/test_payout_execution_api.py`: SHKeeper API concurrency/idempotency additions.
- `shkeeper.io/tests/test_payout_execution_reconciler.py`: reconciler ambiguity/failure additions where not already covered.
- `shkeeper.io/tests/test_payout_callback_outbox.py`: callback ordering/retry additions where needed.
- `shkeeper.io/scripts/verify_payout_sidecar_e2e.py`: add concurrency and multi-execution e2e checks against local sidecar servers.
- `shkeeper.io/scripts/verify_payout_release_gate.py`: include the new dev/UAT dry-run checks in list mode and keep release gate focused on deterministic local checks.
- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationServiceTest.java`: concurrent same idempotency key and concurrent different withdrawal tests.
- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcherTest.java`: changed-payload conflict and retry/status edge tests if gaps remain.
- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`: explicit stale-after-confirmed and unsafe-state manual-review assertions if gaps remain.
- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`: duplicate delivery with refreshed timestamp/signature but same `event_id`.
- `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_execution_boundaries.py`: concurrent fee-wallet payout/resource guard test.
- `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_task_resource_provisioning.py`: ProfeeX/re:Fee failure edge tests.
- `/Users/test/PycharmProjects/ton-shkeeper/tests/test_fee_deposit_seqno_guard.py`: concurrent payout seqno guard test.
- `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_boundaries.py`: concurrent nonce reservation test.
- `/Users/test/PycharmProjects/shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`: staged vs active worker topology tests.
- `shkeeper.io/docs/runbooks/usdt-payout-operations.md`: document how to run the local gate and live dev UAT runner.

## Scenario Matrix

P0 scenarios:

- Same `external_id`, same payload, concurrent submit.
- Same `external_id`, changed payload, concurrent submit.
- Different `external_id`, concurrent payouts from same source wallet.
- Submit timeout followed by status lookup.
- Callback endpoint returns 500, then retry delivers the same event.
- Duplicate callback event id.
- Stale callback after newer state.
- Same event version with conflicting payload.
- Worker/sidecar unavailable during unsafe window.
- Rail paused or kill-switched.
- Low balance/gas/resource.
- Manual payout blocked until explicit `SAFE_FOR_MANUAL_PAYOUT`.

P1 scenarios:

- Burst 10-20 payouts on dev.
- Metrics and alert visibility after each failure.
- Helm staged rollout shape vs active rollout shape.
- Dev deploy drift checks: image tags, chart version, worker queues.

---

### Task 1: Grither Pay Concurrent Withdrawal Creation Tests

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationServiceTest.java`

- [ ] Add a test named `concurrentDuplicateUserIdempotencyKeyCreatesOneWithdrawalExecutionAndDebit`.

Implementation shape:

```java
CountDownLatch start = new CountDownLatch(1);
ExecutorService executor = Executors.newFixedThreadPool(2);
Future<WalletWithdrawal> first = executor.submit(() -> createAfter(start, request));
Future<WalletWithdrawal> second = executor.submit(() -> createAfter(start, request));
start.countDown();
assertThat(first.get().getId()).isEqualTo(second.get().getId());
assertThat(withdrawalRepository.count()).isEqualTo(1);
assertThat(executionRepository.count()).isEqualTo(1);
assertThat(outboxRepository.count()).isEqualTo(1);
assertThat(ledgerEntryRepository.count()).isEqualTo(1);
```

- [ ] Add a test named `concurrentDifferentIdempotencyKeysRespectSinglePendingWithdrawalGuard`.

Expected result: one request succeeds, one fails with `WITHDRAWAL_ALREADY_PENDING`; there is no second ledger debit or second payout execution.

- [ ] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend -Dtest=WalletShKeeperWithdrawalCreationServiceTest test
```

Expected: all tests pass.

### Task 2: Grither Pay Submit Dispatcher Conflict/Timeout Tests

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcherTest.java`

- [ ] Confirm the existing timeout test verifies `payoutClient.getStatus(externalId)` is called after `ResourceAccessException`.

- [ ] Add or keep a test named `changedPayloadConflictMovesExecutionToManualReconciliationAndKeepsFundsReserved`.

Implementation shape:

```java
seedOutbox("PDSP-CONFLICT", REQUEST_HASH);
doThrow(new HttpClientErrorException(HttpStatus.CONFLICT, "PAYOUT_EXECUTION_CONFLICT"))
        .when(payoutClient).submit(any());
dispatcher.dispatchDue(10);
ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PDSP-CONFLICT").orElseThrow();
assertThat(execution.getState()).isEqualTo(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
assertThat(execution.getManualResolutionState()).isEqualTo(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
assertThat(walletWithdrawalRepository.findById(execution.getWalletWithdrawalId()).orElseThrow().getStatus())
        .isEqualTo(WalletWithdrawalStatus.PROCESSING);
```

- [ ] Add a test named `retryExhaustionDoesNotRefundOrCreateReplacementPayout`.

Expected result: outbox is failed/reconciliation-visible, wallet withdrawal remains reserved/processing, no replacement outbox row.

- [ ] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend -Dtest=ShKeeperPayoutSubmitDispatcherTest test
```

Expected: all tests pass.

### Task 3: Grither Pay Callback Ordering And Replay Tests

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`

- [ ] Add a webhook test named `sameEventIdWithRefreshedTimestampAndSignatureIsIdempotent`.

Expected result: first request returns `APPLIED`, second returns `IDEMPOTENT`, one callback event row, no second wallet mutation.

- [ ] Add or confirm a state-applier test named `staleCallbackAfterConfirmedCannotDowngradeCompletedWithdrawal`.

Expected result: stale callback is stored as `STALE`, execution remains `CONFIRMED`, wallet withdrawal remains `COMPLETED`, no refund ledger entry.

- [ ] Add or confirm a state-applier test named `manualReviewStatesKeepWithdrawalReserved`.

Expected result: `RECONCILIATION_REQUIRED`, `FAILED_CHAIN_TERMINAL`, and `MANUAL_REVIEW` map to keep-reserved behavior.

- [ ] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend -Dtest=ShKeeperPayoutWebhookControllerTest,ShKeeperPayoutStateApplicationServiceTest test
```

Expected: all tests pass.

### Task 4: SHKeeper API Concurrency Tests

**Files:**
- Modify: `tests/test_payout_execution_api.py`

- [ ] Add a test named `test_concurrent_duplicate_submit_same_payload_returns_one_execution`.

Implementation shape:

```python
with ThreadPoolExecutor(max_workers=2) as executor:
    responses = list(executor.map(lambda nonce: self.post_execution(payload, nonce=nonce), ["n1", "n2"]))
self.assertEqual(PayoutExecution.query.count(), 1)
self.assertEqual({r.get_json()["execution_id"] for r in responses}, {execution.id})
```

- [ ] Add a test named `test_concurrent_duplicate_submit_changed_payload_conflicts`.

Expected result: one `202`, one `409`, one execution row, stored request hash matches the accepted payload.

- [ ] Add a burst test named `test_burst_different_external_ids_creates_all_rows_without_500`.

Expected result: all responses are `202`, row count equals burst size.

- [ ] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api -v
```

Expected: all tests pass.

### Task 5: SHKeeper Callback Outbox And Reconciler Edge Tests

**Files:**
- Modify: `tests/test_payout_callback_outbox.py`
- Modify: `tests/test_payout_execution_reconciler.py`

- [ ] Confirm existing tests cover outbox retry metadata, no overtaking, poison execution, submit timeout after `ENQUEUEING`, and sidecar ordering conflicts.

- [ ] Add only missing tests:
  - delayed callback retry refreshes timestamp/signature while payload hash/event id remain unchanged;
  - worker unavailable in `CREATED` remains retryable before unsafe window;
  - worker unavailable after `ENQUEUEING` moves to `RECONCILIATION_REQUIRED`.

- [ ] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_reconciler -v
```

Expected: all tests pass.

### Task 6: Sidecar Rail-Specific Concurrency Tests

**Files:**
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_execution_boundaries.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_task_resource_provisioning.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_fee_deposit_seqno_guard.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_boundaries.py`

- [ ] TRON: add concurrent same fee-wallet payout/resource test.

Expected result: one active payout worker path serializes execution; resource provisioning does not double-rent or collide.

- [ ] TRON: add ProfeeX/re:Fee timeout tests.

Expected result: pre-broadcast resource failure is terminal pre-broadcast; ambiguous post-sign/broadcast failure requires reconciliation.

- [ ] TON: add concurrent seqno reservation test.

Expected result: no duplicate seqno for two payouts from the same wallet.

- [ ] ETH: add concurrent nonce reservation test.

Expected result: no duplicate nonce for two payouts from the same wallet; ambiguous nonce evidence blocks blind retry.

- [ ] Run each sidecar suite:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
python -m unittest discover -s tests -v

cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest discover -s tests -v

cd /Users/test/PycharmProjects/ethereum-shkeeper
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

### Task 7: SHKeeper-to-Sidecar E2E Expansion

**Files:**
- Modify: `scripts/verify_payout_sidecar_e2e.py`

- [ ] Add a mode that creates two different executions for the same network before running `PayoutExecutionReconciler.dispatch_ready(batch_size=10)`.

- [ ] Verify both executions reach sidecar `RECEIVED` and preserve distinct `execution_id`, `external_id`, `request_hash`, and `sidecar_payload_hash`.

- [ ] Keep this suite local and deterministic: no real chain broadcast, no dev secrets.

- [ ] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_payout_sidecar_e2e.py
```

Expected: TRON, TON, and ETH all pass.

### Task 8: Helm Worker Topology Tests

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] Add or confirm a staged TRON rail test.

Expected result: paused/kill-switched rail does not require `tron-usdt-payouts`.

- [ ] Add or confirm an active TRON rail test.

Expected result: active rail renders `tron-usdt-payouts`, queue `tron_usdt_fee_payouts`, `--concurrency=1`, `--prefetch-multiplier=1`.

- [ ] Add service/deployment consistency test.

Expected result: if `service/tron-shkeeper` exposes Redis port for active payout flow, worker deployment exists.

- [ ] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python3 -m unittest tests/test_shkeeper_fork_chart.py -v
helm lint charts/shkeeper
```

Expected: tests and lint pass.

### Task 9: Live Dev UAT Runner

**Files:**
- Create: `scripts/payout_dev_uat.py`
- Modify: `docs/runbooks/usdt-payout-operations.md`

- [ ] Implement CLI args:

```text
--shkeeper-base-url
--grither-base-url
--consumer
--key-id
--secret-env
--network
--destination
--amount
--scenario
--dry-run
```

- [ ] Implement scenarios:
  - `same-external-id-same-payload-concurrent`
  - `same-external-id-changed-payload-concurrent`
  - `different-external-ids-concurrent`
  - `submit-timeout-retry`
  - `callback-retry-manual`
  - `metrics-snapshot`

- [ ] Require explicit confirmation flag for real chain-moving tests:

```text
--allow-real-payout
```

Without it, the runner may only call status, dry-run, or non-broadcast scenarios.

- [ ] Print JSON report:

```json
{
  "scenario": "different-external-ids-concurrent",
  "network": "TRON",
  "external_ids": ["UAT-...-1", "UAT-...-2"],
  "result": "PASS",
  "observations": []
}
```

- [ ] Run dry-run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PAYOUT_SECRET=... .venv/bin/python scripts/payout_dev_uat.py \
  --shkeeper-base-url https://dev-shkeeper.example \
  --consumer grither-pay \
  --key-id default \
  --secret-env PAYOUT_SECRET \
  --network TRON \
  --destination T... \
  --amount 1.000000 \
  --scenario same-external-id-same-payload-concurrent \
  --dry-run
```

Expected: signed request construction and scenario plan succeed without sending funds.

### Task 10: Cross-Repo Gate Documentation

**Files:**
- Modify: `docs/runbooks/usdt-payout-operations.md`
- Modify: `scripts/verify_payout_release_gate.py`

- [ ] Add a "Payout UAT Gate" section with local deterministic commands.

- [ ] Add `--list` output for the new UAT runner command, but do not run live dev UAT from the deterministic release gate.

- [ ] Keep live UAT manual/operator-triggered because it can move funds.

## Final Gate Commands

SHKeeper:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_payout_sidecar_e2e.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/verify_payout_release_gate.py
```

Grither Pay:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend \
  -Dtest=WalletShKeeperWithdrawalCreationServiceTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutWebhookControllerTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutManualResolutionServiceTest \
  test
```

Sidecars:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
python -m unittest discover -s tests -v

cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest discover -s tests -v

cd /Users/test/PycharmProjects/ethereum-shkeeper
.venv/bin/python -m unittest discover -s tests -v
```

Helm:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python3 -m unittest tests/test_shkeeper_fork_chart.py -v
helm lint charts/shkeeper
```

Live dev UAT:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
PAYOUT_SECRET=... .venv/bin/python scripts/payout_dev_uat.py \
  --shkeeper-base-url https://dev-shkeeper.example \
  --consumer grither-pay \
  --key-id default \
  --secret-env PAYOUT_SECRET \
  --network TRON \
  --destination T... \
  --amount 1.000000 \
  --scenario different-external-ids-concurrent \
  --allow-real-payout
```

## Completion Criteria

- P0 tests pass in SHKeeper, Grither Pay, sidecars, and Helm.
- Dev UAT runner can produce a JSON report for dry-run and real low-value TRON scenarios.
- Real-payout scenarios require explicit `--allow-real-payout`.
- No runtime workaround is introduced for tests.
- No live dev UAT command is added to automatic release gates.
