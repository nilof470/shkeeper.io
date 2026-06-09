# Payout Sidecar Reliability Design

Date: 2026-06-09
Status: Approved for implementation planning

## Context

Production TON-USDT payout `68304109` exposed a reliability gap in the
sidecar payout execution path.

The TON sidecar accepted SHKeeper execution `30`, enqueued the Celery task, and
the `ton-usdt-payouts` worker received it. The task then failed before the first
side effect with:

```text
MySQL server has gone away (ConnectionResetError(104, 'Connection reset by peer'))
```

The sidecar row stayed in `RECEIVED` with no `attempt_id`, `source_seqno`,
`signed_boc_hash`, `message_hash`, or `broadcast_attempted_at`. Redis had no
queued item and Celery had no active/reserved task. A manual re-enqueue before
restart failed with:

```text
PendingRollbackError: Can't reconnect until invalid transaction is rolled back.
```

After restarting `ton-shkeeper`, the same execution re-enqueued safely because
there was still no blockchain-side evidence. It then broadcasted and confirmed
with TON message hash:

```text
ec686d573edaf3815aad7e1464ff0abd2779e4de0b5baf8b2742c2d1c1807f1f
```

SHKeeper core later synced to `CONFIRMED` and delivered callback event
`f800cd94-9f80-4be9-82ac-19b972f448f9`. Grither Pay did receive that callback,
but the local payout mirror was already in `RECONCILIATION_REQUIRED` /
`MANUAL_REVIEW`, so the newer `CONFIRMED` observation was rejected as
`TERMINAL_STATE_CONFLICT`.

## Problem

The incident was not a permanent TON routing failure, not insufficient funds,
not a Redis queue mismatch, and not a callback delivery failure. It was a
multi-layer reliability failure:

1. TON sidecar Celery task lost a payout after a transient DB connection error
   before blockchain side effects.
2. The SQLAlchemy session in the worker process stayed poisoned, causing a
   later `PendingRollbackError`.
3. SHKeeper core kept stale sidecar status for too long after transient sidecar
   HTTP errors.
4. Grither Pay treated a provider-confirmed recovery callback as a terminal
   conflict after local manual-review state had already been entered.

## Goals

- Prevent TON and ETH sidecar payout tasks from being lost when transient DB
  failures happen before unsafe side effects.
- Keep retry behavior explicitly bounded by side-effect evidence, not by broad
  exception classes.
- Preserve TRON's existing retryable pre-broadcast behavior without forcing
  TON/ETH into TRON's internal DB implementation.
- Make SHKeeper core poll active sidecar executions again quickly after
  transient status errors.
- Allow Grither Pay to apply a later matching provider `CONFIRMED` observation
  from `MANUAL_REVIEW` when it is safe and evidence-backed.
- Add tests that encode the reliability contract for future sidecars.

## Non-Goals

- Do not rewrite TON or ETH sidecars to use TRON's sqlite row-dict
  implementation.
- Do not introduce a shared cross-repository sidecar framework package in this
  change.
- Do not add blind Celery retries for all exceptions.
- Do not retry after nonce, seqno, signed payload, tx hash, message hash, or
  broadcast-attempt evidence exists.
- Do not change legacy payout APIs except where their current implementation
  shares the same task/session safety boundary.

## Existing Sidecar Differences

The services are similar at the payout contract level but not identical
internally.

### TON

TON uses Flask-SQLAlchemy ORM over MySQL in production. Its
`execute_payout_execution` Celery task calls `PayoutExecutionStore.execute()`
directly. The first `_get_row()` happens before the main execution error
handling. The task does not clean SQLAlchemy session state on exit.

Unsafe TON evidence:

- `source_seqno`
- `signed_boc_ref`
- `signed_boc_hash`
- `message_hash`
- `broadcast_attempted_at`

### ETH

ETH follows the same broad pattern as TON: Flask-SQLAlchemy ORM, Celery task
wrapper with no task-level session cleanup, and first `_get_row()` before the
safe execution boundary.

Unsafe ETH evidence:

- `nonce`
- `signed_raw_tx_ref`
- `signed_raw_tx_hash`
- `tx_hash`
- `broadcast_attempted_at`

### TRON

TRON uses a different payout execution storage path with direct sqlite row
helpers. It already has more mature retryable pre-broadcast logic for resource
provisioning and destination activation:

- `_is_retryable_pre_broadcast_error()`
- explicit return to `RECEIVED` when retryable and no unsafe side effects exist
- tests for retryable activation/resource errors

Unsafe TRON evidence:

- resource reservation with non-retryable semantics
- `signed_raw_tx_hash`
- `txid` / tx hashes
- broadcast markers

