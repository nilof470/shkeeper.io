# 001-04 Code Review

Verdict: PASS

Findings: none blocking.

Reviewed areas:
- E2E tests cover pending, approved, manual_review, skipped, cumulative-limit, unsupported, and replay behavior.
- Docs state AMLBot-only and no re:Fee AML path.
- Docs state grither-pay credits only `transactions[].trigger == true` with `deposit_decision="credit"`.
- Sidecar contract test asserts SHKeeper fields and idempotent duplicate POST.
- Sweep threshold and KYT/address monitoring are documented as separate/deferred.

Fixes made before commit:
- Bound `Crypto.instances["BTC"]` to a fake test crypto wallet in E2E callback delivery fixture.

Verification:
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests` passed.
- `/tmp/aml-shkeeper-venv/bin/python -m unittest discover -s tests` passed.
- `python -m compileall shkeeper` passed.
- `git diff --check` passed in both repositories.
