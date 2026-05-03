---
phase: 001-amlbot-deposit-approval-gate
plan: 02
subsystem: aml-policy
tags: [amlbot, deposits, policy, coverage, alembic]
requires:
  - phase: 001-amlbot-deposit-approval-gate
    provides: "aml-shkeeper sidecar checks API from plan 01"
provides:
  - "AmlCheck one-to-one transaction persistence"
  - "AML coverage matrix for every enabled crypto"
  - "AML policy functions for skip/check/manual_review decisions"
  - "aml-shkeeper HTTP client"
affects: [walletnotify, callback-gate, aml-processing, grither-pay-callback]
tech-stack:
  added: []
  patterns:
    - "SHKeeper owns deposit AML policy; sidecar owns provider calls."
    - "Unsupported or limited crypto coverage fails closed to manual_review."
key-files:
  created:
    - "migrations/versions/001_aml_deposit_checks.py"
    - "shkeeper/services/aml_coverage.py"
    - "shkeeper/services/aml_policy.py"
    - "shkeeper/services/aml_shkeeper_client.py"
    - "tests/test_aml_coverage.py"
    - "tests/test_aml_policy.py"
  modified:
    - "shkeeper/__init__.py"
    - "shkeeper/models.py"
key-decisions:
  - "AML skip defaults are 100 USD per check threshold and 300 USD rolling 24h cumulative limit."
  - "Skipped checks use score=None and decision_reason=amount_below_aml_threshold."
  - "SHKeeper does not import AMLBot credentials; it only calls aml-shkeeper."
patterns-established:
  - "Policy functions return AmlCheck snapshots with canonical decision values."
  - "Coverage policies are explicit and tested against enabled Crypto.instances."
requirements-completed: ["SPEC-01", "SPEC-02", "SPEC-03", "SPEC-07", "SPEC-08", "SPEC-09", "SPEC-10", "SPEC-11", "SPEC-13", "SPEC-14"]
duration: 31min
completed: 2026-05-03
---

# Phase 001 Plan 02 Summary

**SHKeeper now has local AML persistence, explicit crypto coverage, sidecar client plumbing, and deterministic deposit policy decisions.**

## Performance

- **Started:** 2026-05-03T07:45:00Z
- **Completed:** 2026-05-03T08:16:00Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments

- Added `AmlCheck` linked 1:1 to `Transaction`, plus Alembic migration.
- Added AML config defaults for sidecar host/auth, score threshold, skip threshold, cumulative skip window, retry, and timeout.
- Added `AML_COVERAGE` with supported AMLBot mappings and fail-closed unsupported/limited policies.
- Added `AmlShkeeperClient` that calls only `/api/v1/checks`; no direct AMLBot credentials are referenced.
- Added policy functions for `build_deposit_id`, `build_idempotency_key`, `should_skip_aml`, `build_skipped_check`, `decision_from_provider_result`, and `is_terminal`.

## Task Commits

1. **SHKeeper AML foundation** - `b7c2443` (`feat(001-02)`)

## Verification

- `python -m compileall shkeeper` passed.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_coverage.py'` passed: 4 tests.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_policy.py'` passed: 8 tests.
- `rg -n "access_key|accessId|AMLBot" shkeeper/services/aml_shkeeper_client.py` returned no matches.
- `git diff --check` passed.

## Code Review

Review completed after implementation. No blocking findings remain.

Auto-fixed during review:
- Added ISO timestamp parsing for sidecar `next_retry_at` and `timeout_at` fields before assignment to DateTime columns.
- Removed unused imports from policy/tests.
- Included `XMR` coverage alongside the planned `MONERO` alias because current SHKeeper Monero crypto symbol is `XMR`.

## Deviations from Plan

Added `XMR` as an explicit coverage key in addition to `MONERO`; this matches the actual `Crypto.instances` symbol and keeps coverage fail-closed for Monero.

## Issues Encountered

The global Python environment lacked Flask and SHKeeper dependencies. A temporary venv was created at `/tmp/shkeeper-venv` for verification.

## User Setup Required

None for this plan. Runtime values can override the AML defaults through environment variables.

## Next Phase Readiness

Plan 03 can now create or reuse `AmlCheck` records from walletnotify, poll aml-shkeeper, and gate final callbacks on terminal AML state.

---
*Phase: 001-amlbot-deposit-approval-gate*
*Completed: 2026-05-03*
