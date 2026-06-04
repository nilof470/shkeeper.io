# USDT Withdrawals Grither Pay Integration Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** route Grither Pay USDT withdrawals through SHKeeper payout executions
while preserving wallet ledger safety, idempotency, and manual-review boundaries.

**Architecture:** Grither Pay remains the customer withdrawal and ledger source
of truth. SHKeeper is a provider execution backend reached through a signed,
idempotent API. Grither Pay commits the withdrawal row, ledger debit/reservation,
local SHKeeper payout execution mirror, and submit outbox row in one DB
transaction; all callbacks and status polls are applied through one monotonic
state applier.

**Tech Stack:** Java, Spring Boot, RestClient, JPA repositories, Liquibase
formatted SQL migrations, existing wallet ledger services, scheduler/ShedLock
patterns, JUnit/Mockito integration tests.

---

## Scope And Source Spec

Repository: `/Users/test/IdeaProjects/grither-pay`

Source integration spec:
`docs/superpowers/specs/2026-06-03-grither-pay-shkeeper-payout-integration-design.md`

SHKeeper API contract:
`docs/superpowers/specs/2026-06-03-usdt-withdrawals-production-readiness-design.md`

This plan is for implementation inside the Grither Pay repository. Do not patch
Grither-specific method or field names into SHKeeper. SHKeeper should see only a
generic API consumer named `grither-pay`.

## Current Implementation Status, 2026-06-04

The Grither Pay repository contains the first SHKeeper payout integration pass:
wallet routing, payout persistence, submit outbox, signed SHKeeper payout
client, callback ingestion, monotonic state application, status sync, and
operator manual-resolution APIs.

Validated locally:

- `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='ShKeeperPayout*Test,WalletShKeeperWithdrawalCreationServiceTest,WalletCryptoWithdrawalPayoutStateServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,AdminShKeeperPayoutResolutionControllerTest,ShKeeperConfigTest'`
  passed: 63 tests, 0 failures, 0 errors.
- `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='AltynWalletStatusMapperTest,AltynWalletStatusPollingAdapterTest,WalletDepositServiceTest,WalletDepositIntegrationTest,WalletCrossSystemIntegrationTest,WalletControllerIntegrationTest,WalletWebhookRoutingTest'`
  passed: 187 tests, 0 failures, 0 errors.
- `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=WalletConcurrencyIntegrationTest`
  passed: 8 tests, 0 failures, 0 errors.
- Fresh full backend regression after
  `./mvnw -q -pl apps/backend clean test -Djacoco.skip=true` passed according
  to current surefire reports: 1973 tests, 0 failures, 0 errors, 0 skipped.
- Fresh backend regression re-run after the SHKeeper product-policy boundary
  clarification:
  `./mvnw -q -pl apps/backend test -Djacoco.skip=true` passed according to
  surefire reports: 420 XML files, 1973 tests, 0 failures, 0 errors, 0 skipped.
- Targeted callback HMAC retry regression after SHKeeper outbox freshness update:
  `./mvnw -q -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSignatureServiceTest`
  passed. Coverage now proves a callback retry with the same `event_id` nonce and
  refreshed timestamp/signature is accepted as replay/idempotency evidence, not
  an auth failure.
- The targeted Spring tests started PostgreSQL via Testcontainers and applied
  Liquibase through `V089_create_shkeeper_payouts.sql`.
- `git diff --check` passed in `/Users/test/IdeaProjects/grither-pay`.

Additional Grither-side reliability fix validated on 2026-06-04:

- Altyn/legacy wallet status mapping now keeps provider payment reference
  separate from provider transaction id. The wallet polling/webhook target
  remains the provider payment reference, while terminal transaction facts use
  the transaction id.
- Expired unbound USDT deposits without a provider external id or prior marker
  remain fail-closed and are not credited by amount-only matching.
- Test cleanup deletes analytics rows before users in wallet integration tests,
  removing the previous full-suite FK failure without weakening production code.

Additional Grither-side payout safety fix validated on 2026-06-04:

- `SAFE_FOR_MANUAL_PAYOUT` now requires structured rail-specific negative
  evidence JSON, not only `negativeEvidenceConfirmed=true`.
- Common required evidence fields: source wallet, destination, amount,
  finalized range, latest status evidence, sidecar state, node/query evidence,
  and known txids/message hashes, even if the list is empty.
- Rail-specific required fields:
  TRON requires expired ref-block evidence, TRC20 Transfer query evidence, and
  resource-state evidence; TON requires expired valid-until, resolved seqno,
  BOC/message hash, source history, Jetton transfer history, and masterchain
  range; ETH requires consumed nonce, finalized same-nonce tx hash, chain id,
  nonce, and ERC20 Transfer-log query evidence.
- Validated locally:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualResolutionServiceTest`
  returned 8 tests, 0 failures, 0 errors.

Additional Grither-side submit alert/recovery fix validated on 2026-06-04:

- Submit retry exhaustion now emits
  `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED` after the submit outbox is moved to
  `FAILED_FINAL` and the payout execution is moved to
  `RECONCILIATION_REQUIRED`.
- The withdrawal stays `PROCESSING` and reserved funds remain reserved for
  manual reconciliation, not automatic refund.
- Direct reconciliation alerts in the submit dispatcher are fail-open: alert
  delivery failure is logged and does not undo the persisted state transition or
  fail the dispatcher cycle.
- Validated locally:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSubmitDispatcherTest`
  returned 10 tests, 0 failures, 0 errors.

Additional Grither-side stale manual-review alert fix validated on 2026-06-04:

