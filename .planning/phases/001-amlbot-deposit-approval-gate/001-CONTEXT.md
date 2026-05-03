# Phase 001: amlbot-deposit-approval-gate - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning

<domain>
## Phase Boundary

This phase adds a transaction-level AML policy gate for confirmed SHKeeper deposits before the final merchant callback can be accepted as creditable by `grither-pay`. SHKeeper remains responsible only for deposit intake, AML policy evaluation, `aml-shkeeper` interaction, and AML-enriched callback delivery. `grither-pay` owns balance crediting and manual review.

</domain>

<spec_lock>
## SPEC Lock

`.planning/phases/001-amlbot-deposit-approval-gate/001-SPEC.md` locks 15 requirements for this phase. Downstream planning must treat the SPEC as the source of WHAT and WHY, and this CONTEXT as the source of HOW decisions made during discussion.

</spec_lock>

<decisions>
## Implementation Decisions

### AML lifecycle in SHKeeper
- **D-01:** Store AML state in a separate `AmlCheck` or `DepositAmlCheck` model linked 1:1 to `Transaction` with `transaction_id` unique.
- **D-02:** Keep `Transaction` as the blockchain/payment record. Do not add all AML lifecycle fields directly to `Transaction`.
- **D-03:** Put AML orchestration behind dedicated services: policy evaluation, `aml-shkeeper` client, AML processing/polling, and callback payload construction.
- **D-04:** The existing scheduler/callback flow can be extended so callbacks are sent only after the transaction is confirmed and its `AmlCheck` is terminal.

### SHKeeper to aml-shkeeper contract
- **D-05:** Introduce a production versioned `aml-shkeeper` API, such as `POST /api/v1/checks` and `GET /api/v1/checks/<deposit_id>`.
- **D-06:** The new contract must require stable `deposit_id` and `idempotency_key`, and must return normalized check state suitable for SHKeeper snapshots and callback retries.
- **D-07:** Duplicate create requests must return the existing check state instead of a hard duplicate error.
- **D-08:** Existing `aml-shkeeper` endpoints like `/check_tx` and `/get_score/<txid>` are reference/legacy surfaces, not the production contract SHKeeper should target.

### De-minimis skip policy
- **D-09:** Current defaults: `AML_MIN_CHECK_AMOUNT_FIAT=100`, `AML_SKIP_CUMULATIVE_LIMIT_FIAT=300`, `AML_SKIP_CUMULATIVE_WINDOW=24h`.
- **D-10:** SHKeeper may skip AMLBot for deposits below USD 100 while cumulative skipped deposits for the same `external_id + crypto + deposit address` stay within USD 300 over 24 hours.
- **D-11:** Once the cumulative skip limit would be exceeded, the deposit goes through AMLBot via `aml-shkeeper`.
- **D-12:** Skipped checks must emit `aml.status="skipped"` and `aml.score=null`; never store or emit a fake zero-risk score.
- **D-13:** Sweep is separate operational logic controlled outside this feature. Current recommendation for operations is `SWEEP_MIN_AMOUNT_FIAT=300`, based on about USD 0.70 sweep cost, USD 0.20-0.25 AML check cost, and a 0.5% target cost ratio.
- **D-14:** If AMLBot KYT/address monitoring pricing is later confirmed to be non-per-transaction, thresholds may be recalculated.

### Crypto coverage policy
- **D-15:** Add an explicit `AML_COVERAGE` matrix for every enabled `Crypto.instances` entry.
- **D-16:** Supported assets map to AMLBot asset/network values and follow the normal skip/check/approve/manual_review policy.
- **D-17:** Unsupported, limited-analysis, or missing mappings fail closed into `deposit_decision="manual_review"` with `decision_reason="unsupported_asset"` or `decision_reason="limited_analysis_requires_review"`.
- **D-18:** Coverage tests must fail if any enabled crypto lacks an explicit policy entry. Runtime behavior must never auto-credit a confirmed deposit because mapping is missing.

### the agent's Discretion

The implementation may choose exact service/module names, retry intervals, timeout values, and migration revision naming, provided it preserves the SPEC requirements and decisions above.

</decisions>

<specifics>
## Specific Ideas

