# Phase 001: AMLBot Deposit Approval Gate - Specification

**Created:** 2026-05-01
**Ambiguity score:** 0.11 (gate: <= 0.20)
**Requirements:** 14 locked

## Goal

Every confirmed SHKeeper deposit for every enabled cryptocurrency is AML-gated through AMLBot before `grither-pay` can credit a user balance, with SHKeeper sending the AML result in the existing payment callback and `grither-pay` making the final credit or manual-review decision.

## Background

SHKeeper currently receives sidecar deposit notifications at `POST /api/v1/walletnotify/<crypto>/<txid>`, persists a `Transaction`, updates the related `Invoice`, and immediately sends the merchant callback once the transaction has enough confirmations. The callback payload is invoice-centric: it contains top-level invoice fields such as `external_id`, `crypto`, `addr`, `paid`, `status`, and `balance_*`, plus a `transactions[]` array where the newly processed transaction is marked with `trigger: true`.

The target business flow uses static deposit addresses: one reusable large invoice per user and per coin. In that mode the invoice usually remains `PARTIAL`, so invoice-level `paid`, `status`, and `balance_*` cannot determine whether a single deposit should be credited. The credit decision must be transaction-level and must be based on the trigger transaction in the callback.

Research into `upstream/custom_aml2` showed useful ideas but not a ready solution. That branch adds an AML checker, invoice statuses, `Transaction.aml_score`, and scheduler jobs, but it is based on an older SHKeeper branch, rewrites unrelated current code, lacks a migration for the AML columns/statuses, evaluates AML at invoice level, still sends callbacks before AML completion, and mixes AML approval with external withdrawal behavior. The desired feature must keep SHKeeper focused on deposit intake, AMLBot checking, and callback delivery only.

AMLBot public API documentation states that transaction checks return `success` when risk data is ready and `pending` while calculations are in progress. Transaction verification accepts fields including transaction hash, receiver address, asset, direction, access ID, generated signature, locale, and flow, and returns AML data including `uid`, `riskscore`, `signals`, `network`, `asset`, `status`, and report links. The docs also note that checks should be performed no earlier than about 5 minutes after first confirmation for up-to-date AMLBot data, and some networks have limited analysis coverage.

## Requirements

1. **AMLBot-only provider policy**: Deposit AML must use AMLBot only.
   - Current: No AML gate exists on `main`; a prior note mentioned re:Fee research and `upstream/custom_aml2` uses an `aml-shkeeper` style service but is not integrated into the current flow.
   - Target: No deposit AML requirement, config, callback field, or decision branch depends on re:Fee AML.
   - Acceptance: Searching the new AML deposit-gating implementation for re:Fee AML provider logic finds no runtime path used for AML decisions.

2. **All enabled deposit cryptos are gated**: Every confirmed incoming `receive` transaction for every enabled SHKeeper crypto must receive an AML terminal decision before the payment callback can be accepted as creditable by `grither-pay`.
   - Current: `walletnotify` sends a callback after enough confirmations without AML.
   - Target: BTC, LTC, DOGE, ETH family, TRX/TRC20, BNB/BEP20, Polygon, Avalanche, Solana, XRP, Arbitrum, Optimism, TON, FIRO, Monero, Lightning, and any future enabled crypto are covered by either an AMLBot check or a fail-closed `manual_review` decision.
   - Acceptance: For each enabled crypto in `Crypto.instances`, a confirmed incoming transaction cannot produce a callback with `deposit_decision: "credit"` unless an AMLBot success result with a usable risk score has been recorded for that transaction.

3. **Transaction-level decisions**: AML state and credit/manual-review decisions must be attached to the individual deposit transaction, not to the invoice as a whole.
   - Current: Existing callbacks contain transaction objects, but no AML data; `upstream/custom_aml2` adds invoice AML statuses and makes invoice-level decisions.
   - Target: Each `Transaction` has its own AML lifecycle and final `deposit_decision`; multiple deposits on the same reusable invoice are evaluated independently.
   - Acceptance: Two transactions on the same invoice can have different AML scores and different decisions without changing the other transaction's decision.

4. **Static-address compatibility**: The callback contract must support the one-user-one-wallet static address flow.
   - Current: Static-address invoices remain large and usually `PARTIAL`; top-level `paid` and `status` are not reliable for per-deposit crediting.
   - Target: `grither-pay` credits only the callback transaction where `transactions[].trigger == true` and ignores invoice-level `paid`, `status`, and `balance_*` for credit decisions.
   - Acceptance: A callback with `paid: false`, `status: "PARTIAL"`, and a trigger transaction with `deposit_decision: "credit"` is sufficient for `grither-pay` to credit exactly the trigger transaction amount.

