# AML-Gated USDT Sweep Design

Date: 2026-06-06
Status: Approved for planning

## Summary

TRON USDT and ETH-USDT deposits must not be swept from a customer one-time
address into the sidecar `fee_deposit` wallet unless SHKeeper has an explicit
AML-safe sweep decision for that deposit. SHKeeper remains the owner of AML
state and callback behavior. TRON and Ethereum sidecars ask SHKeeper for a
backend-only sweep eligibility decision before signing any sweep or drain
transaction. The sidecars fail closed: no explicit `allow` means no sweep.

Confirmed payment callbacks to Grither Pay continue to work for manual review
cases. `MANUAL_REVIEW` stays terminal for callback delivery, but blocks sweep.

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

Out of scope:

- Manual release after Grither Pay operator approval.
- Quarantine or review wallets.
- Non-USDT assets and non-TRON/non-ETH rails.
- Direct AML provider calls from sidecars.
- Changing Grither Pay business decision semantics.

The design keeps room for a future manual release flow. Later, Grither Pay can
call a SHKeeper release endpoint after operator approval; SHKeeper can then make
the same eligibility endpoint return `allow` without requiring sidecar contract
changes.

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

## Policy

Sweep eligibility is derived from SHKeeper's stored deposit state:

| AML state | Sweep decision | Reason |
| --- | --- | --- |
| `APPROVED` | `allow` | `aml_approved` |
| `SKIPPED` | `allow` | `aml_skipped_small_amount` |
| `PENDING` | `wait` | `aml_pending` |
| `CHECKING` | `wait` | `aml_checking` |
| `MANUAL_REVIEW` | `block` | `manual_review` |
| Missing transaction | `wait` | `transaction_not_found` |
| Needs more confirmations | `wait` | `confirmations_pending` |
| Address/crypto mismatch | `block` | `mismatch` |
| Ambiguous match | `block` | `ambiguous_match` |

Sidecars must treat every non-`allow` decision as no sweep. Sidecars must also
treat transport errors, HTTP errors, authentication failures, invalid JSON, and
timeouts as no sweep.

Small-amount deposits keep the existing behavior: if SHKeeper marks the deposit
as `SKIPPED` under the local small-amount AML policy, sweep is allowed.

