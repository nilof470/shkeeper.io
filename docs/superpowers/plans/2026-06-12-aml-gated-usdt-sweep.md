# AML-Gated USDT Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent new guarded USDT deposits from being swept to sidecar fee wallets until SHKeeper has an AML-safe decision or an audited manual resolution.

**Architecture:** SHKeeper is the source of AML and sweep state. Sidecars ask SHKeeper for address-level sweep eligibility before any fee-wallet-funded activation/gas/energy funding or full-balance token sweep signing. Grither Pay receives AML facts and a nullable `aml.review_required` signal, then records admin approve/refund decisions and calls SHKeeper's backend-only manual resolution endpoint. Manual refunds are operator-attested in this release: SHKeeper stores the evidence but does not verify the refund transaction on-chain.

**Tech Stack:** Python Flask, Flask-SQLAlchemy, unittest, TRON sidecar Celery/Python, Ethereum sidecar Celery/Python, Grither Pay Spring Boot/React admin.

---

## Files

- Modify: `shkeeper/models.py` for `AmlCheck.sweep_guard_required` and manual resolution audit model.
- Create: `migrations/versions/20260612_aml_sweep_guard.py` for default-false legacy migration.
- Modify: `shkeeper/services/aml_processing.py` to mark new non-skipped guarded deposits and keep AML outage handling fail-closed.
- Create: `shkeeper/services/sweep_eligibility.py` for address-level eligibility decisions.
- Modify: `shkeeper/api_v1.py` to expose `POST /api/v1/sweep-eligibility` and `POST /api/v1/sweep-resolution`.
- Modify: `shkeeper/callback.py` to keep `aml.review_required` in callback payload.
- Test: `tests/test_aml_processing.py`, `tests/test_aml_callback_payload.py`, `tests/test_aml_sweep_eligibility.py`, `tests/test_aml_sweep_resolution.py`.
- Modify outside this repo after SHKeeper is ready: `/Users/test/PycharmProjects/tron-shkeeper/app/block_scanner.py`, `/Users/test/PycharmProjects/tron-shkeeper/app/tasks.py`, `/Users/test/PycharmProjects/ethereum-shkeeper/app/events.py`, `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`, `/Users/test/PycharmProjects/ethereum-shkeeper/app/token.py`.
- Modify outside this repo after SHKeeper is ready: Grither Pay callback AML DTO/facts, stored AML fact entity/migration, admin deposit API/mappers, deposit policy, review rules/service/request DTO, deposit resolution outbox/dispatcher/client, admin API TS types, and `WalletDeposits.tsx` as detailed in Task 6.

## Repository Boundaries

This plan starts in `/Users/test/PycharmProjects/shkeeper.io`. Tasks 1-4 are executable in this workspace.

Tasks 5 and 6 touch separate repositories. Execute them only after switching to the matching repository workspace, or after write permission is granted for those paths. Do not edit sidecar or Grither Pay files from the SHKeeper workspace under read-only access.

### Task 1: AML Outage Fail-Closed Hotfix

**Files:**
- Modify: `shkeeper/services/aml_processing.py`
- Test: `tests/test_aml_processing.py`

- [x] **Step 1: Write the failing test**

```python
def test_sidecar_create_exception_remains_retryable_until_timeout(self):
    def create_check(client, payload):
        raise RuntimeError("connection reset by peer")

    aml_processing.AmlShkeeperClient.create_check = create_check
    tx = self.make_tx("150")

    check = aml_processing.ensure_aml_for_transaction(tx)

    self.assertEqual(check.status, AmlStatus.CHECKING)
    self.assertEqual(check.provider_status, "checking")
    self.assertIsNone(check.deposit_decision)
    self.assertIsNone(check.decision_reason)
    self.assertEqual(check.error_code, "aml_shkeeper_exception")
    self.assertIn("connection reset by peer", check.error_message)
    self.assertIsNotNone(check.next_retry_at)
    self.assertFalse(aml_processing.is_callback_allowed(tx))
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_aml_processing.AmlProcessingTestCase.test_sidecar_create_exception_remains_retryable_until_timeout
```

