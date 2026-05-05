---
phase: 002-koinkyt-aml-provider-documentation
plan: 01
subsystem: aml-provider-docs
tags: [aml, koinkyt, documentation, integration]
key-files:
  - docs/koinkyt_deposit_gate.md
  - tests/test_aml_contract_docs.py
  - .planning/phases/002-koinkyt-aml-provider-documentation/002-CONTEXT.md
  - .planning/phases/002-koinkyt-aml-provider-documentation/002-RESEARCH.md
  - .planning/phases/002-koinkyt-aml-provider-documentation/002-01-PLAN.md
---

# Phase 002 Plan 01 Summary

## Completed

- Expanded `docs/koinkyt_deposit_gate.md` into the active Koinkyt AML provider contract.
- Updated the target architecture so SHKeeper calls `aml-shkeeper`, and `aml-shkeeper` calls Koinkyt.
- Restored SHKeeper to the sidecar client path and removed the direct SHKeeper Koinkyt client path.
- Added Koinkyt provider handling inside the sibling `aml-shkeeper` service.
- Added explicit sections for supported coverage, response mapping, failure policy, live probe checklist, and open questions.
- Documented the exact supported mappings from the Koinkyt PDF:
  - `BTC -> blockchain=btc, token=`
  - `ETH -> blockchain=eth, token=`
  - `ETH-USDT -> blockchain=eth, token=USDT`
  - `ETH-USDC -> blockchain=eth, token=USDC`
  - `TRX -> blockchain=trx, token=`
  - `USDT -> blockchain=trx, token=USDT`
  - `USDC -> blockchain=trx, token=USDC`
- Preserved the `grither-pay` ownership boundary and trigger transaction credit rule.
- Added documentation contract assertions to `tests/test_aml_contract_docs.py`.

## Verification

- `python -m unittest tests.test_aml_contract_docs` exited 0.
- `python -m py_compile shkeeper/services/aml_shkeeper_client.py shkeeper/services/aml_coverage.py shkeeper/services/aml_processing.py shkeeper/services/aml_policy.py shkeeper/models.py shkeeper/__init__.py tests/test_aml_processing.py tests/test_aml_end_to_end.py tests/test_aml_callback_payload.py tests/test_aml_coverage.py tests/test_aml_contract_docs.py tests/conftest.py` exited 0.
- `python -m py_compile app/aml_bot_api.py app/tasks.py app/api/views.py app/models.py app/config.py tests/test_checks_api.py tests/test_shkeeper_contract.py tests/test_amlbot_normalization.py` exited 0 in `/Users/test/PycharmProjects/aml-shkeeper`.
- `rg -n "Koinkyt Contract|Supported Coverage|Response Mapping|Failure Policy|Live Probe Checklist|Open Questions|/transfer|KOINKYT_API_KEY|AML_SHKEEPER_HOST" docs/koinkyt_deposit_gate.md` found the required documentation sections and strings.
- `rg -n "Koinkyt deposit gate\\]\\(docs/koinkyt_deposit_gate.md\\)" README.md` found the README link.

## Open Items

- Live Koinkyt responses are explicitly deferred. Current docs and integration planning follow `API_Documentation.pdf` only.
- Full `aml-shkeeper` Python test suite still requires project dependencies such as Celery in the local environment.

## Self-Check

PASSED: The documentation contract is explicit, tested, and preserves the known unresolved provider questions instead of hiding them.
