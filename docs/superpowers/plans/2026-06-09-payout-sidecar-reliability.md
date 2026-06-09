# Payout Sidecar Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent transient sidecar worker/database failures from leaving payout executions stuck, while preserving the no-blind-retry rule after signing, nonce/seqno reservation, or broadcast evidence exists.

**Architecture:** Add a narrow retry guard at the Celery task boundary for TON and ETH sidecars, backed by explicit store recovery rules for task-owned pre-side-effect rows. Add an explicit sidecar orphan-recovery endpoint for stale no-evidence `RECEIVED`/`VALIDATED` rows so worker death or retry exhaustion cannot leave an execution permanently detached from the broker. Keep unsafe evidence as the hard boundary: after nonce/seqno, signed payload, message hash, tx hash, or broadcast attempt evidence exists, the worker must not automatically rebuild or rebroadcast. In SHKeeper core, cap active sidecar polling retries separately from submit ambiguity and call orphan recovery only from active polling when the sidecar is safely stuck before effects; in Grither, allow only narrowly proven provider-confirmed recovery from unresolved manual review without changing refund/accounting-terminal safety.

**Tech Stack:** Python 3, Celery, Flask-SQLAlchemy, SQLAlchemy, pytest/unittest, Java 21, Spring Boot, JUnit 5, AssertJ, PostgreSQL/H2 test stack.

---

## Source Context

Primary design spec:
- `docs/superpowers/specs/2026-06-09-payout-sidecar-reliability-design.md`

Production incident that this plan closes:
- External payout id: `68304109`
- TON sidecar execution id: `30`
- Initial sidecar state: `RECEIVED`, with no `attempt_id`, no `source_seqno`, no signed BOC, no message hash, no broadcast marker.
- Worker failure: first task hit `sqlalchemy.exc.OperationalError: MySQL server has gone away` while loading the row.
- Manual re-enqueue before restart hit `PendingRollbackError: Can't reconnect until invalid transaction is rolled back`.
- Rollout restart cleared the poisoned worker/session; re-enqueue succeeded and the sidecar later confirmed message hash `ec686d573edaf3815aad7e1464ff0abd2779e4de0b5baf8b2742c2d1c1807f1f`.
- Grither received the later `CONFIRMED` callback but kept the local row in `RECONCILIATION_REQUIRED` because the row was already operator-controlled/manual-review.

## File Structure

This is a cross-repository implementation plan. Execute each repository task from its own worktree.

### TON sidecar

- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/tasks.py`
  - Owns Celery task retry boundary.
  - Must roll back/remove Flask-SQLAlchemy sessions around every payout task attempt.
  - Must call `self.retry()` only for transient DB errors before unsafe evidence.
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/api/payout.py`
  - Adds an explicit authenticated orphan-recovery endpoint.
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/payout_execution.py`
  - Owns payout state machine.
  - Adds `is_transient_db_error()` and task-owned transient recovery policy.
  - Adds safe orphan re-enqueue for old `RECEIVED`/`VALIDATED` rows with no unsafe evidence.
  - Re-raises transient SQLAlchemy connection/session errors instead of converting them into payout terminal states.
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/config.py`
  - Adds the configured orphan recovery age threshold.
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_boundaries.py`
  - Adds state-machine tests for task-owned transient recovery.
- Create: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_task_retries.py`
  - Adds task-boundary tests for rollback/remove/retry behavior.

### ETH sidecar

- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`
  - Mirrors the TON Celery task retry/session guard.
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/api/payout.py`
  - Mirrors the TON authenticated orphan-recovery endpoint.
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/payout_execution.py`
  - Mirrors the TON transient DB policy using ETH unsafe evidence fields.
  - Mirrors safe orphan re-enqueue for old `RECEIVED`/`VALIDATED` rows with no unsafe evidence.
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/config.py`
  - Adds the configured orphan recovery age threshold.
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_boundaries.py`
  - Adds state-machine tests for task-owned transient recovery.
- Create: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_task_retries.py`
  - Adds task-boundary tests for retry/session cleanup.

### TRON sidecar

- Verify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_execution.py`
  - Existing direct-sqlite state machine already returns retryable pre-broadcast transient resource failures to `RECEIVED`.
- Verify: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_execution_boundaries.py`
  - Existing tests cover stale signing recovery, retryable resource lock timeout, retryable activation errors, and non-retryable provider resource errors.

### SHKeeper core

- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/__init__.py`
  - Adds configured orphan recovery age threshold for the reconciler.
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_execution_reconciler.py`
  - Adds a separate active-polling retry delay cap for `ENQUEUED` and `BROADCAST`.
  - Calls sidecar orphan recovery for old no-evidence `RECEIVED`/`VALIDATED` sidecar status while core state remains `ENQUEUED`.
  - Keeps submit timeout and `ENQUEUEING` ambiguity rules unchanged.
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_sidecar_client.py`
  - Adds the authenticated `recover_orphan()` sidecar call.
- Modify: `/Users/test/PycharmProjects/shkeeper.io/tests/test_payout_execution_reconciler.py`
  - Tightens tests around active polling retry delay cap.
  - Adds explicit orphan-recovery trigger tests.
  - Keeps `ENQUEUEING` status unavailable as reconciliation-required.

### Grither Pay

- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`
  - Adds a narrow provider-confirmed recovery allowance before `TERMINAL_STATE_CONFLICT`.
  - Allows that recovery only while the wallet withdrawal is still reserved (`PENDING` or `PROCESSING`).
  - Adds a global `sidecar_payload_hash` safety check before provider observations can overwrite stored fields.
  - Does not allow recovery after refund/accounting terminal states.
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
  - Adds callback tests for unresolved manual-review provider confirmation.
  - Adds mismatch tests for global and manual-review `sidecar_payload_hash` safety.
  - Keeps confirmed-after-refund conflict behavior.
- Modify if needed: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualResolutionServiceTest.java`
  - Existing manual provider completion tests already cover admin recovery; extend only if new message-hash handling changes the view.

---

## Task 0: Align Design Spec With Final Reliability Contract

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper.io/docs/superpowers/specs/2026-06-09-payout-sidecar-reliability-design.md`

- [ ] **Step 1: Add durable orphan recovery to the design spec**

Update the spec so it states that bounded Celery retry is not sufficient by itself. The final reliability contract must include:

- stale no-evidence `RECEIVED`/`VALIDATED` sidecar rows are not just safe to re-enqueue; they must have an explicit durable recovery path;
- the recovery path is a mutating `POST /payout-executions/{id}/recover-orphan` sidecar endpoint, not `GET /payout-executions/{id}`;
- SHKeeper core may call that endpoint only while the core execution is `ENQUEUED`, the sidecar reports old `RECEIVED`/`VALIDATED`, and there is no tx/message/broadcast evidence;
- failures after nonce/seqno, signed payload, message/tx hash, or broadcast marker still require reconciliation/recovery and must not call orphan re-enqueue.

- [ ] **Step 2: Tighten transient DB exception wording in the spec**

Replace the broad `DBAPIError` transient wording with:

```text
Transient DB errors are OperationalError, PendingRollbackError, or DBAPIError
with connection_invalidated=True. Integrity, data, programming, and constraint
errors are not transient payout-worker failures and must not enter the automatic
retry path.
```

- [ ] **Step 3: Make Grither evidence requirements explicit in the spec**

Update the Grither section so it explicitly requires:

- a global `sidecar_payload_hash` safety check before provider observations can overwrite stored execution fields;
- `request_hash + sidecar_payload_hash` is the canonical binding for amount/destination unless Grither adds first-class amount/destination fields to `ShKeeperPayoutObservation`;
- TON `message_hashes` are valid provider evidence for automatic callbacks, not just admin UI;
- wallet completion must receive `firstProviderHash(txids, message_hashes)` and tests must assert TON callbacks with empty `txids` still populate wallet `txHash` from `message_hashes`.

- [ ] **Step 4: Review the spec diff**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git diff -- docs/superpowers/specs/2026-06-09-payout-sidecar-reliability-design.md
```

Expected:

```text
Spec now describes durable orphan recovery, narrow DBAPIError handling,
global sidecar_payload_hash safety, and TON message_hash wallet evidence.
```

---

## Task 1: TON Store Recovery Contract

**Files:**
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_boundaries.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_contract.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/config.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/payout_execution.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/api/payout.py`

- [ ] **Step 1: Add failing TON tests for transient task and orphan recovery**

Append these tests to `TonPayoutExecutionBoundaryTests` in `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_boundaries.py`:

In `setUp()`, set the orphan age threshold explicitly:

```python
        config["PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC"] = 60
```

The positive orphan recovery tests must make the row old by setting
`state_updated_at` older than that threshold. Fresh no-evidence rows must not
enqueue.

