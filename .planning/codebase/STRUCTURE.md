# Codebase Structure

**Analysis Date:** 2026-04-30

## Directory Layout

```
shkeeper.io/
├── manage.py                      # Flask CLI entry (FlaskGroup + Migrate wiring)
├── Dockerfile                     # gunicorn entry: "shkeeper:create_app()"
├── requirements.txt               # Python deps (Flask 2.2, SQLAlchemy 1.4, APScheduler, etc.)
├── README.md                      # User-facing docs / API reference
├── LICENSE
│
├── shkeeper/                      # Main Flask application package
│   ├── __init__.py                # App factory create_app(), extensions, blueprint registration
│   ├── api_v1.py                  # Blueprint("api_v1", url_prefix="/api/v1/") — merchant + sidecar HTTP API
│   ├── auth.py                    # Blueprint("auth", url_prefix="/") — login, 2FA, decorators
│   ├── wallet.py                  # Blueprint("wallet") — admin HTML UI, /metrics, unlock flow
│   ├── callback.py                # Blueprint("callback") — webhook senders, scheduler entry-points, CLI
│   ├── tasks.py                   # APScheduler interval job definitions
│   ├── models.py                  # All SQLAlchemy entities + business helpers
│   ├── schemas.py                 # Pydantic models (TRON staking responses)
│   ├── wallet_encryption.py       # Fernet/PBKDF2 wallet-encryption singleton
│   ├── events.py                  # Module-level threading.Event for "initialized"
│   ├── exceptions.py              # Domain exception (NotRelatedToAnyInvoice)
│   ├── utils.py                   # format_decimal, remove_exponent
│   │
│   ├── modules/                   # Plugin-style auto-discovered drivers
│   │   ├── classes/               # Abstract bases + per-coin-family adapters
│   │   ├── cryptos/               # Concrete coin/token shims (auto-imported)
│   │   └── rates/                 # Concrete rate-source plugins (auto-imported)
│   │
│   ├── services/                  # Stateless business helpers
│   │   ├── payout_service.py
│   │   ├── balance_service.py
│   │   ├── crypto_cache.py
│   │   └── cache_service.py
│   │
│   ├── templates/                 # Jinja2 (.j2) templates rendered by wallet.py + auth.py
│   │   ├── base.j2 / 404.j2 / 500.j2 / macros.j2
│   │   ├── auth/                  # login, set-password, 2FA setup/verify/backup-codes
│   │   └── wallet/                # wallets, payout, transactions, rates, settings, unlock_*
│   │       └── configure/tron/    # TRON staking / multiserver UI fragments
│   │
│   └── static/                    # css/, js/, images/ served by Flask static
│
├── migrations/                    # Alembic environment + versioned migrations
│   ├── env.py
│   ├── alembic.ini
│   ├── script.py.mako
│   └── versions/                  # 5 revisions covering 2FA, callback_url, fee policies, etc.
│
├── docs/                          # Project docs (multipayout.md, tron_staking.md + images)
├── contrib/                       # Operator scripts (shkeeper-change-password.py)
└── .planning/codebase/            # GSD codebase intel (this directory)
```

## Directory Purposes

### `shkeeper/` (application package)
- Application factory + four blueprints + scheduler + models + plugin registries.
- Top-level `.py` files are flat (no nesting), so all blueprint code is one import-level deep.
- Key files: `__init__.py` (246 lines), `api_v1.py` (816 lines), `wallet.py` (710 lines), `callback.py` (403 lines), `auth.py` (364 lines), `models.py` (900 lines), `tasks.py` (129 lines).

### `shkeeper/modules/`
- Plugin "registries". Both subdirectories use the same dynamic-import idiom: `os.listdir(__file__) → __import__(...)` so any `.py` placed here gets loaded at startup (`shkeeper/modules/cryptos/__init__.py`, `shkeeper/modules/rates/__init__.py`).
- `modules/classes/` — abstract base classes (`crypto.py`, `rate_source.py`) and reusable coin-family bases that talk to a sidecar over HTTP/JSON-RPC: `btc.py`, `bitcoin_like_crypto.py`, `doge.py`, `ltc.py`, `ethereum.py`, `tron_token.py`, `bnb.py`, `polygon.py`, `avalanche.py`, `arbitrum.py`, `optimism.py`, `solana.py`, `ton.py`, `xrp.py`. NOT auto-imported.
- `modules/cryptos/` — one concrete shim per active coin/token. Most are 5-15 lines that set `self.crypto` and `getname()`; exceptions are `monero.py` and `bitcoin_lightning.py` which contain bespoke logic. Examples: `btc.py`, `eth.py`, `usdt.py`, `eth-usdc.py`, `ton.py`, `ton-usdt.py`, `arb-pyusd.py`, `op-token.py`, `solana-usdt.py`.
- `modules/rates/` — `binance.py`, `coinbase.py`, `kraken.py`, `kucoin.py`, `manual.py`. Each subclasses `RateSource` and is auto-instantiated.