- Added payout-local `ShKeeperPayoutManualReviewMonitor`; it scans only open
  manual resolution states (`MANUAL_REVIEW`, `SAFE_FOR_MANUAL_PAYOUT`,
  `MANUAL_PAYOUT_PENDING`) and emits
  `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED` without moving payout state.
- The scan is bounded by `manual-review-alert-batch-size`, guarded by
  `shkeeper.payouts.enabled`, and scheduled through the existing payout
  scheduler/ShedLock pattern.
- The stale query is DB-relative (`updated_at < LOCALTIMESTAMP - age`) because
  `shkeeper_payout_executions.updated_at` is `TIMESTAMP WITHOUT TIME ZONE`.
  This avoids Java `Instant` vs database timestamp ambiguity.
- Alert context masks external id and destination; alert delivery failure is
  logged and does not fail the scan.
- Validated locally:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualReviewMonitorTest`
  returned 2 tests, 0 failures, 0 errors.
- Validated adjacent accounting/state/controller paths:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutManualResolutionServiceTest,AdminShKeeperPayoutResolutionControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutSubmitDispatcherTest`
  returned 30 tests, 0 failures, 0 errors.
- `git diff --check` passed in `/Users/test/IdeaProjects/grither-pay`.

Additional Grither-side status-sync/callback-backlog observability fix validated
on 2026-06-04:

- Status-sync reconciliation is covered through
  `ShKeeperPayoutStatusSyncServiceTest`: a conflicting status poll moves the
  payout execution to `RECONCILIATION_REQUIRED`, keeps funds reserved through
  the state applier, and emits `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED`.
- Added payout-local `ShKeeperPayoutCallbackBacklogMonitor`; it scans
  `shkeeper_payout_callback_events` rows where `applied_at is null` and
  `received_at` is stale, emits an operator alert, and does not replay callbacks
  or mutate payout/wallet state.
- The callback backlog query is DB-relative
  (`received_at < LOCALTIMESTAMP - age`) for the same
  `TIMESTAMP WITHOUT TIME ZONE` reason as the manual-review monitor.
- Alert/log context for reconciliation, submit retry exhaustion, status-sync,
  manual-review, and callback-backlog paths masks external ids and destinations
  where those fields are present. Deduplication keys remain stable.
- Added payout-local Micrometer scheduler metrics through
  `ShKeeperPayoutMetrics`: each scheduled payout operation records a constant
  cardinality success/failure counter and a processed-row summary. Metrics are
  fail-open and cannot break the scheduler loop.
- Validated locally:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperConfigTest,ShKeeperPayoutSchedulerTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutCallbackBacklogMonitorTest`
  returned 32 tests, 0 failures, 0 errors.
- Validated scheduler metrics:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest,ShKeeperPayoutMetricsTest`
  returned 15 tests, 0 failures, 0 errors.
- Validated adjacent payout/config/state set:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest,ShKeeperPayoutMetricsTest,ShKeeperConfigTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutManualResolutionServiceTest,ShKeeperPayoutManualReviewMonitorTest,ShKeeperPayoutCallbackBacklogMonitorTest,AdminShKeeperPayoutResolutionControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest`
  returned 68 tests, 0 failures, 0 errors.
- After masking follow-up:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStatusSyncServiceTest`
  returned 13 tests, 0 failures, 0 errors.

Grither-side ShKeeper amount-cap correction validated on 2026-06-04:

- Follow-up review found that `shkeeper.payouts.networks.*.max-single-amount`
  and `daily-limit` added a second provider-specific product policy layer on top
  of the existing Grither wallet limits.
- The Grither integration no longer exposes or enforces ShKeeper-specific amount
  or daily withdrawal caps. Amount validation remains owned by the existing
  wallet/business limit layer, while ShKeeper payout code validates rail
  enablement, canonical USDT precision, ledger reservation, idempotent outbox,
  callbacks, status sync, and manual review boundaries.
- The wallet creation path still calls provider-facing
  `validateUsdtPayoutAllowed(network, payoutAmount)` after the existing global
  wallet method limit, but that provider call now validates only ShKeeper payout
  availability and canonical positive USDT amount.
