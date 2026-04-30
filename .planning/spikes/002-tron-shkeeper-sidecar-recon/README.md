---
spike: 002
name: tron-shkeeper-sidecar-recon
type: standard
validates: "Given the existing vsys-host/tron-shkeeper sidecar implements ENERGY_DELEGATION_MODE via freeze v2, when we read its sweep/payout/delegate code paths, then we know exactly where to inject a re:Fee call (which function, which env vars, which return contract)"
verdict: VALIDATED
related: [001]
tags: [tron, sidecar, recon]
---

# Spike 002: tron-shkeeper sidecar recon

## What This Validates

**Given** the existing `vsys-host/tron-shkeeper` sidecar implements `ENERGY_DELEGATION_MODE` via TRON freeze v2 + delegate_resource,
**When** we read its sweep/payout/delegate code paths end-to-end,
**Then** we identify the exact insertion point for re:Fee `rent_resource` order creation, the env vars to add, the abstraction shape, and the timing/retry semantics needed to replace freeze-v2 delegation with rented energy.

## Stack & Layout

- **Language/framework:** Python 3.13, Flask 3.1, Celery 5.4 with Redis broker, SQLModel + SQLite
- **Tron client lib:** `tronpy==0.5.0` (pure Python, no Java bridge)
- **HTTP client:** `requests==2.32.3` (already used to talk back to shkeeper main + AML)
- **Top-level dirs:**
  - `/Users/test/PycharmProjects/tron-shkeeper/run.py` — entrypoint (gunicorn loads `server`, plus a block-scanner + best-server-refresh thread)
  - `/Users/test/PycharmProjects/tron-shkeeper/celery_worker.py` — celery worker entrypoint
  - `/Users/test/PycharmProjects/tron-shkeeper/app/__init__.py` — Flask app factory; registers blueprints and starts wallet init
  - `/Users/test/PycharmProjects/tron-shkeeper/app/api/` — HTTP layer: `payout.py`, `staking.py`, `views.py`, `metrics.py`
  - `/Users/test/PycharmProjects/tron-shkeeper/app/tasks.py` — Celery tasks; **the actual TRC-20 sweep + freeze/delegate logic lives here**
  - `/Users/test/PycharmProjects/tron-shkeeper/app/wallet.py` — `Wallet` class (per-symbol balance/transfer)
  - `/Users/test/PycharmProjects/tron-shkeeper/app/utils.py` — `get_energy_delegator()`, `has_free_bw()`, `estimateenergy()`, etc.
  - `/Users/test/PycharmProjects/tron-shkeeper/app/connection_manager.py` — multi-fullnode pool with best-server selection
  - `/Users/test/PycharmProjects/tron-shkeeper/app/config.py` — pydantic-settings; **all `ENERGY_DELEGATION_MODE*` flags live here**
  - `/Users/test/PycharmProjects/tron-shkeeper/app/custom/aml/` — separate AML drain workflow (alternative payout path; same energy-delegation strategy)

## Key Endpoints (sidecar HTTP API)

All authenticated with HTTP Basic (`API_USERNAME`/`API_PASSWORD`). Dispatch is symbol-prefixed via Flask URL converter (`/<symbol>/...`).

