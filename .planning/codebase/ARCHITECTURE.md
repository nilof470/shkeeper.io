<!-- refreshed: 2026-04-30 -->
# Architecture

**Analysis Date:** 2026-04-30

## System Overview

SHKeeper is a self-hosted crypto-payment processor: a Flask monolith that exposes a merchant-facing HTTP API + admin web UI, persists invoices/transactions/payouts to SQLite via SQLAlchemy, and talks to a fleet of per-coin "shkeeper" sidecar nodes (e.g. `bitcoin-shkeeper`, `ethereum-shkeeper`, `tron`, etc.) over HTTP/JSON-RPC. APScheduler runs background polling jobs for confirmations, autopayouts, and webhook retry.

```
                   ┌────────────────────────────────────────────────────────────┐
                   │                       External actors                      │
                   │  Merchant backend    │  Admin browser    │  Coin sidecars  │
                   └──┬───────────────────┴────┬──────────────┴──────┬──────────┘
                      │ HTTP (X-Shkeeper-Api-  │ Cookie session +   │ wallet/payout
                      │  Key)                  │  TOTP              │ notify (Backend
                      ▼                        ▼                    │  Key)
              ┌──────────────────────┐ ┌────────────────────┐       │
              │  HTTP / Blueprints   │ │   HTML UI Render   │       │
              │  (Flask)             │ │  (Jinja2 j2)       │       │
              │ shkeeper/api_v1.py   │ │ shkeeper/wallet.py │       │
              │ shkeeper/auth.py     │ │ templates/*.j2     │       │
              │ shkeeper/callback.py │ └─────────┬──────────┘       │
              └──────────┬───────────┘           │                  │
                         │                       │                  │
                         ▼                       ▼                  │
              ┌──────────────────────────────────────────────┐      │
              │            Service / Business layer          │      │
              │ services/payout_service.py                   │      │
              │ services/balance_service.py                  │      │
              │ services/crypto_cache.py                     │      │
              │ models.py (Invoice.add, Transaction.add,     │      │
              │            Wallet.do_payout, ExchangeRate)   │      │
              └──────┬─────────────────────────────────┬─────┘      │
                     │                                 │            │
                     ▼                                 ▼            │
              ┌──────────────────┐   ┌────────────────────────────┐ │
              │ Crypto driver    │◄──┤ Background scheduler       │ │
              │ plugin registry  │   │ shkeeper/tasks.py          │ │
              │ modules/cryptos/ │   │ (APScheduler interval jobs)│ │
              │ modules/classes/ │   └────────────────────────────┘ │
              │ Crypto.instances │                                  │
              └──────┬───────────┘                                  │
                     │ requests.post(...)                           │
                     ▼                                              │
              ┌────────────────────────────────────┐                │
              │ Coin sidecar HTTP nodes            │◄───────────────┘
              │ (BTC/LTC/DOGE/ETH/TRX/SOL/TON/...) │
              │ + monero-wallet-rpc, lnd, etc.     │
              └────────────────────────────────────┘

              ┌────────────────────────┐    ┌─────────────────────────┐
              │ SQLite (instance/      │    │ Rate sources            │
              │   shkeeper.sqlite)     │    │ modules/rates/{binance, │
              │ models.py + Flask-     │    │  coinbase, kraken,      │
              │  SQLAlchemy + Alembic  │    │  kucoin, manual}.py     │
              └────────────────────────┘    └─────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Flask app factory | App init, config, blueprint registration, scheduler bootstrap, crypto driver registration | `shkeeper/__init__.py` |
| CLI / WSGI entry | `flask` CLI + `gunicorn "shkeeper:create_app()"` callable; `Flask-Migrate` wiring | `manage.py`, `Dockerfile` |
| Public merchant API | `/api/v1/*` invoice creation, balance, payout, multipayout, transactions, addresses | `shkeeper/api_v1.py` |
| Admin web UI | HTML pages (wallets, transactions, rates, payouts, settings, TRON staking) and metrics endpoint | `shkeeper/wallet.py`, `shkeeper/templates/wallet/*.j2` |
| Auth / sessions / 2FA | Login, session loading, TOTP, decorators (`login_required`, `api_key_required`, `basic_auth_optional`, `metrics_basic_auth`) | `shkeeper/auth.py` |
| Wallet/backend ingress | Sidecar-to-shkeeper webhooks: `/walletnotify/<crypto>/<txid>`, `/payoutnotify/<crypto>`, `/decrypt`; merchant outbound notifications | `shkeeper/api_v1.py`, `shkeeper/callback.py` |
| Background jobs | Periodic confirmation polling, autopayout, payout polling, webhook retries, on-startup wallet creation | `shkeeper/tasks.py` |
| Persistence models | SQLAlchemy entities: `User`, `Wallet`, `Invoice`, `InvoiceAddress`, `Transaction`, `UnconfirmedTransaction`, `Payout`, `PayoutTx`, `PayoutDestination`, `ExchangeRate`, `Notification`, `Setting`, `BitcoinLightningInvoice` | `shkeeper/models.py` |
| Cryptocurrency abstraction | Abstract `Crypto` base + intermediate base classes (`Btc`, `Ethereum`, `TronToken`, `BitcoinLikeCrypto`, `Solana`, `Ton`, etc.) and one concrete subclass per coin/token | `shkeeper/modules/classes/`, `shkeeper/modules/cryptos/` |
| Rate provider abstraction | `RateSource` ABC plus per-source plugins (Binance, Coinbase, Kraken, KuCoin, Manual); auto-registered via metaclass | `shkeeper/modules/classes/rate_source.py`, `shkeeper/modules/rates/` |
| Service layer | `PayoutService` (single/multi payout), `get_balances` (fan-out balances), `get_available_cryptos` (TTL-cached enabled-coin list), `TTLCache` | `shkeeper/services/payout_service.py`, `shkeeper/services/balance_service.py`, `shkeeper/services/crypto_cache.py`, `shkeeper/services/cache_service.py` |
| Wallet encryption | Fernet/PBKDF2 key derivation, persistent + runtime status (`pending`/`disabled`/`enabled`), unlock screen | `shkeeper/wallet_encryption.py`, `shkeeper/templates/wallet/unlock_*.j2` |
| Database migrations | Alembic environment + version scripts | `migrations/env.py`, `migrations/versions/*.py` |
| Pydantic schemas | TRON staking response shapes used by `wallet.py` | `shkeeper/schemas.py` |
| Metrics | Prometheus text format from each crypto sidecar fan-out + Flask process metrics, gated by HTTP Basic | `shkeeper/wallet.py` (`/metrics`), `shkeeper/auth.py` (`metrics_basic_auth`) |

## Pattern Overview

**Overall:** Flask blueprint-based modular monolith with a plugin-style cryptocurrency driver registry. Each coin is a Python file that subclasses a shared abstract `Crypto` (or a coin-family base such as `Btc`, `Ethereum`, `TronToken`) and is auto-discovered + auto-instantiated at import time. The Flask process owns business logic, persistence, and webhook fan-out, while actual chain interaction is delegated over HTTP/JSON-RPC to per-coin sidecar containers.

**Key Characteristics:**
- Single Flask app, single SQLite DB, four blueprints (`auth`, `wallet`, `api_v1`, `callback`).
- "Plugin" autodiscovery via `__init_subclass__` hooks — both `Crypto` (`shkeeper/modules/classes/crypto.py:17`) and `RateSource` (`shkeeper/modules/classes/rate_source.py:14`) self-register concrete subclasses on import. The `cryptos/__init__.py` and `rates/__init__.py` files dynamically import every sibling `.py` file (`shkeeper/modules/cryptos/__init__.py:7`).
- Coins toggle on/off via env vars: `default_off` and `default_on` lists in `Crypto.__init_subclass__` decide which coins activate based on `<SYMBOL>_WALLET=enabled|disabled` env flags.
- Background work runs on Flask-APScheduler in the same process (gunicorn `--threads 16 --worker-class gthread`, single worker — `Dockerfile`).
- Per-request and per-task DB sessions via Flask-SQLAlchemy global `db` (`shkeeper/__init__.py:40`).
- Webhook-style integration in both directions: sidecars POST `/walletnotify/<crypto>/<txid>` to shkeeper; shkeeper POSTs to merchant `callback_url` with `X-Shkeeper-Api-Key`.

## Layers

### 1. HTTP / Blueprint layer
- **Purpose:** Receive HTTP from merchants, admin browser, and coin sidecars; render Jinja templates or return JSON.
- **Location:** `shkeeper/api_v1.py` (`bp = Blueprint("api_v1", url_prefix="/api/v1/")`), `shkeeper/wallet.py` (`bp = Blueprint("wallet")`), `shkeeper/auth.py` (`bp = Blueprint("auth", url_prefix="/")`), `shkeeper/callback.py` (`bp = Blueprint("callback")` — no routes, only `bp.cli.command()` hooks).
- **Contains:** `~46` routes in `wallet.py`, `~36` routes in `api_v1.py`, login/2FA/setup-password routes in `auth.py`. Decorators `@login_required`, `@api_key_required`, `@basic_auth_optional`, `@metrics_basic_auth`, `@handle_request_error`.
- **Depends on:** Service layer, models, `Crypto.instances`, `wallet_encryption`.
- **Used by:** External callers (browser, merchant servers, coin sidecars).

### 2. Service / Business layer
- **Purpose:** Cross-cutting coordination logic that does not belong on a single model.
- **Location:** `shkeeper/services/payout_service.py`, `shkeeper/services/balance_service.py`, `shkeeper/services/crypto_cache.py`, `shkeeper/services/cache_service.py`.
- **Contains:** `PayoutService.single_payout` / `multiple_payout` (validates external_id uniqueness, callback URL, dispatches to `crypto.mkpayout`/`crypto.multipayout`, persists `Payout` rows). `get_balances()` fans out per-crypto balance/rate calls through a `ThreadPoolExecutor`. `get_available_cryptos()` returns a 60s-TTL cached snapshot of enabled+synced coins. `TTLCache` is a simple in-memory dict.
- **Depends on:** `models.py`, `Crypto.instances`, `ExchangeRate`.
- **Used by:** API blueprints, background tasks.

### 3. Models / Persistence layer
- **Purpose:** Domain entities and operational helpers (Invoice creation/recalc, Transaction add/update, Payout add/update_from_task, ExchangeRate get/convert/get_fee, Notification queue).
- **Location:** `shkeeper/models.py` (single 900-line module).
- **Contains:** SQLAlchemy `db.Model` classes, `enum.Enum` types (`InvoiceStatus`, `PayoutStatus`, `PayoutPolicy`, `PayoutReservePolicy`, `PayoutTxStatus`, `FeeCalculationPolicy`), `Fiat` static helper.
- **Depends on:** `db` (Flask-SQLAlchemy), `Crypto.instances`, `RateSource.instances`, `wallet_encryption`.
- **Used by:** Everything above; mutates committed in-place via `db.session.commit()` calls inline in classmethods.

### 4. Crypto driver layer
- **Purpose:** Abstract chain interaction; one concrete instance per active coin/token.
- **Location:** `shkeeper/modules/classes/` (abstract bases) and `shkeeper/modules/cryptos/` (concrete coin shims).
- **Contains:** Required interface in `Crypto` ABC: `getname`, `gethost`, `balance`, `getstatus`, `mkaddr`, `getaddrbytx`, `dump_wallet`, `create_wallet`, `mkpayout`, `get_all_addresses` (`shkeeper/modules/classes/crypto.py:92`). Coin-family bases (`Btc`, `Ethereum`, `TronToken`, `BitcoinLikeCrypto`, `Doge`, `Ltc`, `Xrp`, `Solana`, `Ton`, `Bnb`, `Avalanche`, `Polygon`, `Arbitrum`, `Optimism`) implement those by HTTP-calling a sidecar service identified by `<COIN>_API_SERVER_HOST`/`PORT` env vars. Each concrete shim in `shkeeper/modules/cryptos/<symbol>.py` typically just sets `self.crypto` and `getname()`.
- **Depends on:** `requests`, `flask current_app`, env vars.
- **Used by:** Models, blueprints, services, tasks.

### 5. Rate provider layer
- **Purpose:** Pluggable fiat<->crypto exchange-rate sources.
- **Location:** `shkeeper/modules/classes/rate_source.py`, `shkeeper/modules/rates/`.
- **Contains:** `RateSource` ABC with `instances` registry and crypto-symbol normalization sets (`USDT_CRYPTOS`, `USDC_CRYPTOS`, `BTC_CRYPTOS`, `FIRO_CRYPTOS`, `ETH_CRYPTOS`). Concrete sources: `Binance`, `Coinbase`, `Kraken`, `Kucoin`, `Manual`.
- **Used by:** `ExchangeRate.get_rate()` in `shkeeper/models.py:261`.

### 6. Background scheduler layer
- **Purpose:** Periodic coordination of on-chain state ↔ DB ↔ merchant webhooks.
- **Location:** `shkeeper/tasks.py`, started by `scheduler.start()` in `shkeeper/__init__.py:227`.
- **Contains:** Six interval jobs:
  - `callback` (60s) — `update_confirmations()` + `send_callbacks()`.
  - `pending_payouts` (60s) — `poll_all_pending_payouts()` against sidecar task IDs.
  - `unconfirmed_payouts` (60s) — `poll_unconfirmed_payouts()` to mark IN_PROGRESS payouts SUCCESS once confirmations cross threshold.
  - `payout_callback_notifier` (60s) — exponential-backoff merchant webhook retries via `Notification` rows.
  - `payout` (60s) — autopayout per `PayoutPolicy.LIMIT` / `PayoutPolicy.SCHEDULED` rules.
  - `create_wallet` (10s, self-deletes once all coins ready) — calls `crypto.create_wallet()` until each coin sidecar has its wallet.

## Data Flow

### Primary Request Path: merchant creates invoice → user pays → confirmation

1. Merchant POST `/api/v1/<crypto>/payment_request` with `external_id`, `fiat`, `amount`, `callback_url` (`shkeeper/api_v1.py:99`). Auth via `@api_key_required` checks `X-Shkeeper-Api-Key` against `Wallet.apikey` (`shkeeper/auth.py:89`).
2. Handler resolves driver: `crypto = Crypto.instances[crypto_name]` (`shkeeper/api_v1.py:104`); aborts if disabled or sidecar lagging (`getstatus() != "Synced"` when `DISABLE_CRYPTO_WHEN_LAGS`).
3. `Invoice.add(crypto, request)` either updates existing invoice (matched by `external_id`+`callback_url`+`fiat`) or creates one; calls `crypto.mkaddr(details=...)` to generate a fresh receive address (`shkeeper/models.py:414`). Lightning case can also generate a BIP21 BTC on-chain fallback (`shkeeper/models.py:483`).
4. Response includes `wallet` (address), `amount`, `exchange_rate`, `display_name` and optional `bip21` (`Invoice.for_response`, `shkeeper/models.py:509`).
5. User pays the address. The coin sidecar detects the on-chain transaction and POSTs `/walletnotify/<crypto>/<txid>` with header `X-Shkeeper-Backend-Key` (`shkeeper/api_v1.py:467`).
6. `walletnotify` calls `crypto.getaddrbytx(txid)` to fetch (addr, amount, confirmations, category) tuples. For each `receive`:
   - 0 conf: optional `UnconfirmedTransaction.add` + `send_unconfirmed_notification` if `UNCONFIRMED_TX_NOTIFICATION` (`shkeeper/api_v1.py:507`, `shkeeper/callback.py:16`).
   - confirmed: `Transaction.add(crypto, tx)` (`shkeeper/models.py:667`) — finds invoice via `InvoiceAddress`, computes fiat, sets `need_more_confirmations` per `Wallet.confirmations`. Then `tx.invoice.update_with_tx(tx)` recomputes `balance_fiat`/`balance_crypto`, optionally rebases `amount_crypto` if `wallet.recalc` hours have passed, and bumps invoice status to `PARTIAL`/`PAID`/`OVERPAID` based on `llimit`/`ulimit` (`shkeeper/models.py:379`).
   - If enough confirmations already, `send_notification(tx)` (`shkeeper/callback.py:68`) POSTs the merchant `callback_url`; expects HTTP 202.
7. Outgoing transactions (category `send`) are recorded via `Transaction.add_outgoing` against a synthetic `Invoice(status=OUTGOING)` (`shkeeper/models.py:638`).

### Background Confirmation Flow (scheduler polls + retries)

1. Every 60s `task_callback` runs `callback.update_confirmations()` (`shkeeper/callback.py:346`) for every `Transaction(callback_confirmed=False, need_more_confirmations=True)`. It calls `crypto.get_confirmations_by_txid(txid)` and clears `need_more_confirmations` once `>= wallet.confirmations`.
2. Same task runs `callback.send_callbacks()` (`shkeeper/callback.py:149`) which:
   - sends pending unconfirmed-tx notifications;
   - for each `Transaction(callback_confirmed=False, need_more_confirmations=False)` past `NOTIFICATION_TASK_DELAY` seconds, calls `send_notification(tx)`. Outgoing invoices auto-confirm without webhook.
3. Every 60s `task_poll_all_pending_payouts` polls each in-progress `Payout` by `task_id` against `crypto.get_task(...)` and calls `Payout.update_from_task` to convert task results into `PayoutTx` rows (`shkeeper/callback.py:318`, `shkeeper/models.py:736`).
4. Every 60s `task_poll_unconfirmed_payouts` checks per-tx confirmations against `MIN_CONFIRMATION_BLOCK_FOR_PAYOUT` and on success creates a `Notification(type='Payout')` (`shkeeper/callback.py:184`).
5. Every 60s `task_send_payout_callback_notifier` (when `ENABLE_PAYOUT_CALLBACK`) sends payout webhooks with quadratic backoff `(retries+1)**2` seconds (`shkeeper/callback.py:236`), capped at `REQUESTS_NOTIFICATION_RETRIES`.
6. Every 60s `task_payout` evaluates each `Wallet.ppolicy`: `LIMIT` triggers `Wallet.do_payout()` once balance ≥ `pcond`; `SCHEDULED` triggers it every `pcond` minutes (`shkeeper/tasks.py:32`, `shkeeper/models.py:182`).

**State Management:**
- DB: SQLite at `instance/shkeeper.sqlite`, accessed via Flask-SQLAlchemy global `db` (`shkeeper/__init__.py:40`).
- HTTP sessions: Flask-Session filesystem backend at `instance/flask_session/`, wiped on startup unless `DEV_MODE` (`shkeeper/__init__.py:104`).
- Process-local singletons:
  - `Crypto.instances` — dict[str, Crypto] populated by `__init_subclass__`.
  - `RateSource.instances` — dict populated identically.
  - `wallet_encryption._key` / `wallet_encryption._runtime_status` — class-level state for the encryption unlock flow.
  - `cache` — in-memory `TTLCache` for crypto availability snapshot.
  - `scheduler` — APScheduler singleton, init in `__init__.py:27`.
- Module-level event `shkeeper_initialized = threading.Event()` (`shkeeper/events.py:4`) is `set()` at end of `create_app()`.

## Key Abstractions

**Cryptocurrency driver (`Crypto` ABC):**
- Purpose: a uniform interface (`balance`, `mkaddr`, `mkpayout`, `getaddrbytx`, `getstatus`, `dump_wallet`, `create_wallet`, `get_all_addresses`) implemented per coin so Flask layers can stay coin-agnostic.
- Examples: `shkeeper/modules/classes/crypto.py` (ABC), `shkeeper/modules/classes/btc.py` (Bitcoin-style sidecar), `shkeeper/modules/classes/ethereum.py` (EVM sidecar), `shkeeper/modules/classes/tron_token.py` (TRX/TRC20), `shkeeper/modules/cryptos/btc.py` / `eth.py` / `usdt.py` (concrete shims that set `self.crypto` and `getname()`).
- Pattern: Abstract Base Class + auto-registering subclass metaclass hook (`__init_subclass__` populates `Crypto.instances`); coin-family base classes provide HTTP/JSON-RPC sidecar adapters; concrete classes only override identity.

**Rate source (`RateSource` ABC):**
- Purpose: pluggable price oracle.
- Examples: `shkeeper/modules/rates/binance.py`, `coinbase.py`, `kraken.py`, `kucoin.py`, `manual.py`.
- Pattern: Same auto-register-on-subclass pattern as `Crypto`. Stable-coin / wrapped-coin normalization is centralized in `RateSource` constants (`shkeeper/modules/classes/rate_source.py:7`).

**Invoice / Transaction state machine (`InvoiceStatus`):**
- Purpose: model the lifecycle UNPAID → PARTIAL → PAID → OVERPAID, plus CANCELLED / REFUNDED / OUTGOING.
- Logic: `Invoice.update_with_tx` recomputes status based on `balance_fiat` vs `amount_fiat * (llimit|ulimit)/100` thresholds (`shkeeper/models.py:399`).

**Payout pipeline (`Payout`, `PayoutTx`, `Notification`):**
- Purpose: track outbound transactions across sidecar task lifecycle (IN_PROGRESS / SUCCESS / FAIL) with optional per-payout merchant webhook.
- Pattern: insert-on-create, update-by-task-id polling (`Payout.update_from_task`), retry queue via `Notification(type='Payout')` with quadratic backoff.

**Wallet encryption (`wallet_encryption`):**
- Purpose: optionally derive a Fernet key from an admin passphrase and gate access to encrypted wallet keys.
- Pattern: dual status (persistent in DB `Setting` row + runtime in class attribute), blocking `wait_for_key()` loop, sidecars fetch the runtime key via `/<crypto>/decrypt` with `X-Shkeeper-Backend-Key` (`shkeeper/api_v1.py:549`).

## Entry Points

**HTTP server:**
- Location: `manage.py` (`app = create_app()` + `Flask-Migrate`), `shkeeper/__init__.py:55` (`create_app`), `Dockerfile` (`gunicorn ... "shkeeper:create_app()"`).
- Triggers: `gunicorn --workers 1 --threads 16 --worker-class gthread -b 0.0.0.0:5000`, or `flask --app manage.py run` for dev.
- Responsibilities: build Flask app, wire `db`/`migrate`/`scheduler`/`Session`, run `db.create_all()` (or `flask_migrate.upgrade()`), seed default admin user, register `Crypto`/`ExchangeRate` for each plugin, register four blueprints, register 404/500 error handlers, set the initialization event.

**CLI:**
- Location: `manage.py` (uses `flask.cli.FlaskGroup`), `shkeeper/callback.py:362` (`bp.cli.command()` for `flask callback list|send|update|add`), Flask-Migrate's `flask db migrate|upgrade|stamp`.
- Triggers: `python manage.py <cmd>` or `flask --app manage.py <cmd>`.
- Standalone: `contrib/shkeeper-change-password.py` directly edits the SQLite DB on disk.

**Scheduler:**
- Location: `shkeeper/tasks.py`, started in `shkeeper/__init__.py:227` after `db.create_all()`.
- Triggers: in-process Flask-APScheduler interval timers (no external broker).

**Webhook (sidecar inbound):**
- Location: `POST /walletnotify/<crypto>/<txid>`, `POST /payoutnotify/<crypto>`, `GET /<crypto>/decrypt` — all in `shkeeper/api_v1.py` (lines 467, 443, 549).
- Auth: `X-Shkeeper-Backend-Key` header compared to `SHKEEPER_BTC_BACKEND_KEY` env var.

## Architectural Constraints

- **Threading:** Single gunicorn worker with 16 gthread workers + APScheduler thread pool inside the same process. Service layer also uses `concurrent.futures.ThreadPoolExecutor` for fan-out RPC calls (`shkeeper/services/balance_service.py:45`, `shkeeper/services/crypto_cache.py:15`, `shkeeper/wallet.py:582`). All threads share `Crypto.instances`, `wallet_encryption._key`, and the SQLAlchemy session — task code wraps every job body in `with scheduler.app.app_context()`.
- **Global state:** `db` (Flask-SQLAlchemy), `migrate` (Flask-Migrate), `scheduler` (Flask-APScheduler), `Crypto.instances`, `RateSource.instances`, `wallet_encryption` class state, `cache` (`shkeeper/services/cache_service.py:18`), and `shkeeper_initialized` `threading.Event` (`shkeeper/events.py:4`). All declared at module scope in `shkeeper/__init__.py`.
- **Environment-driven activation:** Coins are gated by `<SYMBOL>_WALLET=enabled|disabled` env vars (`shkeeper/modules/classes/crypto.py:74`). Sidecar host/port from `<COIN>_API_SERVER_HOST` / `<COIN>_SERVER_PORT`. RPC creds from `<COIN>_USERNAME` / `<COIN>_PASSWORD`. Backend webhook auth from `SHKEEPER_BTC_BACKEND_KEY`. Metrics auth from `METRICS_USERNAME` / `METRICS_PASSWORD`.
- **Circular-import discipline:** `shkeeper/__init__.py` imports `tasks`, `auth`, `wallet`, `api_v1`, `callback`, and `models` lazily inside `create_app()` to avoid bootstrap cycles; submodules use `from shkeeper import db, requests` and `flask import current_app as app` patterns.
- **Single-DB constraint:** SQLite (`SQLALCHEMY_DATABASE_URI = sqlite:///instance/shkeeper.sqlite`) — concurrent writes from request threads + scheduler share one file; long-running tasks therefore must keep transactions short.

## Anti-Patterns

- **Mass `from shkeeper.models import *`:** Both `shkeeper/api_v1.py:30` and `shkeeper/callback.py:8` and `shkeeper/tasks.py:7` use star-imports of models, which obscures dependencies and makes static analysis harder.
- **Persistence logic embedded inside model classmethods with implicit `db.session.commit()`:** e.g. `Invoice.add` (`shkeeper/models.py:414`), `Transaction.add` (`shkeeper/models.py:667`), `Payout.add` (`shkeeper/models.py:764`) all both build and persist objects, mixing construction with transaction control. This makes it hard to compose them in a single atomic unit-of-work.
- **`Crypto` plugin module also imports its concrete coin shims as side-effect by listing every `.py`:** `shkeeper/modules/cryptos/__init__.py:7` and `shkeeper/modules/rates/__init__.py:7` use `os.listdir` + `__import__`, so adding a stray `.py` to the directory auto-instantiates a class. Helpful for plugins, but not declarative.
- **Per-route try/except that swallows and stringifies tracebacks into JSON:** many handlers in `shkeeper/api_v1.py` (e.g. `payment_request` at `:101`, `add_transaction` at `:264`, `walletnotify` at `:467`) catch `Exception`, return `{"status": "error", "traceback": ...}` rather than relying on the `@handle_request_error` wrapper or Flask error handlers.

## Error Handling

**Strategy:** A mix of (a) per-route try/except wrapping the entire handler body, (b) a `@handle_request_error` decorator (`shkeeper/api_v1.py:55`) used by payout endpoints, and (c) global `app.register_error_handler(500, ...)` / `(404, ...)` rendering Jinja error pages (`shkeeper/__init__.py:241`). The custom `NotRelatedToAnyInvoice` exception (`shkeeper/exceptions.py:1`) is caught explicitly in `walletnotify` to silently 200 unrelated transactions.

**Patterns:**
- Sidecar HTTP failures from `requests.post(...)` are typically caught and converted to a logged warning + a sentinel (e.g. `getstatus()` returns `"Offline"` on any exception, `balance()` returns `False`).
- DB integrity errors on duplicate-tx insert are caught + rolled back: `sqlalchemy.exc.IntegrityError` in `walletnotify` (`shkeeper/api_v1.py:529`).
- Webhook delivery failures increment `Notification.retries` rather than raise (quadratic backoff, capped retries).

## Cross-Cutting Concerns

**Logging:** `flask.logging.default_handler` reformatted in `shkeeper/__init__.py:11` to `LEVEL file:line func(): msg`. APScheduler logger downgraded to INFO unless `DEV_MODE`. `app.logger.propagate = False` to avoid double-logging via root.

**Validation:** Lightweight — most input is `request.get_json(force=True)` then dict access. Pydantic is used only for TRON staking shapes (`shkeeper/schemas.py`). Callback URL validation in `PayoutService.validate_callback_url` (`shkeeper/services/payout_service.py:26`).

**Authentication:** Three orthogonal mechanisms:
- Cookie session + optional TOTP for human admins (`@login_required`, `shkeeper/auth.py:71`; TOTP fields on `User`, `shkeeper/models.py:25`).
- API key in `X-Shkeeper-Api-Key` for merchants (`@api_key_required`, `shkeeper/auth.py:89`), validated against `Wallet.apikey`.
- Backend key in `X-Shkeeper-Backend-Key` for coin sidecars (manually checked inline in each `/walletnotify`/`/payoutnotify`/`/decrypt` route).
- HTTP Basic for `/metrics` (`metrics_basic_auth`, `shkeeper/auth.py:33`) and as a fallback override on payouts (`@basic_auth_optional`).

**Observability:** Prometheus `/metrics` endpoint (`shkeeper/wallet.py:567`) fan-outs `crypto.metrics()` (e.g. `Btc.metrics`, `shkeeper/modules/classes/btc.py:141`) over each coin sidecar's `/metrics`, then appends shkeeper's own Flask process metrics. `_filter_metrics` strips `last_release_info` / `fullnode_version_info` families.

**Persistence config:** Alembic naming convention defined inline (`shkeeper/__init__.py:32`). On first boot `flask_migrate.stamp(revision="head")`; otherwise `flask_migrate.upgrade()`.

---

*Architecture analysis: 2026-04-30*