Expected before implementation: error from `RuntimeError: connection reset by peer`.

- [x] **Step 3: Implement exception normalization**

Add `_client_exception_result(exc)` and wrap the `AmlShkeeperClient().create_check(payload)` call so unexpected client exceptions become retryable `aml-shkeeper` errors.

- [x] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m unittest tests.test_aml_processing.AmlProcessingTestCase.test_sidecar_create_exception_remains_retryable_until_timeout
```

Expected: `OK`.

### Task 2: Guarded Deposit Marker

**Files:**
- Modify: `shkeeper/models.py`
- Create: `migrations/versions/20260612_aml_sweep_guard.py`
- Modify: `shkeeper/services/aml_processing.py`
- Test: `tests/test_aml_processing.py`

- [ ] **Step 1: Write tests for new guarded and legacy deposits**

Before adding the tests, extend `AmlProcessingTestCase.setUp()` with `Wallet`
and `ExchangeRate` rows for `USDT` and `ETH-USDT`; the existing fixture creates
BTC rows only.

Add tests:

```python
def test_new_tron_usdt_above_skip_threshold_sets_sweep_guard_required(self):
    tx = self.make_tx("150", crypto="USDT")
    check = aml_processing.ensure_aml_for_transaction(tx)
    self.assertTrue(check.sweep_guard_required)

def test_new_eth_usdt_above_skip_threshold_sets_sweep_guard_required(self):
    tx = self.make_tx("150", crypto="ETH-USDT")
    check = aml_processing.ensure_aml_for_transaction(tx)
    self.assertTrue(check.sweep_guard_required)

def test_new_tron_usdt_skipped_small_amount_does_not_need_sweep_guard(self):
    tx = self.make_tx("50", crypto="USDT")
    check = aml_processing.ensure_aml_for_transaction(tx)
    self.assertEqual(check.status, AmlStatus.SKIPPED)
    self.assertFalse(check.sweep_guard_required)

def test_legacy_or_non_guarded_check_defaults_to_not_guarded(self):
    tx = self.make_tx("150", crypto="BTC")
    check = aml_processing.ensure_aml_for_transaction(tx)
    self.assertFalse(check.sweep_guard_required)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m unittest tests.test_aml_processing.AmlProcessingTestCase.test_new_tron_usdt_above_skip_threshold_sets_sweep_guard_required tests.test_aml_processing.AmlProcessingTestCase.test_new_eth_usdt_above_skip_threshold_sets_sweep_guard_required tests.test_aml_processing.AmlProcessingTestCase.test_new_tron_usdt_skipped_small_amount_does_not_need_sweep_guard tests.test_aml_processing.AmlProcessingTestCase.test_legacy_or_non_guarded_check_defaults_to_not_guarded
```

Expected: fail because `sweep_guard_required` does not exist.

- [ ] **Step 3: Add model field and migration**

Add to `AmlCheck`:

```python
sweep_guard_required = db.Column(db.Boolean, nullable=False, default=False)
```

Migration must add the column with server default false, backfill existing rows to false, then keep nullable false.

- [ ] **Step 4: Mark only new non-skipped guarded rails**

In AML check creation, set `sweep_guard_required=True` only for non-skipped checks on:

```python
crypto in {"USDT", "ETH-USDT"}
```

Keep skipped small-amount checks at `sweep_guard_required=False`; they are
safe-to-sweep by local AML skip policy and do not participate in the new gate.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m unittest tests.test_aml_processing
```

Expected: `OK`.

### Task 3: SHKeeper Sweep Eligibility Endpoint

**Files:**
- Create: `shkeeper/services/sweep_eligibility.py`
- Modify: `shkeeper/api_v1.py`
- Test: `tests/test_aml_sweep_eligibility.py`

- [ ] **Step 1: Write eligibility tests**

Create `tests/test_aml_sweep_eligibility.py` with helpers that create an invoice, `InvoiceAddress`, `Transaction`, and optional `AmlCheck`. Add these tests:

```python
def test_legacy_address_without_guarded_checks_allows_sweep():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="legacy-tx")
    add_aml_check(tx, status=AmlStatus.APPROVED, sweep_guard_required=False)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "allow"
    assert result["reason"] == "legacy_no_guarded_deposits"
    assert result["matched_transaction_count"] == 0

def test_pending_guarded_deposit_returns_wait():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="pending-tx")
    add_aml_check(tx, status=AmlStatus.CHECKING, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR", txid="pending-tx")

    assert result["decision"] == "wait"
    assert result["reason"] == "aml_checking"
    assert result["transaction_ids"] == [tx.id]

def test_manual_review_guarded_deposit_returns_block():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="manual-tx")
    add_aml_check(tx, status=AmlStatus.MANUAL_REVIEW, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "block"
    assert result["reason"] == "manual_review"

def test_approved_guarded_deposit_returns_allow():
    tx = make_tx(crypto="ETH-USDT", addr="0xabc", txid="approved-tx")
    add_aml_check(tx, status=AmlStatus.APPROVED, sweep_guard_required=True)

    result = decide_sweep_eligibility("ETH-USDT", "ETH", "0xabc")

    assert result["decision"] == "allow"
    assert result["reason"] == "aml_approved"

def test_skipped_small_amount_without_guard_marker_returns_allow():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="skipped-tx")
    add_aml_check(tx, status=AmlStatus.SKIPPED, sweep_guard_required=False)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "allow"
    assert result["reason"] == "legacy_no_guarded_deposits"

def test_live_unknown_txid_returns_wait():
    result = decide_sweep_eligibility("USDT", "TRON", "TADDR", txid="not-yet-recorded")

    assert result["decision"] == "wait"
    assert result["reason"] == "transaction_not_found"

def test_mismatched_address_returns_block():
    tx = make_tx(crypto="USDT", addr="TADDR1", txid="guarded-tx")
    add_aml_check(tx, status=AmlStatus.APPROVED, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR2", txid="guarded-tx")

    assert result["decision"] == "block"
    assert result["reason"] == "mismatch"

def test_one_pending_guarded_deposit_blocks_full_address_allow():
    approved = make_tx(crypto="USDT", addr="TADDR", txid="approved-tx")
    pending = make_tx(crypto="USDT", addr="TADDR", txid="pending-tx")
    add_aml_check(approved, status=AmlStatus.APPROVED, sweep_guard_required=True)
    add_aml_check(pending, status=AmlStatus.CHECKING, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "wait"
    assert result["reason"] == "aml_checking"

def test_manual_resolution_does_not_allow_when_another_guarded_deposit_is_pending():
    resolved = make_tx(crypto="USDT", addr="TADDR", txid="resolved-tx")
    pending = make_tx(crypto="USDT", addr="TADDR", txid="pending-tx")
    add_aml_check(resolved, status=AmlStatus.MANUAL_REVIEW, sweep_guard_required=True)
    add_resolution(resolved, resolution_type="approved")
    add_aml_check(pending, status=AmlStatus.CHECKING, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "wait"
    assert result["reason"] == "aml_checking"

def test_persisted_guarded_rail_transaction_without_guarded_check_waits_for_aml_when_txid_is_supplied():
    make_tx(crypto="USDT", addr="TADDR", txid="legacy-without-aml-tx")

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR", txid="legacy-without-aml-tx")

    assert result["decision"] == "wait"
    assert result["reason"] == "aml_missing"

def test_guarded_check_with_unknown_status_returns_wait():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="broken-aml-tx")
    add_aml_check(tx, status="unexpected_state", sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "wait"
    assert result["reason"] == "aml_missing"

def test_confirmations_pending_returns_wait():
    tx = make_tx(crypto="USDT", addr="TADDR", txid="confirming-tx", need_more_confirmations=True)
    add_aml_check(tx, status=AmlStatus.APPROVED, sweep_guard_required=True)

    result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

    assert result["decision"] == "wait"
    assert result["reason"] == "confirmations_pending"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m unittest tests.test_aml_sweep_eligibility
```

