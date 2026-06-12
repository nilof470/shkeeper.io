# AML-Gated USDT Sweep Design

Date: 2026-06-06
Status: Revised for Grither Pay manual approval and refund flow

## Summary

TRON USDT and ETH-USDT deposits must not be swept from a customer one-time
address into the sidecar `fee_deposit` wallet unless SHKeeper has an explicit
AML-safe sweep decision for that deposit. SHKeeper remains the owner of AML
state and callback behavior. TRON and Ethereum sidecars ask SHKeeper for a
backend-only sweep eligibility decision before signing any sweep or drain
transaction. The sidecars fail closed: no explicit `allow` means no sweep.

Confirmed payment callbacks to Grither Pay continue to work for manual review
cases. `MANUAL_REVIEW` stays terminal for callback delivery, but blocks sweep
until a Grither Pay operator approves the deposit in the Grither Pay admin UI.
That approval credits the client balance in Grither Pay and creates an audited
`approved` sweep resolution in SHKeeper.

If the operator decides not to credit the client, the dirty funds must first be
returned manually from the VPS using a prepared refund script. Only after the
refund transaction is sent and recorded does the operator reject the deposit in
the Grither Pay admin UI. That rejection records refund evidence and tells
SHKeeper that the refunded guarded deposit no longer blocks later address-level
sweeps.

The gate applies only to new non-skipped guarded deposits marked by SHKeeper
after this feature is deployed. Existing dev or pre-gate deposits are legacy
funds and may keep the previous sweep behavior. Legacy balances can be swept
together with a later AML-approved guarded deposit on the same address because
sidecar sweep and drain operations move the whole address token balance.

## Scope

In scope:

- SHKeeper AML sweep eligibility contract for USDT deposits.
- TRON sidecar USDT live scanner and periodic account rescan sweep guard.
- Ethereum sidecar ETH-USDT event listener and periodic balance refresh drain
  guard.
- Fail-closed sidecar behavior when SHKeeper is unavailable, undecided, or
  returns a non-allow decision.
- Tests proving callback delivery for manual review remains independent from
  sweep permission.
- Preserving legacy/pre-gate sweep behavior for deposits that are not marked as
  guarded by SHKeeper.
- Grither Pay admin approval flow for manual-review deposits: approve credits
  the client balance and releases SHKeeper sweep eligibility.
- Grither Pay admin refund/reject flow for manual-review deposits: manual refund
  is performed first, then admin rejection records refund evidence and unblocks
  future sweeps for the cleaned address.
- SHKeeper audited manual resolution contract used only by trusted backend
  callers for approve/release and refund/reject outcomes.

Out of scope:

- Quarantine or review wallets.
- Non-USDT assets and non-TRON/non-ETH rails.
- Direct AML provider calls from sidecars.
- Backfilling old dev or pre-gate transactions into the new sweep guard.

Grither Pay remains the owner of the customer-facing business decision and wallet
ledger. SHKeeper owns AML/sweep state and only exposes backend-only contracts for
eligibility and manual resolution.

## Current Behavior

SHKeeper receives sidecar `walletnotify` calls and creates a `Transaction`.
For supported assets, SHKeeper creates or refreshes an `AmlCheck` through
`aml-shkeeper`. The current policy marks:

- `APPROVED` when the AML score is less than or equal to the configured
  threshold.
- `SKIPPED` when a supported deposit is below the local small-amount policy and
  within the cumulative skip window.
- `MANUAL_REVIEW` when score, alerts, provider errors, timeouts, or incomplete
  results require manual handling.
- `PENDING` or `CHECKING` while AML is still unresolved.

`MANUAL_REVIEW` is terminal for callback delivery today. Grither Pay receives
AML facts and decides what to do operationally. However, the TRON and Ethereum
sidecars currently queue sweep or drain work independently of the final AML
state, so high-risk funds can still be consolidated into `fee_deposit`.

## Guarded Deposit Marker

Add a persistent marker to SHKeeper AML checks:

```python
AmlCheck.sweep_guard_required = db.Column(db.Boolean, nullable=False, default=False)
```

The migration must default existing rows to `false`. Existing transactions and
existing AML checks are therefore legacy by default and are not pulled into the
new sweep gate retroactively.

