# Break-Even: re:Fee modes vs burn-TRX

**Source data:** spike 001 README, live pricing 2026-04-30.
**Assumption:** USDT-TRC20 transfer cost = 65,000 energy (from `/api/functions/cost/{addr}` for activated address with prior USDT history). Burn rate = 210 sun/energy.

## Per-transfer baseline

| What | Cost per single transfer (TRX) |
|---|---|
| Burn-TRX (no energy) | 65,000 × 210 / 1e6 = **13.65** |
| `rent_resource` 1h | 65,000 × 37 / 1e6 = **2.41** |
| `rent_resource` 1d | 65,000 × 170 / 1e6 = **11.05** |
| `rent_resource` 3d | 65,000 × 360.12 / 1e6 = **23.41** ⚠ pricier than burn |
| `rent_resource` 7d | 65,000 × 813.12 / 1e6 = **52.85** ⚠ |
| `rent_resource` 14d | 65,000 × 1579.62 / 1e6 = **102.68** ⚠ |
| `auto_charging` 1h refill | 65,000 × 61.3 / 1e6 = **3.98** |
| `auto_charging` 3d base | 65,000 × 330 / 1e6 = **21.45** (per 3-day window, amortizes if reused) |

**Insight:** Among `rent_resource` options, **only 1h is competitive** for a single transfer. Longer rentals are economical *only* if the same wallet performs many transfers within the rental window — not the SHKeeper sweep pattern.

## Daily cost vs transfers-per-day per user-wallet

| Transfers/day | Burn-TRX | rent_resource 1h × N | auto_charging refills × N | always_charged (flat) | Cheapest |
|---|---|---|---|---|---|
| 0.5  | 6.83  | 1.20  | 1.99  | 8.00 | **rent 1h** |
| 1    | 13.65 | 2.41  | 3.98  | 8.00 | **rent 1h** |
| 2    | 27.30 | 4.81  | 7.96  | 8.00 | **rent 1h** |
| 3    | 40.95 | 7.22  | 11.94 | 8.00 | **rent 1h** |
| **3.3** | **45.05** | **7.95** | **13.13** | **8.00** | **crossover** |
| 4    | 54.60 | 9.62  | 15.92 | 8.00 | **always_charged** |
| 5    | 68.25 | 12.03 | 19.90 | 8.00 | **always_charged** |
| 10   | 136.50 | 24.05 | 39.80 | 8.00 | **always_charged** |
| 20   | 273.00 | 48.10 | 79.60 | 8.00 | **always_charged** |

## Monthly cost per user-wallet by activity profile

Realistic merchant-payment profiles in SHKeeper (per user-wallet):

| Profile | Transfers/month | Burn-TRX | rent_resource 1h | always_charged |
|---|---|---|---|---|
| Cold (rare receiver) | 1 | 13.65 | 2.41 | 240 ⚠ |
| Light (typical merchant user) | 4 | 54.60 | 9.64 | 240 ⚠ |
| Active | 30 (1/day) | 409.50 | 72.30 | 240 |
| Heavy | 150 (5/day) | 2,047.50 | 360.45 | 240 |
| Power-user | 600 (20/day) | 8,190 | 1,443 | 240 |

**The 240 TRX/month for `always_charged`** is the daily rate × 30 days, paid regardless of usage. For a user-wallet that handles only 1-4 transfers/month — the typical merchant-payment receiver — this is **~50-100× more expensive than per-transfer rent_resource**.

`always_charged` only becomes economical at **≥30 transfers/month** per user-wallet (≈1/day), and dominantly cheaper at ≥150 transfers/month.

## Sweet spots

| User-wallet activity profile | Recommended mode |
|---|---|
| Cold + Light (most merchant users) | `rent_resource` 1h on demand |
| Active (≈1 transfer/day) | tied — `rent_resource` slightly better, `always_charged` operationally simpler |
| Heavy (≥3 transfers/day) | `always_charged` |
| Power-user (very active hot wallet) | `always_charged` (or pre-buy bandwidth too) |

**Architectural implication:** A single global mode decision is wrong. Either pick `rent_resource` 1h as default + a tiny override for known-hot wallets, or build a tier auto-detector that flips a wallet to `always_charged` once its rolling-7-day transfer rate crosses ~3 transfers/day.

## Bandwidth note

USDT-TRC20 transfer ≈ 345 bytes. TRON gives every active address 1,500 bandwidth/day for free (no freeze required). That covers ~4 transfers/day per address before burn kicks in (~280 sun/byte). For SHKeeper's per-user-wallet pattern (≤4 transfers/day), bandwidth purchases are usually **not needed** — only energy is.

## Caveats

- Numbers assume cost = 65,000 energy. First-ever USDT transfer from a brand-new address costs ~130,000 energy due to storage slot init — doubles the per-transfer cost in all modes for that single transaction.
- `always_charged` description says "energy is not recharged" — translation ambiguous; assumed to mean that recharges within the daily rate are unlimited. Spike 003 should verify by creating an order and observing behavior over 24h.
- TRX price floats; burn-TRX cost in $ is not fixed. All numbers above are in TRX, not fiat — fiat impact depends on TRX price at sweep time.
- re:Fee fees may have hidden floors/minimums not visible from `tariffs` endpoint. Spike 003 (placing real orders) will surface these.