- Cost target discussed: AML and sweep operating costs should remain around or below 0.5% of processed volume where practical.
- Public AMLBot pricing found during discussion: AMLBot publishes "from USD 0.20/check"; planning should use USD 0.20-0.25/check unless a contract says otherwise.
- For sweep economics, discussion assumed TRON sweep cost around 2 TRX, approximately USD 0.70. Sweep remains out of scope for SHKeeper AML callback gating.
- KYT/address/customer monitoring may become a better mode for micro-deposits if AMLBot confirms pricing is not per incoming transaction.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Locked requirements
- `.planning/phases/001-amlbot-deposit-approval-gate/001-SPEC.md` - Locked requirements, callback contract, boundaries, and acceptance criteria.
- `.planning/notes/2026-04-30-amlbot-only-no-refee.md` - Project decision: AMLBot only, no re:Fee AML.

### Codebase intelligence
- `.planning/codebase/ARCHITECTURE.md` - Current SHKeeper request, transaction, scheduler, and callback architecture.
- `.planning/codebase/INTEGRATIONS.md` - Existing coin sidecar, callback, API, and deployment integration points.
- `.planning/codebase/CONCERNS.md` - Security and correctness concerns relevant to financial callbacks and external integrations.
- `.planning/codebase/TESTING.md` - Current lack of automated tests and recommended test strategy.

### External research
- `https://amlbot.com/` - Public AMLBot pricing floor and product positioning.
- `https://amlbot.com/transaction-monitoring` - AMLBot KYT and continuous monitoring overview.
- `https://amlbot.com/ua/api-integration` - Address monitoring/API integration marketing claims; pricing must be verified before relying on it.
- `https://blog.amlbot.com/amlbot-plans-got-an-upgrade-lite-pro-and-pro/` - Public plan details showing Lite/Pro/Pro+ capabilities and API/monitoring positioning.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `shkeeper/api_v1.py:467` - `walletnotify` receives sidecar deposit notifications, creates confirmed `Transaction` rows, updates invoices, and currently calls `send_notification(tx)` immediately when enough confirmations exist.
- `shkeeper/models.py:596` - `Transaction` is the current deposit record with uniqueness on `(crypto, txid, invoice_id)`; add AML state as a separate model instead of inflating this entity.
- `shkeeper/callback.py:68` - `send_notification(tx)` builds the merchant callback and should be extended to include AML fields on the trigger transaction.
- `shkeeper/callback.py:149` and `shkeeper/callback.py:346` - scheduler helpers already retry callbacks and update confirmations; they are natural integration points for AML polling and callback gating.
- `shkeeper/tasks.py:9` - the 60-second `callback` scheduler job can call AML processing between confirmation updates and callback sending.

### Established Patterns
- SHKeeper is a Flask monolith using SQLAlchemy models, Alembic migrations, and in-process APScheduler jobs.
- Cross-cutting behavior can live in `shkeeper/services/`, matching existing service patterns such as payout and balance services.
- Existing callbacks expect HTTP 202 from the merchant and retry until accepted; AML-enriched callbacks must preserve this retry behavior and payload stability.
- JSON decimal values should follow current callback serialization via `remove_exponent`.

### Integration Points
- Add AML model/migration in SHKeeper for local snapshots and terminal decisions.
- Add an `aml-shkeeper` client service that handles only sidecar HTTP, auth, transport errors, and response normalization.
- Add an AML policy/processing service that computes de-minimis skip, creates/reuses `AmlCheck`, polls pending checks, applies score policy, and marks terminal decisions.
- Update callback construction so only the trigger transaction must include complete AML fields; non-trigger historical transactions may remain backward-compatible unless planner chooses to include non-creditable snapshots.
- Add coverage validation against `Crypto.instances` so every enabled coin has a supported or explicit manual-review policy.

</code_context>

<deferred>
## Deferred Ideas

- AMLBot KYT/address/customer monitoring mode for micro-deposit economics. Revisit after AMLBot confirms whether address/CID monitoring is charged per address/CID/month or per incoming transaction.
- Quarantine wallet flow if operations ever require sweeping funds before AML clearance. This is outside the current SHKeeper callback-gating phase.

</deferred>

---

*Phase: 001-amlbot-deposit-approval-gate*
*Context gathered: 2026-05-03*