Expected: import or 404 failures because endpoint/service does not exist.

- [ ] **Step 3: Implement service decision object**

Create `decide_sweep_eligibility(crypto, network, address, txid=None)` returning:

```python
{
    "decision": "allow" | "wait" | "block",
    "reason": "aml_approved",
    "transaction_ids": [1],
    "matched_transaction_count": 1,
    "aml_statuses": ["approved"],
}
```

The service must query address-level eligibility through
`AmlCheck.sweep_guard_required=True`. A periodic address-level request without a
`txid` treats persisted USDT or ETH-USDT transactions without guarded AML checks
as legacy and returns `allow` when no guarded checks match the address. A live
request that supplies a recorded guarded-rail `txid` without a guarded AML check
returns `wait` with `aml_missing`. `transaction_not_found` applies only when the
caller supplied a live `txid` that SHKeeper has not recorded yet. `aml_missing`
also applies to a matching guarded AML row with an unknown or broken state.

- [ ] **Step 4: Add backend-only endpoint**

Add `POST /api/v1/sweep-eligibility` protected by `X-Shkeeper-Backend-Key`.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m unittest tests.test_aml_sweep_eligibility
```

Expected: `OK`.

### Task 4: SHKeeper Manual Resolution Endpoint

**Files:**
- Modify: `shkeeper/models.py`
- Create: `shkeeper/services/sweep_resolution.py`
- Modify: `shkeeper/api_v1.py`
- Test: `tests/test_aml_sweep_resolution.py`

- [ ] **Step 1: Write resolution tests**

Create `tests/test_aml_sweep_resolution.py` with helpers that post to `/api/v1/sweep-resolution` using `X-Shkeeper-Backend-Key`. Add these tests:

```python
def test_approved_resolution_unblocks_manual_review_when_address_is_otherwise_safe():
    tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR", txid="manual-tx")

    response = post_resolution({
        "resolution_type": "approved",
        "deposit_id": f"shkeeper-tx-{tx.id}",
        "crypto": "USDT",
        "network": "TRON",
        "address": "TADDR",
        "txid": "manual-tx",
        "external_review_id": "gp-review-1",
        "reviewer": "admin@example.com",
        "reason": "Manual approval after compliance review",
        "idempotency_key": "gp-resolution-1",
    })

    assert response.status_code == 200
    assert decide_sweep_eligibility("USDT", "TRON", "TADDR")["decision"] == "allow"

def test_refunded_resolution_requires_refund_evidence():
    tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR", txid="manual-tx")

    response = post_resolution({
        "resolution_type": "refunded",
        "deposit_id": f"shkeeper-tx-{tx.id}",
        "crypto": "USDT",
        "network": "TRON",
        "address": "TADDR",
        "txid": "manual-tx",
        "external_review_id": "gp-review-2",
        "reviewer": "admin@example.com",
        "reason": "Manual refund completed",
        "idempotency_key": "gp-resolution-2",
    })

    assert response.status_code == 400
    assert decide_sweep_eligibility("USDT", "TRON", "TADDR")["decision"] == "block"

def test_refunded_resolution_with_operator_evidence_unblocks_when_address_is_otherwise_safe():
    tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR", txid="manual-tx")

    response = post_resolution({
        "resolution_type": "refunded",
        "deposit_id": f"shkeeper-tx-{tx.id}",
        "crypto": "USDT",
        "network": "TRON",
        "address": "TADDR",
        "txid": "manual-tx",
        "refund_txid": "refund-tx-1",
        "refund_to_address": "TSENDER",
        "refund_amount": "100.000000",
        "external_review_id": "gp-review-3",
        "reviewer": "admin@example.com",
        "reason": "Manual refund completed from VPS script",
        "idempotency_key": "gp-resolution-3",
    })

    assert response.status_code == 200
    assert decide_sweep_eligibility("USDT", "TRON", "TADDR")["decision"] == "allow"

