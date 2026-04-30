# Spike Manifest

## Idea

Интеграция SHKeeper с внешним сервисом аренды энергии re:Fee
(`https://github.com/.../openapi-refeebot.json`) для делегирования TRON energy
на user-кошельки перед sweep USDT-TRC20 на hot wallet мерчанта. Цель —
заменить burn TRX (≈30 TRX за USDT-transfer) на покупку energy у re:Fee,
снизив операционные расходы на TRC20-sweep.

## Requirements

Зафиксировано из пользовательского описания на 2026-04-30. Эти решения
non-negotiable для real build, спайки не должны им противоречить.

- **Одна re:Fee-подписка на одного user** (не одна-на-каждый-sweep). User-wallets живут
  долго, на них могут приходить разные TRC20-транзакции — подписочная модель,
  а не транзакционная rent.
- **Разовое создание ордера с большой суммой** — оплата вперёд, не каждый раз перед sweep.
- **`external_id` в re:Fee = SHKeeper `user_id`** для bidirectional mapping без отдельной таблицы.
- **Триггер делегирования — sweep**, а не incoming USDT (то есть когда мы готовы делать transfer
  на hot wallet, не когда пользователь нам платит).
- **Цель — экономия**, поэтому fallback на burn TRX должен оставаться для случаев, когда re:Fee
  недоступен / без баланса / ордер истёк.

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
| 002 | refee-mode-comparison | comparison | Given the "1 user = 1 wallet, long-lived subscription" pattern, when we model `rent_resource` vs `auto_charging` vs `always_charged` over a realistic 30-day usage profile (N transfers/day), then one mode wins on cost+ops simplicity | PENDING | tron, refee, architecture |
| 003 | refee-external-id-mapping | standard | Given re:Fee API has no `external_id` field in OpenAPI, when we test `additionalProperties: true` on order endpoints + dashboard search by address, then we know whether to use raw `address`-as-key or store `(user_id, refee_order_id)` mapping in our DB | PENDING | tron, refee, data-model |
| 004 | refee-sweep-e2e | standard | Given a user-wallet on TRX testnet/mainnet with USDT and zero TRX, when we call re:Fee delegate → wait for `delegated` → trigger TRC20 transfer to hot wallet, then transfer succeeds with **zero TRX burned** and user-wallet stays usable | PENDING | tron, refee, integration, sweep |
