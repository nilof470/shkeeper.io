---
phase: 001-amlbot-deposit-approval-gate
plan: 04
subsystem: verification-docs
tags: [amlbot, e2e-tests, callback-contract, documentation]
requires:
  - phase: 001-amlbot-deposit-approval-gate
    provides: "AML sidecar, SHKeeper policy, and callback gate"
provides:
  - "E2E-style AML deposit verification"
  - "AMLBot deposit gate operator documentation"
  - "Sidecar contract tests for SHKeeper payloads"
affects: [grither-pay, operators, aml-shkeeper, callbacks]
tech-stack:
  added: []
  patterns:
    - "Docs contract tests assert key callback and threshold strings."
    - "Sidecar contract test locks SHKeeper's expected JSON shape."
key-files:
  created:
    - "tests/conftest.py"
    - "tests/test_aml_end_to_end.py"
    - "tests/test_aml_contract_docs.py"
    - "docs/amlbot_deposit_gate.md"
    - "/Users/test/PycharmProjects/aml-shkeeper/tests/test_shkeeper_contract.py"
  modified:
    - "README.md"
key-decisions:
  - "Docs state SHKeeper never credits grither-pay balances."
  - "Docs keep sweep threshold separate from AML callback gate."
  - "KYT/address monitoring remains deferred until pricing is confirmed."
patterns-established:
  - "Use `transactions[].trigger == true` plus `deposit_decision=credit` as the grither-pay crediting rule."
requirements-completed: ["SPEC-01", "SPEC-02", "SPEC-04", "SPEC-05", "SPEC-06", "SPEC-07", "SPEC-08", "SPEC-09", "SPEC-10", "SPEC-11", "SPEC-12", "SPEC-13", "SPEC-14", "SPEC-15"]
duration: 18min
completed: 2026-05-03
---

# Phase 001 Plan 04 Summary

**The AMLBot deposit gate now has E2E-style verification, sidecar contract tests, and operator-facing callback documentation.**

## Performance

- **Started:** 2026-05-03T08:55:00Z
- **Completed:** 2026-05-03T09:13:00Z
- **Tasks:** 3
- **Files modified:** 6 across SHKeeper and aml-shkeeper

## Accomplishments

- Added SHKeeper E2E-style tests for pending, approved, manual_review, skipped, cumulative-limit, unsupported, and replayed deposit flows.
- Added sidecar contract tests proving SHKeeper payload fields and duplicate POST behavior.
- Documented AMLBot-only policy, static-address callback semantics, canonical decisions/reasons, default thresholds, sweep recommendation, KYT deferral, and sample payloads.
- Added docs contract test and README link.

## Task Commits

1. **Sidecar SHKeeper contract test** - `f2e8cad` (`test(001-04)`) in `/Users/test/PycharmProjects/aml-shkeeper`
2. **SHKeeper E2E/docs contract** - `39b5e5b` (`test(001-04)`)

## Verification

- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests` passed: 32 tests.
- `/tmp/aml-shkeeper-venv/bin/python -m unittest discover -s tests` passed: 9 tests.
- `python -m compileall shkeeper` passed.
- `rg -n "deposit_decision|manual_review|transactions\\[\\]\\.trigger == true|AML_SKIP_CUMULATIVE_LIMIT_FIAT=300" docs/amlbot_deposit_gate.md README.md` found the documented contract.
- `git diff --check` passed in both repositories.

## Code Review

Review completed after implementation. No blocking findings remain.

Auto-fixed during review:
- Added a fake `Crypto.instances["BTC"]` in the E2E fixture so callback delivery tests use the test wallet instead of an unbound runtime crypto instance.

## Deviations from Plan

None - plan executed as written.

## Issues Encountered

The full E2E suite initially failed because the test fixture did not bind `Crypto.instances["BTC"]` to a test wallet object. The fixture was corrected and the full suite passed.

## User Setup Required

None for tests/docs. Runtime deployments still need AML sidecar host/auth and AMLBot provider credentials configured in `aml-shkeeper`.

## Next Phase Readiness

All four implementation plans are complete and ready for final verification.

---
*Phase: 001-amlbot-deposit-approval-gate*
*Completed: 2026-05-03*