- Validated locally:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperConfigTest,WalletShKeeperWithdrawalCreationServiceTest,WalletWithdrawalCreationServiceShKeeperRoutingTest,ShKeeperPayoutMetricsTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutWebhookControllerTest,AdminShKeeperPayoutResolutionControllerTest`
  returned 50 tests, 0 failures, 0 errors.
- Fresh full backend regression after the amount-cap correction:
  `./mvnw -q -pl apps/backend clean test -Djacoco.skip=true` passed according
  to current surefire reports: 1973 tests, 0 failures, 0 errors, 0 skipped.
- `git diff --check` passed in `/Users/test/IdeaProjects/grither-pay` and
  `/Users/test/PycharmProjects/shkeeper.io`.

## SHKeeper Deployment Contract To Assume

The SHKeeper Helm chart exposes payout rails through generic `payouts.*` values.
Grither Pay must not depend on SHKeeper internals or Grither-specific SHKeeper
field names.

Required SHKeeper-side assumptions for Grither Pay implementation:

- submit endpoint: `POST /api/v1/payout-executions`;
- status endpoint: `GET /api/v1/payout-executions/{external_id}`;
- auth: HMAC headers with `X-Payout-Key-Id`, timestamp, nonce, and raw body hash
  as defined in the integration spec;
- callback endpoint id: Grither Pay sends the configured id, for example
  `grither-pay-main`; SHKeeper resolves the URL from
  `PAYOUT_CALLBACK_ENDPOINTS_JSON`;
- no callback URL is accepted from the user request;
- rail source wallets in first release are the current sidecar `fee_deposit`
  source for TRON, TON, and ETH;
- rail enablement is deploy-time capability. If SHKeeper rail values are paused,
  killed, missing restore evidence, missing owned image tag, or missing Secret
  refs, keep that Grither network disabled even if Grither's own feature flag is
  true.

Expected ops Secret payloads are documented in
`docs/superpowers/specs/2026-06-03-grither-pay-shkeeper-payout-integration-design.md`
under "SHKeeper Helm Handoff". Grither Pay config must use payout-specific keys;
do not reuse deposit webhook secrets.

## Validated Existing Files

Existing files to modify:

- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalCreationService.java`
  Routes `USDT` withdrawals to Altyn today. Add SHKeeper routing behind
  `shkeeper.payouts.enabled` and per-network enablement.
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalTransactionService.java`
  Current reserve/create method hard-codes `provider("ALTYN")`. Do not push
  SHKeeper through that method unchanged.
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalProviderStatusService.java`
  Current provider status mapper refunds on provider `FAILED`/`REFUNDED`. Do not
  use this generic path for SHKeeper chain-terminal failures.
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalProviderFactApplier.java`
  Current fact applier writes Altyn fields. Keep it Altyn-only or split it before
  SHKeeper uses provider facts.
- `apps/backend/src/main/java/com/grither/pay/wallet/domain/WalletWithdrawal.java`
  Has generic fields `provider`, `cryptoAddress`, `cryptoCurrency`,
  `cryptoNetwork`, `txHash`, and Altyn-specific fields. Add no new Altyn-prefixed
  SHKeeper semantics.
- `apps/backend/src/main/java/com/grither/pay/wallet/domain/WalletWithdrawalStatus.java`
  Public enum has `PENDING`, `PROCESSING`, `COMPLETED`, `CANCELLED`, `FAILED`.
  First release should keep manual/reconciliation state in SHKeeper-specific
  provider tables instead of expanding this public enum.
- `apps/backend/src/main/java/com/grither/pay/domain/entity/BaseEntity.java`
  already provides optimistic `@Version`. New SHKeeper payout entities should use
  that version field and repository row locks/CAS for monotonic state application.
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletAltynWithdrawalCreationService.java`
  currently reserves `request.usdAmount() + networkFee` for USDT withdrawals but
  sends only `request.usdAmount()` to the provider payout.
- `apps/backend/src/test/java/com/grither/pay/integration/WalletWithdrawalIntegrationTest.java`
  asserts the existing USDT withdrawal response/debit includes the network fee
  (`usdAmount = payout amount + fee`). SHKeeper tests must preserve that product
  accounting.
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/config/ShKeeperConfig.java`
  Existing deposit/invoice config. Add payout config under `shkeeper.payouts`.
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/client/ShKeeperApiClient.java`
  Existing deposit/invoice client. Prefer a separate payout client because payout
  signing must cover raw body, method, path, timestamp, and canonical query.
  The payout key id remains a required header and secret selector.
- `apps/backend/src/main/resources/db/changelog/db.changelog-master.yaml`
  Include a new formatted SQL migration after the current latest migration.
- `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminWalletController.java`
  Admin wallet endpoints already list/detail withdrawals; add SHKeeper payout
  provider-state fields there rather than exposing raw state in the public API.