| Endpoint | Method | Handler file:line | Owns |
|---|---|---|---|
| `/<symbol>/payout/<to>/<amount>` | POST | `app/api/payout.py:75` | Single merchant payout (chains `prepare_payout` → `payout` Celery tasks) |
| `/<symbol>/multipayout` | POST | `app/api/payout.py:24` | Batch merchant payout (chains `prepare_multipayout` → `payout`) |
| `/<symbol>/calc-tx-fee/<amount>` | POST | `app/api/payout.py:18` | Returns `config.TX_FEE` (40 TRX baseline) |
| `/<symbol>/task/<id>` | POST | `app/api/payout.py:84` | Polls Celery `AsyncResult` |
| `/<symbol>/generate-address` | POST | `app/api/views.py:21` | Onetime address generation |
| `/<symbol>/balance`, `/status`, `/transaction/<txid>`, `/dump`, `/addresses`, `/fee-deposit-account`, `/estimate-energy/...` | various | `app/api/views.py` | Read-only wallet state |
| `/staking/info` | GET | `app/api/staking.py:18` | Returns delegation config + on-chain status of fee-deposit + energy-delegator accounts |
| `/staking/`, `/staking/<address>` | GET | `app/api/staking.py:97` | Returns `account_info`, `delegated_resources`, `account_resource` |
| `/staking/freeze/<amount>/<res_type>` | POST | `app/api/staking.py:139` | TRX freeze v2 (via `tronpy.trx.freeze_balance`) |
| `/staking/unfreeze/<amount>/<res_type>` | POST | `app/api/staking.py:155` | TRX unfreeze v2 |
| `/staking/withdraw_unfreezed`, `/staking/withdraw_stake_balance` | POST | `app/api/staking.py:171,194` | Pull unstaked TRX back |
| `/staking/delegate/<address>/<amount>/<res_type>` | POST | `app/api/staking.py:212` | Manual delegate (admin) |
| `/staking/undelegate/<address>/<amount>/<res_type>` | POST | `app/api/staking.py:232` | Manual undelegate (admin) |
| `/staking/claim_voting_reward` | POST | `app/api/staking.py:183` | SR vote rewards |

**Important:** the user-facing merchant payout endpoint (`POST /<symbol>/payout/<to>/<amount>`) in master is **NOT where energy delegation runs**. That endpoint just enqueues `prepare_payout` → `payout` (`app/tasks.py:75`), and the `Wallet.transfer()` method (`app/wallet.py:64`) sends *from the fee_deposit account* (the hot wallet). Energy delegation only runs in the **sweep** task `transfer_trc20_from` (`app/tasks.py:88`), which is the actual user-wallet → fee_deposit drain. This is the operation re:Fee will replace.

## Payout / Sweep Flow (the critical path)

There are TWO distinct flows in this sidecar — confusingly both called "payout". The user's re:Fee integration target is the **sweep flow** (USDT-TRC20 from a user-wallet to fee_deposit), not the merchant payout flow.

### Flow A — Merchant payout (`POST /<symbol>/payout/<dest>/<amount>`)

Source = always the `fee_deposit` (hot wallet). No energy delegation needed because hot wallet is pre-funded with TRX/energy.

1. HTTP entry: `app/api/payout.py:75` `payout(to, amount)` → enqueues celery chain
2. `prepare_payout` task at `app/tasks.py:42` → checks balance, returns `[{dst, amount}]`
3. `payout` task at `app/tasks.py:75` → calls `wallet.transfer(dst, amount)` (parallel via ThreadPoolExecutor)
4. `Wallet.transfer()` at `app/wallet.py:64` → builds + signs + broadcasts via tronpy
5. POST result back to shkeeper main at `tasks.py:526` (`http://{SHKEEPER_HOST}/api/v1/payoutnotify/{symbol}`)

**Source address:** hardcoded inside `Wallet.__init__()` via class-level `main_account = query_db2('select * from keys where type = "fee_deposit"', one=True)` at `app/wallet.py:19`. The optional `src_address=` kwarg on `Wallet.transfer()` (line 64) lets callers override it; the AML wallet uses this.

### Flow B — Sweep / `transfer_trc20_from` (THE TARGET FOR re:Fee)

Source = a user-wallet (`onetime` key), destination = `fee_deposit`. Trigger = periodic `scan_accounts` celery task (`app/tasks.py:553`) which iterates all onetime addresses with USDT balance > threshold.

