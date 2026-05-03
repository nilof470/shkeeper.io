# 001-03 Code Review

Verdict: PASS

Findings: none blocking.

Reviewed areas:
- Confirmed incoming callbacks are blocked unless `AmlCheck` is terminal.
- Scheduler processes AML before callback retries.
- Trigger transaction payload contains AML decision fields; non-trigger and unconfirmed payloads do not.
- Static-address invoice `PARTIAL` status does not prevent trigger transaction credit decision.
- AML checks do not start before configured confirmation requirements are satisfied.

Fixes made before commit:
- Removed temporary `AmlCheck` relationship attachment that caused duplicate inserts on provider result application.
- Deferred AML check creation until `need_more_confirmations=False`.
- Cleared stale retry schedule on terminal AML results.

Verification:
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_processing.py'` passed.
- `/tmp/shkeeper-venv/bin/python -m unittest discover -s tests -p 'test_aml_callback_payload.py'` passed.
- `python -m compileall shkeeper` passed.
- `git diff --check` passed.