def test_resolution_rejects_non_manual_review_deposit():
    tx = make_approved_guarded_tx(crypto="USDT", network="TRON", addr="TADDR", txid="approved-tx")
    response = post_approved_resolution(tx, idempotency_key="gp-resolution-4")

    assert response.status_code == 409

def test_resolution_rejects_legacy_non_guarded_deposit():
    tx = make_manual_review_tx(crypto="USDT", network="TRON", addr="TADDR", txid="legacy-manual-tx", sweep_guard_required=False)
    response = post_approved_resolution(tx, idempotency_key="gp-resolution-5")

    assert response.status_code == 409

def test_resolution_is_idempotent_by_idempotency_key():
    tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR", txid="manual-tx")
    first = post_approved_resolution(tx, idempotency_key="gp-resolution-6")
    second = post_approved_resolution(tx, idempotency_key="gp-resolution-6")

    assert first.status_code == 200
    assert second.status_code == 200
    assert manual_resolution_count(tx) == 1

def test_conflicting_idempotency_key_is_rejected():
    first_tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR1", txid="manual-tx-1")
    second_tx = make_manual_review_guarded_tx(crypto="USDT", network="TRON", addr="TADDR2", txid="manual-tx-2")
    post_approved_resolution(first_tx, idempotency_key="gp-resolution-7")
    response = post_approved_resolution(second_tx, idempotency_key="gp-resolution-7")

    assert response.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m unittest tests.test_aml_sweep_resolution
```

Expected: import or 404 failures.

- [ ] **Step 3: Add audit model**

Add a model storing deposit id, transaction id, txid, crypto, network, address, resolution type, reviewer, reason, external review id, idempotency key, refund evidence, request digest, and timestamps.

- [ ] **Step 4: Implement endpoint**

Add `POST /api/v1/sweep-resolution` protected by backend key. Accept only:

```json
{"resolution_type": "approved"}
```

or:

```json
{
  "resolution_type": "refunded",
  "refund_txid": "refund-tx-1",
  "refund_to_address": "TSENDER",
  "refund_amount": "100.000000"
}
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m unittest tests.test_aml_sweep_resolution tests.test_aml_sweep_eligibility
```

Expected: `OK`.

### Task 5: Sidecar Integration

**Files:**
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/block_scanner.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/tasks.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/events.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/token.py`

- [ ] **Step 1: Add sidecar tests**

TRON tests must assert guarded USDT sweep does not call token transfer,
fee-wallet account activation, TRX funding, energy provisioning, or broadcast on
`wait`, `block`, timeout, invalid JSON, 403, or 500.

Ethereum tests must assert guarded ETH-USDT drain does not call token transfer,
fee-wallet ETH gas seeding, signing, or broadcast on `wait`, `block`, timeout,
invalid JSON, 403, or 500.

- [ ] **Step 2: Implement SHKeeper eligibility client in each sidecar**

Use backend key, short timeout, and fail-closed parsing. Only exact `decision == "allow"` permits sweep/drain.

- [ ] **Step 3: Guard final signing points**

TRON guard must be at the start of `transfer_trc20_from`, before account
activation, TRX funding, energy provisioning, and token transfer signing.

Ethereum guard must be inside `drain_account` or the helper that calls
`drain_tocken_account`, before fee-wallet ETH gas seeding and token transfer
signing. Live event calls should pass `txid`; periodic rescan calls can omit it
and rely on address-level eligibility.

- [ ] **Step 4: Disable legacy TRON AMLBot path for guarded USDT**

When new SHKeeper guard is enabled for USDT, bypass `EXTERNAL_DRAIN_CONFIG` custom AML/payout path.

### Task 6: Grither Pay Integration

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/callback/ShKeeperAmlPayload.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperAmlFacts.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperTransactionAmlFactService.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperTransactionAmlFact.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/api/ShKeeperAdminDepositDetail.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperAdminDepositSectionMapper.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperDepositPolicy.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperDepositReviewService.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperDepositReviewRules.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/web/dto/request/ShKeeperDepositReviewRequest.java`
- Create: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperDepositResolutionOutbox.java`
- Create: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/persistence/ShKeeperDepositResolutionOutboxRepository.java`
- Create: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperDepositResolutionOutboxService.java`
- Create: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/application/ShKeeperDepositResolutionDispatcher.java`
- Create or extend: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/client/ShKeeperApiClient.java`
- Create: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/resources/db/changelog/migrations/V091_shkeeper_deposit_aml_review_required_and_resolution_outbox.sql`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/resources/db/changelog/db.changelog-master.yaml`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/admin/src/api/walletApi.ts`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/admin/src/components/wallet/WalletDeposits.tsx`