TRON should not be used as a code template for TON/ETH internals. It should be
used as the behavior reference for pre-broadcast retry safety.

## Reliability Contract

All sidecars should satisfy the same payout execution contract:

1. A payout task may be retried automatically only when the failure happened
   before unsafe side effects.
2. A payout task must not be blindly retried after unsafe side-effect evidence
   exists.
3. Transient infrastructure errors before unsafe side effects are not payout
   failures. They are worker/runtime failures and should leave the execution
   retryable.
4. Business or preflight failures before unsafe side effects may transition to
   `FAILED_PRE_BROADCAST` when the failure is terminal for the request.
5. Failures after unsafe side effects must move through reconciliation or
   explicit recovery, not normal retry.
6. Stale `RECEIVED` or `VALIDATED` executions with no side-effect evidence are
   not just safe to re-enqueue. They must have an explicit durable recovery
   path through an authenticated mutating recovery operation.
7. Stale `SIGNING`, `SIGNED`, or `BROADCASTING` executions require recovery
   logic based on the specific sidecar's evidence fields.

## Recommended Design

### 1. TON and ETH Task Session Guard

Add a small task-level guard around `execute_payout_execution` in both TON and
ETH sidecars.

Behavior:

- best-effort `db.session.rollback()` at task start to clear inherited poisoned
  transaction state;
- run `PayoutExecutionStore.execute()`;
- on transient SQLAlchemy DB failures, rollback/remove the session, inspect the
  payout row, and then either retry, release the row back to `RECEIVED`, or leave
  it for reconciliation/recovery based on the side-effect evidence;
- always `db.session.remove()` in `finally`.

Transient DB errors are OperationalError, PendingRollbackError, or DBAPIError
with connection_invalidated=True. Integrity, data, programming, and constraint
errors are not transient payout-worker failures and must not enter the automatic
retry path.

The retry must stay narrow. It should not catch broad `Exception`, chain API
errors, signing errors, broadcast errors, or business `PayoutExecutionError`.
It also must not blindly retry every transient DB error after the store has
entered `SIGNING`. After rollback, the task should reload the row and apply this
decision table:

| Current row state | Unsafe evidence | Action |
| --- | --- | --- |
| `RECEIVED` or `VALIDATED` | none | Celery retry with bounded backoff |
| `SIGNING` owned by this task attempt | none | clear lease/attempt and move back to `RECEIVED`, then retry or allow normal auto-enqueue |
| `SIGNING`, `SIGNED`, or `BROADCASTING` | present or ownership is ambiguous | no blind retry; use stale recovery or reconciliation |
| terminal state | any | no retry |

This avoids the common bad implementation where the retry wrapper surrounds the
whole store call and immediately reruns after a partial state transition.

Recommended retry parameters:

- max retries: 5
- initial delay: 5 seconds
- exponential backoff capped at 60 seconds
- jitter enabled if implemented manually

The task guard should log execution id, retry count, and exception class.

### 2. TON and ETH Store-Level DB Boundary

Move initial row loading into a safe DB boundary in `PayoutExecutionStore`.
The current TON/ETH first `_get_row()` can raise before the function reaches
its normal error handling. That is how the incident was triggered.

Design:

- `_get_row()` failures from transient DB exceptions should propagate to the
  task-level retry guard, not become payout state transitions.
- `_mark_failed_or_reconciliation()` should not turn transient DB exceptions
  into `FAILED_PRE_BROADCAST` or `RECONCILIATION_REQUIRED` when no side effects
  are known.
- Existing side-effect-aware behavior remains: after unsafe evidence exists,
  non-retryable exceptions still move to reconciliation.

This keeps SQLAlchemy infrastructure failures separate from payout business
failures.

### 3. TON and ETH Unsafe Retry Boundary Tests

Add focused regression tests in each sidecar.

Required tests:

- first `_get_row()` raises `OperationalError`: Celery task retries and row
  remains unchanged/retryable;
- poisoned session is cleaned before the next task attempt;
- transient DB error before unsafe evidence does not create
  `FAILED_PRE_BROADCAST`;
- transient DB error after moving to `SIGNING` but before nonce/seqno evidence
  clears the task-owned lease and makes the execution retryable;
- exception after `source_seqno`/`nonce` or signed evidence does not trigger
  blind retry;
- normal successful payout path still broadcasts once.

The tests should patch/stub side effects. They must not hit live TON, ETH, or
provider APIs.

### 4. TRON Contract Check

Do not rewrite TRON. Add only the smallest tests needed to prove it satisfies
the same reliability contract.