### `shkeeper/services/`
- Thin "service" layer for cross-cutting flows that don't sit naturally on a model class.
- `payout_service.py` — `PayoutService` with `single_payout` / `multiple_payout` / `validate_callback_url`.
- `balance_service.py` — `get_balances(includes)` parallel fan-out across enabled coins.
- `crypto_cache.py` — `get_available_cryptos()` 60s TTL-cached snapshot of enabled+synced coins.
- `cache_service.py` — `TTLCache.remember(key, ttl, callback)` in-memory dict cache (singleton `cache`).

### `shkeeper/templates/`
- Jinja2 templates (`.j2` extension). `base.j2` is the layout, `macros.j2` shared snippets.
- `templates/auth/` — login + 2FA (`setup-2fa`, `verify-2fa`, `disable-2fa`, `2fa-backup-codes`, `regenerate-backup`), `set-password`.
- `templates/wallet/` — admin pages: `wallets.j2` (list), `manage.j2` (per-coin), `payout.j2` + `payout_btc_*.j2` / `payout_eth_*.j2` / `payout_tron.j2`, `payouts.j2`, `transactions.j2`, `rates.j2`, `settings.j2`, `unlock_*.j2` (encryption flow), `manage_server_*.j2` (RPC settings).
- `templates/wallet/configure/tron/` — TRON staking + multiserver dialogs.

### `shkeeper/static/`
- Served at `/static/`. JS is per-page (`custom-manage.js`, `custom-payout.js`, `custom-rates.js`, `tron_multiserver.js`, etc.). Vendored `jquery.min.js`, `moment.min.js`, `daterangepicker.min.js`. CSS + images.

### `migrations/`
- Alembic environment generated by Flask-Migrate. `env.py` sets up online/offline mode. `versions/` contains 5 revision scripts (e.g. `e4f8a9b2c1d3_add_2fa_support_to_user_model.py`, `cd6076e578ca_add_fee_policies.py`).
- New tables/columns: prefer `db.create_all()` for greenfield + `flask db migrate -m "..."` for schema changes (note example in `manage.py:7`).

### `docs/`
- Markdown product docs: `multipayout.md`, `tron_staking.md`, plus `images/` (incl. `tron_staking/` screenshots).

### `contrib/`
- Operator-facing standalone scripts that don't import the Flask app. Currently only `shkeeper-change-password.py`, which writes directly to the SQLite file at a hard-coded k3s PVC path.

### `.planning/codebase/`
- GSD codebase intelligence directory (where this document lives).

## Key File Locations

**Entry Points:**
- `manage.py` — Flask CLI entry; `python manage.py <cmd>` runs the FlaskGroup.
- `shkeeper/__init__.py:55` — `create_app()`, the factory used by both CLI and gunicorn.
- `Dockerfile` — gunicorn invocation: `gunicorn ... -b 0.0.0.0:5000 "shkeeper:create_app()"`.
- `shkeeper/tasks.py` — background-job entrypoints (decorator-based).

**Configuration:**
- `shkeeper/__init__.py:58-89` — defaults via `app.config.from_mapping(...)`. All runtime knobs are environment variables (`TRON_MULTISERVER_GUI`, `FORCE_WALLET_ENCRYPTION`, `UNCONFIRMED_TX_NOTIFICATION`, `REQUESTS_TIMEOUT`, `DEV_MODE`, `ENABLE_PAYOUT_CALLBACK`, `MIN_CONFIRMATION_BLOCK_FOR_PAYOUT`, `NOTIFICATION_TASK_DELAY`, `DISABLE_CRYPTO_WHEN_LAGS`, `EXTRA_CURRENCIES`, etc.).
- `instance/config.py` — optional override file loaded if present (`from_pyfile("config.py", silent=True)`). Not in repo.
- Coin gating: env `<SYMBOL>_WALLET=enabled|disabled` (see `shkeeper/modules/classes/crypto.py:23-87`).
- Sidecar endpoints: env `<COIN>_API_SERVER_HOST` / `<COIN>_SERVER_PORT` / `<COIN>_USERNAME` / `<COIN>_PASSWORD` (per-coin-family base class, e.g. `shkeeper/modules/classes/btc.py:16`).
- Webhook signing: `SHKEEPER_BTC_BACKEND_KEY` env var.
- Metrics auth: `METRICS_USERNAME` / `METRICS_PASSWORD`.