```python
    def test_task_owned_transient_failure_retries_received_without_mutation(self):
        self.create_execution()

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "retry")
        self.assertEqual(row.state, "RECEIVED")
        self.assertIsNone(row.lease_owner)
        self.assertIsNone(row.attempt_id)
        self.assertFalse(row.reconciliation_required)

    def test_task_owned_transient_failure_resets_signing_without_unsafe_evidence(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            lease_owner="task-1",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
            attempt_id="attempt-1",
        )

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "retry")
        self.assertEqual(row.state, "RECEIVED")
        self.assertIsNone(row.lease_owner)
        self.assertIsNone(row.lease_expires_at)
        self.assertIsNone(row.attempt_id)
        self.assertFalse(row.reconciliation_required)

    def test_task_owned_transient_failure_does_not_retry_signing_with_seqno(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            lease_owner="task-1",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
            attempt_id="attempt-1",
            source_seqno=101,
        )

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "raise")
        self.assertEqual(row.state, "SIGNING")
        self.assertEqual(row.source_seqno, 101)
        self.assertEqual(row.lease_owner, "task-1")

    def test_task_owned_transient_failure_does_not_steal_other_worker_signing(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            lease_owner="task-other",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
            attempt_id="attempt-other",
        )

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "raise")
        self.assertEqual(row.state, "SIGNING")
        self.assertEqual(row.lease_owner, "task-other")
        self.assertEqual(row.attempt_id, "attempt-other")

    def test_status_is_read_only_for_received_orphan(self):
        self.create_execution()
        self.set_execution_fields(state_updated_at="2026-01-01T00:00:00.000000Z")
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.status(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        enqueue.assert_not_called()

    def test_recover_orphan_reenqueues_received_without_unsafe_evidence(self):
        self.create_execution()
        self.set_execution_fields(state_updated_at="2026-01-01T00:00:00.000000Z")
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        row = self.get_execution()
        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], True)
        self.assertEqual(row.state, "RECEIVED")
        enqueue.assert_called_once_with(self.execution_id, row.payout_queue)

    def test_recover_orphan_does_not_reenqueue_fresh_received(self):
        self.create_execution()
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "not_old_enough")
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_active_lease(self):
        self.create_execution()
        self.set_execution_fields(
            state="VALIDATED",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            lease_owner="worker-active",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        self.assertEqual(status["state"], "VALIDATED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "active_lease")
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_when_unsafe_evidence_exists(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            source_seqno=101,
            message_hashes_json='["message-hash-present"]',
            lease_owner=None,
            lease_expires_at=None,
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        self.assertEqual(status["state"], "SIGNING")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_when_message_hash_list_exists(self):
        self.create_execution()
        self.set_execution_fields(
            state="RECEIVED",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            message_hashes_json='["message-hash-present"]',
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="TON-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "unsafe_evidence_exists")
        enqueue.assert_not_called()
```

- [ ] **Step 2: Run the TON boundary tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan" -q
```

Expected:

```text
FAILED ... AttributeError: type object 'PayoutExecutionStore' has no attribute 'recover_task_owned_transient_failure'
```

- [ ] **Step 3: Add transient DB classification and task-owned recovery to TON store**

In `/Users/test/PycharmProjects/ton-shkeeper/app/config.py`, add a central config value near the other payout execution settings:

```python
    'PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC': int(os.environ.get('PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC', '300')),
```

In `/Users/test/PycharmProjects/ton-shkeeper/app/payout_execution.py`, change the imports:

```python
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, PendingRollbackError, SQLAlchemyError
```

Add this helper after `maybe_int()`:

```python
def is_transient_db_error(exc):
    if isinstance(exc, (OperationalError, PendingRollbackError)):
        return True
    return isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False)
```

Update `_has_unsafe_side_effect()` so non-empty `message_hashes_json` is also unsafe evidence. Convert it from `@staticmethod` to `@classmethod` if needed so it can reuse the JSON parsing helper below.

Add these helpers near `_lease_expired()`:

```python
    @staticmethod
    def _message_hashes(row):
        try:
            return json.loads(row.message_hashes_json or "[]")
        except (TypeError, ValueError):
            return []

    @classmethod
    def _orphan_recovery_old_enough(cls, row):
        updated_at = parse_iso(row.state_updated_at)
        if updated_at is None:
            return False
        min_age = int(config["PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC"])
        return (utc_now_naive() - updated_at).total_seconds() >= min_age
```

Add this class method to `PayoutExecutionStore` after `recover_stale_signing()`:

```python
    @classmethod
    def recover_task_owned_transient_failure(cls, execution_id, *, lease_owner):
        row = cls._get_row(execution_id)
        if row is None:
            return "raise"
        if row.state in NO_DOWNGRADE_STATES:
            return "raise"
        if row.state in (STATE_RECEIVED, STATE_VALIDATED):
            return "retry"
        if (
            row.state == STATE_SIGNING
            and row.lease_owner == lease_owner
            and not cls._has_unsafe_side_effect(row)
        ):
            cls._transition(
                row,
                STATE_RECEIVED,
                lease_owner=None,
                lease_expires_at=None,
                attempt_id=None,
                reconciliation_required=False,
            )
            return "retry"
        return "raise"
```

Add this explicit orphan recovery method after `recover_task_owned_transient_failure()`:

```python
    @classmethod
    def recover_orphan_execution(cls, execution_id, *, authenticated_consumer, endpoint_symbol):
        row = PayoutExecution.query.filter_by(
            execution_id=str(execution_id),
            consumer=authenticated_consumer,
        ).first()
        if row is None:
            raise PayoutExecutionError(
                "Payout execution was not created",
                code="NO_EXECUTION_CREATED",
                status_code=404,
            )
        recovery = {
            "attempted": True,
            "enqueued": False,
            "reason": None,
        }
        if row.state not in (STATE_RECEIVED, STATE_VALIDATED):
            recovery["reason"] = "state_not_recoverable"
        elif cls._has_unsafe_side_effect(row):
            recovery["reason"] = "unsafe_evidence_exists"
        elif row.lease_owner and not cls._lease_expired(row):
            recovery["reason"] = "active_lease"
        elif not cls._orphan_recovery_old_enough(row):
            recovery["reason"] = "not_old_enough"
        else:
            cls.enqueue_execution(row.execution_id, row.payout_queue)
            recovery["enqueued"] = True
            recovery["reason"] = "enqueued"
        status = cls._row_to_status(row)
        status["orphan_recovery"] = recovery
        return status
```

This is intentionally separate from `status()`. `GET /payout-executions/{id}` must not enqueue orphan work, rebuild signed payloads, or rebroadcast; only the explicit recovery endpoint may enqueue.

- [ ] **Step 4: Ensure transient SQLAlchemy errors are not converted to payout terminal states**

In `/Users/test/PycharmProjects/ton-shkeeper/app/payout_execution.py`, update both broad exception handlers inside `execute()` so transient DB errors are re-raised first.

For the first transition block, replace:

```python
        except PayoutExecutionError as exc:
            if exc.code != "PAYOUT_EXECUTION_CAS_CONFLICT":
                raise
            row = cls._get_row(execution_id)
            return cls._row_to_status(row)
```

with:

```python
        except PayoutExecutionError as exc:
            if exc.code != "PAYOUT_EXECUTION_CAS_CONFLICT":
                raise
            row = cls._get_row(execution_id)
            return cls._row_to_status(row)
        except SQLAlchemyError as exc:
            if is_transient_db_error(exc):
                raise
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

For the signing/broadcast block, replace:

```python
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

with:

```python
        except SQLAlchemyError as exc:
            if is_transient_db_error(exc):
                raise
            return cls._mark_failed_or_reconciliation(execution_id, exc)
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

- [ ] **Step 5: Add the TON orphan recovery API endpoint**

In `/Users/test/PycharmProjects/ton-shkeeper/app/api/payout.py`, add this helper after `_status_response()`:

```python
def _recover_orphan_response(execution_id):
    try:
        return PayoutExecutionStore.recover_orphan_execution(
            execution_id,
            authenticated_consumer=g.payout_consumer,
            endpoint_symbol=g.symbol,
        ), 202
    except PayoutExecutionError as exc:
        return _payout_error_response(exc, "recover_orphan")
```

Add the route after `payout_execution_v1_status()`:

```python
@api.post("/payout-executions/<execution_id>/recover-orphan")
@payout_auth_required
def payout_execution_v1_recover_orphan(execution_id):
    return _recover_orphan_response(execution_id)
```

Extend `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_contract.py` with route-level tests for the new endpoint:

- valid signed request to `/TON-USDT/payout-executions/{id}/recover-orphan` returns `202` and an `orphan_recovery` object;
- missing or bad payout auth is rejected;
- unknown execution returns the existing signed API error shape;
- fresh no-evidence execution returns `enqueued=false` / `reason=not_old_enough`;
- active-lease execution returns `enqueued=false` / `reason=active_lease`;
- unsafe-evidence execution returns `enqueued=false` and does not call enqueue.

- [ ] **Step 6: Run the TON boundary tests and verify they pass**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan or stale_signing" -q
python -m pytest tests/test_payout_execution_contract.py \
  -k "recover_orphan" -q
```

Expected:

```text
... passed
```

- [ ] **Step 7: Commit TON store recovery contract**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
git add app/config.py app/payout_execution.py app/api/payout.py tests/test_payout_execution_boundaries.py
git add tests/test_payout_execution_contract.py
git commit -m "fix: recover ton payout tasks after transient db failures"
```

Expected:

```text
[... fix: recover ton payout tasks after transient db failures]
```

---

## Task 2: TON Celery Task Guard

**Files:**
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/tasks.py`
- Create: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_task_retries.py`

- [ ] **Step 1: Add failing TON task-boundary tests**

Create `/Users/test/PycharmProjects/ton-shkeeper/tests/test_payout_execution_task_retries.py`:

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.exc import OperationalError, PendingRollbackError

from tests.test_payout_execution_contract import (
    CONSUMER,
    KEY_ID,
    SECRET,
    TEST_DATABASE,
    payload,
    reset_modules,
)


class RetryRequested(Exception):
    pass


class FakeRequest:
    id = "task-123"
    retries = 0


class FakeTask:
    request = FakeRequest()

    def __init__(self):
        self.retry_call = None

    def retry(self, *, exc, countdown):
        self.retry_call = {"exc": exc, "countdown": countdown}
        raise RetryRequested()


class TonPayoutExecutionTaskRetryTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DATABASE):
            os.unlink(TEST_DATABASE)

        from app.config import config

        config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{TEST_DATABASE}"
        config["PAYOUT_CONSUMER_KEYS"] = {
            CONSUMER: {
                "rails": ["TON-USDT"],
                "keys": {KEY_ID: SECRET},
            }
        }
        config["PAYOUT_AUTH_MAX_AGE_SECONDS"] = 300
        config["TON_USDT_PAYOUT_QUEUE"] = "ton_usdt_payouts"
        config["PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED"] = False
        config["PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED"] = False
        reset_modules()
        import sys

        sys.modules.pop("app.tasks", None)

        from app import create_app
        from app.db_import import db
        import werkzeug

        if not hasattr(werkzeug, "__version__"):
            werkzeug.__version__ = "3"

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.db = db
        db.drop_all()
        db.create_all()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.db.engine.dispose()
        self.ctx.pop()
        if os.path.exists(TEST_DATABASE):
            os.unlink(TEST_DATABASE)

    def test_operational_error_rolls_back_removes_session_and_retries_when_store_allows_retry(self):
        from app import tasks
        from app.models import db

        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()

        with patch("app.tasks.Coin", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="retry",
                ) as recover:
                    with patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback:
                        with patch.object(db.session, "remove", wraps=db.session.remove) as remove:
                            with self.assertRaises(RetryRequested):
                                tasks.run_execute_payout_execution(fake_task, "30")

        recover.assert_called_once_with("30", lease_owner="task-123")
        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertEqual(fake_task.retry_call["countdown"], 5)
        self.assertGreaterEqual(rollback.call_count, 1)
        self.assertGreaterEqual(remove.call_count, 1)

    def test_pending_rollback_error_retries_after_session_cleanup(self):
        from app import tasks

        exc = PendingRollbackError("Can't reconnect until invalid transaction is rolled back")
        fake_task = FakeTask()

        with patch("app.tasks.Coin", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="retry",
                ):
                    with self.assertRaises(RetryRequested):
                        tasks.run_execute_payout_execution(fake_task, "30")

        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertEqual(fake_task.retry_call["countdown"], 5)

    def test_transient_db_error_is_not_retried_when_store_detects_unsafe_evidence(self):
        from app import tasks

        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()

        with patch("app.tasks.Coin", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="raise",
                ):
                    with self.assertRaises(OperationalError):
                        tasks.run_execute_payout_execution(fake_task, "30")

        self.assertIsNone(fake_task.retry_call)

    def test_first_row_load_operational_error_is_retried_without_mutating_execution(self):
        from app import tasks
        from app.payout_execution import PayoutExecutionStore

        accepted = PayoutExecutionStore.submit(
            payload(external_id="WD-first-row-load"),
            authenticated_consumer=CONSUMER,
            endpoint_symbol="TON-USDT",
        )
        execution_id = accepted["execution_id"]
        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()
        original_get_row = PayoutExecutionStore._get_row
        calls = {"count": 0}

        def flaky_get_row(execution_id):
            calls["count"] += 1
            if calls["count"] == 1:
                raise exc
            return original_get_row(execution_id)

        with patch("app.tasks.Coin", return_value=Mock()):
            with patch.object(PayoutExecutionStore, "_get_row", side_effect=flaky_get_row):
                with self.assertRaises(RetryRequested):
                    tasks.run_execute_payout_execution(fake_task, execution_id)

        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertGreaterEqual(calls["count"], 2)
        row = PayoutExecutionStore._get_row(execution_id)
        self.assertEqual(row.state, "RECEIVED")
```

- [ ] **Step 2: Run the TON task tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
FAILED ... AttributeError: module 'app.tasks' has no attribute 'run_execute_payout_execution'
```

- [ ] **Step 3: Implement the TON task guard**

In `/Users/test/PycharmProjects/ton-shkeeper/app/tasks.py`, add imports near the top:

```python
from sqlalchemy.exc import SQLAlchemyError
```

Add these helpers above the Celery task:

```python
def _db_retry_countdown(retries):
    return min(5 * (2 ** max(retries, 0)), 60)


def _cleanup_db_session():
    try:
        db.session.rollback()
    except Exception:
        logger.warning("TON payout task db rollback failed", exc_info=True)
    finally:
        db.session.remove()


def run_execute_payout_execution(task, execution_id):
    from .payout_execution import PayoutExecutionStore, is_transient_db_error

    try:
        db.session.rollback()
    except Exception:
        logger.warning("TON payout task initial db rollback failed", exc_info=True)

    try:
        coin = Coin("TON-USDT")
        return PayoutExecutionStore.execute(
            execution_id,
            coin=coin,
            lock_factory=ton_usdt_payout_seqno_lock,
            lease_owner=task.request.id,
        )
    except SQLAlchemyError as exc:
        if not is_transient_db_error(exc):
            raise
        _cleanup_db_session()
        try:
            action = PayoutExecutionStore.recover_task_owned_transient_failure(
                execution_id,
                lease_owner=task.request.id,
            )
        except SQLAlchemyError as recovery_exc:
            if not is_transient_db_error(recovery_exc):
                raise
            _cleanup_db_session()
            action = "retry"
        if action == "retry":
            raise task.retry(
                exc=exc,
                countdown=_db_retry_countdown(getattr(task.request, "retries", 0)),
            )
        raise
    finally:
        db.session.remove()
```

Replace the body of `execute_payout_execution()` with:

```python
@celery.task(bind=True, max_retries=5)
def execute_payout_execution(self, execution_id):
    return run_execute_payout_execution(self, execution_id)
```

- [ ] **Step 4: Run the TON task tests and focused boundary tests**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest \
  tests/test_payout_execution_task_retries.py \
  tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan or stale_signing" -q
```

Expected:

```text
... passed
```

- [ ] **Step 5: Run full TON payout test subset**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Commit TON task guard**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
git add app/tasks.py tests/test_payout_execution_task_retries.py
git commit -m "fix: retry ton payout task after transient db disconnect"
```

Expected:

```text
[... fix: retry ton payout task after transient db disconnect]
```

---

## Task 3: ETH Store Recovery Contract

**Files:**
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_boundaries.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_contract.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/config.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/payout_execution.py`
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/api/payout.py`

- [ ] **Step 1: Add failing ETH tests for transient task and orphan recovery**

Append these tests to `EthPayoutExecutionBoundaryTests` in `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_boundaries.py`:

In `setUp()`, set the orphan age threshold explicitly:

```python
        config["PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC"] = 60
```

The positive orphan recovery tests must make the row old by setting
`state_updated_at` older than that threshold. Fresh no-evidence rows must not
enqueue.

```python
    def test_task_owned_transient_failure_retries_received_without_mutation(self):
        self.create_execution()

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "retry")
        self.assertEqual(row.state, "RECEIVED")
        self.assertIsNone(row.lease_owner)
        self.assertIsNone(row.attempt_id)
        self.assertFalse(row.reconciliation_required)

    def test_task_owned_transient_failure_resets_signing_without_unsafe_evidence(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            lease_owner="task-1",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
            attempt_id="attempt-1",
        )

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "retry")
        self.assertEqual(row.state, "RECEIVED")
        self.assertIsNone(row.lease_owner)
        self.assertIsNone(row.lease_expires_at)
        self.assertIsNone(row.attempt_id)
        self.assertFalse(row.reconciliation_required)

    def test_task_owned_transient_failure_does_not_retry_signing_with_nonce(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            lease_owner="task-1",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
            attempt_id="attempt-1",
            nonce=101,
        )

        action = self.store_module.PayoutExecutionStore.recover_task_owned_transient_failure(
            self.execution_id,
            lease_owner="task-1",
        )

        row = self.get_execution()
        self.assertEqual(action, "raise")
        self.assertEqual(row.state, "SIGNING")
        self.assertEqual(row.nonce, 101)
        self.assertEqual(row.lease_owner, "task-1")

    def test_status_is_read_only_for_received_orphan(self):
        self.create_execution()
        self.set_execution_fields(state_updated_at="2026-01-01T00:00:00.000000Z")
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.status(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        enqueue.assert_not_called()

    def test_recover_orphan_reenqueues_received_without_unsafe_evidence(self):
        self.create_execution()
        self.set_execution_fields(state_updated_at="2026-01-01T00:00:00.000000Z")
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        row = self.get_execution()
        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], True)
        self.assertEqual(row.state, "RECEIVED")
        enqueue.assert_called_once_with(self.execution_id, row.payout_queue)

    def test_recover_orphan_does_not_reenqueue_fresh_received(self):
        self.create_execution()
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "not_old_enough")
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_active_lease(self):
        self.create_execution()
        self.set_execution_fields(
            state="VALIDATED",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            lease_owner="worker-active",
            lease_expires_at="2999-01-01T00:00:00.000000Z",
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        self.assertEqual(status["state"], "VALIDATED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "active_lease")
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_when_unsafe_evidence_exists(self):
        self.create_execution()
        self.set_execution_fields(
            state="SIGNING",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            nonce=101,
            tx_hashes_json='["tx-hash-present"]',
            lease_owner=None,
            lease_expires_at=None,
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        self.assertEqual(status["state"], "SIGNING")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        enqueue.assert_not_called()

    def test_recover_orphan_does_not_reenqueue_when_tx_hash_list_exists(self):
        self.create_execution()
        self.set_execution_fields(
            state="RECEIVED",
            state_updated_at="2026-01-01T00:00:00.000000Z",
            tx_hashes_json='["tx-hash-present"]',
        )
        store = self.store_module.PayoutExecutionStore

        with patch.object(store, "enqueue_execution") as enqueue:
            status = store.recover_orphan_execution(
                self.execution_id,
                authenticated_consumer=CONSUMER,
                endpoint_symbol="ETH-USDT",
            )

        self.assertEqual(status["state"], "RECEIVED")
        self.assertEqual(status["orphan_recovery"]["enqueued"], False)
        self.assertEqual(status["orphan_recovery"]["reason"], "unsafe_evidence_exists")
        enqueue.assert_not_called()
```