TRON already covers retryable resource/activation failures. Add a regression
test only if current coverage does not prove:

- pre-broadcast transient execution failure with no unsafe evidence remains
  retryable;
- failures after signed tx or txid evidence do not re-enter normal retry.

### 5. Durable Sidecar Orphan Recovery

Bounded Celery retry is not sufficient by itself. A worker can die, the broker
task can disappear, or all retries can exhaust while the sidecar row is still in
`RECEIVED` or `VALIDATED` with no unsafe evidence.

These stale no-evidence rows are not just safe to re-enqueue; the final
reliability contract requires a durable recovery path so lost broker or worker
state cannot leave the execution orphaned forever.

Add an explicit authenticated sidecar endpoint:

```text
POST /payout-executions/{id}/recover-orphan
```

This endpoint may enqueue work only when all are true:

- the authenticated consumer owns the execution;
- sidecar state is `RECEIVED` or `VALIDATED`;
- there is no unsafe evidence: nonce/seqno, signed payload ref/hash,
  tx/message hash, tx/message hash list, or broadcast-attempt marker;
- there is no active unexpired worker lease.

`GET /payout-executions/{id}` must not enqueue orphan work. Existing status
refresh behavior may remain, but broker/task recovery must be reachable only
through the explicit mutating endpoint.

### 6. SHKeeper Core Poll Backoff and Orphan Trigger

Core SHKeeper currently leaves active `ENQUEUED`/`BROADCAST` polling subject to
the same exponential delay path that can grow to one hour after repeated
transient exceptions. That made the confirmed sidecar state stay stale until a
manual force sync.

Change polling behavior:

- for `ENQUEUED` and `BROADCAST`, `SidecarStatusUnavailable` should keep the
  state unchanged and retry with a short capped delay;
- cap active poll retry delay at 300 seconds;
- implement the cap in the polling path, not by globally changing all dispatch
  error backoff; preflight, submit, and enqueue-recovery errors keep their
  existing ambiguity semantics unless explicitly changed;
- keep `PAYOUT_DISPATCH_EXCEPTION` diagnostic fields for visibility;
- clear the transient diagnostic fields when later sidecar progress is applied.

Do not move active polling failures to reconciliation unless the sidecar returns
identity mismatch, execution not found, or contradictory evidence.

For `ENQUEUED` executions, after a successful status read, SHKeeper core may call
`POST /payout-executions/{id}/recover-orphan` only when the sidecar status is
old `RECEIVED` or `VALIDATED` and contains no tx, message, or broadcast
evidence. `ENQUEUEING` submit ambiguity remains conservative and must not use
this orphan path. Failures after nonce/seqno, signed payload, message/tx hash,
or broadcast marker still require reconciliation/recovery and must not call
orphan re-enqueue.

### 7. Grither Pay Provider-Confirmed Recovery

Grither Pay should not reject a matching newer `CONFIRMED` provider observation
only because local state is `RECONCILIATION_REQUIRED` / `MANUAL_REVIEW`.

Add a narrow recovery exception to `terminalStateConflict()`:

Allow applying incoming `CONFIRMED` when all are true:

- current state is `RECONCILIATION_REQUIRED` or `MANUAL_REVIEW`;
- manual resolution state is `MANUAL_REVIEW`;
- public withdrawal status is still processing/reserved, not completed, failed,
  cancelled, or refunded;
- incoming `request_hash` and `sidecar_payload_hash` match the stored execution;
- incoming event version is newer;
- incoming callback has provider evidence: non-empty `txids` or
  `message_hashes`;
- stored state was not already resolved by manual payout or negative evidence.

When allowed, apply the normal `CONFIRMED` path and complete the wallet
withdrawal. This is provider recovery, not manual operator completion.

Before any provider observation can overwrite stored execution fields, Grither
must enforce a global `sidecar_payload_hash` safety check. Together,
`request_hash + sidecar_payload_hash` is the canonical binding for amount and
destination unless Grither adds first-class amount/destination fields to
`ShKeeperPayoutObservation`.

This recovery is for future newer callbacks. Rows that have already been marked
`TERMINAL_STATE_CONFLICT` at the same event version, such as the production
`68304109` incident, should remain admin/manual recovery unless a separate,
explicit reprocess-from-raw-callback tool is designed. Do not silently replay
same-version raw callback rows through the normal webhook path.

Keep `TERMINAL_STATE_CONFLICT` for:

- failed/refunded/cancelled accounting states;
- manual payout completed through a different path;
- mismatched request hash, sidecar payload hash, destination, amount, or
  execution identity;
- confirmed observations without tx/message evidence.

