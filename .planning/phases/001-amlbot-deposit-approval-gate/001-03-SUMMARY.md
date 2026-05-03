---
phase: 001-amlbot-deposit-approval-gate
plan: 03
subsystem: callbacks
tags: [amlbot, walletnotify, callbacks, scheduler, grither-pay]
requires:
  - phase: 001-amlbot-deposit-approval-gate
    provides: "aml-shkeeper sidecar checks API and SHKeeper AML policy foundation"
provides:
  - "AML processing lifecycle for confirmed deposits"
  - "Callback gate requiring terminal AmlCheck state"
  - "Trigger transaction AML callback payload fields"
affects: [walletnotify, callback-retries, grither-pay-crediting]
tech-stack:
  added: []
  patterns:
    - "Scheduler processes AML polling before callback retries."
    - "Final callback builder is testable without performing HTTP."
key-files:
  created:
    - "shkeeper/services/aml_processing.py"
    - "tests/test_aml_processing.py"
    - "tests/test_aml_callback_payload.py"
  modified:
    - "shkeeper/api_v1.py"
    - "shkeeper/callback.py"
    - "shkeeper/tasks.py"
    - "shkeeper/services/aml_policy.py"
key-decisions:
  - "AML checks are started only after required confirmations are reached."
  - "Unconfirmed callbacks remain observability-only and carry no AML decision fields."
  - "Only the trigger transaction gets deposit_decision, decision_reason, idempotency_key, deposit_id, and aml."
patterns-established:
  - "Direct send_notification calls are guarded by is_callback_allowed."
  - "Pending/checking AML blocks final callback until scheduler resolves a terminal state."
requirements-completed: ["SPEC-02", "SPEC-03", "SPEC-04", "SPEC-05", "SPEC-06", "SPEC-07", "SPEC-08", "SPEC-09", "SPEC-10", "SPEC-11", "SPEC-12", "SPEC-14", "SPEC-15"]
duration: 37min
completed: 2026-05-03
---

# Phase 001 Plan 03 Summary

**Confirmed incoming deposits now wait for terminal AML state before SHKeeper sends the final grither-pay callback.**

## Performance

- **Started:** 2026-05-03T08:17:00Z
- **Completed:** 2026-05-03T08:54:00Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments

- Added `aml_processing` orchestration for skip, unsupported/manual_review, sidecar create, sidecar polling, timeout, and callback gating.
- Updated walletnotify to run AML only after enough confirmations and to send final callback only when terminal AML allows it.
- Updated scheduler callback task to process pending AML checks before retrying callbacks.
- Refactored final callback construction into `build_payment_notification`.
- Added transaction-level AML fields for the trigger transaction while preserving existing top-level callback fields.

## Task Commits

1. **AML lifecycle and callback gate** - `c3e9b92` (`feat(001-03)`)

## Verification

- `python -m compileall shkeeper` passed.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_processing.py'` passed: 6 tests.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_callback_payload.py'` passed: 6 tests.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_policy.py'` passed: 8 tests.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_coverage.py'` passed: 4 tests.
- `rg -n "send_notification\\(tx\\)" shkeeper/api_v1.py shkeeper/callback.py` shows only AML-gated incoming paths.
- `git diff --check` passed.

## Code Review

Review completed after implementation. No blocking findings remain.

Auto-fixed during review:
- Avoided SQLAlchemy double-insert by making policy-generated temporary `AmlCheck` snapshots use `transaction_id` without attaching a second relationship object.
- Delayed sidecar AML checks until `need_more_confirmations=False`, avoiding paid AML checks for deposits that are not final enough.
- Cleared stale `next_retry_at` when a provider result becomes terminal.

## Deviations from Plan

The plan said walletnotify should call `ensure_aml_for_transaction(tx)` immediately after persistence. Implementation intentionally gates that call behind `not tx.need_more_confirmations` to avoid unnecessary paid AML checks before the configured confirmation threshold. Scheduler still ensures AML once confirmations are sufficient.

## Issues Encountered

Unit tests exposed the relationship double-insert issue and missing test fixture address mapping for unconfirmed callbacks; both were fixed before commit.

## User Setup Required

None for this plan.

## Next Phase Readiness

Plan 04 can add broader E2E and documentation on the now-stable callback contract.

---
*Phase: 001-amlbot-deposit-approval-gate*
*Completed: 2026-05-03*