- `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminManualWithdrawalController.java`
  Existing manual withdrawal complete/cancel flow must not be reused to resolve
  SHKeeper ambiguous payouts without negative evidence.
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletAdminWithdrawalReadService.java`
  Add SHKeeper provider-state/evidence projection for operator views.
- `apps/backend/src/main/java/com/grither/pay/wallet/web/mapper/WalletWithdrawalMapper.java`
  Map provider/internal SHKeeper fields to admin DTOs.
- `apps/backend/src/main/java/com/grither/pay/web/dto/response/WalletWithdrawalAdminResponse.java`
  Add nullable admin-only SHKeeper payout fields.
- `apps/backend/src/main/java/com/grither/pay/web/dto/response/WalletWithdrawalStatusResponse.java`
  Do not add public `MANUAL_REVIEW`/`RECONCILIATION_REQUIRED` statuses in the
  first release unless product explicitly changes the public status model.

Existing test areas to extend:

- `apps/backend/src/test/java/com/grither/pay/integration/WalletControllerIntegrationTest.java`
- `apps/backend/src/test/java/com/grither/pay/integration/WalletWithdrawalIntegrationTest.java`
- `apps/backend/src/test/java/com/grither/pay/integration/WalletCrossSystemIntegrationTest.java`
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletWithdrawalCreationServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletWithdrawalProviderStatusTargetServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletWithdrawalProviderFactApplierTest.java`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/client/*`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/config/*`

## New Files

Create provider payout files:

- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutClient.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitRequest.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStatusResponse.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutCallbackPayload.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutState.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutFailureClass.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSignatureService.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcher.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationService.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStatusSyncService.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutProperties.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualResolutionService.java`

Create provider payout domain/persistence files:

- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperPayoutExecution.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperPayoutSubmitOutbox.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperPayoutCallbackEvent.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/domain/ShKeeperPayoutManualResolutionState.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/persistence/ShKeeperPayoutExecutionRepository.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/persistence/ShKeeperPayoutSubmitOutboxRepository.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/persistence/ShKeeperPayoutCallbackEventRepository.java`

Create wallet integration files:

- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationService.java`
- `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalAccountingService.java`

Create web/scheduler files:

- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookController.java`
- `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminShKeeperPayoutResolutionController.java`
- `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutScheduler.java`

Create migration:

- `apps/backend/src/main/resources/db/changelog/migrations/V089_create_shkeeper_payouts.sql`

Create tests:

- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutClientTest.java`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSignatureServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcherTest.java`
- `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalAccountingServiceTest.java`
- `apps/backend/src/test/java/com/grither/pay/web/controller/admin/AdminShKeeperPayoutResolutionControllerTest.java`

## Task 1: Payout Transport Contract

**Files:**

- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutClient.java`
- Create: payout request/response/callback records under `providers/shkeeper/payout`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSignatureService.java`
- Modify: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/config/ShKeeperConfig.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutClientTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSignatureServiceTest.java`

- [x] Write tests proving submit signs raw JSON body with timestamp, nonce,
  method, canonical path, canonical query, body SHA-256, and required key id
  header. `X-Payout-Key-Id` selects the secret in SHKeeper v1; it is not part of
  the v1 signature base.
- [x] Write tests proving status lookup signs
  `GET /api/v1/payout-executions/{external_id}` with an empty canonical query
  string and the same signature base.
- [x] Write tests proving a signature for POST cannot be replayed against GET, and
  a signature for one path/query cannot be replayed against another.
- [x] Write tests proving callback verification rejects missing signature, stale
  timestamp, wrong method/path/query signature, and tampered body. Valid repeated
  callback nonce/event id is classified as replay instead of rejected at HMAC
  layer, because SHKeeper callback retries reuse the same signed event; the
  persistent callback-event table handles dedupe in Task 5.
- [x] Write tests proving `sidecarExecutionId` can be `null`.
- [x] Write tests proving response parsing requires `executionId`, `externalId`, `contractVersion`, `state`, `eventVersion`, and `stateTransitionId`.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest='ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest'
```

Current validation on 2026-06-04: command passed with 7 tests, 0 failures,
0 errors.

- [x] Implement the payout DTOs and client. Keep existing invoice/deposit
  `ShKeeperApiClient` behavior unchanged.
- [x] Add `shkeeper.payouts.*` config validation. Fail closed when payouts are
  enabled without API URL, key id, secret, callback key id, callback secret, and
  at least one enabled network.
- [x] Run the same tests and confirm they pass:
  `./mvnw -pl apps/backend test -Dtest='ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest,ShKeeperConfigTest'`
  passed with 19 tests.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay/providers/shkeeper apps/backend/src/test/java/com/grither/pay/providers/shkeeper
git commit -m "feat: add shkeeper payout transport contract"
```

## Task 2: Payout Persistence

**Files:**

- Create: `apps/backend/src/main/resources/db/changelog/migrations/V089_create_shkeeper_payouts.sql`
- Modify: `apps/backend/src/main/resources/db/changelog/db.changelog-master.yaml`
- Create: domain and repository files listed in "New Files"
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutPersistenceTest.java`

- [x] Write persistence tests proving:
  unique `wallet_withdrawal_id`, unique `external_id`, unique nullable
  `execution_id` when present, unique nullable `sidecar_execution_id` when
  present, unique `shkeeper_payout_submit_outbox.payout_execution_id`, unique
  outbox `external_id`, unique `(external_id, request_hash)`, and idempotent
  callback `event_id`.
- [x] Write persistence tests proving `shkeeper_payout_callback_events` is
  mandatory and stores raw payload hash/signature metadata; do not rely on
  `last_event_id` alone.
- [x] Write tests proving repository lookup by `externalId`, `executionId`, and
  `state + updatedAt` for scheduler scans.
- [x] Write tests proving repository claim methods use row lock/CAS semantics for
  submit outbox and state application.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ShKeeperPayoutPersistenceTest
```

Current validation on 2026-06-04:
`./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutPersistenceTest`
passed with 5 tests, 0 failures, 0 errors. Liquibase applied 90 changesets,
including `V089_create_shkeeper_payouts.sql` and
`V090_create_shkeeper_payout_manual_resolution_audit.sql`, against
Testcontainers PostgreSQL.

- [x] Implement `shkeeper_payout_executions`, `shkeeper_payout_submit_outbox`,
  and `shkeeper_payout_callback_events`.
- [x] Include `payout_amount`, `network_fee`, and `reserved_amount` columns on
  `shkeeper_payout_executions`; SHKeeper submit amount is `payout_amount`, while
  Grither ledger reservation is `reserved_amount`.
- [x] Keep SHKeeper payout state out of Altyn-prefixed columns.
- [x] Run the persistence tests and confirm they pass:
  `./mvnw -pl apps/backend test -Dtest='ShKeeperPayoutPersistenceTest,ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest,ShKeeperConfigTest'`
  passed with 24 tests and Liquibase V089 applied on PostgreSQL.
- [ ] Commit:

```bash
git add apps/backend/src/main/resources/db/changelog apps/backend/src/main/java/com/grither/pay/providers/shkeeper apps/backend/src/test/java/com/grither/pay/providers/shkeeper
git commit -m "feat: persist shkeeper payout executions"
```

## Task 3: Atomic USDT Withdrawal Creation

**Files:**

- Create: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationService.java`
- Modify: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalCreationService.java`
- Modify carefully or leave unchanged: `WalletWithdrawalTransactionService.java`
- Test: `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletShKeeperWithdrawalCreationServiceTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletWithdrawalCreationServiceShKeeperRoutingTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/integration/WalletCrossSystemIntegrationTest.java`

- [x] Write tests proving `createUsdtWithdrawal(...)` uses the existing Altyn
  path when `shkeeper.payouts.enabled=false`.
- [x] Write tests proving enabled TRON/TON/ETH SHKeeper payout creates:
  `wallet_withdrawals.provider="SHKEEPER"`, status `PENDING`, crypto address,
  crypto currency `USDT`, normalized network, a wallet ledger debit for
  `payoutAmount + networkFee`, one `shkeeper_payout_executions` row, and one
  submit outbox row in the same transaction. TRON has full atomic coverage;
  TON/ETH have rail mapping coverage at creation time.
- [x] Write tests proving the SHKeeper submit payload amount is the normalized
  payout amount only, not `payoutAmount + networkFee`.
- [x] Extend integration coverage proving response
  `usdAmount`/ledger debit still include the network fee for SHKeeper-backed
  USDT withdrawals. Implemented as `WalletWithdrawalShKeeperIntegrationTest`
  because the scenario needs a SHKeeper-enabled Spring context; local validation:
  `./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=WalletWithdrawalShKeeperIntegrationTest`
  returned 1 test, 0 failures, 0 errors.
- [x] Write tests proving `external_id` equals immutable
  `WalletWithdrawal.publicNumber`.
- [x] Write tests proving crash/exception before transaction commit creates no
  withdrawal, no ledger debit, and no SHKeeper outbox.
- [x] Write tests proving duplicate user idempotency key returns the existing
  withdrawal and does not create a second SHKeeper execution.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest='WalletShKeeperWithdrawalCreationServiceTest,WalletCrossSystemIntegrationTest'
```

Current validation on 2026-06-04:
`./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest='WalletShKeeperWithdrawalCreationServiceTest,WalletCrossSystemIntegrationTest'`
passed with 15 tests, 0 failures, 0 errors. Liquibase again applied 90
changesets including V089/V090 through the Spring/Testcontainers path.

- [x] Implement `WalletShKeeperWithdrawalCreationService` using existing wallet
  ledger operations and repositories.
- [x] Route `WalletWithdrawalCreationService.createUsdtWithdrawal(...)` to
  SHKeeper only when payout config and target network are enabled.
- [x] Keep SBP/CARD behavior unchanged.
- [x] Do not route SHKeeper through the current Altyn-hard-coded reserve method
  unless that method is first made provider-neutral with tests.
- [x] Run tests and confirm they pass:
  `./mvnw -pl apps/backend test -Dtest='WalletWithdrawalCreationServiceShKeeperRoutingTest,WalletShKeeperWithdrawalCreationServiceTest,ShKeeperPayoutPersistenceTest,ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest,ShKeeperConfigTest'`
  passed with 31 tests after adding TON rail coverage. `ArchitectureRulesTest`
  passed with 362 tests after the Task 3 main-code changes. `git diff --check`
  is clean in both `grither-pay` and `shkeeper.io`.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay/wallet apps/backend/src/main/java/com/grither/pay/providers/shkeeper apps/backend/src/test/java/com/grither/pay
git commit -m "feat: create shkeeper usdt withdrawal outbox"
```

## Task 4: Submit Dispatcher And Status Recovery

**Files:**

- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcher.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStatusSyncService.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutScheduler.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSubmitDispatcherTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStatusSyncServiceTest.java`

- [x] Write tests proving dispatcher claims one pending outbox row and submits the
  same `externalId` and `requestHash`.
- [x] Write tests proving two scheduler instances cannot claim or submit the same
  outbox row concurrently.
- [x] Write tests proving HTTP success stores `executionId`, hashes, ordering
  metadata, and moves provider state to the response state.
- [x] Write tests proving submit timeout performs status lookup for the same
  `externalId`.
- [x] Write tests proving retryable network failure leaves the same outbox row
  retryable and never creates a new `externalId`.
- [x] Write tests proving `409 IDEMPOTENCY_CONFLICT` moves execution to
  `RECONCILIATION_REQUIRED`, keeps funds reserved, and alerts.
- [x] Write tests proving a terminal/refunded `WalletWithdrawal` blocks submit
  before any SHKeeper chain payout call and moves the payout to reconciliation.
- [x] Write tests proving a stale worker cannot commit after its outbox lease is
  reclaimed by another worker.
- [x] Write tests proving status sync applies stale non-terminal execution status
  and skips terminal executions.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutPersistenceTest,ShKeeperPayoutClientTest,ShKeeperPayoutSignatureServiceTest,ShKeeperConfigTest
```

Result after implementation: 32 tests passed.

- [x] Implement bounded retry with backoff and existing scheduler/ShedLock style.
- [x] Make dispatcher idempotent by `externalId` and `requestHash`.
- [x] Claim outbox rows with `FOR UPDATE SKIP LOCKED`, an atomic
  `UPDATE ... WHERE status in (...) AND locked_at is expired`, or the existing
  ShedLock style plus row-level CAS. Do not hold only an in-memory lock.
- [x] Add owner-aware lease/CAS guard on submit result, retryable failure, and
  idempotency-conflict commit paths so a stale worker cannot clear another
  worker's lock.
- [x] Bound status sync backlog at DB query level with `Pageable`, not only an
  in-memory stream limit.
- [x] Run tests and confirm they pass:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ArchitectureRulesTest
```

Result after implementation: 362 architecture tests passed.
- [x] Use provider-local scheduler
  `providers/shkeeper/payout/ShKeeperPayoutScheduler`; this matches existing
  provider-local scheduler style and keeps provider internals out of the global
  scheduler package. `ArchitectureRulesTest` validates this boundary.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay/providers/shkeeper apps/backend/src/test/java/com/grither/pay/providers/shkeeper
git commit -m "feat: dispatch shkeeper payout submit outbox"
```

## Task 5: Callback And Monotonic State Application

**Files:**

- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookController.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationService.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutReconciliationAlertService.java`
- Create: `apps/backend/src/main/java/com/grither/pay/wallet/api/WalletCryptoWithdrawalPayoutStateOperations.java`
- Create: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletCryptoWithdrawalPayoutStateService.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationRollbackTest.java`

- [x] Write tests proving callback HMAC verification uses raw body, timestamp,
  nonce, method, path, query, required key id header, and replay classification.
- [x] Write tests proving duplicate `eventId` is idempotent.
- [x] Write tests proving stale `eventVersion` cannot regress state.
- [x] Write tests proving same-version conflicting state/evidence moves to
  `RECONCILIATION_REQUIRED`, keeps funds reserved, and alerts.
- [x] Write tests proving concurrent newer callbacks/status polls cannot both
  commit. One transition wins through row lock or optimistic `@Version`/CAS, and
  the loser reloads before deciding stale/idempotent/conflict.
- [x] Write tests proving callback event insert, provider state update, and wallet
  status update are committed atomically; rollback leaves no partially applied
  callback event. Terminal ledger settlement remains in Task 6.
- [x] Write tests proving status polling, callbacks, and initial submit responses
  use the same monotonic state applier.
- [x] Write tests proving an accepted submit response followed by local state
  application failure moves to `RECONCILIATION_REQUIRED` and does not retry the
  unsafe submit window.
- [x] Write tests proving `sidecarExecutionId=null` does not break dedupe,
  status lookup, or reconciliation.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutStateApplicationRollbackTest,ShKeeperPayoutWebhookControllerTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutPersistenceTest,ShKeeperPayoutSubmitDispatcherTest
```

Result after implementation: 24 tests passed.

- [x] Implement the webhook endpoint at
  `/api/webhooks/shkeeper/payout-executions`.
- [x] Implement state application transaction boundaries:
  provider state update, wallet status update, callback event dedupe, and
  rollback behavior commit together. Terminal ledger settlement remains in Task 6.
- [x] Load `ShKeeperPayoutExecution` and linked `WalletWithdrawal` with
  pessimistic row locks through the provider state applier and wallet API facade.
  Do not apply payout state from detached/stale entities.
- [x] Do not use `WalletWithdrawalProviderStatusService.applyProviderPayoutStatus`
  for SHKeeper chain-terminal failure mapping.
- [x] Keep Grither wallet internals behind a wallet API facade:
  provider code depends on `WalletCryptoWithdrawalPayoutStateOperations`, not
  `wallet.domain`, `wallet.persistence`, or `wallet.application` internals.
- [x] Keep same-version anomalies auditable:
  `shkeeper_payout_callback_events(event_id)` and `state_transition_id` remain
  unique, while `(execution_id, event_version)` is non-unique so conflicting
  signed observations can be stored and classified as reconciliation.
- [x] Run architecture rules and confirm they pass:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ArchitectureRulesTest
```

Result after implementation: 362 architecture tests passed.
- [x] Run `git diff --check`.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay/providers/shkeeper apps/backend/src/test/java/com/grither/pay/providers/shkeeper
git commit -m "feat: apply shkeeper payout states monotonically"
```

## Task 6: Accounting And Manual Review

**Files:**

- Create/implemented as: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletCryptoWithdrawalPayoutStateService.java`
  behind `wallet.api` (`WalletCryptoWithdrawalPayoutStateOperations`). This keeps
  SHKeeper provider code on the wallet API boundary instead of importing wallet
  internals.
- Create: `apps/backend/src/main/java/com/grither/pay/providers/api/ProviderShKeeperPayoutManualResolutionOperations.java`
  and related provider-facing command/view records.
- Create: `apps/backend/src/main/java/com/grither/pay/admin/api/AdminShKeeperPayoutManualResolutionOperations.java`
  and related admin-facing command/view records.
- Create: `apps/backend/src/main/java/com/grither/pay/admin/application/AdminShKeeperPayoutManualResolutionService.java`
  as the web-safe admin facade over `providers.api`.
- Create: `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminShKeeperPayoutResolutionController.java`
  depending only on `admin.api`, not `providers.shkeeper`.
- Modify: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletWithdrawalProviderStatusService.java` only if needed to prevent SHKeeper from entering unsafe generic refund path.
- Modify: `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminWalletController.java`
- Modify: `apps/backend/src/main/java/com/grither/pay/web/controller/admin/AdminManualWithdrawalController.java` only to prevent unsafe reuse for SHKeeper ambiguous payouts or to link to a dedicated resolution flow.
- Modify: `apps/backend/src/main/java/com/grither/pay/wallet/application/WalletAdminWithdrawalReadService.java`
- Modify: `apps/backend/src/main/java/com/grither/pay/wallet/web/mapper/WalletWithdrawalMapper.java`
- Modify: `apps/backend/src/main/java/com/grither/pay/web/dto/response/WalletWithdrawalAdminResponse.java`
- Test: `apps/backend/src/test/java/com/grither/pay/wallet/application/WalletCryptoWithdrawalPayoutStateServiceTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualResolutionServiceTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateApplicationServiceTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/web/controller/admin/AdminShKeeperPayoutResolutionControllerTest.java`

- [x] Write tests proving `CONFIRMED` completes withdrawal exactly once and does
  not refund.
- [x] Write tests proving `FAILED_PRE_BROADCAST` refunds exactly once through a
  deterministic ledger idempotency key.
- [x] Write tests proving `FAILED_PRE_BROADCAST` refunds the reserved amount
  (`payout_amount + network_fee`) exactly once.
- [x] Write tests proving `FAILED_CHAIN_TERMINAL` does not call
  `failWithdrawal(...)`, does not create a `WITHDRAWAL_REVERSAL`, and moves to
  `MANUAL_REVIEW`.
- [x] Write tests proving `RECONCILIATION_REQUIRED`, `MANUAL_REVIEW`,
  `SAFE_FOR_MANUAL_PAYOUT`, and `MANUAL_PAYOUT_PENDING` keep funds reserved.
  Accounting tests cover `RECONCILIATION_REQUIRED` and `MANUAL_REVIEW`;
  manual-resolution tests cover `SAFE_FOR_MANUAL_PAYOUT` and
  `MANUAL_PAYOUT_PENDING`.
- [x] Write tests proving manual payout is blocked until negative evidence is
  recorded.
- [x] Write tests proving manual payout completion records manual tx evidence and
  completes the original withdrawal.
- [x] Write tests proving manual payout completion accepts evidence from the
  existing SHKeeper admin/manual payout flow without submitting a new automatic
  SHKeeper payout execution.
- [x] Write tests proving admin/operator responses show SHKeeper execution id,
  explicit withdrawal `publicNumber`, optional sidecar id, provider state,
  reconciliation flag, tx/message evidence, failure class, fee split, and next
  safe action without exposing these states as new public
  `WalletWithdrawalStatusResponse` values.
- [x] Add append-only audit trail for manual-resolution operator actions.
  Evidence on 2026-06-04: `shkeeper_payout_manual_resolution_audit` stores
  actor, action, previous/new provider and manual states, reason, raw evidence,
  manual tx/message hash, request hash, sidecar payload hash, state transition
  id, and event id. `ShKeeperPayoutManualResolutionServiceTest` verifies audit
  rows for negative evidence, manual-payout pending, and manual completion; an
  architecture rule keeps persistence behind
  `ShKeeperPayoutManualResolutionAuditRecorder`.
- [x] Write tests proving completed manual payout retry rejects mismatched
  tx/message evidence instead of silently returning success.
- [x] Write tests proving negative evidence for another payout identity or
  transfer target (`externalId`, `requestHash`, `network`, destination, amount)
  cannot unlock `SAFE_FOR_MANUAL_PAYOUT`.
- [x] Write tests proving manual completion fails closed if the wallet withdrawal
  became terminal before completion.
- [x] Run focused accounting/state tests:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutStateApplicationRollbackTest,ShKeeperPayoutWebhookControllerTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutPersistenceTest,ShKeeperPayoutSubmitDispatcherTest
```

Result after accounting sub-block implementation: 31 tests passed.

Additional manual-resolution sub-block verification:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Dtest=ShKeeperPayoutManualResolutionServiceTest,AdminShKeeperPayoutResolutionControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest,ShKeeperPayoutSubmitDispatcherTest
```

Result after manual-resolution and rail-specific evidence implementation:
30 tests passed.

- [x] Implement accounting transitions without changing existing Altyn accounting
  semantics.
- [x] Keep public `WalletWithdrawalStatus` as `PROCESSING` for manual/reconciliation
  states unless product explicitly adds new public statuses.
- [x] Implement rail-specific negative-evidence validation before
  `SAFE_FOR_MANUAL_PAYOUT`:
  the Grither service now rejects unstructured/free-text evidence and requires
  common source/destination/amount/finalized-range/status/node evidence plus
  rail-specific TRON/TON/ETH evidence before any manual payout can be marked
  safe. Txpool disappearance is not evidence.
- [x] Bind manual negative evidence to the exact local payout execution before
  `SAFE_FOR_MANUAL_PAYOUT`; evidence must match `externalId`, `requestHash`,
  `network`, destination, and payout amount.
- [x] Guard automatic state application after irreversible accounting/operator
  states. A newer conflicting observation after `FAILED_PRE_BROADCAST` now moves
  the payout execution to `RECONCILIATION_REQUIRED`/`MANUAL_REVIEW` without
  completing the withdrawal or refunding twice.
- [x] Set submit-side reconciliation (`IDEMPOTENCY_CONFLICT`,
  `SUBMIT_RETRY_EXHAUSTED`, local apply failure) to
  `manual_resolution_state=MANUAL_REVIEW`, matching callback-side reconciliation.
- [x] Run tests and confirm they pass.
- [x] Run `ArchitectureRulesTest`.
- [x] Run `git diff --check`.
- [x] Run provider boundary scan confirming SHKeeper provider production code has
  no direct imports from `wallet.domain`, `wallet.persistence`, or
  `wallet.application`.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay/wallet apps/backend/src/main/java/com/grither/pay/providers apps/backend/src/main/java/com/grither/pay/admin apps/backend/src/main/java/com/grither/pay/web apps/backend/src/test/java/com/grither/pay
git commit -m "feat: protect shkeeper payout accounting"
```

## Task 7: Configuration, Schedulers, And Observability

**Files:**

- Modify: `apps/backend/src/main/resources/application.yaml`
- Modify: environment-specific config files if present in the Grither Pay repo
- Modify/Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutScheduler.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualReviewMonitor.java`
- Create: `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutCallbackBacklogMonitor.java`
- Modify/Create: alert/notification integration using existing `AlertOperations`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/config/ShKeeperConfigTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutSchedulerTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutManualReviewMonitorTest.java`
- Test: `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutCallbackBacklogMonitorTest.java`

- [x] Add config tests proving payout config fails closed when enabled without
  required secrets or network allowlist.
- [x] Add scheduler tests proving submit dispatcher and status sync skip when
  `shkeeper.payouts.enabled=false`.
- [x] Add alert tests for submit retry exhaustion. The dispatcher alerts after
  the DB transition to reconciliation and remains fail-open if alert delivery
  itself fails.
- [x] Add alert tests for same-version conflict. The state application test
  proves same-version conflicting callback state moves the payout to
  reconciliation, keeps funds reserved, and emits
  `SHKEEPER_PAYOUT_RECONCILIATION_REQUIRED`.
- [x] Add alert tests for manual review age. The payout-local monitor alerts on
  stale open manual resolution states and does not fail the scan when alert
  delivery fails.
- [x] Add remaining alert tests for state-sync reconciliation required and
  callback backlog. Status-sync is covered through
  `ShKeeperPayoutStatusSyncServiceTest`; callback backlog is covered through
  `ShKeeperPayoutCallbackBacklogMonitorTest`.
- [x] Add scheduler failure alerts for unexpected submit-dispatch and status-sync
  exceptions. The scheduler catches the failure, emits `SCHEDULER_FAILURE`, and
  does not terminate the scheduled loop if alert delivery itself fails.
- [x] Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest
```

Result after scheduler/callback-backlog/metrics sub-block implementation: 13
scheduler tests passed; scheduler metrics focused set returned 15 tests, 0
failures, 0 errors; config/scheduler/status-sync/callback-backlog focused set
returned 32 tests, 0 failures, 0 errors.

Additional adjacent payout/config/state verification:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=ShKeeperPayoutSchedulerTest,ShKeeperPayoutMetricsTest,ShKeeperConfigTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutStatusSyncServiceTest,ShKeeperPayoutManualResolutionServiceTest,ShKeeperPayoutManualReviewMonitorTest,ShKeeperPayoutCallbackBacklogMonitorTest,AdminShKeeperPayoutResolutionControllerTest,WalletCryptoWithdrawalPayoutStateServiceTest,ShKeeperPayoutStateApplicationServiceTest
```

Result: 68 tests passed.

- [x] Implement initial Micrometer scheduler metrics. Logging/alerts are
  implemented with masked external id and destination where those fields are
  present. Broader business/SLA payout metrics remain part of Phase 6
  observability.
- [x] Run tests and confirm the scheduler/config/state sub-block passes.
- [ ] Commit:

```bash
git add apps/backend/src/main/java/com/grither/pay apps/backend/src/main/resources apps/backend/src/test/java/com/grither/pay
git commit -m "feat: operate shkeeper payout integration safely"
```

## Verification Gate

- [x] Run the full backend test suite:

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -q -pl apps/backend clean test -Djacoco.skip=true
```

Fresh surefire report result on 2026-06-04: 1973 tests, 0 failures, 0 errors,
0 skipped.

- [x] Validate Liquibase through the project's Spring/Testcontainers path. The
  focused and broad Grither tests applied 90 changesets including
  `V089_create_shkeeper_payouts.sql` and
  `V090_create_shkeeper_payout_manual_resolution_audit.sql`; no standalone
  Liquibase Maven plugin exists in the repository.
- [x] Request independent review focused on:
  ledger atomicity, submit outbox recovery, status/callback monotonicity,
  `FAILED_CHAIN_TERMINAL` accounting, manual payout evidence, and Altyn fallback
  regression risk.
  Independent review on 2026-06-04 found two validated issues: Altyn orphan
  recovery could refund `provider=SHKEEPER` pending withdrawals, and manual
  negative evidence was not bound to the exact payout execution. Code review also
  found and fixed the adjacent Altyn processing/ambiguous/status lookup provider
  boundary. These were fixed with focused tests.

```bash
cd /Users/test/IdeaProjects/grither-pay
./mvnw -pl apps/backend test -Djacoco.skip=true -Dtest=WalletWithdrawalProviderStatusTargetServiceTest,ShKeeperPayoutSubmitDispatcherTest,ShKeeperPayoutManualResolutionServiceTest
```

Result after review fixes: 25 tests, 0 failures, 0 errors.
- [ ] Keep `shkeeper.payouts.enabled=false` until SHKeeper API and at least one
  target rail sidecar pass their own acceptance gates and Helm renders that rail
  with dedicated payout worker, storage/migrations, secrets, NetworkPolicy, and
  safe Redis/rollout posture.
- [ ] Enable only one rail at a time with an operator kill switch. Keep
  SHKeeper-side amount/day caps out of the Grither integration; payout limits
  stay in the existing Grither wallet/business limit layer.

## Production Acceptance

- [x] SHKeeper disabled: existing Altyn/SBP/CARD behavior is unchanged.
- [x] SHKeeper enabled for one network: USDT withdrawal uses provider
  `SHKEEPER`, external id equals `WalletWithdrawal.publicNumber`, and only one
  outbox row is created.
- [x] Submit timeout does not create duplicate payout and recovers through status
  lookup.
- [x] Callback/status stale or conflicting data cannot regress wallet state.
- [x] `FAILED_PRE_BROADCAST` is the only SHKeeper failure class that can refund
  automatically.
- [x] `FAILED_CHAIN_TERMINAL` and `RECONCILIATION_REQUIRED` keep funds reserved
  and require operator evidence.
- [x] SHKeeper submit outbox recovery refuses to submit if the linked wallet
  withdrawal is already terminal.
- [x] Altyn provider-status lookup, processing polling, ambiguous polling, and
  orphan recovery are scoped to `provider=ALTYN` and cannot mutate or poll
  SHKeeper withdrawals.
- [x] Manual payout completion produces final evidence and completes the original
  withdrawal.
- [x] Manual negative evidence must match the exact payout identity and transfer
  fields before manual payout is marked safe.