- [ ] **Step 1: Parse and persist `aml.review_required`**

Add nullable `reviewRequired` to the ShKeeper callback AML DTO, AML facts
object, stored AML fact entity, admin detail DTO, admin mapper, and admin TS
types:

```java
@JsonProperty("review_required")
Boolean reviewRequired
```

Add a Liquibase migration that adds nullable
`shkeeper_transaction_aml_facts.review_required`. Keep it nullable so callbacks
received before this field existed remain distinguishable from an explicit
`false`. Persist it with the stored AML facts/policy snapshot so admin detail and
review rules can distinguish terminal SHKeeper manual-review from local `HELD`
states.

Add tests in `ShKeeperTransactionAmlFactServiceTest`,
`ShKeeperAdminDepositSectionMapperTest`, and a callback integration test proving
the value is parsed, stored, and returned to admin detail.

- [ ] **Step 2: Use `aml.review_required` as the automatic review signal**

Do not recompute automatic AML outcome from score when SHKeeper supplies
`review_required`. Keep the field tri-state:

- `true`: return `MANUAL_REVIEW`.
- `false`: return `AUTO_CREDIT` after existing non-AML local checks pass.
- `null`: legacy callback; keep the current score-threshold policy.

Add tests:

```java
@Test
void reviewRequiredTrueRoutesToManualReviewWithoutScoreThresholdRecalculation() {
    ShKeeperAmlFacts aml = aml("success", "0.01", true);
    ShKeeperLocalDepositDecision decision = decide(aml, effectivePolicy("0.10"));
    assertThat(decision).isEqualTo(ShKeeperLocalDepositDecision.MANUAL_REVIEW);
}

@Test
void reviewRequiredFalseCanCreditEvenWhenLegacyLocalThresholdWouldDisagree() {
    ShKeeperAmlFacts aml = aml("success", "0.72", false);
    ShKeeperLocalDepositDecision decision = decide(aml, effectivePolicy("0.10"));
    assertThat(decision).isEqualTo(ShKeeperLocalDepositDecision.AUTO_CREDIT);
}

@Test
void missingReviewRequiredUsesLegacyScorePolicy() {
    ShKeeperAmlFacts aml = aml("success", "0.72", null);
    ShKeeperLocalDepositDecision decision = decide(aml, effectivePolicy("0.10"));
    assertThat(decision).isEqualTo(ShKeeperLocalDepositDecision.MANUAL_REVIEW);
}
```

Also update callback fixture/integration coverage so old fixtures without
`review_required` still behave exactly as before.

- [ ] **Step 3: Restrict actionable admin states**

Only terminal SHKeeper manual-review deposits can show approve/refund actions.
Backend rules, not only frontend buttons, must require:

- provider transaction status is `MANUAL_REVIEW`;
- stored AML fact has `reviewRequired == true`;
- matched invoice/user and positive amount for approve.

Pending/checking AML and reconciliation-only `HELD` states remain non-actionable.
Replace current tests that approve/reject `HELD` as valid behavior with tests
that reject `HELD` and `MANUAL_REVIEW` without `reviewRequired=true`.

- [ ] **Step 4: Add approve outbox command**

Admin approve credits the client wallet idempotently and records a durable
outbox command to call SHKeeper `sweep-resolution` with
`resolution_type=approved`.

The outbox must be persisted in the same database transaction as the successful
manual approval. Include transaction id, SHKeeper `deposit_id`, txid, crypto,
network, address, reviewer, reason, external review id, idempotency key,
request hash, status, attempt count, next attempt time, last error, and
timestamps. An async dispatcher calls SHKeeper with backend key and retries
transient failures. A failed SHKeeper call after commit must not roll back the
wallet credit; admin detail must show the resolution retry state.