- [ ] **Step 2: Run the ETH boundary tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan" -q
```

Expected:

```text
FAILED ... AttributeError: type object 'PayoutExecutionStore' has no attribute 'recover_task_owned_transient_failure'
```

- [ ] **Step 3: Add transient DB classification and task-owned recovery to ETH store**

In `/Users/test/PycharmProjects/ethereum-shkeeper/app/config.py`, add a central config value near the other payout execution settings:

```python
    'PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC': int(os.environ.get('PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC', '300')),
```

In `/Users/test/PycharmProjects/ethereum-shkeeper/app/payout_execution.py`, change the imports:

```python
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, PendingRollbackError, SQLAlchemyError
```

Add this helper after `maybe_int()`:

```python
def is_transient_db_error(exc):
    if isinstance(exc, (OperationalError, PendingRollbackError)):
        return True
    return isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False)
```

Update `_has_unsafe_side_effect()` so non-empty tx hash list storage, if present in the ETH sidecar schema/status, is also unsafe evidence. Convert it from `@staticmethod` to `@classmethod` if needed so it can reuse a JSON parsing helper.

Add these helpers near `_lease_expired()`:

```python
    @staticmethod
    def _stored_tx_hashes(row):
        for attr in ("tx_hashes_json", "txids_json"):
            raw = getattr(row, attr, None)
            if raw:
                try:
                    values = json.loads(raw or "[]")
                except (TypeError, ValueError):
                    values = []
                if values:
                    return values
        return []

    @classmethod
    def _orphan_recovery_old_enough(cls, row):
        updated_at = parse_iso(row.state_updated_at)
        if updated_at is None:
            return False
        min_age = int(config["PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC"])
        return (utc_now_naive() - updated_at).total_seconds() >= min_age
```

Add this class method to `PayoutExecutionStore` after `recover_stale_signing()`:

```python
    @classmethod
    def recover_task_owned_transient_failure(cls, execution_id, *, lease_owner):
        row = cls._get_row(execution_id)
        if row is None:
            return "raise"
        if row.state in NO_DOWNGRADE_STATES:
            return "raise"
        if row.state in (STATE_RECEIVED, STATE_VALIDATED):
            return "retry"
        if (
            row.state == STATE_SIGNING
            and row.lease_owner == lease_owner
            and not cls._has_unsafe_side_effect(row)
        ):
            cls._transition(
                row,
                STATE_RECEIVED,
                lease_owner=None,
                lease_expires_at=None,
                attempt_id=None,
                reconciliation_required=False,
            )
            return "retry"
        return "raise"
```

Add this explicit orphan recovery method after `recover_task_owned_transient_failure()`:

```python
    @classmethod
    def recover_orphan_execution(cls, execution_id, *, authenticated_consumer, endpoint_symbol):
        row = PayoutExecution.query.filter_by(
            execution_id=str(execution_id),
            consumer=authenticated_consumer,
        ).first()
        if row is None:
            raise PayoutExecutionError(
                "Payout execution was not created",
                code="NO_EXECUTION_CREATED",
                status_code=404,
            )
        recovery = {
            "attempted": True,
            "enqueued": False,
            "reason": None,
        }
        if row.state not in (STATE_RECEIVED, STATE_VALIDATED):
            recovery["reason"] = "state_not_recoverable"
        elif cls._has_unsafe_side_effect(row):
            recovery["reason"] = "unsafe_evidence_exists"
        elif row.lease_owner and not cls._lease_expired(row):
            recovery["reason"] = "active_lease"
        elif not cls._orphan_recovery_old_enough(row):
            recovery["reason"] = "not_old_enough"
        else:
            cls.enqueue_execution(row.execution_id, row.payout_queue)
            recovery["enqueued"] = True
            recovery["reason"] = "enqueued"
        status = cls._row_to_status(row)
        status["orphan_recovery"] = recovery
        return status
```

As with TON, this is intentionally separate from `status()`. `GET /payout-executions/{id}` must not enqueue orphan work, rebuild signed payloads, or rebroadcast.

- [ ] **Step 4: Re-raise transient SQLAlchemy errors from ETH `execute()`**

In `/Users/test/PycharmProjects/ethereum-shkeeper/app/payout_execution.py`, update both broad exception handlers inside `execute()`.

For the first transition block, replace:

```python
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

with:

```python
        except SQLAlchemyError as exc:
            if is_transient_db_error(exc):
                raise
            return cls._mark_failed_or_reconciliation(execution_id, exc)
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

For the signing/broadcast block, replace:

```python
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

with:

```python
        except SQLAlchemyError as exc:
            if is_transient_db_error(exc):
                raise
            return cls._mark_failed_or_reconciliation(execution_id, exc)
        except Exception as exc:
            return cls._mark_failed_or_reconciliation(execution_id, exc)
```

- [ ] **Step 5: Add the ETH orphan recovery API endpoint**

In `/Users/test/PycharmProjects/ethereum-shkeeper/app/api/payout.py`, add this helper after `payout_execution_status()` or near the existing payout execution helpers:

```python
def _recover_orphan_response(execution_id):
    try:
        return PayoutExecutionStore.recover_orphan_execution(
            execution_id,
            authenticated_consumer=g.payout_consumer,
            endpoint_symbol=g.symbol,
        ), 202
    except PayoutExecutionError as exc:
        return _payout_error_response(exc, "recover_orphan")
```

Add the route near the existing v1 payout execution routes:

```python
@api.post("/payout-executions/<execution_id>/recover-orphan")
@payout_auth_required
def payout_execution_recover_orphan(execution_id):
    return _recover_orphan_response(execution_id)
```

Extend `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_contract.py` with route-level tests for the new endpoint:

- valid signed request to `/ETH-USDT/payout-executions/{id}/recover-orphan` returns `202` and an `orphan_recovery` object;
- missing or bad payout auth is rejected;
- unknown execution returns the existing signed API error shape;
- fresh no-evidence execution returns `enqueued=false` / `reason=not_old_enough`;
- active-lease execution returns `enqueued=false` / `reason=active_lease`;
- unsafe-evidence execution returns `enqueued=false` and does not call enqueue.

- [ ] **Step 6: Run the ETH boundary tests and verify they pass**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan or stale_signing" -q
python -m pytest tests/test_payout_execution_contract.py \
  -k "recover_orphan" -q
```

Expected:

```text
... passed
```

- [ ] **Step 7: Commit ETH store recovery contract**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
git add app/config.py app/payout_execution.py app/api/payout.py tests/test_payout_execution_boundaries.py
git add tests/test_payout_execution_contract.py
git commit -m "fix: recover eth payout tasks after transient db failures"
```

Expected:

```text
[... fix: recover eth payout tasks after transient db failures]
```

---

## Task 4: ETH Celery Task Guard

**Files:**
- Modify: `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`
- Create: `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_task_retries.py`

- [ ] **Step 1: Add failing ETH task-boundary tests**

Create `/Users/test/PycharmProjects/ethereum-shkeeper/tests/test_payout_execution_task_retries.py`:

```python
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.exc import OperationalError, PendingRollbackError

from tests.test_payout_execution_contract import (
    CONSUMER,
    KEY_ID,
    SECRET,
    TEST_DATABASE,
    payload,
    reset_modules,
)


class RetryRequested(Exception):
    pass


class FakeRequest:
    id = "task-eth-123"
    retries = 0


class FakeTask:
    request = FakeRequest()

    def __init__(self):
        self.retry_call = None

    def retry(self, *, exc, countdown):
        self.retry_call = {"exc": exc, "countdown": countdown}
        raise RetryRequested()


class EthPayoutExecutionTaskRetryTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_DATABASE):
            os.unlink(TEST_DATABASE)

        from app.config import config

        config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{TEST_DATABASE}"
        config["PAYOUT_CONSUMER_KEYS"] = {
            CONSUMER: {
                "rails": ["ETH-USDT"],
                "keys": {KEY_ID: SECRET},
            }
        }
        config["PAYOUT_AUTH_MAX_AGE_SECONDS"] = 300
        config["ETH_USDT_PAYOUT_QUEUE"] = "eth_usdt_payouts"
        config["PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED"] = False
        config["PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED"] = False
        config["PAYOUT_EXECUTION_REQUIRE_AUTO_ENQUEUE"] = False
        reset_modules()
        import sys

        sys.modules.pop("app.tasks", None)

        from app import create_app
        from app.db_import import db
        import werkzeug

        if not hasattr(werkzeug, "__version__"):
            werkzeug.__version__ = "3"

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.db = db
        db.drop_all()
        db.create_all()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.db.engine.dispose()
        self.ctx.pop()
        if os.path.exists(TEST_DATABASE):
            os.unlink(TEST_DATABASE)

    def test_operational_error_rolls_back_removes_session_and_retries_when_store_allows_retry(self):
        from app import tasks
        from app.models import db

        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()

        with patch("app.tasks.Token", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="retry",
                ) as recover:
                    with patch.object(db.session, "rollback", wraps=db.session.rollback) as rollback:
                        with patch.object(db.session, "remove", wraps=db.session.remove) as remove:
                            with self.assertRaises(RetryRequested):
                                tasks.run_execute_payout_execution(fake_task, "30")

        recover.assert_called_once_with("30", lease_owner="task-eth-123")
        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertEqual(fake_task.retry_call["countdown"], 5)
        self.assertGreaterEqual(rollback.call_count, 1)
        self.assertGreaterEqual(remove.call_count, 1)

    def test_pending_rollback_error_retries_after_session_cleanup(self):
        from app import tasks

        exc = PendingRollbackError("Can't reconnect until invalid transaction is rolled back")
        fake_task = FakeTask()

        with patch("app.tasks.Token", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="retry",
                ):
                    with self.assertRaises(RetryRequested):
                        tasks.run_execute_payout_execution(fake_task, "30")

        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertEqual(fake_task.retry_call["countdown"], 5)

    def test_transient_db_error_is_not_retried_when_store_detects_unsafe_evidence(self):
        from app import tasks

        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()

        with patch("app.tasks.Token", return_value=Mock()):
            with patch("app.payout_execution.PayoutExecutionStore.execute", side_effect=exc):
                with patch(
                    "app.payout_execution.PayoutExecutionStore.recover_task_owned_transient_failure",
                    return_value="raise",
                ):
                    with self.assertRaises(OperationalError):
                        tasks.run_execute_payout_execution(fake_task, "30")

        self.assertIsNone(fake_task.retry_call)

    def test_first_row_load_operational_error_is_retried_without_mutating_execution(self):
        from app import tasks
        from app.payout_execution import PayoutExecutionStore

        accepted = PayoutExecutionStore.submit(
            payload(external_id="WD-first-row-load"),
            authenticated_consumer=CONSUMER,
            endpoint_symbol="ETH-USDT",
        )
        execution_id = accepted["execution_id"]
        exc = OperationalError("select 1", {}, RuntimeError("server has gone away"))
        fake_task = FakeTask()
        original_get_row = PayoutExecutionStore._get_row
        calls = {"count": 0}

        def flaky_get_row(execution_id):
            calls["count"] += 1
            if calls["count"] == 1:
                raise exc
            return original_get_row(execution_id)

        with patch("app.tasks.Token", return_value=Mock()):
            with patch.object(PayoutExecutionStore, "_get_row", side_effect=flaky_get_row):
                with self.assertRaises(RetryRequested):
                    tasks.run_execute_payout_execution(fake_task, execution_id)

        self.assertIs(fake_task.retry_call["exc"], exc)
        self.assertGreaterEqual(calls["count"], 2)
        row = PayoutExecutionStore._get_row(execution_id)
        self.assertEqual(row.state, "RECEIVED")
```

