# Spike Manifest

## Idea

Интеграция SHKeeper с внешним сервисом аренды энергии re:Fee для делегирования
TRON energy на user-кошельки перед sweep USDT-TRC20 на hot wallet мерчанта.
Use case — режим **Static Address Mode** (`README.md#static-address-mode-advanced`):
один постоянный TRON-адрес на user через reusable invoice с `external_id=user_id`.
На него приходит USDT, периодически SHKeeper sweep'ит на hot wallet — и в этот
момент нужна энергия. Сейчас её даёт нативный freeze v2 (`ENERGY_DELEGATION_MODE`
в `tron-shkeeper` sidecar), хотим заменить на покупку у re:Fee.

## Requirements (revised 2026-04-30)

После уточнения с пользователем:

- **Режим re:Fee: `rent_resource` 1h на каждый sweep** (locked, option A).
  Не `always_charged`, не `auto_charging`. Цитата пользователя:
  «нет, никакого always charged. Нужно по api вызывать эндпоинт в момент
  автоматического sweep с пользовательского кошелька на наш основной».
- **Триггер**: момент инициации sweep в sidecar `tron-shkeeper` (когда он готов
  отправить USDT-TRC20 transfer с user-address на hot wallet).
- **Fallback на burn TRX** должен сохраниться (mirror existing
  `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT`) — на случай если re:Fee
  недоступен, нет баланса, или order failed.
- **Integration target — `vsys-host/tron-shkeeper` sidecar**, НЕ этот репо.
  Этот репо (`shkeeper.io`) только проксирует HTTP-вызовы к sidecar через
  `tron_token.py:mkpayout`. Реальная sweep/delegate-логика — в sidecar.
- **Не путать**: «разовое создание ордера с большой суммой» в моих ранних
  заметках = SHKeeper Static Address Mode (большой reusable invoice с
  `external_id=user_id`), НЕ re:Fee subscription. Spike 001 misread исправлен.

## Spike 001 — VALIDATED 2026-04-30

См. `001-refee-auth-and-economics/`. Результат: live тарифы re:Fee получены,
break-even рассчитан. Для типичного merchant-payment профиля (1-4 USDT/мес/wallet)
`rent_resource` 1h в ~50× дешевле любой подписочной модели — экономика
подтверждает выбранный пользователем режим.

Headline numbers:
- USDT-TRC20 transfer = 65,000 energy = 13.65 TRX burn baseline
- `rent_resource` 1h = 2.41 TRX/transfer (saving ~83% vs burn)
- re:Fee balance аккаунта = 0 sun → spike 003+004 заблокированы до пополнения

## Open architectural questions (resolved by spikes)

- ~~Какой re:Fee-режим лучше~~ — RESOLVED: `rent_resource` 1h. Spike 002 переформулирован.

## Spike 002 — key findings (2026-04-30)

