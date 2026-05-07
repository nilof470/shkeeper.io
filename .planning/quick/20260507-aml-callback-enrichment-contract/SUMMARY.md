---
status: complete
completed_at: 2026-05-07
type: quick-summary
---

# Summary

Implemented the AML callback enrichment contract.

## Changed

- Unsupported AML assets now bypass AML check creation and do not block final
  callbacks.
- Callback transaction AML data now uses factual enrichment fields with
  `aml.checked`.
- `aml.supported`, `aml.check_status`, `aml.reason_code`, and `aml.policy`
  explain unchecked AML cases without returning business decisions.
- Merchant callback no longer emits `deposit_decision`, `decision_reason`, AML
  `status`, or AML `threshold`.
- Unsupported assets are represented as `aml.checked=false` with
  `provider_status=unsupported` and `error_code=unsupported_asset`.
- Koinkyt deposit gate docs and focused AML tests were updated for the new
  grither-pay decision boundary.

## Verification

- Passed: `python -m py_compile` for changed Python files.
- Passed: `git diff --check`.
- Blocked: focused `unittest` subset cannot run in the local Python because
  Flask is not installed in this environment (`ModuleNotFoundError: No module
  named 'flask'`).

## Follow-Up

Run the focused tests in an environment with project dependencies installed:

```bash
python -m unittest tests.test_aml_coverage tests.test_aml_processing tests.test_aml_end_to_end tests.test_aml_callback_payload tests.test_aml_contract_docs
```