- [ ] **Step 2: Run the ETH task tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
FAILED ... AttributeError: module 'app.tasks' has no attribute 'run_execute_payout_execution'
```

- [ ] **Step 3: Implement the ETH task guard**

In `/Users/test/PycharmProjects/ethereum-shkeeper/app/tasks.py`, add imports near the top:

```python
from sqlalchemy.exc import SQLAlchemyError
```

Add these helpers above the Celery task:

```python
def _db_retry_countdown(retries):
    return min(5 * (2 ** max(retries, 0)), 60)


def _cleanup_db_session():
    try:
        db.session.rollback()
    except Exception:
        logger.warning("ETH payout task db rollback failed", exc_info=True)
    finally:
        db.session.remove()


def run_execute_payout_execution(task, execution_id):
    from .payout_execution import PayoutExecutionStore, is_transient_db_error

    try:
        db.session.rollback()
    except Exception:
        logger.warning("ETH payout task initial db rollback failed", exc_info=True)

    try:
        token = Token("ETH-USDT")
        return PayoutExecutionStore.execute(
            execution_id,
            token=token,
            lock_factory=eth_usdt_payout_nonce_lock,
            lease_owner=task.request.id,
        )
    except SQLAlchemyError as exc:
        if not is_transient_db_error(exc):
            raise
        _cleanup_db_session()
        try:
            action = PayoutExecutionStore.recover_task_owned_transient_failure(
                execution_id,
                lease_owner=task.request.id,
            )
        except SQLAlchemyError as recovery_exc:
            if not is_transient_db_error(recovery_exc):
                raise
            _cleanup_db_session()
            action = "retry"
        if action == "retry":
            raise task.retry(
                exc=exc,
                countdown=_db_retry_countdown(getattr(task.request, "retries", 0)),
            )
        raise
    finally:
        db.session.remove()
```

Replace the body of `execute_payout_execution()` with:

```python
@celery.task(bind=True, max_retries=5)
def execute_payout_execution(self, execution_id):
    return run_execute_payout_execution(self, execution_id)
```

- [ ] **Step 4: Run the ETH task and boundary tests**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest \
  tests/test_payout_execution_task_retries.py \
  tests/test_payout_execution_boundaries.py \
  -k "task_owned_transient_failure or orphan or stale_signing" -q
```

Expected:

```text
... passed
```

- [ ] **Step 5: Run full ETH payout test subset**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Commit ETH task guard**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
git add app/tasks.py tests/test_payout_execution_task_retries.py
git commit -m "fix: retry eth payout task after transient db disconnect"
```

Expected:

```text
[... fix: retry eth payout task after transient db disconnect]
```

---

## Task 5: SHKeeper Core Active Polling Retry Cap

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper.io/tests/test_payout_execution_reconciler.py`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/__init__.py`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_execution_reconciler.py`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_sidecar_client.py`

- [ ] **Step 1: Tighten the active polling retry test**

In `/Users/test/PycharmProjects/shkeeper.io/tests/test_payout_execution_reconciler.py`, add this helper inside `PayoutExecutionReconcilerTestCase`:

Add this import near the top if it is not already present:

```python
from unittest.mock import patch
```

```python
    def seconds_until_next_dispatch(self, execution):
        return (execution.next_dispatch_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
```

In `test_enqueued_status_unavailable_retries_without_reconciliation`, add these assertions after `self.assertIsNotNone(execution.next_dispatch_at)`:

```python
        self.assertLessEqual(self.seconds_until_next_dispatch(execution), 305)
        self.assertGreaterEqual(self.seconds_until_next_dispatch(execution), 250)
```

Add this new test to the same class:

```python
    def test_enqueued_status_unavailable_uses_polling_cap_after_many_attempts(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        execution.dispatch_attempts = 469
        execution.next_dispatch_at = None
        db.session.commit()

        client.raise_on_status = SidecarStatusUnavailable("status timeout")
        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertIsNotNone(execution.next_dispatch_at)
        self.assertLessEqual(self.seconds_until_next_dispatch(execution), 305)
        self.assertGreaterEqual(self.seconds_until_next_dispatch(execution), 250)

    def test_broadcast_status_unavailable_uses_polling_cap_after_many_attempts(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "BROADCASTED",
            "sidecar_state_version": 2,
            "sidecar_state_transition_id": "sidecar-transition-2",
            "state_updated_at": "2026-06-03T10:01:00Z",
            "txids": ["tx-2"],
        }
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.BROADCAST)
        execution.dispatch_attempts = 469
        execution.next_dispatch_at = None
        db.session.commit()

        client.raise_on_status = SidecarStatusUnavailable("status timeout")
        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual(execution.state, PayoutExecutionState.BROADCAST)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertIsNotNone(execution.next_dispatch_at)
        self.assertLessEqual(self.seconds_until_next_dispatch(execution), 305)
        self.assertGreaterEqual(self.seconds_until_next_dispatch(execution), 250)
```

Extend `FakeSidecarClient` in the same test file:

```python
        self.recover_orphan_response = dict(self.status_response)
        self.raise_on_recover_orphan = None
```

and add this method:

```python
    def recover_orphan(self, execution):
        self.calls.append(("recover_orphan", execution.id, execution.state.name))
        if self.raise_on_recover_orphan:
            raise self.raise_on_recover_orphan
        return self._with_execution_identity(execution, self.recover_orphan_response)
```

Add these orphan recovery tests to `PayoutExecutionReconcilerTestCase`:

```python
    def test_old_received_sidecar_status_triggers_orphan_recovery(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        client.calls = []
        old_received = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
            "txids": [],
            "message_hashes": [],
        }
        client.status_response = old_received
        client.recover_orphan_response = {
            **old_received,
            "orphan_recovery": {
                "attempted": True,
                "enqueued": True,
                "reason": "enqueued",
            },
        }

        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual([call[0] for call in client.calls], ["status", "recover_orphan"])
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertFalse(execution.reconciliation_required)

    def test_received_sidecar_status_with_message_hashes_does_not_recover_orphan(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 2,
            "sidecar_state_transition_id": "sidecar-transition-2",
            "state_updated_at": "2026-06-03T10:00:00Z",
            "txids": [],
            "message_hashes": ["message-hash-present"],
        }

        PayoutExecutionReconciler.dispatch_ready(client=client)

        self.assertEqual([call[0] for call in client.calls], ["status"])

    def test_received_sidecar_status_with_txids_does_not_recover_orphan(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 2,
            "sidecar_state_transition_id": "sidecar-transition-2",
            "state_updated_at": "2026-06-03T10:00:00Z",
            "txids": ["tx-hash-present"],
            "message_hashes": [],
        }

        PayoutExecutionReconciler.dispatch_ready(client=client)

        self.assertEqual([call[0] for call in client.calls], ["status"])

    def test_fresh_received_sidecar_status_does_not_recover_orphan(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": datetime.now(timezone.utc).isoformat(),
            "txids": [],
            "message_hashes": [],
        }

        PayoutExecutionReconciler.dispatch_ready(client=client)

        self.assertEqual([call[0] for call in client.calls], ["status"])

    def test_orphan_recovery_min_age_config_is_honored(self):
        self.app.config["PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC"] = 3600
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
            "txids": [],
            "message_hashes": [],
        }

        with patch.object(
            PayoutExecutionReconciler,
            "_utcnow",
            return_value=datetime(2026, 6, 3, 10, 30, 0),
        ):
            PayoutExecutionReconciler.dispatch_ready(client=client)

        self.assertEqual([call[0] for call in client.calls], ["status"])

    def test_orphan_recovery_failure_keeps_enqueued_with_polling_cap(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        execution.dispatch_attempts = 469
        execution.next_dispatch_at = None
        db.session.commit()
        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
            "txids": [],
            "message_hashes": [],
        }
        client.raise_on_recover_orphan = SidecarStatusUnavailable("recover timeout")

        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual([call[0] for call in client.calls], ["status", "recover_orphan"])
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertLessEqual(self.seconds_until_next_dispatch(execution), 305)
```

- [ ] **Step 2: Run the SHKeeper core tests and verify the new cap test fails**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python -m pytest tests/test_payout_execution_reconciler.py \
  -k "status_unavailable_uses_polling_cap or enqueued_status_unavailable_retries" -q
```

