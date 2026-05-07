---
status: complete
reviewed_at: 2026-05-07
type: code-review
commit: 911099b
---

# Code Review

## Findings

### MEDIUM: `aml.checked=false` can be ambiguous for skipped supported assets

File: `shkeeper/callback.py`

Lines: 62-75

For supported assets that SHKeeper skips because of `AML_MIN_CHECK_AMOUNT_FIAT`
or cumulative skip policy, `build_skipped_check()` creates an `AmlCheck` with
`provider_status=None`, `score=None`, and no `error_code`. The new callback
therefore sends `aml.checked=false`, but does not explain why AML was not
checked.

This weakens the new contract because grither-pay cannot distinguish:

- provider unsupported
- provider timeout/error
- SHKeeper local threshold skip
- incomplete/missing score

The fix should add neutral technical metadata for local skips, for example
`provider_status="skipped"` and `error_code="aml_skipped_by_shkeeper_policy"`,
or remove SHKeeper-side skip policy entirely if grither-pay should fully own AML
threshold decisions.

### LOW: Unsupported legacy `AmlCheck` rows will produce incomplete unsupported metadata

File: `shkeeper/callback.py`

Lines: 79-87

The new unsupported payload is only generated when `trigger_tx.aml_check` is
missing. If old rows already exist from the previous behavior where unsupported
assets were persisted as `manual_review`, callback generation will use
`_aml_payload()` instead and can emit `checked=false` with `provider_status=None`
and no `error_code=unsupported_asset`.

This is probably acceptable for a pre-integration development environment, but
it is worth handling defensively before production if any unsupported AML rows
were created before the deployment.

## Verification Notes

- Syntax check passed with `python -m py_compile`.
- `git diff --check` passed.
- Runtime unit tests could not be executed locally because Flask is not
  installed in the local Python environment.