For new deposits on guarded rails, `ensure_aml_for_transaction()` sets
`sweep_guard_required=true` when it creates a non-skipped AML check:

- `USDT` on TRON.
- `ETH-USDT` on Ethereum.

Small-amount deposits that SHKeeper marks as `SKIPPED` under the local AML skip
policy do not need the sweep guard marker in this release. They are considered
safe-to-sweep by policy and should not block sweep.

Unsupported assets and non-guarded rails do not set this marker in this release.
The marker, not a deployment timestamp, defines whether a deposit participates in
the sweep gate. This keeps dev data and pre-gate balances untouched while making
new non-skipped guarded deposits explicit.

## Policy

Sweep eligibility is derived from SHKeeper's stored guarded deposit state:

| AML state | Sweep decision | Reason |
| --- | --- | --- |
| No guarded AML checks for address | `allow` | `legacy_no_guarded_deposits` |
| `APPROVED` | `allow` | `aml_approved` |
| `SKIPPED` small-amount deposit | `allow` | `aml_skipped_small_amount` |
| `PENDING` | `wait` | `aml_pending` |
| `CHECKING` | `wait` | `aml_checking` |
| `MANUAL_REVIEW` without manual resolution | `block` | `manual_review` |
| `MANUAL_REVIEW` with valid `approved` resolution | `allow` | `manual_approved` |
| `MANUAL_REVIEW` with valid `refunded` resolution | `allow` | `manual_refund` |
| Guarded AML check with missing/broken AML state | `wait` | `aml_missing` |
| Missing transaction after live `walletnotify` | `wait` | `transaction_not_found` |
| Needs more confirmations | `wait` | `confirmations_pending` |
| Address/crypto mismatch | `block` | `mismatch` |
| Ambiguous match | `block` | `ambiguous_match` |

Sidecars must treat every non-`allow` decision as no sweep. Sidecars must also
treat transport errors, HTTP errors, authentication failures, invalid JSON, and
timeouts as no sweep.

Small-amount deposits keep the existing behavior: if SHKeeper marks the deposit
as `SKIPPED` under the local small-amount AML policy, sweep is allowed and the
deposit does not need to participate in the new guarded sweep gate.

Legacy deposits keep the existing sweep behavior. If an address has no guarded
AML checks, the endpoint returns `allow` with `legacy_no_guarded_deposits`. If an
address has both legacy funds and guarded deposits, the guarded deposits control
the whole address-level decision. This is intentional because sweep and drain
move the whole token balance on the address.

A periodic address-level request with no `txid` and no guarded AML checks is
treated as legacy for sweep eligibility. Without a deployment timestamp this is
the only unambiguous way to avoid pulling old dev/pre-gate balances into the new
gate.

A live scanner request that supplies a recorded USDT or ETH-USDT `txid` must
fail closed if that transaction has no guarded AML check yet. It returns `wait`
with `aml_missing`, because the sidecar is correlating a concrete new deposit
and must not sweep before SHKeeper's AML state exists. A live scanner request
with a `txid` that SHKeeper has not recorded yet still returns `wait` with
`transaction_not_found`. `aml_missing` also applies after a guarded marker exists
but the guarded AML row is internally incomplete or has an unknown/broken state.

Decision precedence for guarded deposits:

1. Any mismatched or ambiguous guarded deposit returns `block`.
2. Any `MANUAL_REVIEW` guarded deposit without a valid manual resolution returns
   `block`.
3. Otherwise, any guarded AML check that is broken, still pending, checking, or
   attached to a transaction awaiting confirmations returns `wait`.
4. Otherwise, when every guarded deposit is `APPROVED` or `MANUAL_REVIEW` with a
   valid manual resolution, return `allow`. Small-amount `SKIPPED` deposits are
   non-guarded allow-cases in this release.

The effective risk threshold is `0.70` in the intended deployment. SHKeeper's
`AML_MAX_ACCEPT_SCORE` is the AML decision source; `aml-shkeeper`
`AML_DEFAULT_THRESHOLD` is only a provider-side fallback for checks where
SHKeeper did not send a threshold.

AML outage policy:

- If SHKeeper cannot reach `aml-shkeeper`, or `aml-shkeeper` returns transport,
  auth, HTTP, invalid JSON, or another retryable infrastructure error, the
  deposit remains `CHECKING`. Final callback is not sent, Grither Pay does not
  credit the client, and sweep eligibility returns `wait` until retry succeeds or
  `AML_PENDING_TIMEOUT_SECONDS` expires.
- If the retry window expires, SHKeeper moves the deposit to `MANUAL_REVIEW` with
  `decision_reason=aml_pending_timeout`. Final callback is sent with
  `aml.review_required=true`, Grither Pay shows the deposit to admins for manual
  handling, and sweep eligibility returns `block`.
- If Koinkyt or another AML provider returns a terminal provider error,
  SHKeeper moves the deposit to `MANUAL_REVIEW` with
  `decision_reason=aml_provider_error`. This is not treated as clean or dirty by
  automation; it is an unknown AML result that requires operator review. Final
  callback is sent with `aml.review_required=true`, and sweep remains blocked
  until an `approved` or operator-attested `refunded` manual resolution is
  recorded.
- Unexpected exceptions in SHKeeper AML client code are normalized into retryable
  `aml-shkeeper` infrastructure errors. They must not make `walletnotify` fail
  after the transaction has already been persisted.

Grither Pay must not make a second automatic AML policy decision from `score`
using its own threshold. The callback should expose a stable SHKeeper review
signal, for example `aml.review_required=true` and `aml.reason_code`, while
keeping raw SHKeeper internal status and threshold out of the merchant payload.
Grither Pay can display score and provider facts to admins, but the automatic
credit/manual-review split must follow SHKeeper's review signal when it is
present. For backward compatibility, Grither Pay must treat `aml.review_required`
as nullable/tri-state:

- `true`: route to `MANUAL_REVIEW` and do not auto-credit.
- `false`: treat SHKeeper's AML decision as automatically creditable, while still
  enforcing local non-AML checks such as matched invoice, positive amount, and
  ledger precision.
- missing or `null`: legacy callback; fall back to the existing Grither Pay score
  policy so old dev fixtures and replayed callbacks are not silently treated as
  SHKeeper-approved.

If the callback change is staged later, the interim deployment must keep Grither
Pay's local threshold exactly equal to SHKeeper's threshold and cover that with
config tests.

## SHKeeper Endpoint

Add a backend-only endpoint:

```http
POST /api/v1/sweep-eligibility
X-Shkeeper-Backend-Key: <backend key>
Content-Type: application/json
```

Request for TRON USDT:

```json
{
  "crypto": "USDT",
  "network": "TRON",
  "address": "TQZL6tWjV3L1y7mK7Q9TESTADDRESS",
  "txid": "tron-deposit-tx-1"
}
```

Request for ETH-USDT:

