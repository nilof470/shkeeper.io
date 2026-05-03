---
phase: 001-amlbot-deposit-approval-gate
plan: 01
subsystem: aml-sidecar
tags: [amlbot, flask, celery, idempotency, callbacks]
requires: []
provides:
  - "aml-shkeeper /api/v1/checks create-or-return API"
  - "Normalized AMLBot provider evidence persistence"
  - "Sidecar unittest coverage for idempotency and normalization"
affects: [aml-shkeeper, shkeeper-aml-client, deposit-callback-gate]
tech-stack:
  added: []
  patterns: ["Sidecar owns provider credentials and raw AMLBot evidence; SHKeeper consumes normalized check state."]
key-files:
  created:
    - "/Users/test/PycharmProjects/aml-shkeeper/tests/test_checks_api.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/tests/test_amlbot_normalization.py"
  modified:
    - "/Users/test/PycharmProjects/aml-shkeeper/app/api/views.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/app/models.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/app/tasks.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/app/aml_bot_api.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/app/config.py"
    - "/Users/test/PycharmProjects/aml-shkeeper/requirements.txt"
key-decisions:
  - "Sidecar duplicate creates return existing state by deposit_id or idempotency_key."
  - "Below-min skip decisions are removed from aml-shkeeper; SHKeeper owns de-minimis policy."
  - "Provider errors and missing scores never become approved sidecar states."
patterns-established:
  - "Versioned sidecar API shape is stable for SHKeeper polling."
  - "Legacy endpoints remain compatibility-only and no longer hard-fail duplicates."
requirements-completed: ["SPEC-01", "SPEC-02", "SPEC-09", "SPEC-12", "SPEC-14"]
duration: 28min
completed: 2026-05-03
---

# Phase 001 Plan 01 Summary

**aml-shkeeper now exposes an idempotent AMLBot checks API with persisted raw provider evidence and normalized polling state.**

## Performance

- **Started:** 2026-05-03T07:16:00Z
- **Completed:** 2026-05-03T07:44:00Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Added `/api/v1/checks` POST/GET with Basic Auth, `deposit_id`, and `idempotency_key`.
- Expanded `Transactions` for provider status, raw response JSON, signals JSON, threshold, retries, timeout, UID, report URL, and errors.
- Refactored Celery processing to operate on persisted check rows and normalize AMLBot success, pending, and error responses.
- Preserved legacy `/<symbol>/check_tx` and `/<symbol>/get_score/<txid>` while removing fake below-min `score=0`.

## Task Commits

1. **Sidecar production API, provider normalization, and tests** - `5ac35a7` (`feat(001-01)`)

## Verification

- `python -m compileall app` passed in `/Users/test/PycharmProjects/aml-shkeeper`.
- `/tmp/aml-shkeeper-venv/bin/python -m unittest discover -s tests` passed: 7 tests.
- `rg -n "score = 0|tx already in DB" app` returned no matches.
- `rg -n "deposit_id|idempotency_key|raw_response_json|signals_json" app` shows the new contract fields.

## Code Review

Review completed after implementation. No blocking findings remain.

Auto-fixed during review:
- Pinned `Werkzeug<3` because fresh installs of `Flask==2.2.5` pull Werkzeug 3.x and break Flask test/runtime compatibility.
- Made SQLite test engine options avoid MySQL-only `connect_timeout` and `READ COMMITTED`.
- Removed unused task imports and hardened Flask context cleanup.

## Deviations from Plan

The plan did not mention `requirements.txt` or `db_import.py`, but both changes were required to run the new sidecar tests in a fresh environment and avoid a real Flask/Werkzeug compatibility fault. No change to production AML semantics.

## Issues Encountered

The base Python environment had no sidecar dependencies installed. A temporary venv was created at `/tmp/aml-shkeeper-venv`; dependencies were installed there only for verification.

## User Setup Required

None for this plan. Live AMLBot credentials are still external runtime configuration.

## Next Phase Readiness

SHKeeper can now target `aml-shkeeper` through `/api/v1/checks` using stable deposit identity, once SHKeeper-side persistence and policy are added in plan 02.

---
*Phase: 001-amlbot-deposit-approval-gate*
*Completed: 2026-05-03*