5. **Callback is delayed until AML terminal state**: SHKeeper must not send the final payment callback for a confirmed deposit until AML has reached a terminal decision.
   - Current: `walletnotify` calls `send_notification(tx)` immediately when `need_more_confirmations` is false, and the scheduler retries callbacks without AML gating.
   - Target: Confirmed transactions enter an AML pending state first; the final callback is sent only after the transaction resolves to `credit` or `manual_review`.
   - Acceptance: In a test where AMLBot returns `pending`, no final payment callback is sent until a later poll/recheck produces a terminal decision or timeout policy resolves it to `manual_review`.

6. **Existing callback shape is preserved**: The payment callback must remain backward-compatible at the top level and only extend transaction objects with AML decision fields.
   - Current: `send_notification(tx)` sends `external_id`, `crypto`, `addr`, `fiat`, `balance_fiat`, `balance_crypto`, `paid`, `status`, `transactions`, fee fields, and `overpaid_fiat`.
   - Target: Those existing fields remain present and semantically unchanged; AML fields are added to transaction entries, with complete AML fields required on the trigger transaction.
   - Acceptance: A JSON schema or callback test confirms all current callback fields still exist and the trigger transaction contains `deposit_decision`, `decision_reason`, and `aml`.

7. **Canonical callback decision values**: Callback decision values must be small, stable, and owned by our integration contract.
   - Current: There is no `deposit_decision`; earlier discussion rejected a generic `creditable=true` field.
   - Target: The trigger transaction contains `deposit_decision: "credit"` only when the deposit may be auto-credited; all other terminal outcomes use `deposit_decision: "manual_review"`.
   - Acceptance: No other `deposit_decision` values appear in generated callbacks.

8. **Canonical decision reasons**: SHKeeper must send a normalized `decision_reason` for audit and grither-pay manual-review routing.
   - Current: AMLBot does not provide our `decision_reason`; this is our normalized field.
   - Target: Supported reasons include at least `score_below_threshold`, `risk_score_above_threshold`, `aml_pending_timeout`, `aml_provider_error`, `unsupported_asset`, `incomplete_aml_result`, and `limited_analysis_requires_review`.
   - Acceptance: Each manual-review callback contains exactly one non-empty `decision_reason` from the supported reason set.

9. **AML payload contains provider evidence**: The trigger transaction must include enough AMLBot evidence for `grither-pay` and operators to understand the decision without querying SHKeeper.
   - Current: Callback transactions include txid, date, amounts, trigger, and crypto only.
   - Target: The trigger transaction includes an `aml` object with `provider`, `provider_status`, `status`, `score`, `threshold`, `uid`, `asset`, `network`, `signals`, and optional report/error metadata when available.
   - Acceptance: A successful AMLBot check with a risk score produces a callback whose `aml.uid`, `aml.score`, `aml.threshold`, and `aml.signals` match the normalized AMLBot response stored for the transaction.

10. **Score policy is explicit and fail-closed**: Auto-credit is allowed only when AMLBot returns a successful, complete result and the risk score is within the configured threshold.
    - Current: No score policy exists on `main`; `upstream/custom_aml2` uses score values but does not provide the target callback decision contract.
    - Target: `score <= AML_MAX_ACCEPT_SCORE` resolves to `deposit_decision: "credit"` with `decision_reason: "score_below_threshold"`; any higher score, missing score, provider error, timeout, unsupported asset, or incomplete result resolves to `manual_review`.
    - Acceptance: Tests cover below-threshold, equal-threshold, above-threshold, pending timeout, provider error, unsupported asset, and missing-score cases.

11. **AMLBot pending and timing behavior is handled**: Pending AMLBot results must be retried without blocking the scheduler indefinitely.
    - Current: SHKeeper has a 60-second scheduler loop for confirmation and callback retry, but no AML polling lifecycle.
    - Target: AML checks store attempt count, next retry time, last provider status, provider UID when available, and timeout deadline; pending checks are retried or rechecked until success, provider failure, or timeout.
    - Acceptance: A pending result is retried according to configured intervals, and after timeout it produces a single `manual_review` callback with `decision_reason: "aml_pending_timeout"`.

12. **AMLBot asset coverage is explicit**: Every SHKeeper crypto symbol must map to an AMLBot asset/network policy or to an explicit unsupported/manual-review policy.
    - Current: Existing SHKeeper symbols differ from AMLBot asset names; the explored `aml-shkeeper` mapping covers only a subset of current SHKeeper coins.
    - Target: The system has a visible coverage map for all enabled SHKeeper cryptos, including wrapped tokens and networks, and no deposit can bypass AML because of a missing mapping.
    - Acceptance: A coverage test enumerates all crypto modules and fails if any enabled crypto lacks an AMLBot mapping or explicit unsupported/manual-review entry.

