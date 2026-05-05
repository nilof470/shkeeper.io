# Phase 002: koinkyt-aml-provider-documentation - Context

**Gathered:** 2026-05-05
**Status:** Ready for documentation-only planning
**Source:** Inline `$gsd-plan-phase` fallback. Full automated GSD route was unavailable because this checkout has phase artifacts but no `.planning/ROADMAP.md`/`.planning/STATE.md`, and the local `gsd-sdk` does not expose the `query` subcommands expected by the workflow.

<domain>
## Phase Boundary

This phase plans the provider change inside `aml-shkeeper` from AMLBot to Koinkyt, with documentation as the primary deliverable. SHKeeper keeps calling the sidecar contract.

The immediate goal is not to prove live Koinkyt edge cases. The immediate goal is to produce a precise implementation contract, operator documentation, migration notes, and a validation checklist so the code change can be reviewed against the vendor PDF documentation. Real API probes are explicitly deferred.

</domain>

<decisions>
## Implementation Decisions

### Provider Direction
- **D-01:** AMLBot integration is disabled for new deposit AML decisions.
- **D-02:** SHKeeper will keep calling `aml-shkeeper`; `aml-shkeeper` will call Koinkyt.
- **D-03:** The existing SHKeeper-side AML lifecycle remains provider-neutral where possible: local `AmlCheck`, skip thresholds, callback gating, terminal status handling, and `deposit_decision` semantics stay intact.
- **D-04:** Provider identity in AML payloads becomes `koinkyt`.

### Koinkyt API Contract
- **D-05:** Koinkyt authentication uses `X-API-Key`.
- **D-06:** Default base URL is `https://explorer.coinkyt.com/openapi/v1`.
- **D-07:** Transaction checks use `GET /transaction` with `blockchain`, optional `token`, and `transaction`.
- **D-08:** Supported blockchain values from `API_Documentation.pdf` are `btc`, `eth`, and `trx`.
- **D-09:** Supported token values from `API_Documentation.pdf` are native token as an empty value, `USDT`, and `USDC`.
- **D-10:** Koinkyt returns `risk_score`, `risk_score_grade`, `id`, entity arrays, indirect distribution, `link`, and optional `alerts`; `aml-shkeeper` normalizes these into the sidecar response, and SHKeeper stores that response as its AML snapshot.

### Documentation-First Scope
- **D-11:** Documentation must explicitly separate documented vendor behavior from assumptions awaiting live API probes.
- **D-12:** Documentation may include cURL examples for later validation, but they are not part of the current execution gate.
- **D-13:** Documentation must describe fail-closed behavior for unsupported assets, missing API key, provider errors, retryable responses, and incomplete responses.
- **D-14:** Documentation must preserve `grither-pay` callback semantics: SHKeeper sends AML-enriched callback, but `grither-pay` owns balance crediting and manual review.
- **D-15:** Until real checks are run later, implementation and docs follow `API_Documentation.pdf` only.

### Unknowns Deferred To Later Live Requests
- **Q-01:** Does `GET /transaction` return usable `risk_score` for TRC20/ERC20 token transfer txids, or must SHKeeper call `GET /transfer` with `input_address`/`output_address`?
- **Q-02:** For deposits, can SHKeeper determine the required Koinkyt `input_address` if `/transfer` is needed for ETH/TRX token transfers?
- **Q-03:** Which Koinkyt HTTP 404 messages are final (`Transaction not found`) vs temporary (`No data, please try again later`) in actual JSON shape?
- **Q-04:** Does Koinkyt require `risk_profile_ids` for high/moderate/low alerting, or is `risk_score` sufficient for the SHKeeper deposit gate?
- **Q-05:** Does Koinkyt support LTC, DOGE, SOL, BNB, Polygon, Avalanche, Arbitrum, Optimism, TON, XRP, FIRO, Monero, or BTC-Lightning under another endpoint or plan not shown in the provided PDF? Until verified, these fail closed to manual review.

### the agent's Discretion

The implementation may choose exact module names and helper boundaries, but documentation must name the final files, env vars, request parameters, response mappings, failure policy, and test/probe commands concretely.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Vendor Documentation
- `/Users/test/Downloads/API_Documentation.pdf` - Koinkyt API documentation, version 2026-04-07.
- `/tmp/koinkyt_api.txt` - Local `pdftotext` extraction of the PDF if present in the current machine session.

### Existing AML Phase
- `.planning/phases/001-amlbot-deposit-approval-gate/001-CONTEXT.md` - Prior AML lifecycle decisions and callback boundary.
- `.planning/phases/001-amlbot-deposit-approval-gate/001-SPEC.md` - Locked prior AML requirements.
- `.planning/phases/001-amlbot-deposit-approval-gate/001-RESEARCH.md` - Prior AMLBot-side research and economics.
- `.planning/notes/2026-04-30-amlbot-only-no-refee.md` - Historical decision, now superseded for AMLBot but still useful as context.

### Codebase Intelligence
- `.planning/codebase/INTEGRATIONS.md` - Existing external integration surfaces and env-var patterns.
- `.planning/codebase/CONVENTIONS.md` - Service/module/test conventions.
- `.planning/codebase/CONCERNS.md` - Financial callback and security concerns.
- `.planning/codebase/TESTING.md` - Test strategy and limitations.

### Current Documentation Targets
- `docs/koinkyt_deposit_gate.md` - Operator/developer contract for Koinkyt AML deposit gating.
- `README.md` - Top-level docs link to Koinkyt deposit gate.

</canonical_refs>

<specifics>
## Specific Ideas

- Keep provider docs in one place rather than spreading Koinkyt details through tests.
- Include exact SHKeeper env vars: `AML_SHKEEPER_HOST`, `AML_SHKEEPER_USERNAME`, `AML_SHKEEPER_PASSWORD`.
- Include exact `aml-shkeeper` Koinkyt env vars: `CURRENT_PROVIDER=koinkyt`, `KOINKYT_HOST`, `KOINKYT_API_KEY`, `KOINKYT_RISK_PROFILE_IDS`.
- Include exact request fields by asset mapping:
  - BTC: `blockchain=btc`, `token=`
  - ETH: `blockchain=eth`, `token=`
  - ETH-USDT: `blockchain=eth`, `token=USDT`
  - ETH-USDC: `blockchain=eth`, `token=USDC`
  - TRX: `blockchain=trx`, `token=`
  - USDT: `blockchain=trx`, `token=USDT`
  - USDC: `blockchain=trx`, `token=USDC`
- Keep live probes as later validation only; do not require exposing credentials in repo docs or commit history.

</specifics>

<deferred>
## Deferred Ideas

- Live Koinkyt API probes for BTC, ETH, TRX, USDT, USDC, invalid txid, and invalid API key response shapes.
- Full KYT/address monitoring design.
- Risk-profile CRUD automation in SHKeeper.
- In-app UI for viewing Koinkyt report links and risk entities.
- Migration of historical AMLBot checks to a provider-neutral archive format.

</deferred>

---

*Phase: 002-koinkyt-aml-provider-documentation*
*Context gathered: 2026-05-05 via inline gsd-plan-phase fallback*