**Core Logic:**
- HTTP API: `shkeeper/api_v1.py` (merchant + sidecar webhook routes).
- Admin UI: `shkeeper/wallet.py` (HTML routes + `/metrics`).
- Persistence: `shkeeper/models.py` (SQLAlchemy models + business methods).
- Crypto plugin registry: `shkeeper/modules/classes/crypto.py` + `shkeeper/modules/cryptos/`.
- Rate plugin registry: `shkeeper/modules/classes/rate_source.py` + `shkeeper/modules/rates/`.
- Background jobs: `shkeeper/tasks.py` (driven from scheduled APScheduler intervals).
- Notification senders: `shkeeper/callback.py:68` (`send_notification`), `:16` (`send_unconfirmed_notification`), `:264` (`send_payout_notification`).

**Testing:**
- Not detected — no `tests/`, `test_*.py`, `pytest`, or `unittest` references in `requirements.txt`, `Dockerfile`, or repo tree. Manual / live testing only.

## Naming Conventions

**Files:** snake_case `.py`. Concrete coin shims sometimes use hyphens (`eth-usdc.py`, `arb-pyusd.py`, `op-token.py`, `ton-usdt.py`, `polygon-usdc.py`) — these are dynamically imported by filename, so hyphens are tolerated by the loader (`shkeeper/modules/cryptos/__init__.py:7`).

**Directories:** snake_case (`modules/classes`, `modules/cryptos`, `modules/rates`, `services`, `templates/wallet/configure/tron`).

**Classes:**
- Coin-family abstract bases: PascalCase matching the chain (`Btc`, `Ethereum`, `TronToken`, `BitcoinLikeCrypto`, `Doge`, `Ltc`, `Solana`, `Ton`, `Xrp`, `Bnb`, `Avalanche`, `Polygon`, `Arbitrum`, `Optimism`).
- Concrete coin shims: lowercase matching the symbol (`btc`, `eth`, `usdt`, `usdc`, `trx`, `xrp`, `sol`, `matic`, `avax`, `bnb`, `arbeth`, `opeth`); larger ones use PascalCase (`Monero`, `BitcoinLightning`).
- Models: PascalCase (`User`, `Wallet`, `Invoice`, `InvoiceAddress`, `Transaction`, `UnconfirmedTransaction`, `Payout`, `PayoutTx`, `Notification`, `ExchangeRate`, `Setting`, `BitcoinLightningInvoice`, `PayoutDestination`).
- Enums: PascalCase + UPPERCASE members (`InvoiceStatus.UNPAID`, `PayoutPolicy.LIMIT`, `FeeCalculationPolicy.PERCENT_FEE`).
- Services: PascalCase (`PayoutService`, `TTLCache`).

**Functions:** snake_case for routes and helpers (e.g. `payment_request`, `walletnotify`, `send_callbacks`, `update_confirmations`, `task_callback`).

**Routes:**
- API: `/api/v1/<crypto_name>/<action>` lower-case kebab/snake mix (`/payment_request`, `/payout-destinations`, `/payment-gateway`, `/fee-deposit-address`, `/multipayout`, `/payout/status`).
- Webhooks (no prefix): `/walletnotify/<crypto>/<txid>`, `/payoutnotify/<crypto>`, `/<crypto>/decrypt`.
- Admin UI (no prefix, `wallet` blueprint): `/wallets`, `/wallet/<crypto>`, `/payout/<crypto>`, `/rates`, `/transactions`, `/settings`, `/metrics`, `/unlock`.
- Auth: `/login`, `/logout`, `/setup-2fa`, `/verify-2fa`, `/disable-2fa`, `/regenerate-backup`.

**Decorators (custom):** `@login_required`, `@api_key_required`, `@basic_auth_optional`, `@metrics_basic_auth`, `@handle_request_error`.

## Where to Add New Code

**New API endpoint (merchant-facing):**
- Primary code: append a `@bp.get/post(...)` to `shkeeper/api_v1.py`. Pattern: protect with `@api_key_required` for merchants, `@login_required` for admin-only, `@handle_request_error` for payout-style endpoints.
- For sidecar→shkeeper webhooks: same file, manually check `X-Shkeeper-Backend-Key` header (see `walletnotify` at `shkeeper/api_v1.py:467`).
- Tests: no test infra — exercise manually via `curl` or sidecar containers.