### 8. TON Message Hash Handling

TON may provide message hashes without separate transaction ids. Admin and
manual resolution paths should treat TON message hash as valid provider
evidence. Automatic callback application must also treat `message_hashes` as
provider evidence for TON payouts.

Recommended UI/API behavior:

- label operator field as `TX/message hash` where the rail is TON;
- permit message hash without txid;
- if a required `txHash` field still exists in the UI, auto-fill it from
  `messageHash` only for TON and mark it as a provider reference, not a distinct
  chain txid;
- wallet completion must receive `firstProviderHash(txids, message_hashes)`,
  so TON callbacks with empty `txids` still populate the wallet `txHash` field
  with the message hash.

## Operational Behavior After Fix

For a transient DB disconnect before side effects:

1. sidecar task rolls back/removes session;
2. Celery retries;
3. execution remains `RECEIVED` or `VALIDATED`;
4. no callback claims failure;
5. if retries exhaust, execution remains observable as stuck/retryable rather
   than falsely terminal.

For a failure after unsafe evidence:

1. sidecar does not blind retry;
2. execution moves through existing reconciliation/recovery logic;
3. core polls sidecar status quickly enough to observe later chain confirmation;
4. Grither can apply matching provider `CONFIRMED` evidence from manual review.

## Risk Analysis

### Double Broadcast

Risk: a broad retry catches an error after broadcast and sends a second payout.

Mitigation: retry only narrow transient DB exceptions at the task boundary, and
preserve side-effect evidence checks in store logic.

### False Failed Pre-Broadcast

Risk: infrastructure errors get stored as payout failures.

Mitigation: classify transient DB exceptions separately from business/preflight
errors.

### Hidden Stuck Executions

Risk: a task still disappears due worker crash or broker behavior.

Mitigation: existing metrics plus stuck execution alerts; safe re-enqueue only
for states without unsafe evidence.

### Over-Unifying Sidecars

Risk: forcing TON/ETH to TRON internals creates large rewrite and new bugs.

Mitigation: unify the contract and tests, not the storage implementation.

### Grither Applies Unsafe Confirmation

Risk: Grither completes a withdrawal from a malicious or mismatched callback.

Mitigation: require newer version, matching request identity, matching sidecar
payload hash, manual-review state, still-processing wallet state, and tx/message
evidence.

## Testing Plan

### TON Sidecar

- focused task retry tests for transient SQLAlchemy failures;
- focused store tests for no state transition on pre-side-effect DB transient;
- existing payout execution boundary tests;
- existing payout status confirmation tests.

### ETH Sidecar

- same SQLAlchemy task/store tests as TON;
- existing ETH payout execution boundary tests;
- existing ETH confirmation tests.

### TRON Sidecar

- existing payout execution boundary tests;
- add contract regression only if current retryable pre-broadcast tests do not
  cover lost task/no-side-effect behavior.

### SHKeeper Core

- reconciler test for active `ENQUEUED` status unavailable using capped retry
  delay;
- existing test that later sidecar progress clears previous transient error;
- payout metrics tests for stuck execution visibility.

### Grither Pay

- callback apply test: `MANUAL_REVIEW` + matching newer `CONFIRMED` with
  message hash completes wallet withdrawal;
- wallet completion test: TON callback with empty `txids` and non-empty
  `message_hashes` populates wallet `txHash` from `message_hashes`;
- conflict test: mismatched request hash still produces `TERMINAL_STATE_CONFLICT`;
- conflict test: manually completed or refunded withdrawal still rejects later
  provider callback.

## Rollout Plan

1. Deploy TON/ETH sidecar task/session guard, orphan-recovery endpoints, and tests.
2. Deploy SHKeeper core poll backoff cap and orphan-recovery trigger after the
   sidecar endpoints exist.
3. Deploy Grither provider-confirmed recovery rule and TON message-hash wallet
   evidence handling.
4. Verify with a controlled small TON-USDT payout and one ETH-USDT dry-run or
   staging payout.
5. Watch stuck payout metrics, sidecar worker logs, callback delivery results,
   and Grither payout mirror states for one payout cycle.

## Implementation Boundaries

This design spans multiple repositories:

- `/Users/test/PycharmProjects/ton-shkeeper`
- `/Users/test/PycharmProjects/ethereum-shkeeper`
- `/Users/test/PycharmProjects/tron-shkeeper`
- `/Users/test/PycharmProjects/shkeeper.io`
- `/Users/test/IdeaProjects/grither-pay`

Implementation should proceed in small commits by repository. The first code
plan should start with TON/ETH sidecar task safety because that is the root
cause of the production incident.
