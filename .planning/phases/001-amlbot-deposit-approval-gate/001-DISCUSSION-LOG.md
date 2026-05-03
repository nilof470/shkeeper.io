# Phase 001: amlbot-deposit-approval-gate - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md - this log preserves the alternatives considered.

**Date:** 2026-05-03
**Phase:** 001-amlbot-deposit-approval-gate
**Areas discussed:** AML lifecycle in SHKeeper, SHKeeper to aml-shkeeper contract, De-minimis skip policy, Crypto coverage policy

---

## AML lifecycle in SHKeeper

| Option | Description | Selected |
|--------|-------------|----------|
| Separate AmlCheck model plus AML service layer | Keep Transaction focused and model AML as its own lifecycle. | yes |
| Add AML fields directly to Transaction | Simpler schema, but expands Transaction responsibility. | |
| Store AML state only in aml-shkeeper | Less SHKeeper storage, but weak callback retry/audit behavior. | |

**User's choice:** Accepted separate `AmlCheck`/`DepositAmlCheck` linked 1:1 to `Transaction`.
**Notes:** User asked for the scalable, well-architected, SOLID-friendly approach. Decision: keep `Transaction` as the blockchain/payment record and put AML policy, sidecar calls, polling, and callback gating in services.

---

## SHKeeper to aml-shkeeper contract

| Option | Description | Selected |
|--------|-------------|----------|
| New versioned API with deposit_id/idempotency_key and normalized responses | Production contract for idempotent SHKeeper integration. | yes |
| Reuse current /check_tx and /get_score/<txid> as-is | Faster but txid-only and not enough for static-address/multi-output cases. | |
| Embed AMLBot calls directly in SHKeeper | Rejected by architecture decision to use aml-shkeeper. | |

**User's choice:** Accepted a new versioned `aml-shkeeper` contract.
**Notes:** Existing endpoints are treated as reference/legacy. Duplicate create requests must return existing state, not a hard duplicate error.

---

## De-minimis skip policy

| Option | Description | Selected |
|--------|-------------|----------|
| AML threshold 100 USD and cumulative skip 300 USD/24h; check after cumulative limit | Balances user UX, AML cost, and sweep economics. | yes |
| AML threshold 50 USD and cumulative skip 50 USD/24h | Earlier option; AML-only economics were fine but combined sweep economics were weaker. | |
| KYT/address monitoring mode if AMLBot pricing supports non-per-transaction monitoring | Deferred until pricing is confirmed. | |
| No skip; AMLBot-check every deposit | Too expensive for micro-deposits. | |

**User's choice:** Accepted current defaults: `AML_MIN_CHECK_AMOUNT_FIAT=100`, `AML_SKIP_CUMULATIVE_LIMIT_FIAT=300`, `AML_SKIP_CUMULATIVE_WINDOW=24h`.
**Notes:** Sweep is separate operational logic. Recommended sweep threshold is USD 300, but SHKeeper AML phase should not implement sweep behavior.

---

## Crypto coverage policy

| Option | Description | Selected |
|--------|-------------|----------|
| Explicit coverage matrix with fail-closed manual_review for unsupported/limited/missing mapping | Ensures every enabled crypto has a policy. | yes |
| Only configure mappings for currently supported AMLBot assets | Risk of silent coverage gaps. | |
| Allow unsupported assets to auto-credit below configured limits | Rejected as unsafe. | |
| Disable unsupported cryptocurrencies entirely | Operationally too restrictive for this phase. | |

**User's choice:** Accepted explicit `AML_COVERAGE` for all enabled cryptos.
**Notes:** Supported assets go through AMLBot; unsupported/limited/missing mappings fail closed into `manual_review`.

---

## the agent's Discretion

- Exact module names, retry intervals, timeout values, and migration revision naming.

## Deferred Ideas

- AMLBot KYT/address/customer monitoring mode if pricing is non-per-transaction.
- Quarantine wallet flow for any future sweep-before-clearance architecture.