The effective risk threshold is `0.10` in the intended deployment. SHKeeper's
`AML_MAX_ACCEPT_SCORE` is the decision source; `aml-shkeeper`
`AML_DEFAULT_THRESHOLD` is a fallback for checks where SHKeeper did not send a
threshold. Operators should keep both values aligned.

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
  "address": "T...",
  "txid": "..."
}
```

Request for ETH-USDT:

```json
{
  "crypto": "ETH-USDT",
  "network": "ETH",
  "address": "0x...",
  "txid": "0x..."
}
```

Response:

```json
{
  "decision": "allow",
  "reason": "aml_approved",
  "transaction_id": 123,
  "aml_status": "approved"
}
```

The endpoint:

- Uses the same backend key trust boundary as `walletnotify`.
- Treats eligibility as an address-level decision because sidecar sweep and
  drain operations move the address token balance, not a single transaction
  amount.
- Uses `txid` as a correlation hint when it is present, but never allows sweep
  for the whole address if any matching confirmed receive transaction for the
  same `crypto` and `address` is blocked or unresolved.
- Returns `wait` when a matching transaction has not been recorded yet or still
  needs confirmations.
- Returns `allow` only when all known confirmed receive transactions for the
  same `crypto` and `address` are `APPROVED` or `SKIPPED`.
- Returns `wait` when any matching transaction is `PENDING` or `CHECKING`.
- Returns `block` when any matching transaction is `MANUAL_REVIEW`, mismatched,
  or ambiguous in a way that could sweep blocked funds.
- Does not call Koinkyt or `aml-shkeeper`.
- Does not recalculate scores in the sidecar.
- Does not expose merchant-facing business decision fields.

For unsupported assets, this first release should not return `allow` for the
TRON USDT or ETH-USDT guarded paths unless the asset-specific SHKeeper policy
has explicitly created an allow state. The endpoint can remain generic, but the
implementation and tests focus on the two requested rails.

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

The guard must live inside or immediately before `transfer_trc20_from`, so tests,
manual Celery commands, live scanner calls, and periodic rescan calls all fail
closed by default. This release does not add an operator bypass.

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

The final safety point is `drain_account` or a helper it calls, because manual
or periodic task invocations can bypass the event listener. This release does
not add an operator bypass.

## Callback Flow

Callback behavior stays separate from sweep permission:

- `PENDING` and `CHECKING` AML still block final callbacks.
- `APPROVED`, `SKIPPED`, and `MANUAL_REVIEW` are terminal for callback delivery.
- `MANUAL_REVIEW` callbacks still go to Grither Pay with AML facts such as
  `aml.checked`, `aml.check_status`, `aml.reason_code`, `aml.score`, and
  `aml.signals`.
- The callback payload continues not to expose SHKeeper internal
  `deposit_decision`, `decision_reason`, AML `status`, or threshold fields.

This gives Grither Pay timely manual-review evidence while preventing automatic
fund consolidation.

## Error Handling

Sidecars fail closed:

- SHKeeper timeout: no sweep.
- SHKeeper HTTP 500/403/404: no sweep.
- Invalid or missing JSON response: no sweep.
- `decision=wait`: no sweep, retry through periodic rescan.
- `decision=block`: no sweep, no automatic retry state change.
- Missing transaction immediately after `walletnotify`: no sweep. A later
  rescan can ask again.

SHKeeper endpoint errors should be rare and logged with enough context to
diagnose the rail, address, txid, and reason. Sidecar logs must avoid secrets
and should include decision and reason.

## Security

- The endpoint is backend-only and protected by `X-Shkeeper-Backend-Key`.
- Sidecars must not make risk decisions locally.
- Sidecars must not use `AML_DEFAULT_THRESHOLD` to recalculate approve/reject.
- Sidecars must never treat an unavailable SHKeeper as approval.
- Manual release is not implemented in this scope, so there is no admin bypass
  path to audit yet.

## Observability

This release should log structured decisions before skipping or executing
sweep. Metrics are optional for the first implementation, but the design should
leave room for counters:

- `sweep_eligibility_allow_total`
- `sweep_eligibility_wait_total`
- `sweep_eligibility_block_total`
- `sweep_eligibility_error_total`

Labels should be limited to rail, crypto, network, and reason. Addresses and
txids should stay out of metric labels.

## Testing

SHKeeper:

- `APPROVED` AML check returns `allow`.
- `SKIPPED` small-amount AML check returns `allow`.
- `PENDING` and `CHECKING` return `wait`.
- `MANUAL_REVIEW` returns `block`.
- `MANUAL_REVIEW` still allows final callback delivery to Grither Pay.
- Unknown transaction returns no `allow`.
- Address or crypto mismatch returns no `allow`.
- Backend key is required.
- Endpoint does not call `aml-shkeeper` or Koinkyt.

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
2. Deploy TRON sidecar with eligibility guard enabled for USDT.
3. Deploy Ethereum sidecar with eligibility guard enabled for ETH-USDT.
4. Watch sidecar logs for `wait` and `block` decisions and confirm Grither Pay
   callbacks still arrive for manual-review deposits.
5. Keep `AML_MAX_ACCEPT_SCORE` and `AML_DEFAULT_THRESHOLD` aligned at `0.10` for
   the target deployment.

## Open Future Work

Manual release after Grither Pay approval is intentionally not part of this
scope. A future design can add:

- Grither Pay to SHKeeper manual release API.
- Operator, reason, and evidence audit fields.
- Eligibility transition from `block` to `allow` after release.
- Optional quarantine wallet routing instead of direct `fee_deposit` sweep.

The sidecar contract does not need to change for that future work because it
already trusts the SHKeeper eligibility decision.
