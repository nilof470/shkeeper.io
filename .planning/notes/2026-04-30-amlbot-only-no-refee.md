---
date: "2026-04-30 20:22"
promoted: false
---

AML decision: do not integrate AML from re:Fee. AML checks must use AMLBot only.

Current target architecture: AML is required for all SHKeeper deposit cryptocurrencies. SHKeeper receives deposits, performs AMLBot checks, and sends AML-enriched callbacks to `grither-pay`. SHKeeper does not credit user balances and does not implement manual deposit review UI. `grither-pay` credits only `deposit_decision: "credit"` trigger transactions; all other outcomes go to manual review in `grither-pay`.

`upstream/custom_aml2` is a reference for useful AMLBot/background-check ideas only, not a ready integration path. It is invoice-level, branch-stale, and includes external-drain behavior that is out of scope for the current design.