- Integration target is the **sweep** flow, NOT the merchant payout flow. Both confusingly called "payout" in the codebase.
- **Single insertion point:** `tron-shkeeper/app/tasks.py:354` — replace inner `delegate_energy(sun_needed)` with `RefeeEnergyProvider.acquire(receiver=onetime_publ_key, energy=energy_needed)`.
- Sweep trigger: periodic celery task `scan_accounts` every `BALANCES_RESCAN_PERIOD` (default 3600s) calls `transfer_trc20_from(onetime, symbol)` for each onetime address whose token balance ≥ `min_transfer_threshold` (default $5).
- Recommended abstraction: new module `app/energy_provider.py` with `EnergyProvider.acquire(receiver, energy) -> bool`, two implementations (`StakingEnergyProvider` extracted from current logic, `RefeeEnergyProvider` new). Dispatch via new env var `ENERGY_SOURCE in {"staking","refee"}`.
- Energy estimation is dynamic — `tron_client.get_estimated_energy(...)` returns the actual on-chain figure; pass that to re:Fee, don't hardcode 65k.
- Post-transfer `undelegate_energy` task at `tasks.py:421` becomes a no-op for re:Fee (auto-returns after 1h) — just guard with `if ENERGY_SOURCE == "staking"`.
- The `resource-delegation-mode` branch is **already merged** (PR #18, 2025-11-06) — no parallel-development risk.
- shkeeper.io main repo needs **zero** code changes for re:Fee — purely a sidecar concern.
- Existing `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT` flag can be reused as fallback semantics for both providers.

## Spike 001 — key findings (2026-04-30)

- API base URL confirmed: `https://api.refee.bot/v2`
- USDT-TRC20 transfer cost: ~65,000 energy for activated address with prior USDT history
- Burn-TRX baseline: 13.65 TRX per transfer
- Cheapest options by transfer rate:
  - **<3.3 transfers/day:** `rent_resource` 1h at 2.41 TRX/transfer
  - **>3.3 transfers/day:** `always_charged` flat 8 TRX/day
- **CRITICAL:** User's stated "one big subscription per user" intent costs ~50× more than per-sweep rent for typical merchant traffic (1-4 transfers/month per wallet). See `001-refee-auth-and-economics/break_even.md`. Architectural decision needs revisit before spike 002.

## Open architectural questions (resolved by spikes)

- Какой re:Fee-режим лучше под "одна подписка/user, длительная" — `rent_resource` (разовая аренда),
  `auto_charging` (auto-refill при min) или `always_charged` (постоянное поддержание)? — Spike 002 (now blocked on revised requirements)
- Поддерживает ли re:Fee `external_id` в API (в OpenAPI его нет)? Если нет — как делать reverse lookup? — Spike 003
- Можно ли реально снизить burn TRX до ~0 через delegated energy для TRC20-transfer
  и какой минимум energy надо? — Spike 001 (cost) + 004 (e2e)
- Как интегрировать триггер в существующий `shkeeper/modules/cryptos/trx.py` /
  `shkeeper/modules/classes/tron_token.py` без переписывания sweep-flow? — Spike 004

## Spikes

| # | Name | Type | Validates | Verdict | Tags |
|---|------|------|-----------|---------|------|
| 001 | refee-auth-and-economics | standard | Given API key + a TRX address, when we hit `/api/users/me` and `/api/functions/cost/{address}` and `/api/{rent,auto_charging,always_charged}/tariffs`, then we get balance + per-mode prices and can compute break-even vs burn-TRX | ✓ VALIDATED | tron, refee, economics |
| ~~002~~ | ~~refee-mode-comparison~~ | ~~comparison~~ | Mode decided up-front (`rent_resource` 1h) — comparison spike skipped | DROPPED | — |
| 002 | tron-shkeeper-sidecar-recon | standard | Given the existing `vsys-host/tron-shkeeper` sidecar implements `ENERGY_DELEGATION_MODE` via freeze v2, when we read its sweep/payout/delegate code paths, then we know exactly where to inject a re:Fee call (which function, which env vars, which return contract) and whether `aml-shkeeper` already shows a reusable re:Fee client pattern | ✓ VALIDATED | tron, sidecar, recon |
| 003 | refee-rent-order-lifecycle | standard | Given a topped-up re:Fee account, when we POST `/api/rent_resource/orders` with `resource=energy, duration_label=1h, amount=65000` for a real TRX-address, then we observe order status transitions (`pending → delegated → completed`), measure delegation latency (seconds), and confirm energy actually arrives on-chain | PENDING | tron, refee, integration |
| 004 | refee-sweep-e2e | standard | Given a static-address-mode user-wallet with USDT and 0 TRX, when sidecar (or our prototype) calls re:Fee → waits delegated → triggers USDT-TRC20 transfer to hot wallet, then transfer succeeds with **zero TRX burned** and energy returns to re:Fee after 1h | PENDING | tron, refee, sweep, e2e |
