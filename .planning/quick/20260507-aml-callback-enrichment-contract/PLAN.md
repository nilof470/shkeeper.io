---
status: in_progress
created_at: 2026-05-07
type: quick-plan
---

# Plan

## Scope

Implement the AML callback enrichment contract from `SPEC.md`.

## Steps

1. Change AML coverage handling so unsupported provider assets do not create a
   terminal `manual_review` AML check and do not block final callbacks.
2. Ensure every trigger transaction gets an `aml` object:
   - `checked=true` for usable provider success results.
   - `checked=false` for unsupported assets, skipped checks, provider errors,
     missing scores, and timeouts.
3. Remove business decision fields from merchant callback payloads:
   `deposit_decision`, `decision_reason`, AML `status`, and AML `threshold`.
4. Keep technical AML provider data in callback payloads:
   provider, provider_status, score, uid, asset, network, signals, report_url,
   error_code, and error_message.
5. Update focused AML callback, processing, coverage, and docs tests.
6. Run the available test subset. If the local environment lacks dependencies,
   document the blocker and provide exact command to run in the app environment.

## Non-Goals

- No grither-pay implementation in this repo.
- No database migration unless existing nullable AML columns become insufficient.
- No change to blockchain invoice/payment fields.
- No change to refee/TRON energy behavior.