```json
{
  "crypto": "ETH-USDT",
  "network": "ETH",
  "address": "0x1111111111111111111111111111111111111111",
  "txid": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

`txid` is optional. Live scanner calls should send it as a correlation hint.
Periodic rescan calls normally send only `crypto`, `network`, and `address`.

Response:

```json
{
  "decision": "allow",
  "reason": "aml_approved",
  "transaction_ids": [123],
  "matched_transaction_count": 1,
  "aml_statuses": ["approved"]
}
```

The endpoint:

- Uses the same backend key trust boundary as `walletnotify`.
- Treats eligibility as an address-level decision because sidecar sweep and
  drain operations move the address token balance, not a single transaction
  amount.
- Uses `txid` as a correlation hint when it is present, but never allows sweep
  for the whole address if any guarded confirmed receive transaction for the
  same `crypto` and `address` is blocked or unresolved.
- Returns `allow` with `legacy_no_guarded_deposits` for address-level requests
  when no matching guarded AML check exists for the same `crypto` and `address`.
- Treats address-level matching transactions without
  `AmlCheck.sweep_guard_required=true` as legacy, even when the rail is USDT or
  ETH-USDT.
- Treats live `txid` requests for a recorded guarded-rail transaction without a
  guarded AML check as `wait`/`aml_missing`.
- Returns `wait` when a live `txid` has not been recorded yet or still needs
  confirmations.
- Returns `allow` only when all matching guarded receive transactions for the
  same `crypto` and `address` are `APPROVED` or `MANUAL_REVIEW` with a valid
  manual resolution.
- Returns `wait` when any matching guarded AML check has an unknown/broken state,
  or is `PENDING` or `CHECKING`.
- Returns `block` when any matching guarded transaction is `MANUAL_REVIEW`,
  mismatched, or ambiguous in a way that could sweep blocked funds, unless every
  matching `MANUAL_REVIEW` transaction has an approved or refunded manual
  resolution.
- Does not call Koinkyt or `aml-shkeeper`.
- Does not recalculate scores in the sidecar.
- Does not expose merchant-facing business decision fields.

For unsupported assets, this first release should not return `allow` for the
TRON USDT or ETH-USDT guarded paths unless there are no guarded checks for that
address or the asset-specific SHKeeper policy has explicitly created an allow
state. The endpoint can remain generic, but the implementation and tests focus
on the two requested rails.

## SHKeeper Manual Resolution Endpoint

Add one backend-only endpoint used by Grither Pay after an operator resolves a
manual-review deposit:

```http
POST /api/v1/sweep-resolution
X-Shkeeper-Backend-Key: <backend key>
Content-Type: application/json
```

Approval request:

```json
{
  "resolution_type": "approved",
  "deposit_id": "shkeeper-tx-123",
  "crypto": "USDT",
  "network": "TRON",
  "address": "TQZL6tWjV3L1y7mK7Q9TESTADDRESS",
  "txid": "tron-deposit-tx-1",
  "external_review_id": "grither-pay-review-456",
  "reviewer": "admin@example.com",
  "reason": "Manual approval after compliance review",
  "idempotency_key": "grither-pay-shkeeper-resolution-456"
}
```

Refunded request:

```json
{
  "resolution_type": "refunded",
  "deposit_id": "shkeeper-tx-123",
  "crypto": "USDT",
  "network": "TRON",
  "address": "TQZL6tWjV3L1y7mK7Q9TESTADDRESS",
  "txid": "tron-deposit-tx-1",
  "refund_txid": "tron-refund-tx-1",
  "refund_to_address": "TSENDERREFUNDADDRESS",
  "refund_amount": "100.000000",
  "external_review_id": "grither-pay-review-789",
  "reviewer": "admin@example.com",
  "reason": "Manual refund completed from VPS script",
  "idempotency_key": "grither-pay-shkeeper-refund-789"
}
```

`deposit_id` is the primary identifier because SHKeeper already sends it in the
callback transaction item. `txid`, `crypto`, `network`, and `address` are
required cross-checking evidence. The endpoint must reject resolutions where the
evidence does not match the guarded deposit being resolved.

The endpoint:

- Uses the same backend key trust boundary as `walletnotify` and
  `sweep-eligibility`.
- Is idempotent by `idempotency_key` and by the resolved SHKeeper deposit.
- Requires `resolution_type`, `deposit_id`, `txid`, `crypto`, `network`,
  `address`, reviewer, reason, external review id, and idempotency key.
- Allows only `resolution_type=approved` and `resolution_type=refunded`.
- Requires refund txid, refund destination, and refund amount for `refunded`.
- Stores one audit row with deposit id, internal transaction id, txid, crypto,
  network, address, resolution type, external review id, reviewer, reason,
  resolved timestamp, raw request digest, and refund evidence when present.
- Only resolves guarded transactions that are in `MANUAL_REVIEW` due to a
  terminal SHKeeper manual-review outcome such as high score, provider error,
  pending timeout, alerts, or incomplete AML result.
- Rejects pending/checking AML, guarded AML rows with missing/broken state,
  non-guarded legacy deposits, and `HELD` states that are not backed by a
  terminal SHKeeper manual-review outcome.
- Does not create or modify Grither Pay ledger entries.
- Does not execute any blockchain transaction.
- Does not call Koinkyt or `aml-shkeeper`.

For `refunded`, the endpoint records that the blocked deposit was handled by a
manual refund and should no longer block future address-level sweep eligibility.
It is not a refund execution API. The actual on-chain refund is done outside
SHKeeper by an operator through a prepared VPS script.

Refund evidence is operator-attested in this release. SHKeeper does not verify
the refund transaction on-chain before unblocking sweep. The trust boundary is:
the operator performs the refund manually through the prepared VPS script, enters
the evidence and reason in Grither Pay, and Grither Pay calls the backend-only
SHKeeper resolution endpoint. SHKeeper validates that the original deposit is a
guarded `MANUAL_REVIEW` deposit, stores the evidence for audit, and treats the
deposit as no longer blocking future sweep.

After a manual resolution, the next `sweep-eligibility` call can return `allow`
only if all other guarded deposits on the same address are also sweep-safe. If
the address has another guarded deposit still pending, checking, attached to a
broken guarded AML row, mismatched, or manual-review without resolution, the
address-level decision remains `wait` or `block`.

## Grither Pay Admin Approval Flow

Grither Pay admin remains the operator UI. SHKeeper must not expose a separate
operator-facing approval screen for this flow.

Approve flow:

1. Operator opens a SHKeeper deposit in the Grither Pay admin only after SHKeeper
   has delivered a terminal manual-review outcome. A generic Grither Pay
   `HELD` state is not enough; pending/checking AML, guarded AML rows with
   missing/broken state, or reconciliation-only holds must remain non-actionable
   until the underlying condition is resolved.
2. Admin UI shows AML facts, SHKeeper review signal, score, report URL, txid,
   address, network, user, ledger state, and reconciliation findings.
3. Operator enters a required reason and clicks `Approve and credit`.
4. Grither Pay records the review decision and credits the client wallet balance
   idempotently.
5. In the same transaction, Grither Pay records an outbox command to call
   SHKeeper `sweep-resolution` with `resolution_type=approved`.
6. An async worker sends the resolution request to SHKeeper and retries transient
   failures.
7. Admin detail shows both states separately:
   - client credit: pending, credited, failed;
   - SHKeeper sweep resolution: pending, resolved, failed.

Refund/reject flow:

1. Operator decides not to credit the manual-review deposit.
2. Operator runs the prepared VPS refund script outside SHKeeper and sends the
   dirty funds back according to the approved manual process.
3. The script output must include refund txid, refund destination, refund amount,
   source address, asset, network, and timestamp.
4. Operator returns to Grither Pay admin, enters the refund evidence and a
   required reason, then clicks `Reject after manual refund`.
5. Grither Pay records the review decision, refund evidence, and rejected
   provider transaction state. The client wallet is not credited.
6. In the same transaction, Grither Pay records an outbox command to call
   SHKeeper `sweep-resolution` with `resolution_type=refunded`.
7. An async worker sends the resolution request to SHKeeper and retries
   transient failures.
8. Admin detail shows both states separately:
   - client credit: not credited;
   - manual refund: recorded, pending SHKeeper resolution, resolved, failed.

There must be no admin action that both rejects a dirty deposit and unblocks
sweep before refund evidence is recorded. A plain rejection without refund
evidence keeps SHKeeper eligibility blocked.

The `approved` resolution request must be after, or durably coupled with, the
Grither Pay credit decision. A failed network call to SHKeeper must not roll back
a successful client credit after commit; it should stay visible as a retryable
resolution failure in admin.

The `refunded` resolution request must be after, or durably coupled with, the
Grither Pay rejected-with-refund decision. A failed network call to SHKeeper must
not erase the refund evidence; it should stay visible as a retryable resolution
failure in admin.

## Client UX

The client should not see raw AML score, provider report, risk category, or
internal reason codes.

Recommended public states:

- AML pending/checking: show the regular processing state.
- Manual review or held by AML policy: show a neutral review state, for example
  `Платёж получен и находится на проверке. Мы уведомим вас о результате.`
- Approved and credited: show the normal completed deposit state.
- Rejected after manual refund evidence is recorded: show `Пополнение отклонено
  после проверки. Обратитесь в поддержку.`

Grither Pay should notify the client on state transitions into manual review,
completion, and rejection. Operator/admin alerts should be sent immediately when a
deposit enters manual review or held state; stale-review alerts remain an
escalation path rather than the first notification.

## TRON Sidecar Integration

Guard every USDT sweep entry point before `transfer_trc20_from` signs or
broadcasts a transaction.

Live scanner path:

1. The block scanner observes a successful incoming USDT transfer to a watched
   one-time address.
2. It calls SHKeeper `walletnotify` as it does today.
3. Before queueing or calling `transfer_trc20_from`, it calls
   `sweep-eligibility`.
4. Only `decision=allow` can enqueue or run `transfer_trc20_from`.
5. `wait` or `block` leaves the balance on the one-time address.

Periodic rescan path:

1. `scan_accounts` finds a USDT balance over the configured sweep threshold.
2. Before calling `transfer_trc20_from`, it calls `sweep-eligibility`.
3. Only `allow` sweeps. All other outcomes are logged and retried on the next
   rescan if the balance is still present.

The guard must live at the start of `transfer_trc20_from`, before account
activation, TRX funding, energy provisioning, or token transfer signing. Tests,
manual Celery commands, live scanner calls, and periodic rescan calls must all
fail closed by default. Sidecars do not implement an operator bypass; they only
trust SHKeeper's eligibility response.

The existing TRON `EXTERNAL_DRAIN_CONFIG` / custom AMLBot payout path must not run
for guarded USDT deposits. It is a legacy sidecar-local AML path and would create
a second, conflicting AML decision after SHKeeper already processed the deposit.
Deployment should either disable that path completely for USDT or bypass it when
the new SHKeeper sweep eligibility guard is enabled.

## Ethereum Sidecar Integration

Guard every ETH-USDT drain entry point before `drain_tocken_account` signs or
broadcasts a transaction.

Live event path:

1. The Ethereum event listener observes a successful incoming ETH-USDT transfer
   to a known account.
2. It calls SHKeeper `walletnotify` as it does today.
3. Before queueing `drain_account("ETH-USDT", address)`, it calls
   `sweep-eligibility`.
4. Only `decision=allow` can enqueue the drain.

Periodic balance refresh path:

1. `refresh_balances` finds an ETH-USDT balance over
   `MIN_TOKEN_TRANSFER_THRESHOLD` or token-specific threshold.
2. Before queueing `drain_account`, it calls `sweep-eligibility`.
3. Only `allow` drains. All other outcomes leave the tokens on the customer
   address until a later refresh.

The final safety point is `drain_account` or a helper it calls, before fee-wallet
ETH gas funding and before token transfer signing, because manual or periodic
task invocations can bypass the event listener. Sidecars do not implement an
operator bypass; they only trust SHKeeper's eligibility response.

## Sweep Amount Semantics

Current TRON and Ethereum sidecars sweep or drain the full token balance of the
one-time address after the balance crosses the existing rail threshold. This
release keeps that behavior: thresholds still decide when a sweep attempt is
worth making, and SHKeeper eligibility decides whether the attempt is allowed.

Because the transfer amount is the full current token balance, the sidecar must
call `sweep-eligibility` at the final safety point before any fee-wallet-funded
activation, gas/energy funding, signing, or broadcasting. A future hardening can
add `safe_sweep_amount` from SHKeeper and amount-limited sidecar transfers, but
that is a larger contract change and is not required for this dev rollout.

## Callback Flow

Callback behavior stays separate from sweep permission:

- `PENDING` and `CHECKING` AML still block final callbacks.
- `APPROVED`, `SKIPPED`, and `MANUAL_REVIEW` are terminal for callback delivery.
- `MANUAL_REVIEW` callbacks still go to Grither Pay with AML facts such as
  `aml.checked`, `aml.check_status`, `aml.review_required`, `aml.reason_code`,
  `aml.score`, and `aml.signals`.
- The callback payload continues not to expose SHKeeper internal
  `deposit_decision`, `decision_reason`, AML `status`, or threshold fields.

This gives Grither Pay timely manual-review evidence while preventing automatic
fund consolidation. Grither Pay should use `aml.review_required` as the automatic
manual-review trigger instead of recomputing a threshold decision from `score`
when the field is present. Missing `review_required` remains a legacy callback
and uses the existing score-threshold policy.

## Error Handling

Sidecars fail closed:

- SHKeeper timeout: no sweep.
- SHKeeper HTTP 500/403/404: no sweep.
- Invalid or missing JSON response: no sweep.
- `decision=wait`: no sweep, retry through periodic rescan.
- `decision=block`: no sweep. Periodic rescans may ask again; they must only
  sweep after SHKeeper later returns `allow`, for example after a valid
  `approved` or operator-attested `refunded` manual resolution.
- Missing transaction immediately after `walletnotify`: no sweep. A later
  rescan can ask again.
- `decision=allow` with `legacy_no_guarded_deposits`: sweep is allowed for
  legacy/pre-gate balances.

SHKeeper endpoint errors should be rare and logged with enough context to
diagnose the rail, address, txid, and reason. Sidecar logs must avoid secrets
and should include decision and reason.

## Security

- The endpoint is backend-only and protected by `X-Shkeeper-Backend-Key`.
- Sidecars must not make risk decisions locally.
- Sidecars must not use `AML_DEFAULT_THRESHOLD` to recalculate approve/reject.
- Sidecars must never treat an unavailable SHKeeper as approval.
- Manual resolution is audited in SHKeeper and initiated only from Grither Pay
  backend after a recorded operator approval or refund/reject decision.
- `refunded` resolution is operator-attested; SHKeeper stores refund evidence and
  does not perform on-chain verification in this release.
- A rejection without refund evidence must not unblock sweep.
- Grither Pay must not expose AML score, report, or risk details to the client.

## Observability

This release should log structured decisions before skipping or executing
sweep. Metrics are optional for the first implementation, but the design should
leave room for counters:

- `sweep_eligibility_allow_total`
- `sweep_eligibility_wait_total`
- `sweep_eligibility_block_total`
- `sweep_eligibility_error_total`
- `sweep_resolution_created_total`
- `sweep_resolution_rejected_total`
- `sweep_resolution_refund_attested_total`

Labels should be limited to rail, crypto, network, and reason. Addresses and
txids should stay out of metric labels.

## Testing

SHKeeper:

- `APPROVED` AML check returns `allow`.
- `SKIPPED` small-amount AML check returns `allow`.
- `PENDING` and `CHECKING` return `wait`.
- `MANUAL_REVIEW` returns `block`.
- `aml-shkeeper` transport/auth/HTTP/invalid JSON errors remain retryable
  `CHECKING`, block final callback, and return `wait` for sweep.
- Unexpected AML client exceptions are normalized into retryable `CHECKING`
  instead of propagating out of `walletnotify`.
- AML pending timeout becomes `MANUAL_REVIEW` with
  `decision_reason=aml_pending_timeout`, allows final callback, and blocks sweep.
- Terminal Koinkyt/provider errors become `MANUAL_REVIEW` with
  `decision_reason=aml_provider_error`, allow final callback, and block sweep.
- `MANUAL_REVIEW` with a valid `approved` resolution returns `allow` only when all other
  guarded deposits on the same address are also sweep-safe.
- `MANUAL_REVIEW` with a valid operator-attested `refunded` resolution returns `allow`
  only when all other guarded deposits on the same address are also sweep-safe.
- Resolving one `MANUAL_REVIEW` deposit does not allow sweep when another guarded
  deposit on the same address is still pending, checking, attached to a broken
  guarded AML row, mismatched, or unresolved manual review.
- Resolving one `MANUAL_REVIEW` deposit as manually refunded does not allow sweep
  when another guarded deposit on the same address is still pending, checking,
  attached to a broken guarded AML row, mismatched, or unresolved manual review.
- Existing transactions and AML checks with `sweep_guard_required=false` do not
  block address-level periodic sweep and return `allow` with
  `legacy_no_guarded_deposits` when no guarded checks match the address.
- Existing USDT or ETH-USDT transactions without an AML check also remain legacy
  for address-level periodic sweep and return `allow` with
  `legacy_no_guarded_deposits` when no guarded checks match the address.
- Live `txid` requests for recorded USDT or ETH-USDT transactions without a
  guarded AML check return `wait` with `aml_missing`.
- New non-skipped guarded deposits set `sweep_guard_required=true` when SHKeeper
  creates the AML check.
- New skipped small-amount USDT and ETH-USDT checks do not need
  `sweep_guard_required=true` and do not block sweep.
- A guarded AML check with an unknown or broken AML state returns `wait` with
  `aml_missing`.
- `MANUAL_REVIEW` still allows final callback delivery to Grither Pay.
- Unknown transaction returns no `allow`.
- Address or crypto mismatch returns no `allow`.
- Backend key is required.
- Endpoint does not call `aml-shkeeper` or Koinkyt.
- Manual resolution endpoint rejects missing reviewer, missing reason, missing
  `deposit_id`, unsupported `resolution_type`, mismatched
  address/crypto/network/txid, non-guarded transactions, non-`MANUAL_REVIEW`
  transactions, non-actionable `HELD` states, and duplicate conflicting
  idempotency keys.
- `refunded` resolution rejects missing refund txid, missing refund destination,
  missing refund amount, reviewer, reason, or idempotency key.

Grither Pay:

- Admin approve records manual review decision and credits the wallet
  idempotently.
- `aml.review_required=true` creates an actionable `MANUAL_REVIEW`; `false`
  permits auto-credit after local non-AML validations; missing/null falls back to
  the existing score-threshold policy.
- Admin approve records a durable outbox command for SHKeeper `sweep-resolution`
  with `resolution_type=approved`.
- SHKeeper resolution failure after credit remains visible and retryable in admin.
- Admin reject after manual refund records review decision and refund evidence,
  does not credit the wallet, and records a durable outbox command for SHKeeper
  `sweep-resolution` with `resolution_type=refunded`.
- Admin must not offer a sweep-unblocking reject path without refund evidence.
- SHKeeper resolution failure after reject remains visible and retryable in admin.
- Client sees neutral `На проверке` wording for manual-review/held deposits and
  does not see AML score, report URL, provider details, or internal reason codes.

TRON sidecar:

- Live scanner asks eligibility before queueing `transfer_trc20_from`.
- Periodic `scan_accounts` asks eligibility before calling
  `transfer_trc20_from`.
- `allow` preserves the existing sweep behavior.
- `wait`, `block`, invalid JSON, HTTP error, timeout, and auth error do not call
  `transfer_trc20_from`.
- Existing TRON sweep threshold behavior remains unchanged after `allow`.

Ethereum sidecar:

- Event listener asks eligibility before queueing `drain_account` for ETH-USDT.
- `refresh_balances` asks eligibility before queueing ETH-USDT drain.
- `allow` preserves existing drain behavior.
- `wait`, `block`, invalid JSON, HTTP error, timeout, and auth error do not call
  `drain_tocken_account`.
- Existing ETH-USDT token threshold behavior remains unchanged after `allow`.

## Rollout

1. Deploy SHKeeper endpoint first. With no sidecar changes, behavior remains
   unchanged.
2. Deploy the `sweep_guard_required` migration with default `false` for existing
   rows.
3. Enable marking of new non-skipped guarded deposits in SHKeeper AML check
   creation.
4. Deploy SHKeeper manual resolution endpoint with audit storage.
5. Deploy Grither Pay admin approve and reject-after-refund outbox changes.
6. Deploy TRON sidecar with eligibility guard enabled for USDT.
7. Deploy Ethereum sidecar with eligibility guard enabled for ETH-USDT.
8. Watch sidecar logs for `wait` and `block` decisions and confirm Grither Pay
   callbacks still arrive for manual-review deposits.
9. Approve one dev manual-review deposit in Grither Pay admin and verify client
   credit, SHKeeper resolution audit, later `sweep-eligibility=allow`, and sidecar
   sweep.
10. Manually refund one dev manual-review deposit with the prepared VPS script,
   reject it in Grither Pay admin with refund evidence, verify SHKeeper refund
   resolution audit, later `sweep-eligibility=allow`, and sidecar sweep of any
   remaining clean/legacy balance.
11. Keep `AML_MAX_ACCEPT_SCORE` and `AML_DEFAULT_THRESHOLD` aligned at `0.70` for
   the target deployment.

## Open Future Work

Future design can add:

- Optional quarantine wallet routing instead of direct `fee_deposit` sweep.
- More granular resolution policies for addresses with multiple guarded deposits.
- Dedicated compliance queue analytics and SLA dashboards in Grither Pay admin.
