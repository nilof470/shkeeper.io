# Phase 001: amlbot-deposit-approval-gate - Research

**Created:** 2026-05-03
**Status:** Complete

## Research Complete

This phase is a two-repository integration: SHKeeper must own deposit policy and callback gating, while `aml-shkeeper` must become the AMLBot adapter with idempotent check creation and provider evidence storage.

## Current SHKeeper Flow

- `shkeeper/api_v1.py:467` receives `POST /api/v1/walletnotify/<crypto>/<txid>`.
- Confirmed incoming transactions are persisted by `Transaction.add` in `shkeeper/models.py:668`.
- The invoice aggregate is updated by `tx.invoice.update_with_tx(tx)`.
- If confirmations are sufficient, `walletnotify` currently calls `send_notification(tx)` immediately at `shkeeper/api_v1.py:527`.
- The scheduler job in `shkeeper/tasks.py:9` calls `callback.update_confirmations()` and then `callback.send_callbacks()` every 60 seconds.
- `send_notification(tx)` in `shkeeper/callback.py:68` builds the existing merchant callback and marks `tx.callback_confirmed=True` only after HTTP 202.

## Current aml-shkeeper Flow

- The service exposes symbol-prefixed endpoints through `app/api/__init__.py`, currently `/<symbol>/check_tx`, `/<symbol>/get_score/<txid>`, and `/<symbol>/dump`.
- `app/api/views.py` checks only `tx_id` for duplicates and returns a hard duplicate error.
- `app/api/views.py:add_transaction_to_db` implements a min-check shortcut that stores score `0`; this conflicts with the accepted SHKeeper-owned skip policy.
- `app/models.py:Transactions` lacks `deposit_id`, `idempotency_key`, raw provider response, signals, report URL, timestamps for retry policy, and a strong uniqueness model.
- `app/tasks.py` owns AMLBot check/recheck polling through Celery and Redis.
- `app/aml_bot_api.py` currently has a partial symbol-to-asset map for BTC/LTC/DOGE, ETH family, TRX/TRC20, and SOL family.

## Implementation Approach

1. Productionize `aml-shkeeper` first with a new versioned API:
   - `POST /api/v1/checks`
   - `GET /api/v1/checks/<deposit_id>`
   - keep legacy symbol endpoints as compatibility/reference surfaces.
2. Add SHKeeper AML foundations:
   - `AmlCheck` 1:1 with `Transaction`
   - config defaults for threshold, cumulative skip, score policy, sidecar endpoint, and retry timing
   - `AML_COVERAGE` matrix for every known SHKeeper crypto symbol
   - `AmlShkeeperClient`, `AmlPolicyService`, and `AmlProcessingService`.
3. Replace direct callback emission with an AML gate:
   - confirmed deposits create/reuse an `AmlCheck`
   - final callback is delayed until `AmlCheck` is terminal
   - outgoing transactions and unconfirmed observability callbacks keep existing behavior.
4. Add tests and contract docs:
   - use stdlib `unittest` and `unittest.mock` to avoid introducing package downloads
   - include coverage tests for all `Crypto.instances` policies
   - include callback schema/static-address/idempotency tests.

## Validation Architecture

Recommended test infrastructure uses Python stdlib `unittest` for both repositories.

Quick commands:

```bash
python -m compileall shkeeper
python -m unittest discover -s tests
```

For `aml-shkeeper`:

```bash
python -m compileall app
python -m unittest discover -s tests
```

Required validation dimensions:

- Sidecar duplicate `POST /api/v1/checks` returns the same check state.
- Sidecar stores raw provider response, signals, UID, report URL, attempts, status, errors, and timestamps.
- SHKeeper coverage matrix has an explicit policy for every enabled `Crypto.instances` entry.
- De-minimis skip emits `aml.status="skipped"` and `score=null`; skipped deposits never call `aml-shkeeper`.
- Callback is not sent while AML is pending.
- Trigger transaction callback includes `deposit_id`, `idempotency_key`, `deposit_decision`, `decision_reason`, and `aml`.
- Static-address invoices can remain `PARTIAL` while trigger transaction is creditable.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Two repositories must change together | Plan sidecar contract and SHKeeper client separately, then integrate through stable JSON fixtures. |
| Existing `Transaction` model becomes overloaded | Add separate `AmlCheck` model with `transaction_id` unique. |
| Missing crypto mapping silently auto-credits | Add explicit `AML_COVERAGE` matrix and coverage test. Runtime fail-closed to `manual_review`. |
| Provider pending result blocks callbacks forever | Store `timeout_at`, `next_retry_at`, attempts, and resolve timeout to `manual_review`. |
| Callback retry sends changing payloads | Store local AML snapshot on `AmlCheck` and build callback from persisted fields. |
| Small deposit economics are abused by splitting | Apply USD 100 per-tx threshold and USD 300 rolling cumulative skip limit per `external_id + crypto + address` over 24h. |

## Output

Research should feed four implementation plans:

1. `001-01-PLAN.md` - `aml-shkeeper` production check API.
2. `001-02-PLAN.md` - SHKeeper AML model, config, coverage, and policy/client services.
3. `001-03-PLAN.md` - SHKeeper AML lifecycle integration and callback gate.
4. `001-04-PLAN.md` - tests, fixtures, and callback contract docs.