1. Periodic task `scan_accounts` at `app/tasks.py:555` runs every `BALANCES_RESCAN_PERIOD` (3600s default). For each onetime account whose TRC20 balance ≥ `min_transfer_threshold`, it calls `transfer_trc20_from(account, symbol)` at `app/tasks.py:703`.
2. `transfer_trc20_from` at `app/tasks.py:88` — **the function re:Fee plugs into**:
   - L94-100: get tronpy client, contract, decimals, fee-deposit keys
   - L108: `energy_delegator_priv, energy_delegator_pub = get_energy_delegator()` (`app/utils.py:99`)
   - L186-197: balance/threshold check
   - L199: `if config.ENERGY_DELEGATION_MODE:` — **THE BRANCH**
   - L204-209: estimate bandwidth needed (delegate + undelegate + transfer = 904 bytes)
   - L211-222: bandwidth pre-check (`has_free_bw` of energy_delegator) + optional burn-TRX-for-bandwidth
   - L224-283: ensure onetime account is on-chain (activate by sending 0.1 TRX if needed)
   - L285-292: `tron_client.get_estimated_energy(...)` for `transfer(address,uint256)` → `energy_needed`
   - L294-306: if onetime already has enough EnergyLimit, skip delegation
   - L309-355: else, compute `sun_needed` via `calc_sun_for_energy_delegation()` (L115-120: ratio of `TotalEnergyWeight × energy / TotalEnergyLimit`, multiplied by `ENERGY_DELEGATION_FACTOR`), then call inner `delegate_energy(sun_needed)` (L122-184)
   - `delegate_energy()`:
     - L124-127: `wallet/getcandelegatedmaxsize` RPC to verify pool has capacity
     - L149-158: `tron_client.trx.delegate_resource(owner=delegator, receiver=onetime, balance=sun_to_delegate, resource="ENERGY")` → build → sign → broadcast → wait
     - L168-184: re-fetch onetime resources to confirm `EnergyLimit ≥ energy_needed`
   - L401-410: build the actual `contract.functions.transfer(main_publ_key, token_balance).with_owner(onetime).fee_limit(...)` USDT-TRC20 transfer, sign with onetime priv key, broadcast
   - L412-416: if `ENERGY_DELEGATION_MODE`, schedule `undelegate_energy(onetime_publ_key)` via celery (`app/tasks.py:421`) which calls `tron_client.trx.undelegate_resource(...)`
3. `else:` branch at L366-395 — TRX-burn-mode payout: send `INTERNAL_TX_FEE` (40 TRX) from main → onetime, then onetime burns it for energy on the transfer.
4. AML alt path: `app/custom/aml/classes.py:18` `AmlWallet.payout_for_tx()` — calls `Wallet.transfer(dst, amount, src_address=account)`, but **does NOT call `delegate_energy`**; in AML mode the sidecar only burns TRX (relies on whatever bandwidth/energy onetime already has). re:Fee will need to be wired here too if AML is in scope.

### Source-address-selection insight

The merchant payout endpoint URL only carries `dest+amount`; the **source is implicit and always the fee_deposit account** (`app/wallet.py:19,67`). This means re:Fee delegation does NOT belong on the merchant payout path — that account is pre-funded.

Energy delegation belongs **only on the sweep path** where the SOURCE is a fresh `onetime` user-wallet that has zero TRX/energy of its own. The source for that path is the `onetime_publ_key` argument to `transfer_trc20_from`.

## Existing Energy Delegation (master branch)

