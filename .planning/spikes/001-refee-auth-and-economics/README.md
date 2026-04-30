---
spike: 001
name: refee-auth-and-economics
type: standard
validates: "Given API key + arbitrary TRX address, when we call /api/users/me, /api/functions/{tariffs,cost/{addr}} and the three mode-tariff endpoints, then we get balance + per-mode pricing and can compute break-even vs burn-TRX"
verdict: VALIDATED
related: []
tags: [tron, refee, economics]
---

# Spike 001: re:Fee — Auth & Economics

## What This Validates

**Given** an API key and a real activated TRX address,
**When** we hit `/api/users/me`, `/api/functions/tariffs`, `/api/functions/cost/{addr}`, and the three mode tariff endpoints (`rent_resource`, `always_charged`, `auto_charging`),
**Then** we get authenticated, see real pricing, and can compute when each mode beats burn-TRX.

## Research

API base URL: `https://api.refee.bot/v2` (note `refee.bot`, not `refeebot.com`).
Auth: `X-API-Key` header.
TRX/sun ratio: 1 TRX = 1,000,000 sun.
Burn-TRX rate: 1 energy = 210 sun (TRON network constant).

The user has no balance on re:Fee yet (`balance_sun: 0`), so any *active* spike (003/004 — creating orders) is blocked until top-up. Spike 001 is read-only and unblocked.

## How to Run

User executes the curl bundle described in the orchestration; this spike only consumes the JSON outputs. Inputs (API key, test address) are user-controlled — Claude never sees them.

## What to Expect

Two artifacts in this spike folder:
- `README.md` (this file) — verdict + investigation trail
- `break_even.md` — full economics table comparing burn-TRX vs the three re:Fee modes across realistic transfer-per-day profiles

## Investigation Trail

### Iteration 1 — pricing snapshot

Live pricing pulled on 2026-04-30:

**`/api/functions/tariffs`:**
- AML check: 3.067 TRX
- Activate address: 1.7 TRX

**`/api/rent_resource/tariffs`** (sun per energy unit, returned after period):

| Period | sun/energy | Notes |
|---|---|---|
| 1h | 37 | Cheapest per unit. bandwidth_price 1000 sun. |
| 1d | 170 | bandwidth N/A |
| 3d | 360.12 | bandwidth N/A |
| 7d | 813.12 | bandwidth N/A |
| 14d | 1579.62 | bandwidth N/A |

**`/api/always_charged/tariffs`:**
- package: 131,000 energy
- price: 8,000,000 sun/day = **8 TRX/day**
- Restock when ≥5% consumed; daily rate covers unlimited restocks within the day (per OpenAPI description).

**`/api/auto_charging/tariffs`:**
- energy_package_price: 330 sun/unit (3-day base purchase)
- energy_charge_price: 61.3 sun/unit (1-hour refill)
- bandwidth_package_price: 3000 sun, charge: 1000 sun

### Iteration 2 — cost for one USDT-TRC20 transfer

`/api/functions/cost/{addr}` returned `cost: 65,000` energy for the chosen address. This is consistent with TRON's USDT TRC-20 cost when storage slot already initialized (first-ever transfer from a fresh address would be ~120-130k due to slot init).

→ At burn-TRX rate: 65,000 × 210 sun = 13,650,000 sun = **13.65 TRX per transfer** (no energy).

### Iteration 3 — break-even modeling

See `break_even.md`. Key insight: **the cheapest mode depends on transfers/day per user-wallet**, and the crossover point lands roughly at **3.3 transfers/day**:

- Below 3.3 transfers/day: `rent_resource` 1h per sweep is cheapest by a wide margin
- Above 3.3 transfers/day: `always_charged` becomes cheapest (flat 8 TRX/day, unlimited restocks)
- `auto_charging` sits between the two; never optimal in this comparison except when set up with both base + refill on a steady traffic pattern

`auto_charging` 1h refill (61.3 sun/unit) is **65% more expensive** than direct `rent_resource` 1h (37 sun/unit) for the same energy delivery. The premium pays for automation.

### Iteration 4 — the architectural surprise

The user's stated requirement on 2026-04-30 was: "разовое создание ордера с большой суммой и использования userID в externalID". This wording strongly implies a long-lived subscription mode (always_charged or large auto_charging base). However, the math says:

- Typical merchant-payment use case → user-wallet receives USDT 1-2 times/month, gets swept once each time → **0.03-0.07 transfers/day**.
- At that rate, `always_charged` costs **8 TRX/day × 30 = 240 TRX/month per user-wallet just to keep the subscription warm**, while the user actually does only 1-2 transfers worth ~4 TRX of energy.
- `rent_resource` 1h per sweep at 2.41 TRX/transfer × 2 transfers/month = **~5 TRX/month per user-wallet** — roughly **50× cheaper** than the always_charged subscription for this profile.

The user's "one big order per user" intent therefore costs ~50× more than per-sweep rent for typical merchant-payment activity. The intent only pays off if every user-wallet handles >3.3 transfers/day, which is implausible for SHKeeper's use case (each user-wallet usually receives a small number of merchant payments per period).

**This is a load-bearing architectural finding and must be resolved before spike 002.**

## Results

**Verdict: VALIDATED** — auth works, pricing pulled cleanly, cost endpoint returns sensible numbers.

**Headline numbers:**
- Burn-TRX baseline: 13.65 TRX per USDT transfer (65k energy × 210 sun/energy)
- Cheapest mode for ≤3 transfers/day: `rent_resource` 1h at 2.41 TRX/transfer (saves ~83% vs burn)
- Cheapest mode for >3.3 transfers/day: `always_charged` at flat 8 TRX/day (saves up to 94% at high volume)
- Bandwidth: TRC-20 transfer ≈ 345 bytes; default daily free allocation 1500 bandwidth/day covers ~4 transfers/day per address — bandwidth purchase rarely needed in our scenario

**Surprises (worth highlighting):**
1. `rent_resource` 1h is dramatically cheaper than `auto_charging` 1h refill (37 vs 61.3 sun/unit) — auto_charging is paying premium for automation, not for cheaper energy.
2. `rent_resource` periods longer than 1d are *more expensive* than burn-TRX per transfer when used for a single transfer — they only make sense if the user's wallet does many transfers within the rental window.
3. The user's "one big subscription per user" architectural intent is uneconomical for typical merchant-payment traffic by ~50× — needs revisit.

**Open items for spike 002:**
- Operational cost of `rent_resource` per-sweep (extra API call, wait-for-`delegated`, error handling) vs set-and-forget always_charged — does ops complexity justify a price premium?
- Hybrid model: detect activity threshold per wallet (transfers/day rolling window), switch from `rent_resource` to `always_charged` automatically? Worth modeling.
- Concrete activity profile per SHKeeper deployment — without real data we are guessing.