Expected:

```text
FAILED ... AssertionError: ... not less than or equal to 305
```

The failure should happen on the high-attempt test because the current generic dispatcher backoff caps at 3600 seconds.

- [ ] **Step 3: Add a polling-specific retry cap**

In `/Users/test/PycharmProjects/shkeeper.io/shkeeper/__init__.py`, add a central config value near the other payout execution reconciler settings:

```python
        PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC=int(
            os.environ.get("PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC", "300")
        ),
```

In `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_execution_reconciler.py`, import `current_app` if it is not already imported, and add class constants near `POLL_STATES`:

```python
    ACTIVE_POLL_STATUS_RETRY_DELAY_SECONDS = 300
    ORPHAN_RECOVERY_SIDECAR_STATES = ("RECEIVED", "VALIDATED")
```

Add these helpers near `_poll_sidecar_status()`:

```python
    @classmethod
    def _has_unsafe_sidecar_evidence(cls, status):
        evidence_fields = (
            "source_seqno",
            "nonce",
            "signed_boc_ref",
            "signed_boc_hash",
            "signed_raw_tx_ref",
            "signed_raw_tx_hash",
            "message_hash",
            "tx_hash",
            "broadcast_attempted_at",
        )
        if any(status.get(field) for field in evidence_fields):
            return True
        return bool(status.get("txids")) or bool(status.get("message_hashes"))

    @classmethod
    def _should_recover_orphan(cls, execution, status):
        if execution.state != PayoutExecutionState.ENQUEUED:
            return False
        sidecar_state = status.get("sidecar_state") or status.get("state")
        if sidecar_state not in cls.ORPHAN_RECOVERY_SIDECAR_STATES:
            return False
        if cls._has_unsafe_sidecar_evidence(status):
            return False
        sidecar_updated_at = PayoutExecutionService._parse_sidecar_datetime(
            status.get("sidecar_state_updated_at") or status.get("state_updated_at")
        )
        if sidecar_updated_at is None:
            return False
        age_seconds = (cls._utcnow() - sidecar_updated_at).total_seconds()
        min_age = int(
            current_app.config.get("PAYOUT_EXECUTION_ORPHAN_RECOVERY_MIN_AGE_SEC", 300)
        )
        return age_seconds >= min_age
```

Change `_poll_sidecar_status()` from:

```python
    @classmethod
    def _poll_sidecar_status(cls, execution, client):
        try:
            response = client.status(execution)
        except SidecarExecutionNotFound:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_EXECUTION_LOST",
                error_message="Sidecar lost an accepted payout execution",
                reconciliation_required=True,
            )
        return PayoutExecutionService.apply_sidecar_status(execution, response)
```

to:

```python
    @classmethod
    def _poll_sidecar_status(cls, execution, client):
        try:
            response = client.status(execution)
            if cls._should_recover_orphan(execution, response):
                response = client.recover_orphan(execution)
        except SidecarExecutionNotFound:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_EXECUTION_LOST",
                error_message="Sidecar lost an accepted payout execution",
                reconciliation_required=True,
            )
        except SidecarStatusUnavailable as exc:
            execution.error_code = "PAYOUT_DISPATCH_EXCEPTION"
            execution.error_message = str(exc)
            execution.next_dispatch_at = cls._utcnow() + timedelta(
                seconds=cls.ACTIVE_POLL_STATUS_RETRY_DELAY_SECONDS
            )
            db.session.add(execution)
            db.session.commit()
            return execution
        return PayoutExecutionService.apply_sidecar_status(execution, response)
```

Do not change `release_execution_after_error()`, `_submit()`, or `_recover_enqueueing()` behavior. Generic exceptions in active polling still use the existing exponential dispatch-error path; only `SidecarStatusUnavailable` gets the 300-second active polling cap. `SidecarSubmitTimeout` and status-unavailable during `ENQUEUEING` remain ambiguous and must still move to reconciliation.

- [ ] **Step 4: Add the SHKeeper sidecar orphan recovery client call**

In `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_sidecar_client.py`, add this method to `HttpPayoutSidecarClient` after `status()`:

```python
    def recover_orphan(self, execution):
        suffix = f"/payout-executions/{execution.id}/recover-orphan"
        path = f"/{execution.sidecar_symbol}{suffix}"
        body = self._compact_body({})
        try:
            response = requests.post(
                self._url(execution, suffix),
                auth=self._auth(execution),
                data=body,
                headers=self._signed_headers(execution, "POST", path, body),
                timeout=self._timeout(),
            )
        except requests.exceptions.Timeout as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        payload = self._json(response, "recover_orphan")
        if response.status_code == 404 and payload.get("code") in (
            "NOT_FOUND",
            "NO_EXECUTION_CREATED",
        ):
            raise SidecarExecutionNotFound(payload.get("code"))
        if response.status_code >= 400:
            raise SidecarStatusUnavailable(
                f"Sidecar orphan recovery endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                payload=payload,
            )
        return payload
```

- [ ] **Step 5: Run focused SHKeeper core tests**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python -m pytest tests/test_payout_execution_reconciler.py \
  -k "orphan_recovery or enqueued_status_unavailable or enqueueing_status_unavailable or submit_timeout" -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Run the full reconciler test file**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python -m pytest tests/test_payout_execution_reconciler.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 7: Commit SHKeeper core retry cap**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git add shkeeper/services/payout_execution_reconciler.py shkeeper/services/payout_sidecar_client.py tests/test_payout_execution_reconciler.py
git commit -m "fix: cap active payout sidecar polling retries"
```

Expected:

```text
[... fix: cap active payout sidecar polling retries]
```

---

## Task 6: Grither Provider-Confirmed Manual Review Recovery

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`

- [ ] **Step 1: Add a failing Grither test for unresolved manual-review confirmation**

In `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`, add this test after `postRefundConfirmationMovesToReconciliationWithoutCompletingOrRefundingAgain()`:

```java
    @Test
    void confirmedProviderObservationCompletesUnresolvedManualReviewWhenWithdrawalStillProcessing() throws Exception {
        WalletWithdrawal withdrawal = seedExecution(
                "PAPP019",
                ShKeeperPayoutState.ENQUEUED,
                4,
                "transition-current-19",
                SIDECAR_HASH);
        withdrawal.setStatus(WalletWithdrawalStatus.PROCESSING);
        walletWithdrawalRepository.saveAndFlush(withdrawal);
        ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PAPP019").orElseThrow();
        execution.setState(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        execution.setManualResolutionState(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
        execution.setPublicWithdrawalStatus(WalletWithdrawalStatus.PROCESSING.name());
        execution.setReconciliationRequired(true);
        execution.setFailureClass(ShKeeperPayoutFailureClass.AMBIGUOUS.name());
        execution.setErrorCode("TERMINAL_STATE_CONFLICT");
        execution.setErrorMessage("Previous provider state was ambiguous");
        executionRepository.saveAndFlush(execution);
        ShKeeperPayoutCallbackPayload confirmed = callback(
                "evt-app-19",
                "PAPP019",
                ShKeeperPayoutState.CONFIRMED,
                5,
                "transition-confirmed-19",
                9419L,
                9519L,
                null,
                List.of());

        ShKeeperPayoutStateApplicationResult result = apply(confirmed);

        assertThat(result.applyResult()).isEqualTo(ShKeeperPayoutStateApplyResult.APPLIED);
        ShKeeperPayoutExecution reloadedExecution = executionRepository.findByExternalId("PAPP019").orElseThrow();
        assertThat(reloadedExecution.getState()).isEqualTo(ShKeeperPayoutState.CONFIRMED.name());
        assertThat(reloadedExecution.getManualResolutionState()).isEqualTo(ShKeeperPayoutManualResolutionState.NONE);
        assertThat(reloadedExecution.isReconciliationRequired()).isFalse();
        assertThat(reloadedExecution.getErrorCode()).isNull();
        assertThat(reloadedExecution.getTxidsJson()).isEqualTo("[]");
        assertThat(reloadedExecution.getMessageHashesJson()).contains("message-hash-evt-app-19");
        WalletWithdrawal reloadedWithdrawal = walletWithdrawalRepository.findById(withdrawal.getId()).orElseThrow();
        assertThat(reloadedWithdrawal.getStatus()).isEqualTo(WalletWithdrawalStatus.COMPLETED);
        assertThat(reloadedWithdrawal.getTxHash()).isEqualTo("message-hash-evt-app-19");
        verify(alertOperations, never()).send(eq(AlertType.SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED), any(), any(), any());
    }

    @Test
    void confirmedProviderObservationWithPayloadHashMismatchStaysInManualReview() throws Exception {
        WalletWithdrawal withdrawal = seedExecution(
                "PAPP020",
                ShKeeperPayoutState.ENQUEUED,
                4,
                "transition-current-20",
                "different-sidecar-payload-hash");
        withdrawal.setStatus(WalletWithdrawalStatus.PROCESSING);
        walletWithdrawalRepository.saveAndFlush(withdrawal);
        ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PAPP020").orElseThrow();
        execution.setState(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        execution.setManualResolutionState(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
        execution.setPublicWithdrawalStatus(WalletWithdrawalStatus.PROCESSING.name());
        execution.setReconciliationRequired(true);
        execution.setFailureClass(ShKeeperPayoutFailureClass.AMBIGUOUS.name());
        execution.setErrorCode("TERMINAL_STATE_CONFLICT");
        execution.setErrorMessage("Previous provider state was ambiguous");
        executionRepository.saveAndFlush(execution);
        ShKeeperPayoutCallbackPayload confirmed = callback(
                "evt-app-20",
                "PAPP020",
                ShKeeperPayoutState.CONFIRMED,
                5,
                "transition-confirmed-20",
                9420L,
                9520L,
                null,
                List.of());

        ShKeeperPayoutStateApplicationResult result = apply(confirmed);

        assertThat(result.applyResult()).isEqualTo(ShKeeperPayoutStateApplyResult.RECONCILIATION_REQUIRED);
        ShKeeperPayoutExecution reloadedExecution = executionRepository.findByExternalId("PAPP020").orElseThrow();
        assertThat(reloadedExecution.getState()).isEqualTo(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        assertThat(reloadedExecution.getManualResolutionState()).isEqualTo(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
        assertThat(reloadedExecution.getErrorCode()).isEqualTo("TERMINAL_STATE_CONFLICT");
        WalletWithdrawal reloadedWithdrawal = walletWithdrawalRepository.findById(withdrawal.getId()).orElseThrow();
        assertThat(reloadedWithdrawal.getStatus()).isEqualTo(WalletWithdrawalStatus.PROCESSING);
    }

    @Test
    void confirmedProviderObservationCompletesUnresolvedManualReviewWhenWithdrawalStillPending() throws Exception {
        WalletWithdrawal withdrawal = seedExecution(
                "PAPP021",
                ShKeeperPayoutState.ENQUEUED,
                4,
                "transition-current-21",
                SIDECAR_HASH);
        ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PAPP021").orElseThrow();
        execution.setState(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        execution.setManualResolutionState(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
        execution.setPublicWithdrawalStatus(WalletWithdrawalStatus.PENDING.name());
        execution.setReconciliationRequired(true);
        execution.setFailureClass(ShKeeperPayoutFailureClass.AMBIGUOUS.name());
        execution.setErrorCode("TERMINAL_STATE_CONFLICT");
        execution.setErrorMessage("Previous provider state was ambiguous");
        executionRepository.saveAndFlush(execution);
        ShKeeperPayoutCallbackPayload confirmed = callback(
                "evt-app-21",
                "PAPP021",
                ShKeeperPayoutState.CONFIRMED,
                5,
                "transition-confirmed-21",
                9421L,
                9521L,
                null,
                List.of());

        ShKeeperPayoutStateApplicationResult result = apply(confirmed);

        assertThat(result.applyResult()).isEqualTo(ShKeeperPayoutStateApplyResult.APPLIED);
        ShKeeperPayoutExecution reloadedExecution = executionRepository.findByExternalId("PAPP021").orElseThrow();
        assertThat(reloadedExecution.getState()).isEqualTo(ShKeeperPayoutState.CONFIRMED.name());
        assertThat(reloadedExecution.getManualResolutionState()).isEqualTo(ShKeeperPayoutManualResolutionState.NONE);
        assertThat(reloadedExecution.isReconciliationRequired()).isFalse();
        WalletWithdrawal reloadedWithdrawal = walletWithdrawalRepository.findById(withdrawal.getId()).orElseThrow();
        assertThat(reloadedWithdrawal.getStatus()).isEqualTo(WalletWithdrawalStatus.COMPLETED);
        assertThat(reloadedWithdrawal.getTxHash()).isEqualTo("message-hash-evt-app-21");
    }

    @Test
    void sidecarPayloadHashMismatchMovesNormalCallbackToReconciliationWithoutOverwritingStoredHash() throws Exception {
        WalletWithdrawal withdrawal = seedExecution(
                "PAPP022",
                ShKeeperPayoutState.ENQUEUED,
                4,
                "transition-current-22",
                "different-sidecar-payload-hash");
        withdrawal.setStatus(WalletWithdrawalStatus.PROCESSING);
        walletWithdrawalRepository.saveAndFlush(withdrawal);
        ShKeeperPayoutCallbackPayload confirmed = callback(
                "evt-app-22",
                "PAPP022",
                ShKeeperPayoutState.CONFIRMED,
                5,
                "transition-confirmed-22",
                9422L,
                9522L,
                null,
                List.of());

        ShKeeperPayoutStateApplicationResult result = apply(confirmed);

        assertThat(result.applyResult()).isEqualTo(ShKeeperPayoutStateApplyResult.RECONCILIATION_REQUIRED);
        ShKeeperPayoutExecution reloadedExecution = executionRepository.findByExternalId("PAPP022").orElseThrow();
        assertThat(reloadedExecution.getState()).isEqualTo(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        assertThat(reloadedExecution.getErrorCode()).isEqualTo("SIDECAR_PAYLOAD_HASH_MISMATCH");
        assertThat(reloadedExecution.getSidecarPayloadHash()).isEqualTo("different-sidecar-payload-hash");
        WalletWithdrawal reloadedWithdrawal = walletWithdrawalRepository.findById(withdrawal.getId()).orElseThrow();
        assertThat(reloadedWithdrawal.getStatus()).isEqualTo(WalletWithdrawalStatus.PROCESSING);
        assertThat(reloadedWithdrawal.getTxHash()).isNull();
    }

    @Test
    void statusSyncConfirmedObservationDoesNotAutoCompleteUnresolvedManualReview() {
        WalletWithdrawal withdrawal = seedExecution(
                "PAPP023",
                ShKeeperPayoutState.ENQUEUED,
                4,
                "transition-current-23",
                SIDECAR_HASH);
        withdrawal.setStatus(WalletWithdrawalStatus.PROCESSING);
        walletWithdrawalRepository.saveAndFlush(withdrawal);
        ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PAPP023").orElseThrow();
        execution.setState(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        execution.setManualResolutionState(ShKeeperPayoutManualResolutionState.MANUAL_REVIEW);
        execution.setPublicWithdrawalStatus(WalletWithdrawalStatus.PROCESSING.name());
        execution.setReconciliationRequired(true);
        execution.setErrorCode("TERMINAL_STATE_CONFLICT");
        executionRepository.saveAndFlush(execution);

        ShKeeperPayoutStateApplicationResult result = stateApplicationService.applyStatusResponse(response(
                "PAPP023",
                ShKeeperPayoutState.CONFIRMED,
                5,
                "transition-status-23"));

        assertThat(result.applyResult()).isEqualTo(ShKeeperPayoutStateApplyResult.RECONCILIATION_REQUIRED);
        ShKeeperPayoutExecution reloadedExecution = executionRepository.findByExternalId("PAPP023").orElseThrow();
        assertThat(reloadedExecution.getState()).isEqualTo(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
        WalletWithdrawal reloadedWithdrawal = walletWithdrawalRepository.findById(withdrawal.getId()).orElseThrow();
        assertThat(reloadedWithdrawal.getStatus()).isEqualTo(WalletWithdrawalStatus.PROCESSING);
        assertThat(reloadedWithdrawal.getTxHash()).isNull();
    }
```

Keep the existing `postRefundConfirmationMovesToReconciliationWithoutCompletingOrRefundingAgain()` test unchanged. It is the safety guard proving that confirmed-after-refund still does not complete the withdrawal.

- [ ] **Step 2: Run the Grither state application tests and verify the new test fails**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.confirmedProviderObservationCompletesUnresolvedManualReviewWhenWithdrawalStillProcessing
```

Expected:

```text
FAILED ... expected: APPLIED but was: RECONCILIATION_REQUIRED
```

- [ ] **Step 3: Add the narrow recovery predicate**

In `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`, add these constants near the existing public status constants:

```java
    private static final String PUBLIC_STATUS_PENDING = "PENDING";
    private static final String PUBLIC_STATUS_PROCESSING = "PROCESSING";
```

Add this private enum near the constants:

```java
    private enum ObservationSource {
        CALLBACK,
        STATUS_SYNC
    }
```

Change `applyStoredCallbackEvent(...)` to call:

```java
        ShKeeperPayoutStateApplicationResult result = applyObservation(
                execution,
                observation,
                receivedAt,
                ObservationSource.CALLBACK);
```

Change `applyStatusObservation(...)` to call:

```java
        return applyObservation(execution, observation, receivedAt, ObservationSource.STATUS_SYNC);
```

Then extend the private `applyObservation(...)` signature to accept `ObservationSource source`.

Add this helper near `terminalStateConflict()`:

```java
    private static boolean isProviderConfirmedRecoveryAllowed(
            ShKeeperPayoutExecution execution,
            ShKeeperPayoutObservation incoming,
            ShKeeperPayoutState currentState,
            ShKeeperPayoutState incomingStoredState,
            ObservationSource source
    ) {
        return source == ObservationSource.CALLBACK
                && (currentState == ShKeeperPayoutState.RECONCILIATION_REQUIRED
                || currentState == ShKeeperPayoutState.MANUAL_REVIEW)
                && incomingStoredState == ShKeeperPayoutState.CONFIRMED
                && execution.getManualResolutionState() == ShKeeperPayoutManualResolutionState.MANUAL_REVIEW
                && isReservedPublicStatus(execution.getPublicWithdrawalStatus())
                && Objects.equals(execution.getRequestHash(), incoming.requestHash())
                && Objects.equals(execution.getSidecarPayloadHash(), incoming.sidecarPayloadHash())
                && hasIncomingEvidence(incoming)
                && !hasAccountingTerminal(execution);
    }

    private static boolean isReservedPublicStatus(String publicStatus) {
        return PUBLIC_STATUS_PENDING.equals(publicStatus)
                || PUBLIC_STATUS_PROCESSING.equals(publicStatus);
    }

    private static boolean hasIncomingEvidence(ShKeeperPayoutObservation incoming) {
        return !isEmptyList(incoming.txids()) || !isEmptyList(incoming.messageHashes());
    }

    private static boolean hasAccountingTerminal(ShKeeperPayoutExecution execution) {
        return PUBLIC_STATUS_FAILED.equals(execution.getPublicWithdrawalStatus())
                || PUBLIC_STATUS_COMPLETED.equals(execution.getPublicWithdrawalStatus());
    }
