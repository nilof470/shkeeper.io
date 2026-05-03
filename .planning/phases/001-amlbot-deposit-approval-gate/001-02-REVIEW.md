# 001-02 Code Review

Verdict: PASS

Findings: none blocking.

Reviewed areas:
- One-to-one `AmlCheck.transaction_id` relationship and migration constraints.
- AML defaults for 100 USD min check, 300 USD cumulative skip, 24h window, 0.10 score threshold.
- Coverage matrix fail-closed behavior for unsupported and limited assets.
- Sidecar client boundary: no direct AMLBot credential usage.
- Policy decisions for skip, threshold credit, provider error, timeout, missing score, unsupported asset.

Fixes made before commit:
- Parsed sidecar ISO timestamp strings into DateTime values.
- Added `XMR` coverage because current Monero module uses `crypto = "XMR"`.
- Removed unused imports.

Verification:
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_coverage.py'` passed.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_policy.py'` passed.
- `python -m compileall shkeeper` passed.
- `git diff --check` passed.