- **Master switch read at:** `app/tasks.py:199` (`if config.ENERGY_DELEGATION_MODE:`) and `app/tasks.py:412` (post-transfer undelegate trigger). Also gates wallet-init in `app/utils.py:84` (`init_wallet`) and `get_energy_delegator()` (`app/utils.py:99`).
- **Delegate function:** the inner `delegate_energy(sun_to_delegate)` defined inline inside `transfer_trc20_from` at `app/tasks.py:122-184`. Direct tronpy call: `tron_client.trx.delegate_resource(owner=delegator, receiver=onetime, balance=sun, resource="ENERGY")` at line 149.
- **When triggered:** per-payout, JIT, only when `onetime_energy_available < energy_needed` (`app/tasks.py:297-303` skips delegation if onetime already has enough). Synchronous: `signed_tx.broadcast().wait()` (line 158) blocks until on-chain confirmation, then re-checks resources before continuing.
- **Pre-staked pool model:** YES — there is a long-lived `energy_delegator` account that has TRX frozen via freeze-v2 (`/staking/freeze` endpoint). `delegate_energy` only redirects already-frozen capacity to the onetime address; it does not freeze new TRX per call. Capacity check via `wallet/getcandelegatedmaxsize` (`app/tasks.py:124-127`).
- **Amount estimation:** `calc_sun_for_energy_delegation(energy, res)` at `app/tasks.py:115-120`:
  ```
  trx = ceil(TotalEnergyWeight * energy_needed / TotalEnergyLimit)
  trx *= ENERGY_DELEGATION_MODE_ENERGY_DELEGATION_FACTOR  # default 1.0, can over-provision via 1.2
  return int(trx * 1_000_000)  # to sun
  ```
  `energy_needed` itself comes from `tron_client.get_estimated_energy(onetime, contract, "transfer(address,uint256)", encoded_args)` at `app/tasks.py:286-291` — the chain-side simulator.
- **Undelegate trigger:** `app/tasks.py:412-416` schedules `undelegate_energy.delay(onetime_publ_key)` AFTER the TRC-20 transfer completes successfully. The `undelegate_energy` task at `app/tasks.py:421-467` looks up `frozen_balance_for_energy` via `get_delegated_resource_v2` and calls `tron_client.trx.undelegate_resource(...)` to reclaim. This is the equivalent of "release the energy" — re:Fee handles this automatically after 1h.

## resource-delegation-mode branch — what differs from master

```bash
git -C /Users/test/PycharmProjects/tron-shkeeper log master..origin/resource-delegation-mode --oneline
# (empty)
git -C /Users/test/PycharmProjects/tron-shkeeper log origin/resource-delegation-mode..master --oneline
# 6 commits — master is AHEAD
```