```

If `isEmptyList(...)` is below this method in the same class, Java still allows the call because method order does not matter.

In `safetyError(...)`, add a global sidecar payload identity guard after the existing request-hash check and before asset/network checks:

```java
        if (hasText(incoming.sidecarPayloadHash())
                && hasText(execution.getSidecarPayloadHash())
                && !execution.getSidecarPayloadHash().equals(incoming.sidecarPayloadHash())) {
            return "SIDECAR_PAYLOAD_HASH_MISMATCH";
        }
```

This check must happen before `applyNewerObservation(...)` can copy incoming provider fields onto the stored execution row.

Replace the existing hash helper at the bottom of the class:

```java
    private static String firstTxHash(List<String> txids) {
        if (txids == null || txids.isEmpty() || !hasText(txids.get(0))) {
            return null;
        }
        return txids.get(0);
    }
```

with:

```java
    private static String firstProviderHash(List<String> txids, List<String> messageHashes) {
        if (txids != null && !txids.isEmpty() && hasText(txids.get(0))) {
            return txids.get(0);
        }
        if (messageHashes != null && !messageHashes.isEmpty() && hasText(messageHashes.get(0))) {
            return messageHashes.get(0);
        }
        return null;
    }
```

- [ ] **Step 4: Use the predicate before terminal conflict**

In `terminalStateConflict(...)`, extend the method signature to accept `ObservationSource source` and change:

```java
        if (isManualOrAccountingTerminal(currentState)
                && incomingStoredState != currentState
                && !isAllowedPreBroadcastCancellation(execution, currentState, incomingStoredState)) {
            return TERMINAL_STATE_CONFLICT;
        }
```

to:

```java
        if (isManualOrAccountingTerminal(currentState)
                && incomingStoredState != currentState
                && !isAllowedPreBroadcastCancellation(execution, currentState, incomingStoredState)
                && !isProviderConfirmedRecoveryAllowed(execution, incoming, currentState, incomingStoredState, source)) {
            return TERMINAL_STATE_CONFLICT;
        }
```

Update the `applyObservation(...)` call site so terminal conflict passes `source`:

```java
        String terminalStateConflict = terminalStateConflict(execution, incoming, source);
```

In `applyNewerObservation(...)`, replace the wallet command hash argument:

```java
                        firstTxHash(incoming.txids()),
```

with:

```java
                        firstProviderHash(incoming.txids(), incoming.messageHashes()),
```

The existing `applyNewerObservation(...)` path should then store hashes, clear errors, update state to `CONFIRMED`, and complete the wallet withdrawal through the same path as a normal provider confirmation. For TON-style callbacks with an empty `txids` list, the wallet withdrawal `txHash` field receives the first provider `message_hashes` value so the admin UI has mandatory evidence.

- [ ] **Step 5: Run the new Grither test and the refund-safety test**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.confirmedProviderObservationCompletesUnresolvedManualReviewWhenWithdrawalStillProcessing \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.confirmedProviderObservationCompletesUnresolvedManualReviewWhenWithdrawalStillPending \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.confirmedProviderObservationWithPayloadHashMismatchStaysInManualReview \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.sidecarPayloadHashMismatchMovesNormalCallbackToReconciliationWithoutOverwritingStoredHash \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.statusSyncConfirmedObservationDoesNotAutoCompleteUnresolvedManualReview \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest.postRefundConfirmationMovesToReconciliationWithoutCompletingOrRefundingAgain
```

Expected:

```text
BUILD SUCCESSFUL
```

- [ ] **Step 6: Run the full Grither SHKeeper payout state tests**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutManualResolutionServiceTest
```

Expected:

```text
BUILD SUCCESSFUL
```

- [ ] **Step 7: Commit Grither provider-confirmed recovery**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
git add apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java \
  apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java
git commit -m "fix: apply confirmed shkeeper payouts from unresolved review"
```

Expected:

```text
[... fix: apply confirmed shkeeper payouts from unresolved review]
```

---

## Task 7: TRON Parity Verification

**Files:**
- Verify: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_execution_boundaries.py`
- Verify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_execution.py`

- [ ] **Step 1: Run TRON stale signing and retryable resource tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "stale_signing or resource_lock_timeout or retryable_activation_resource_error or provider_unavailable_resource_code" -q
```

Expected:

```text
... passed
```

- [ ] **Step 2: Verify TRON retry behavior by reading the focused tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
rg -n "test_stale_signing_without_side_effects_is_safe_to_retry|test_resource_lock_timeout_is_retryable_without_side_effects|test_retryable_activation_resource_error_returns_to_received_without_refund_state|test_provider_unavailable_resource_code_is_not_implicitly_retryable" tests/test_payout_execution_boundaries.py
```

Expected output contains all four names:

```text
test_stale_signing_without_side_effects_is_safe_to_retry
test_resource_lock_timeout_is_retryable_without_side_effects
test_retryable_activation_resource_error_returns_to_received_without_refund_state
test_provider_unavailable_resource_code_is_not_implicitly_retryable
```

- [ ] **Step 3: Record the TRON parity verdict in the final implementation summary**

Use this wording in the implementation close-out:

```text
TRON was not changed. It already uses direct sqlite helpers for payout execution, so the Flask-SQLAlchemy PendingRollbackError failure mode does not apply. Existing tests confirm retryable pre-broadcast resource failures return to RECEIVED without refund state, while signed/broadcast evidence still requires reconciliation.
```

No TRON commit is needed if the tests pass and no missing parity gap is found.

---

## Task 8: Cross-Repository Verification

**Files:**
- Verify all modified repositories.

- [ ] **Step 1: Verify TON**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
python -m pytest tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 2: Verify ETH**

Run:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
python -m pytest tests/test_payout_execution_contract.py tests/test_payout_execution_boundaries.py tests/test_payout_execution_task_retries.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 3: Verify SHKeeper core**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
python -m pytest tests/test_payout_execution_reconciler.py -q
```

Expected:

```text
... passed
```

- [ ] **Step 4: Verify Grither payout state**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutStateApplicationServiceTest \
  --tests com.grither.pay.providers.shkeeper.payout.ShKeeperPayoutManualResolutionServiceTest
```

Expected:

```text
BUILD SUCCESSFUL
```

- [ ] **Step 5: Verify TRON parity**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
python -m pytest tests/test_payout_execution_boundaries.py \
  -k "stale_signing or resource_lock_timeout or retryable_activation_resource_error or provider_unavailable_resource_code" -q
```

Expected:

```text
... passed
```

- [ ] **Step 6: Inspect git diffs in every modified repo**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper && git status --short && git diff --stat
cd /Users/test/PycharmProjects/ethereum-shkeeper && git status --short && git diff --stat
cd /Users/test/PycharmProjects/shkeeper.io && git status --short && git diff --stat
cd /Users/test/IdeaProjects/grither-pay && git status --short && git diff --stat
```

Expected after commits, for each repository that has already committed its task:

```text
git status --short prints no rows
```

Each repository should print no uncommitted changes except this plan repository if the plan has not been committed yet.

- [ ] **Step 7: Commit this implementation plan**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git add docs/superpowers/plans/2026-06-09-payout-sidecar-reliability.md
git commit -m "docs: plan payout sidecar reliability fixes"
```

Expected:

```text
[... docs: plan payout sidecar reliability fixes]
```

---

## Rollout Notes

- Deploy TON sidecar before enabling SHKeeper core orphan recovery for TON; the core call depends on `POST /payout-executions/{id}/recover-orphan`.
- Deploy ETH sidecar before enabling SHKeeper core orphan recovery for ETH; it shares the Flask-SQLAlchemy/Celery lost-task failure mode.
- Deploy SHKeeper core retry cap and orphan-recovery trigger after sidecar endpoints exist; the cap reduces one-hour polling gaps and the orphan trigger re-enqueues old no-evidence `RECEIVED`/`VALIDATED` sidecar rows.
- Deploy Grither after SHKeeper callback/status fixes; it prevents future provider-confirmed manual-review rows from staying stuck while the wallet withdrawal is still reserved (`PENDING` or `PROCESSING`) and no refund/accounting terminal action has happened.
- Existing Grither rows already marked `TERMINAL_STATE_CONFLICT` with empty stored hashes remain admin/manual recovery unless a separate replay tool is designed and tested.

## Self-Review

- Spec coverage:
  - Design spec is aligned with the final reliability contract before code changes: Task 0.
  - TON transient DB failure before unsafe evidence: Task 1 and Task 2.
  - ETH parity with TON: Task 3 and Task 4.
  - Durable recovery for worker death or retry exhaustion with stale no-evidence sidecar rows: Task 1, Task 3, and Task 5.
  - No blind retry after seqno/nonce/signed/broadcast evidence: Task 1 and Task 3 tests.
  - SHKeeper core active polling retry cap and explicit sidecar orphan recovery call: Task 5.
  - Grither future provider-confirmed recovery from reserved states without refund/accounting-terminal regression: Task 6.
  - Grither global and recovery-path identity safety uses both `request_hash` and `sidecar_payload_hash`: Task 6.
  - TON callbacks with empty `txids` still populate the admin-required provider hash from `message_hashes`: Task 6.
  - TRON risk comparison: Task 7.
- Placeholder scan:
  - No red-flag placeholder markers or unspecified implementation step is present.
  - Every code-changing task contains concrete code or exact replacement snippets.
- Type consistency:
  - Python uses `recover_task_owned_transient_failure(..., lease_owner=...)` consistently in TON and ETH.
  - Python uses explicit `recover_orphan_execution(...)` sidecar methods and `recover_orphan(...)` core client calls; GET status does not enqueue orphan work.
  - Java uses existing domain names: `ShKeeperPayoutState`, `ShKeeperPayoutManualResolutionState`, `WalletWithdrawalStatus`, and `ShKeeperPayoutObservation`.