13. **Idempotency and retry safety**: Duplicate sidecar notifications, scheduler retries, and callback retries must not double-credit or create conflicting AML checks.
    - Current: `Transaction` uniqueness prevents duplicate transactions in normal paths, and callback retry uses `callback_confirmed`, but AML adds another async step.
    - Target: AML check creation and callback emission are idempotent per transaction; retries reuse the same AML record and callback payload until the merchant returns HTTP 202.
    - Acceptance: Replaying the same `walletnotify` and callback retry sequence does not create a second terminal AML record and does not produce a second distinct creditable callback for the same transaction.

14. **grither-pay owns balance credit and manual review**: SHKeeper must not credit user balances, implement manual deposit review UI, or decide grither-pay wallet state directly.
    - Current: SHKeeper only sends callbacks to merchant systems; grither-pay wallet crediting is external to this repository.
    - Target: SHKeeper sends AML-enriched callbacks; `grither-pay` credits the user's wallet only when the trigger transaction has `deposit_decision: "credit"`, and sends every other result to manual review in the grither-pay admin flow.
    - Acceptance: The SHKeeper callback contract is sufficient for grither-pay to route `credit` and `manual_review` outcomes without calling SHKeeper admin UI or relying on SHKeeper invoice-level paid status.

## Callback Contract

The final payment callback keeps the current SHKeeper top-level payload and extends the trigger transaction:

```json
{
  "external_id": "user-123:USDT",
  "crypto": "USDT",
  "addr": "T...",
  "fiat": "USD",
  "balance_fiat": "500.00",
  "balance_crypto": "500.000000",
  "paid": false,
  "status": "PARTIAL",
  "transactions": [
    {
      "txid": "new-tx-hash",
      "date": "2026-05-01 12:00:00",
      "amount_crypto": "100.000000",
      "amount_fiat": "100.00",
      "amount_fiat_without_fee": "99.50",
      "fee_fiat": "0.50",
      "trigger": true,
      "crypto": "USDT",
      "deposit_decision": "credit",
      "decision_reason": "score_below_threshold",
      "aml": {
        "provider": "amlbot",
        "provider_status": "success",
        "status": "approved",
        "score": "0.04",
        "threshold": "0.10",
        "uid": "amlbot-check-id",
        "asset": "TRX",
        "network": "TRON",
        "signals": {}
      }
    }
  ],
  "fee_percent": "0.5",
  "fee_fixed": "0",
  "fee_policy": "PERCENT_FEE",
  "overpaid_fiat": "0.00"
}
```

Manual-review example for a risky transaction:

```json
{
  "txid": "new-tx-hash",
  "date": "2026-05-01 12:00:00",
  "amount_crypto": "100.000000",
  "amount_fiat": "100.00",
  "amount_fiat_without_fee": "99.50",
  "fee_fiat": "0.50",
  "trigger": true,
  "crypto": "USDT",
  "deposit_decision": "manual_review",
  "decision_reason": "risk_score_above_threshold",
  "aml": {
    "provider": "amlbot",
    "provider_status": "success",
    "status": "declined",
    "score": "0.72",
    "threshold": "0.10",
    "uid": "amlbot-check-id",
    "asset": "TRX",
    "network": "TRON",
    "signals": {
      "sanctions": 0.015,
      "mixer": 0.01,
      "risky_exchange": 0.403
    }
  }
}
```

## Boundaries

**In scope:**
- Deposit AML gating for confirmed incoming SHKeeper transactions.
- AMLBot as the only AML provider for this feature.
- Transaction-level AML lifecycle and terminal deposit decisions.
- Callback payload extension for the trigger transaction.
- Support policy for all enabled SHKeeper crypto symbols through AMLBot mapping or explicit fail-closed manual review.
- Persistence and migration for AML result, decision, raw provider evidence, attempts, errors, and timestamps.
- Scheduler or background processing needed to poll pending AML results and send callbacks after terminal decision.
- Tests or verification fixtures for callback payload, decision policy, static-address flow, idempotency, and crypto coverage.
- Documentation of the callback contract consumed by `grither-pay`.

**Out of scope:**
- re:Fee AML integration - the project decision is AMLBot only.
- SHKeeper admin UI for manual deposit review - manual review belongs in `grither-pay`.
- Automatic wallet crediting inside SHKeeper - `grither-pay` owns user balance updates.
- Invoice-level AML status as the source of credit decisions - static-address deposits require transaction-level decisions.
- External drain, auto-withdraw, refund, or payout AML behavior - this phase is deposit callback gating only.
- Replacing SHKeeper invoice mechanics or static-address invoice reuse - existing invoice creation and address reuse remain intact.
- Full implementation of the `grither-pay` admin review UI - this spec defines the callback contract and expected downstream behavior only.

## Constraints