- [ ] **Step 5: Add refund/reject evidence flow**

Admin reject becomes `Reject after manual refund`. The request DTO, backend
service, and admin form must require refund txid, destination, amount, source,
asset, network, and reason. Plain reject without refund evidence must not create
a SHKeeper resolution or unblock sweep.

After validation, Grither Pay records the review decision, refund evidence, and a
durable outbox command to call SHKeeper `sweep-resolution` with
`resolution_type=refunded`. It does not credit the wallet. SHKeeper resolution
failures remain visible and retryable in admin.

- [ ] **Step 6: Update client/admin UX**

Client sees neutral review text and never sees score, report URL, provider
details, or internal reason codes. Admin sees AML evidence, `reviewRequired`,
resolution status, retryable SHKeeper failures, and operator-entered refund
evidence. Frontend action buttons must follow the same actionable predicate as
backend rules, but backend rules remain authoritative.

### Task 7: End-to-End Verification

**Files:**
- No new files required.

- [ ] **Step 1: Run SHKeeper AML tests**

```bash
.venv/bin/python -m unittest tests.test_aml_callback_payload tests.test_aml_end_to_end tests.test_aml_processing tests.test_aml_shkeeper_client tests.test_aml_sweep_eligibility tests.test_aml_sweep_resolution
```

Expected: `OK`.

- [ ] **Step 2: Run sidecar guard tests**

In the TRON sidecar workspace, run the targeted guard tests proving non-`allow`
eligibility does not perform activation, fee funding, energy provisioning, token
transfer, signing, or broadcast.

In the Ethereum sidecar workspace, run the targeted guard tests proving
non-`allow` eligibility does not perform fee-wallet gas seeding, token transfer,
signing, or broadcast.

- [ ] **Step 3: Run Grither Pay AML/review tests**

In the Grither Pay workspace, run targeted tests covering:

- `reviewRequired=true`, `false`, and missing/null policy behavior.
- AML fact persistence/admin mapping for `reviewRequired`.
- `HELD` and `MANUAL_REVIEW` without `reviewRequired=true` are non-actionable.
- approve creates wallet credit and SHKeeper approved-resolution outbox exactly once.
- reject requires refund evidence and creates refunded-resolution outbox without credit.
- outbox dispatcher retries transient SHKeeper resolution failures.

- [ ] **Step 4: Verify dev approved flow**

Create a dev manual-review deposit, approve it in Grither Pay, verify client credit, SHKeeper resolution audit, later `sweep-eligibility=allow`, and sidecar sweep.

- [ ] **Step 5: Verify dev refunded flow**

Create a dev manual-review deposit, manually refund with the VPS script, reject in Grither Pay with refund evidence, verify SHKeeper refund resolution audit, later `sweep-eligibility=allow`, and sidecar sweep after the operator-attested resolution.

- [ ] **Step 6: Verify outage flow**

Simulate `aml-shkeeper` unavailable. Confirm new deposits remain `CHECKING`, no final callback is sent, Grither Pay does not credit, and sidecars get no `allow`.

- [ ] **Step 7: Verify legacy/no-marker flow**

Seed a persisted USDT or ETH-USDT transaction with no guarded AML check and
verify `sweep-eligibility=allow` with `legacy_no_guarded_deposits`. Then seed a
guarded AML check with an unknown state and verify `wait` with `aml_missing`.

## Self-Review

- Spec coverage: The plan covers guarded marker, eligibility, manual resolution, AML outage behavior, callback `review_required`, sidecar fail-closed guards, Grither Pay admin/client UX, and dev verification.
- Placeholder scan: No task uses unresolved placeholder markers or unspecified implementation steps.
- Type consistency: The plan consistently uses `sweep_guard_required`, `aml.review_required`, `sweep-eligibility`, `sweep-resolution`, `resolution_type=approved`, and `resolution_type=refunded`.