**Finding:** The `resource-delegation-mode` branch was MERGED into master in commit `5055925` (PR #18, 2025-11-06: "Merge pull request #18 from vsys-host/resource-delegation-mode — Resource delegation mode"). Master now contains the entire energy-delegation implementation (260+ lines added to `app/api/staking.py`, 534+ to `app/tasks.py`, all the `ENERGY_DELEGATION_MODE*` config flags, `KeyType.energy_delegation`, etc.). The remote branch `resource-delegation-mode` is now stale (6 commits behind master).

The diff between current `master` and stale `resource-delegation-mode` shows only minor master-side improvements:
- `a9baff6` — skip transfer from main account in `transfer_trc20_from` and `transfer_trx_from`
- `f229a9f` — add fee-deposit account to watch list
- `6c42ca7` — move `has_free_bw(BANDWIDTH_PER_TRC20_TRANSFER_CALL)` check to AFTER mode-branching so it runs in both delegation and burn modes
- `8a28cde`, `c325b3b` — unrelated (transaction info, client timeout)

**There is NO new abstraction** in `resource-delegation-mode` that would make re:Fee plug-and-play. The branch is the freeze-v2 / delegate-v2 implementation. It does NOT separate "delegation source" from "delegation transport"; the function `delegate_energy()` inlines the tronpy call directly. So re:Fee integration must either (a) introduce a new abstraction layer, or (b) branch by env var inside `transfer_trc20_from`.

## Best integration point for re:Fee

**The single insertion point:** `app/tasks.py:transfer_trc20_from` between L294 (after `energy_needed` is computed and confirmed > available) and L401 (where the actual TRC-20 contract call is built). Specifically, replace the inner `delegate_energy(sun_to_delegate)` call at L354 with a `rent_energy_via_refee(receiver=onetime_publ_key, energy=energy_needed)` call.

### Recommended shape: small abstraction + env-var dispatch

Introduce a new module `app/energy_provider.py` exposing one method:

```python
class EnergyProvider:
    def acquire(self, receiver: TronAddress, energy: int) -> bool:
        """Make `energy` units available on `receiver`. Returns True if delegated and confirmed."""
class StakingEnergyProvider(EnergyProvider):  # current freeze-v2 logic, lifted from delegate_energy()
class RefeeEnergyProvider(EnergyProvider):    # new, calls rent_resource API
def get_energy_provider() -> EnergyProvider:
    if config.ENERGY_SOURCE == "refee": return RefeeEnergyProvider(...)
    return StakingEnergyProvider(...)
```

Replace `delegate_energy(sun_needed)` at `app/tasks.py:354` with `get_energy_provider().acquire(onetime_publ_key, energy_needed)`. Skip `calc_sun_for_energy_delegation` for re:Fee (re:Fee charges in energy units, not sun).

Skip the post-transfer `undelegate_energy.delay(...)` at `app/tasks.py:412-416` when `ENERGY_SOURCE == "refee"` — energy returns to re:Fee automatically after 1h.

### Wait-for-`delegated` semantics

re:Fee `POST /api/rent_resource/orders` returns immediately with `status: pending`. We need `status == delegated` before broadcasting the TRC-20 transfer (otherwise the transfer will fail with `OUT_OF_ENERGY` or burn TRX). The current `delegate_energy()` at `app/tasks.py:158` uses `signed_tx.broadcast().wait()` which blocks until on-chain confirmation (~3-15s typical) and then re-fetches `account_resource` at L168-176 to confirm `EnergyLimit ≥ energy_needed`. Mirror this pattern:

- Poll `GET /api/rent_resource/orders/{order_id}` (or whatever read endpoint exists — verify in spike 003) every ~2s up to a 60s deadline
- Cross-check on-chain via `tron_client.get_account_resource(onetime_publ_key)` to confirm energy actually arrived (defense in depth — re:Fee bug or out-of-band undelegate would otherwise cause silent failure)
- On timeout: if `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT` is true, fall back to TRX-burn flow (`app/tasks.py:366` else branch); otherwise return without broadcasting (matches existing `return` at L355 when delegate fails)

### What needs to be passed to re:Fee

From `transfer_trc20_from`:
- `address` field in re:Fee POST = `onetime_publ_key` (the user-wallet — confirmed by re:Fee OpenAPI: "Wallet address" / "The address of the resource recipient", i.e. delegate TO this address)
- `amount` field = `energy_needed` from `tron_client.get_estimated_energy(...)` at L286-291. Spike 001 measured this as **65,000 for activated USDT-experienced address**, ~120-130k for first-ever transfer (storage slot init). The dynamic value from `get_estimated_energy` is the right input — don't hardcode 65000.
- `resource` = `"energy"` (lowercase per re:Fee OpenAPI)
- `duration_label` = `"1h"` (cheapest tier per spike 001 break-even — 37 sun/energy = 2.41 TRX/transfer vs 13.65 TRX burn)

### New env vars to add to `app/config.py`

```python
ENERGY_SOURCE: Literal["staking", "refee"] = "staking"   # default keeps current behavior
REFEE_API_BASE_URL: str = "https://api.refee.bot/v2"
REFEE_API_KEY: SecretStr | None = None
REFEE_RENT_DURATION_LABEL: Literal["1h","1d","3d","7d","14d"] = "1h"
REFEE_RENT_ENERGY_OVERPROVISION_FACTOR: Decimal = Decimal("1.05")  # 5% safety margin over estimated
REFEE_RENT_POLL_INTERVAL_SEC: float = 2.0
REFEE_RENT_TIMEOUT_SEC: int = 60
REFEE_FALLBACK_ON_FAILURE: bool = True   # if true and delegated fails, try burn-TRX flow
```

The existing `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT` flag (already in `config.py:68`) covers fallback semantics for the staking provider; re-use it for re:Fee provider too.

### Why an abstraction beats a pure if/else

The `transfer_trc20_from` function is already 280+ lines and intermixes concerns (activation, bandwidth, energy estimation, delegation, transfer, undelegate). Adding a third inline branch (`if ENERGY_SOURCE == "refee": ... elif ENERGY_DELEGATION_MODE: ... else: <burn>`) would push it past readability. A 50-line `RefeeEnergyProvider.acquire()` in a new module + a 1-line replacement at L354 is much cleaner and matches how `Wallet` already abstracts the per-symbol transfer.

## Sweep Trigger (threshold-based)

The user mentioned: "перед вызовом sweep на основной баланс при достижении определенной суммы". Locating this:

**In sidecar:** the threshold check is in `transfer_trc20_from` at `app/tasks.py:187-197`:
```python
min_threshold = config.get_min_transfer_threshold(symbol)
balance = Decimal(token_balance) / 10**precision
if balance <= min_threshold:
    logger.warning(...)
    return  # skip sweep
```
Per-symbol threshold from `Settings.get_min_transfer_threshold()` at `app/config.py:99-110`. Defaults: `USDT=5`, `USDC=5`. Overridable via `USDT_MIN_TRANSFER_THRESHOLD` / `USDC_MIN_TRANSFER_THRESHOLD` env vars.

The trigger itself (the "when do we run a sweep") is the periodic `scan_accounts` celery task at `app/tasks.py:553-717`, scheduled in `setup_periodic_tasks` at `app/tasks.py:817` (`sender.add_periodic_task(BALANCES_RESCAN_PERIOD, scan_accounts.s())`). Default cadence: every 3600s. `scan_accounts` walks all onetime accounts, fetches each token balance, and if `> 0` queues `transfer_trc20_from(account, symbol)` (at `app/tasks.py:703`); the threshold filter inside `transfer_trc20_from` then drops sub-threshold balances.

**In shkeeper main:** there is also a `task_payout()` cron at `/Users/test/PycharmProjects/shkeeper.io/shkeeper/tasks.py:33` that handles `PayoutPolicy.LIMIT` — but that drives the **merchant payout** flow, not the sidecar-internal sweep. The sweep is fully owned by the sidecar's `scan_accounts` task. The shkeeper main `mkpayout()` (at `/Users/test/PycharmProjects/shkeeper.io/shkeeper/modules/classes/tron_token.py:126`) hits `POST /<crypto>/payout/<dest>/<amount>`, which goes through the **merchant payout** flow (Flow A above) — this is NOT where re:Fee plugs in.

**Implication:** re:Fee integration is purely a sidecar concern. The shkeeper main repo does not need any changes to support per-sweep energy rental. (It MAY need new admin UI to surface the new env vars and re:Fee balance — orthogonal.)

## Architectural Questions Resolved

- The `resource-delegation-mode` branch is **already merged into master** (PR #18, 2025-11-06). It is NOT an in-progress refactor that abstracts the delegation source. Master IS the freeze-v2 implementation.
- Energy delegation runs on the **sweep** path (`transfer_trc20_from`), NOT the **merchant payout** path (`/payout/<dest>/<amount>`). The merchant payout always sends from fee_deposit (pre-funded), so it doesn't need delegation. This is a critical distinction the spike brief slightly conflated.
- The source address for delegation is the `onetime_publ_key` parameter passed into `transfer_trc20_from(onetime_acc, symbol)` — the user-wallet address.
- Energy estimation is dynamic via `tron_client.get_estimated_energy(...)` (`app/tasks.py:286-291`). The 65,000 figure from spike 001 is a real measurement for activated USDT-experienced addresses; first-ever transfers cost ~2× more. Pass the dynamic value to re:Fee, not a hardcoded constant.
- Replacing freeze-v2 with re:Fee is a **single-function** change: replace the inner `delegate_energy()` call at `app/tasks.py:354` with a re:Fee adapter. The surrounding flow (energy_needed estimation, post-transfer cleanup, fallback to burn-TRX) remains valid.
- The `undelegate_energy` celery task (`app/tasks.py:421`) becomes a no-op for re:Fee because re:Fee auto-returns energy after 1h. Just guard it with `if config.ENERGY_SOURCE == "staking"`.
- AML alternative payout path (`app/custom/aml/classes.py`) does NOT currently use energy delegation at all — it only burns TRX. If AML is in scope, re:Fee would need to be wired there separately (smaller surface, ~50 lines). Out of scope for the initial integration unless explicitly requested.
- Verdict on architecture: re:Fee fits cleanly. No need to refactor `transfer_trc20_from`'s flow control.

## Open Questions

- **Spike 003 must verify:** does re:Fee `POST /api/rent_resource/orders` return an `order_id` and a polling endpoint (`GET /api/rent_resource/orders/{id}`) with status enum `pending → delegated → completed`? Concrete latency from `pending` to `delegated` (need to budget our timeout — current freeze-v2 path takes ~3-15s on-chain wait).
- **Idempotency:** if our sidecar crashes mid-flow after re:Fee accepts the order but before we broadcast the TRC-20 transfer, do we lose the energy (paid for, never used)? re:Fee may need an `external_id` or order reuse mechanism. Check OpenAPI for idempotency-key support.
- **Energy already on address:** `transfer_trc20_from` at L297-303 skips delegation if onetime already has `EnergyLimit ≥ energy_needed`. Same skip should apply for re:Fee — but if a previous re:Fee rental is still active (within 1h window), can we detect that on-chain via `get_account_resource` to avoid double-paying? Yes — `get_delegated_resource_account_index_v2` at L311 already shows existing delegations including from re:Fee's pool.
- **Bandwidth:** re:Fee tariffs include bandwidth (1000 sun for 1h), but spike 001 found bandwidth purchase rarely needed (free 1500/day covers 4 transfers). Decision: skip bandwidth purchase from re:Fee, rely on free + existing burn fallback. Verify in spike 004.
- **Multi-token contracts:** the recon focused on USDT (and USDC, by symmetry). Same flow applies to any TRC-20 added to `config.TOKENS` — re:Fee adapter is symbol-agnostic. No special-casing needed.
- **Environment variable migration:** keeping `ENERGY_DELEGATION_MODE=true` AND `ENERGY_SOURCE=refee` simultaneously is ambiguous. Recommendation: when `ENERGY_SOURCE=refee` is set, log a warning and ignore `ENERGY_DELEGATION_MODE` (re:Fee path supersedes). Or: deprecate `ENERGY_DELEGATION_MODE` in favor of `ENERGY_SOURCE in {"staking", "refee", "burn"}`.
- **Forbidden-files check:** `.env`, `*.pem`, `*.key`, `secrets.*`, `credentials.*` — none found in the repo (existence check via `find`, not read). The `app/wallet_encryption.py` module pulls the encryption password from shkeeper main via HTTP at runtime (`http://{SHKEEPER_HOST}/api/v1/{symbol}/decrypt`), so secrets are not on disk.

## Verdict

**VALIDATED.**

Integration point identified with file-and-line precision: replace `delegate_energy(sun_needed)` at `/Users/test/PycharmProjects/tron-shkeeper/app/tasks.py:354` with a re:Fee adapter call. New abstraction layer (`app/energy_provider.py`) recommended over inline if/else for readability. New env vars enumerated. Wait-for-`delegated` semantics defined (poll re:Fee + cross-check `get_account_resource`, 60s timeout, fallback to existing burn-TRX path). The `resource-delegation-mode` branch is already merged — no parallel-development risk. Spike 003 (`refee-rent-order-lifecycle`) is the natural next step to confirm the re:Fee polling contract and measure delegation latency.