- SHKeeper is a Flask monolith with SQLite, SQLAlchemy, Alembic migrations, and APScheduler in-process background jobs.
- Existing merchant callback retry semantics must remain: receiver returns HTTP 202, otherwise SHKeeper retries.
- The feature must work with static-address invoices where `paid` may remain false and `status` may remain `PARTIAL`.
- AMLBot transaction checks can return `pending`; the system must tolerate delayed AML completion.
- AMLBot documentation advises waiting about 5 minutes after first confirmation for up-to-date check data; implementation planning must decide whether to delay initial check, poll, or both.
- Some AMLBot networks have limited analysis coverage. Limited, unsupported, or incomplete coverage must be visible in `aml` metadata and must not silently auto-credit unless an explicit configured policy allows it.
- Secrets must be environment/config driven and must not be stored in source control.
- Callback payloads must remain JSON serializable with decimal values encoded consistently with existing SHKeeper callbacks.

## Acceptance Criteria

- [ ] A confirmed deposit cannot trigger the final payment callback before AML reaches `credit` or `manual_review`.
- [ ] The trigger transaction in every final callback contains `deposit_decision`, `decision_reason`, and `aml`.
- [ ] `deposit_decision` is only `credit` or `manual_review`.
- [ ] `score <= AML_MAX_ACCEPT_SCORE` with AMLBot `success` and a usable score produces `deposit_decision: "credit"`.
- [ ] Above-threshold score, provider error, timeout, unsupported asset, limited-analysis-required review, and incomplete result each produce `deposit_decision: "manual_review"`.
- [ ] Existing callback top-level fields and existing transaction amount fields remain present.
- [ ] Static-address invoices can remain `PARTIAL` while the trigger transaction is still creditable.
- [ ] Multiple deposits to the same invoice/address are evaluated and callbacked independently.
- [ ] Coverage validation fails if an enabled SHKeeper crypto has no AMLBot mapping and no explicit unsupported/manual-review policy.
- [ ] Duplicate `walletnotify` or scheduler retries do not create duplicate terminal AML decisions for the same transaction.
- [ ] Callback retries send a stable payload until `grither-pay` returns HTTP 202.
- [ ] No SHKeeper admin UI manual-review workflow is added.
- [ ] No re:Fee AML runtime path is used.

## Ambiguity Report

| Dimension           | Score | Min   | Status | Notes |
|---------------------|-------|-------|--------|-------|
| Goal Clarity        | 0.92  | 0.75  | PASS   | Outcome is transaction-level AML gating before grither-pay credit. |
| Boundary Clarity    | 0.91  | 0.70  | PASS   | Provider, SHKeeper role, grither-pay role, and out-of-scope UI/payout work are explicit. |
| Constraint Clarity  | 0.82  | 0.65  | PASS   | Static-address, callback retry, AMLBot pending, and crypto coverage constraints are captured. |
| Acceptance Criteria | 0.86  | 0.70  | PASS   | Callback, score policy, idempotency, and coverage checks are pass/fail. |
| **Ambiguity**       | 0.11  | <=0.20| PASS   | Ready for discuss-phase / implementation planning. |

Status: PASS = met minimum, WARN = below minimum.

## Interview Log

| Round | Perspective | Question summary | Decision locked |
|-------|-------------|------------------|-----------------|
| 1 | Researcher | Is there an existing AML integration to reuse? | `upstream/custom_aml2` is a reference only, not ready to merge or use as-is. |
| 1 | Researcher | Which AML provider is allowed? | AML must use AMLBot only; re:Fee AML is excluded. |
| 2 | Simplifier | What is the simplest correct SHKeeper responsibility? | SHKeeper receives deposit, performs AMLBot check, and sends an AML-enriched callback to `grither-pay`. |
| 2 | Simplifier | Who decides user balance crediting? | `grither-pay` credits only approved deposits and routes every other result to manual review. |
| 3 | Boundary Keeper | Should SHKeeper admin UI handle manual review? | No. Manual deposit review belongs in `grither-pay`, not SHKeeper admin. |
| 3 | Boundary Keeper | Does invoice `paid` decide crediting in static-address mode? | No. `grither-pay` must process the trigger transaction, not invoice aggregate state. |
| 4 | Failure Analyst | What happens for non-approved AML states? | Anything except explicit AML approval becomes `manual_review`. |
| 4 | Failure Analyst | What should the callback expose? | Preserve existing callback fields and add `deposit_decision`, `decision_reason`, and `aml` to the trigger transaction. |
| 5 | Seed Closer | What does all-crypto AML mean when AMLBot support is partial or unavailable? | Every enabled crypto is covered by mapping or fail-closed manual review; no deposit bypasses AML gating. |

---

*Phase: 001-amlbot-deposit-approval-gate*
*Spec created: 2026-05-01*
*Next step: $gsd-discuss-phase 001 - implementation decisions for how to build what is specified above*