**New admin UI page:**
- Route: add to `shkeeper/wallet.py` with `@login_required`, return `render_template("wallet/<name>.j2", ...)`.
- Template: create `shkeeper/templates/wallet/<name>.j2` extending `base.j2`.
- Page-specific JS: drop `shkeeper/static/js/custom-<name>.js` and load from the template.

**New cryptocurrency driver:**
- If it's a brand-new chain family: subclass `Crypto` in `shkeeper/modules/classes/<chain>.py`, implementing `getname`, `gethost`, `balance`, `getstatus`, `mkaddr`, `getaddrbytx`, `dump_wallet`, `create_wallet`, `mkpayout`, `get_all_addresses` (see ABC at `shkeeper/modules/classes/crypto.py:92`). Optionally provide `metrics()`, `estimate_tx_fee()`, `get_task()`, `multipayout()`.
- If it reuses an existing chain (Bitcoin-like, EVM, Tron): subclass the family base directly.
- Concrete shim: create `shkeeper/modules/cryptos/<symbol>.py` with a class whose `__init__` sets `self.crypto = "SYMBOL"` and a `getname()` method (see `shkeeper/modules/cryptos/btc.py` or `usdt.py` as templates).
- Activation: add the symbol to `default_off` or `default_on` in `shkeeper/modules/classes/crypto.py:23-87` and gate it via env `<SYMBOL>_WALLET=enabled`.
- Rate normalization: if the new coin needs alias-mapping for rate fetching, add it to the appropriate set in `shkeeper/modules/classes/rate_source.py:7` (e.g. `USDT_CRYPTOS`).
- Wiring: nothing else needed — `shkeeper/modules/cryptos/__init__.py` auto-imports it; `Crypto.__init_subclass__` instantiates it; `Wallet.register_currency` and `ExchangeRate.register_currency` get called from `create_app()` (`shkeeper/__init__.py:187`).

**New rate source:**
- Create `shkeeper/modules/rates/<source>.py` subclassing `RateSource` with class attribute `name = "<source>"` and method `get_rate(self, fiat, crypto)`.
- Auto-registered via `RateSource.__init_subclass__` (`shkeeper/modules/classes/rate_source.py:14`); appears in admin "Rates" page dropdown.

**New background job:**
- Path: `shkeeper/tasks.py`. Add `@scheduler.task("interval", id="<unique>", seconds=N)` and wrap body in `with scheduler.app.app_context(): ...` (see existing patterns at `shkeeper/tasks.py:9`).

**New database model:**
- Path: append a `db.Model` subclass at the bottom of `shkeeper/models.py` (single-file convention).
- Migration: `flask --app manage.py db migrate -m "<message>"` then commit the new file under `migrations/versions/`.
- If you add it to existing imports, also extend the `from .models import (...)` block in `shkeeper/__init__.py:154`.

**New CLI command:**
- Add `@bp.cli.command()` to any existing blueprint (callback uses this pattern: `shkeeper/callback.py:362`). Invoke as `flask --app manage.py <blueprint> <cmd>`.

**Operator script (no Flask context):**
- Drop into `contrib/`. These scripts are expected to talk directly to the SQLite file or filesystem (see `contrib/shkeeper-change-password.py`).

**Utilities:**
- Decimal helpers: `shkeeper/utils.py` (`format_decimal`, `remove_exponent`).
- Domain exceptions: `shkeeper/exceptions.py`.
- Pydantic schemas (currently only TRON): `shkeeper/schemas.py`.

## Special Directories

**migrations/:**
- Purpose: Alembic database migrations managed by Flask-Migrate.
- Generated: Yes (via `flask db migrate`).
- Committed: Yes — `migrations/versions/*.py` is in repo.
- Naming convention: defined in `shkeeper/__init__.py:32-39` (`ix_`, `uq_`, `ck_`, `fk_`, `pk_` prefixes).

**docs/:**
- Purpose: Long-form documentation for specific features (`multipayout.md`, `tron_staking.md`) plus screenshots in `images/` and `images/tron_staking/`.
- Not auto-rendered — referenced from `README.md`.

**contrib/:**
- Purpose: Operator/admin scripts that run outside the application process. Expected to be idempotent and self-contained.

**shkeeper/static/, shkeeper/templates/:**
- Standard Flask `static_folder` and `template_folder` (created implicitly by `Flask(__name__)` in `shkeeper/__init__.py:57`).

**.planning/codebase/:**
- GSD codebase intelligence — refreshed by `/gsd-map-codebase` and consumed by other GSD commands.

---

*Structure analysis: 2026-04-30*
