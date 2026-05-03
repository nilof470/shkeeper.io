---
date: "2026-04-30 20:22"
promoted: false
---

AML decision: do not integrate AML from re:Fee. AML checks must use AMLBot only.

Current target architecture: every SHKeeper deposit cryptocurrency is covered by an AML policy gate. Above-threshold deposits are checked through AMLBot. Below-threshold deposits may skip AMLBot for unit economics, but only with explicit `aml.status: "skipped"` audit metadata, no fake score `0`, and rolling cumulative limits to prevent deposit splitting. SHKeeper receives deposits, applies the AML policy, and sends AML-enriched callbacks to `grither-pay`. SHKeeper does not credit user balances and does not implement manual deposit review UI. `grither-pay` credits only `deposit_decision: "credit"` trigger transactions; all other outcomes go to manual review in `grither-pay`.

Architecture decision: use and productionize the existing `aml-shkeeper` sidecar for above-threshold AMLBot provider calls, raw AMLBot evidence storage, and provider recheck/polling. SHKeeper owns de-minimis/cumulative skip decisions, local transaction-level AML snapshots, callback gating, and callback payload construction.

`upstream/custom_aml2` is a reference for useful AMLBot/background-check ideas only, not a ready integration path. It is invoice-level, branch-stale, and includes external-drain behavior that is out of scope for the current design.
