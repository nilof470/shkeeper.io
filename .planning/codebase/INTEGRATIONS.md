# External Integrations

**Analysis Date:** 2026-04-30

SHKeeper integrates with three classes of external systems:
1. **Per-coin "shkeeper-*" sidecars** — separate microservices (one per coin family) that run the actual blockchain node clients. SHKeeper talks to them over HTTP basic-auth to a small uniform RPC surface (`/balance`, `/status`, `/generate-address`, `/transaction/<txid>`, `/payout/...`, `/multipayout`, `/calc-tx-fee/...`, `/dump`, `/get_all_addresses`, `/fee-deposit-account`, `/task/<id>`, `/metrics`).
2. **Public exchange APIs** — for fiat<->crypto rate quotes.
3. **Merchant callback URLs** — outgoing HTTPS POSTs to merchant systems on invoice/payment/payout events.

There is no message broker, no Redis, no Celery, no Sentry, and no email/SMS provider integrated. Background work uses APScheduler in-process (`shkeeper/tasks.py`).

## APIs & External Services

**Cryptocurrency Sidecars / RPCs:**

All non-Monero coins follow the same pattern: SHKeeper POSTs to `http://<host>:<port>/<CRYPTO>/<verb>` with HTTP basic auth. Auth creds come from `<CRYPTO>_USERNAME` and `<CRYPTO>_PASSWORD`; host/port come from `<NETWORK>_API_SERVER_HOST` / `<NETWORK>_SERVER_PORT`. Defaults are k8s service names like `bitcoin-shkeeper`, `ethereum-shkeeper`, etc.

- BTC (Bitcoin)
  - Client: `shkeeper.requests` HTTP -> `bitcoin-shkeeper:6000` REST
  - Files: `shkeeper/modules/classes/btc.py`, `shkeeper/modules/cryptos/btc.py`
  - Auth env vars: `BTC_API_SERVER_HOST`, `BTC_SERVER_PORT`, `BTC_USERNAME`, `BTC_PASSWORD`

- LTC (Litecoin)
  - Client: HTTP -> `litecoin-shkeeper:6000`
  - Files: `shkeeper/modules/classes/ltc.py`, `shkeeper/modules/cryptos/ltc.py`
  - Auth env vars: `LTC_API_SERVER_HOST`, `LTC_SERVER_PORT`, `LTC_USERNAME`, `LTC_PASSWORD`

- DOGE (Dogecoin)
  - Client: HTTP -> `dogecoin-shkeeper:6000`
  - Files: `shkeeper/modules/classes/doge.py`, `shkeeper/modules/cryptos/doge.py`
  - Auth env vars: `DOGE_API_SERVER_HOST`, `DOGE_SERVER_PORT`, `DOGE_USERNAME`, `DOGE_PASSWORD`

- ETH (Ethereum) and ERC-20 tokens (ETH-USDT, ETH-USDC, ETH-PYUSD)
  - Client: HTTP -> `ethereum-shkeeper:6000`
  - Files: `shkeeper/modules/classes/ethereum.py`, `shkeeper/modules/cryptos/eth.py`, `eth-usdt.py`, `eth-usdc.py`, `eth-pyusd.py`
  - Auth env vars: `ETHEREUM_API_SERVER_HOST`, `ETHEREUM_SERVER_PORT`, `ETH_USERNAME`, `ETH_PASSWORD`

- TRX (Tron) and TRC-20 tokens (USDT, USDC)
  - Client: HTTP -> `<TRON_API_SERVER_HOST>:6000`
  - Files: `shkeeper/modules/classes/tron_token.py`, `shkeeper/modules/cryptos/trx.py`, `usdt.py`, `usdc.py`
  - Auth env vars: `TRON_API_SERVER_HOST`, `TRON_API_SERVER_PORT`, `<TRC20>_USERNAME`, `<TRC20>_PASSWORD` (per-token credentials, e.g. `USDT_USERNAME`)
  - Extra Tron-only endpoints exposed on the same sidecar: `/staking`, `/staking/info`, `/staking/freeze/<amount>/<resource>` (see `TronToken.get_account_info`, `get_staking_config`, `stake_trx` in `shkeeper/modules/classes/tron_token.py`).

- BNB (Binance Smart Chain) and BEP-20 tokens (BNB-USDT, BNB-USDC)
  - Client: HTTP -> `bnb-shkeeper:6000` (subclasses `Ethereum`)
  - Files: `shkeeper/modules/classes/bnb.py`, `shkeeper/modules/cryptos/bnb.py`, `bnb-usdt.py`, `bnb-usdc.py`
  - Auth env vars: `BNB_API_SERVER_HOST`, `BNB_SERVER_PORT`, `BNB_USERNAME`, `BNB_PASSWORD`

- MATIC (Polygon) and Polygon tokens (USDT, USDC)
  - Client: HTTP -> `polygon-shkeeper:6000`
  - Files: `shkeeper/modules/classes/polygon.py`, `shkeeper/modules/cryptos/matic.py`, `polygon-usdt.py`, `polygon-usdc.py`
  - Auth env vars: `POLYGON_API_SERVER_HOST`, `POLYGON_SERVER_PORT`, `POLYGON_USERNAME`, `POLYGON_PASSWORD`

- AVAX (Avalanche) and Avalanche tokens (USDT, USDC)
  - Client: HTTP -> `avalanche-shkeeper:6000`
  - Files: `shkeeper/modules/classes/avalanche.py`, `shkeeper/modules/cryptos/avax.py`, `avalanche-usdt.py`, `avalanche-usdc.py`
  - Auth env vars: `AVALANCHE_API_SERVER_HOST`, `AVALANCHE_SERVER_PORT`, `AVALANCHE_USERNAME`, `AVALANCHE_PASSWORD`

- SOL (Solana) and Solana tokens (USDT, USDC, PYUSD)
  - Client: HTTP -> `solana-shkeeper:6000`
  - Files: `shkeeper/modules/classes/solana.py`, `shkeeper/modules/cryptos/sol.py`, `solana-usdt.py`, `solana-usdc.py`, `solana-pyusd.py`
  - Auth env vars: `SOLANA_API_SERVER_HOST`, `SOLANA_SERVER_PORT`, `SOLANA_USERNAME`, `SOLANA_PASSWORD`

- XRP (Ripple)
  - Client: HTTP -> `xrp-shkeeper:6000` (subclasses `Ethereum`)
  - Files: `shkeeper/modules/classes/xrp.py`, `shkeeper/modules/cryptos/xrp.py`
  - Auth env vars: `XRP_API_SERVER_HOST`, `XRP_SERVER_PORT`, `XRP_USERNAME`, `XRP_PASSWORD`
  - X-address conversion + optional `dest_tag` support; payout subtracts a 10-XRP buffer to keep fee account active.

- ARBETH (Arbitrum) and Arbitrum tokens (ARB-USDC, ARB-PYUSD, ARB-TOKEN)
  - Client: HTTP -> `arbitrum-shkeeper:6000`
  - Files: `shkeeper/modules/classes/arbitrum.py`, `shkeeper/modules/cryptos/arbeth.py`, `arb-usdc.py`, `arb-pyusd.py`, `arb-token.py`
  - Auth env vars: `ARBITRUM_API_SERVER_HOST`, `ARBITRUM_SERVER_PORT`, `ARB_USERNAME`, `ARB_PASSWORD`

- OPETH (Optimism) and Optimism tokens (OP-USDT, OP-USDC, OP-TOKEN)
  - Client: HTTP -> `optimism-shkeeper:6000`
  - Files: `shkeeper/modules/classes/optimism.py`, `shkeeper/modules/cryptos/opeth.py`, `op-usdt.py`, `op-usdc.py`, `op-token.py`
  - Auth env vars: `OPTIMISM_API_SERVER_HOST`, `OPTIMISM_SERVER_PORT`, `OP_USERNAME`, `OP_PASSWORD`

- TON (The Open Network) and TON-USDT (Jetton)
  - Client: HTTP -> `ton-shkeeper:6000` (subclasses `Ethereum`)
  - Files: `shkeeper/modules/classes/ton.py`, `shkeeper/modules/cryptos/ton.py`, `ton-usdt.py`
  - Auth env vars: `TON_API_SERVER_HOST`, `TON_SERVER_PORT`, `TON_USERNAME`, `TON_PASSWORD`

- FIRO and FIRO-SPARK
  - Client: direct Bitcoin-style JSON-RPC to `firod:8332` (no shkeeper sidecar)
  - Files: `shkeeper/modules/classes/bitcoin_like_crypto.py`, `shkeeper/modules/cryptos/firo.py`, `firo-spark.py`
  - Auth env vars: `FIRO_USERNAME`, `FIRO_PASSWORD`. Backups served via `<CRYPTO>_NGINX_URL` env (defaults `http://<host>:5555/<filename>`).

- XMR (Monero) — handled in-process, NOT via a shkeeper sidecar
  - Client: `monero==1.1.1` library: `monero.daemon.Daemon`, `monero.backends.jsonrpc.JSONRPCWallet`, `monero.wallet.Wallet`
  - Files: `shkeeper/modules/cryptos/monero.py`
  - Auth env vars: `MONERO_DAEMON_HOST` (default `monerod`), `MONERO_DAEMON_PORT` (default `1111`), `MONERO_DAEMON_USER`, `MONERO_DAEMON_PASS`, `MONERO_WALLET_RPC_HOST` (default `monero-wallet-rpc`), `MONERO_WALLET_RPC_USER`, `MONERO_WALLET_RPC_PASS`, `MONERO_WALLET_NAME`, `MONERO_WALLET_PASS`. Note `MONERO_WALLET_RPC_PORT` is hardcoded to `2222` to avoid an env clash with k8s service env injection.

- BTC-LIGHTNING (Bitcoin Lightning via LND + LNbits + RTL)
  - Clients: direct HTTPS to LND REST, plus admin calls to LNbits
  - Files: `shkeeper/modules/cryptos/bitcoin_lightning.py`
  - LND auth: macaroon read from `<LND_SHARED_DIR>/data/chain/bitcoin/<LND_NETWORK>/admin.macaroon`, TLS cert from `<LND_SHARED_DIR>/tls.cert`. Network selectable via `LND_NETWORK` (mainnet/testnet/regtest).
  - Env vars: `LND_REST_URL`, `LND_SHARED_DIR`, `LND_NETWORK`, `LNBITS_URL`, `LNBITS_ADMIN_PASSWORD`, `LNBITS_SHARED_DIR`, `RTL_WEB_URL`, `LIGHTNING_INVOICE_TTL`, `LIGHTNING_INVOICE_REFRESH_PERIOD`, `LIGHTNING_INVOICE_ERROR_WAIT_PERIOD`, `LIGHTNING_SEND_TO_SHKEEPER_PERIOD`, `LIGHTNING_REQUESTS_TIMEOUT`, `LIGHTNING_WALLET_UNLOCK_PERIOD`, `LIGHTNING_WALLET_SEED_SAVER_PERIOD`, `LIGHTNING_GENERATE_ONCHAIN_ADDRESS`. LNbits header: `X-API-KEY` (admin key fetched from LNbits at startup).
  - Background threads: invoice listener (long-poll `/v1/invoices/subscribe`), invoice refresher, invoice notificator, seed saver, lnurl setup — all started in `BitcoinLightning.start_threads`.

**Public Rate Sources (for fiat<->crypto exchange rate):**

Pluggable via `RateSource` subclass auto-registration in `shkeeper/modules/rates/__init__.py`. Selectable per crypto/fiat pair via the `ExchangeRate` model (UI: `shkeeper/wallet.py:set_exchange_rate`). All four use no auth.

- Binance — `https://api.binance.com/api/v3/ticker/price` (`shkeeper/modules/rates/binance.py`)
- Coinbase — `https://api.coinbase.com/v2/exchange-rates` (`shkeeper/modules/rates/coinbase.py`)
- Kraken — `https://api.kraken.com/0/public/Ticker` (`shkeeper/modules/rates/kraken.py`)
- KuCoin — `https://api.kucoin.com/api/v1/prices` (`shkeeper/modules/rates/kucoin.py`)
- Manual — admin enters rate by hand (`shkeeper/modules/rates/manual.py`)

**Other External Services:**

None. No telegram bot, no email provider, no SMS, no analytics, no Sentry. Customer support is GitHub Issues + the `t.me/shkeeper_updates` channel mentioned in `README.md`.

## Data Storage

**Database:**
- SQLite (single file, default at `<flask_instance_path>/shkeeper.sqlite`)
  - Connection: built into `SQLALCHEMY_DATABASE_URI` in `shkeeper/__init__.py:62-64`; not configurable via env in current code.
  - Client: SQLAlchemy 1.4 + Flask-SQLAlchemy 2.5.1 (models in `shkeeper/models.py`)
  - Migrations: Flask-Migrate / Alembic (`migrations/env.py`, `migrations/versions/`). 5 migrations on `main` covering: callback URL, fee policies, reservation policy + amount, transaction unique constraints, 2FA support.
  - Tables: `User`, `Wallet`, `Invoice`, `InvoiceAddress`, `Transaction`, `UnconfirmedTransaction`, `Notification`, `Payout`, `PayoutTx`, `PayoutDestination`, `ExchangeRate`, `Setting`, `BitcoinLightningInvoice`. Foreign keys/constraints follow the SQLAlchemy `MetaData` naming convention configured in `shkeeper/__init__.py:32-39`.

**File Storage:**
- Flask-Session filestore: `<instance>/flask_session/` (cleared on every app start unless `DEV_MODE`).
- Wallet backup downloads streamed through SHKeeper from per-coin sidecars; for FIRO and other Bitcoin-likes the backup is served via an `<CRYPTO>_NGINX_URL` sidecar.
- LND/LNbits shared volumes: `LND_SHARED_DIR` (default `/lightning_shared`), `LNBITS_SHARED_DIR` (default `/lnbits_shared`) — must be mounted into the SHKeeper container so it can read `admin.macaroon`, `tls.cert`, and the RTL cookie.

**Caching:**
- In-process TTL cache for `/api/v1/crypto` and `/api/v1/crypto/balances` endpoints (60-second TTL). Implementation: `shkeeper/services/cache_service.py` (`TTLCache.remember`), wired in `shkeeper/services/crypto_cache.py:get_available_cryptos`. Per-process — multi-replica deployments will see drift up to 60 s.
- No Redis, no Memcached.

## Authentication & Identity

**Auth Provider:**
- Custom (Flask-Session + bcrypt), no third-party IdP.
  - Login UI: `shkeeper/auth.py:login` / `set_password` / `logout`. Templates under `shkeeper/templates/auth/`.
  - Session cookie loaded by `load_logged_in_user` (`shkeeper/auth.py:105-118`).
  - Single admin user (`username="admin"`, id=1) seeded in `create_app()` if missing (`shkeeper/__init__.py:166-176`). Password hash: bcrypt rounds=12 (`shkeeper/models.py:32`).
  - Out-of-band password reset: `contrib/shkeeper-change-password.py` mutates the SQLite directly inside the PVC.

**API authentication (multiple schemes coexist):**
- Per-wallet API key — header `X-Shkeeper-Api-Key`. Decorator `api_key_required` in `shkeeper/auth.py:89-102`. Validated against `Wallet.apikey` (any wallet's key grants access to the API surface; all wallets share one key in practice). Key is auto-generated on fresh install via `secrets.token_urlsafe(16)` (`shkeeper/__init__.py:65`).
- HTTP Basic Auth — `basic_auth_optional` decorator (`shkeeper/auth.py:50-68`). Used by payout endpoints; verifies username/password against the admin `User` row.
- Metrics Basic Auth — `metrics_basic_auth` decorator (`shkeeper/auth.py:33-47`). Separate creds via `METRICS_USERNAME`/`METRICS_PASSWORD` env (default `shkeeper/shkeeper`).
- Backend webhook key — header `X-Shkeeper-Backend-Key`, env `SHKEEPER_BTC_BACKEND_KEY` (default `shkeeper`). Used by per-coin sidecars when calling SHKeeper back at `/api/v1/walletnotify/<crypto>/<txid>`, `/api/v1/payoutnotify/<crypto>`, and `/api/v1/<crypto>/decrypt` (see `shkeeper/api_v1.py:451,482,564,616`).

**2FA:**
- `pyotp==2.9.0` for TOTP. Implementation: `shkeeper/auth.py:199-365` (verify, setup, disable, regenerate-backup) + `User.verify_totp`, `User.generate_totp_secret`, `User.generate_backup_codes`, `User.verify_backup_code` in `shkeeper/models.py:37-84`.
- TOTP secret stored Base32 in `user.totp_secret`; `valid_window=1` (~30 s drift each side); QR generated with `segno`.
- 10 single-use backup codes, hashed with bcrypt (rounds=12) and stored as a JSON array in `user.backup_codes`.

**Wallet Encryption (separate from login):**
- Optional at-rest encryption of wallet seeds/keys derived from a user-supplied password.
- Implementation: `shkeeper/wallet_encryption.py`. PBKDF2-HMAC-SHA256 (length=32, iterations=500_000, fixed 16-byte salt `Shkeeper4TheWin!`) feeds a `cryptography.fernet.Fernet`. Password verified via bcrypt hash stored in `Setting("WalletEncryptionPasswordHash")`.
- Persistent state machine in `Setting("WalletEncryptionPersistentStatus")`: `pending` / `disabled` / `enabled`.
- Runtime status held in process memory only; `wait_for_key()` blocks worker threads until the admin enters the key after a restart unless `DEV_MODE=1` + `DEV_MODE_ENC_PW` is set (`shkeeper/__init__.py:214-223`).
- `FORCE_WALLET_ENCRYPTION=1` forces the pending state on fresh installs.

## Monitoring & Observability

**Error Tracking:**
- None. No Sentry/Rollbar/Datadog/Bugsnag integration. Errors are logged via `app.logger.exception` to stdout/stderr (see Logs).

**Metrics:**
- `prometheus-client==0.16.0` exposes `GET /metrics`, behind metrics-basic-auth.
  - Endpoint: `shkeeper/wallet.py:567-594` (`metrics()`). Aggregates per-coin sidecar `/metrics` in parallel (one ThreadPool per request) plus `prometheus_client.generate_latest()` for SHKeeper-internal counters.
  - Default Python collectors are unregistered to keep output clean (`shkeeper/wallet.py:55-57`).
  - A second filter pass strips `*_last_release_info` and `*_fullnode_version_info` metric families (`_filter_metrics` at `shkeeper/wallet.py:600-618`).
  - Each coin's `metrics()` method (e.g. `shkeeper/modules/classes/btc.py:141-155`) appends a `<host>_status` gauge for online/offline.

**Logs:**
- Standard `logging` module. Custom formatter set in `shkeeper/__init__.py:11-15`: `"%(levelname)s %(filename)s:%(lineno)s %(funcName)s(): %(message)s"`. Level: `DEBUG` if `DEV_MODE` or `app.debug`, else `INFO`. `app.logger.propagate = False`.
- gunicorn access + error logs go to stdout/stderr (`-` in `Dockerfile`).

## CI/CD & Deployment

**Hosting:**
- Container image `vsyshost/shkeeper` on Docker Hub (built by `.github/workflows/ci.yml`).
- Production: Kubernetes (k3s) + Helm chart `vsys-host/shkeeper` from `https://vsys-host.github.io/helm-charts`. Reverse proxy: Traefik. Optional TLS via cert-manager + Let's Encrypt. Reference manifests embedded in `README.md`.
- Sibling Helm chart used during install: `mittwald/kubernetes-secret-generator` for generating cluster-side secrets at install time.

**CI Pipeline:**
- `.github/workflows/ci.yml` — On `v*.*.*` tag push: builds and pushes a semver-tagged image. Uses Docker Hub creds in repo secrets (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`).
- `.github/workflows/ci-dev.yml` — On every push: builds and pushes a `dev-<branch>-<sha>` tag.
- `.github/workflows/issue_create_auto_reply.yml` + `.github/issue_create_auto_reply.md` — Auto-reply to new GitHub issues.
- No tests run in CI. No linting step.

## Environment Configuration

**Required env vars (selected; see STACK.md for the full Flask config map):**

App-level:
- `SECRET_KEY` (must override the `"dev"` default before production)
- `METRICS_USERNAME`, `METRICS_PASSWORD`
- `SHKEEPER_BTC_BACKEND_KEY` (shared secret for sidecars to call SHKeeper webhooks)
- `EXTRA_CURRENCIES` (comma-separated extra fiat ISO codes; USD/EUR always on)
- `REQUESTS_TIMEOUT`, `REQUESTS_NOTIFICATION_TIMEOUT`, `MAX_RETRIES`, `NOTIFICATION_TASK_DELAY`
- `ENABLE_PAYOUT_CALLBACK`, `MIN_CONFIRMATION_BLOCK_FOR_PAYOUT`, `UNCONFIRMED_TX_NOTIFICATION`, `DISABLE_CRYPTO_WHEN_LAGS`
- `FORCE_WALLET_ENCRYPTION`, `DEV_MODE`, `DEV_MODE_ENC_PW`
- `TRON_MULTISERVER_GUI`, `TRON_STAKING_GUI`

Per-coin gating (`<SYMBOL>_WALLET=enabled|disabled`):
- BTC, LTC, DOGE default ON. All others (ETH/family, TRX/family, BNB/family, MATIC/family, AVAX/family, SOL/family, XRP, ARB/family, OP/family, TON/family, FIRO/family, BTC-LIGHTNING, MONERO) default OFF — opt in by setting the env to `enabled`. See `shkeeper/modules/classes/crypto.py:23-87`.

Per-coin RPC creds (one set per coin family):
- `<CRYPTO>_API_SERVER_HOST`, `<CRYPTO>_SERVER_PORT`, `<CRYPTO>_USERNAME`, `<CRYPTO>_PASSWORD`. For Tron tokens, USERNAME/PASSWORD are per-token (e.g. `USDT_USERNAME`).

Lightning:
- `LND_REST_URL`, `LND_NETWORK`, `LND_SHARED_DIR`, `LNBITS_URL`, `LNBITS_ADMIN_PASSWORD`, `LNBITS_SHARED_DIR`, `RTL_WEB_URL`, `LIGHTNING_*` knobs.

Monero:
- `MONERO_DAEMON_HOST/PORT/USER/PASS`, `MONERO_WALLET_RPC_HOST/USER/PASS`, `MONERO_WALLET_NAME/PASS`.

**Secrets location:**
- No `.env`, `.env.example`, or `secrets.*` file present at the repo root (verified by directory listing). The repo's `.gitignore` does list `.env`, so any local-only `.env` would be ignored.
- Production secrets are managed by Helm/`mittwald/kubernetes-secret-generator` per the install instructions in `README.md`.
- A `SECRET_KEY="dev"` literal default exists in `shkeeper/__init__.py:60` and a `"shkeeper"` default for several auth knobs — these are intentional fallbacks but MUST be overridden before any internet-exposed deployment.

## Webhooks & Callbacks

**Incoming (sidecars -> SHKeeper):**

All gated by header `X-Shkeeper-Backend-Key` matching env `SHKEEPER_BTC_BACKEND_KEY`. Bodies are JSON. See `shkeeper/api_v1.py`.

- `POST /api/v1/walletnotify/<crypto_name>/<txid>` — sidecar tells SHKeeper "I saw a transaction". Triggers `crypto.getaddrbytx(txid)`, splits send/receive, writes `Transaction`/`UnconfirmedTransaction`, updates the matching `Invoice`, and (if confirmed enough) calls `send_notification(tx)` synchronously. (`shkeeper/api_v1.py:467-547`)
- `POST /api/v1/payoutnotify/<crypto_name>` — sidecar reports a completed payout (currently logs only; commented-out `Payout.add` line suggests prior persistence). (`shkeeper/api_v1.py:443-465`)
- `GET /api/v1/<crypto_name>/decrypt` — sidecar fetches the runtime wallet decryption key. (`shkeeper/api_v1.py:549-578`)

Inbound merchant API (auth: `X-Shkeeper-Api-Key`) — full surface documented in `README.md` sections 5.2.x. Highlights:
- `POST /api/v1/<crypto>/payment_request` — create invoice (`shkeeper/api_v1.py:99-140`)
- `POST /api/v1/<crypto>/quote` — fiat<->crypto quote without creating an invoice (`shkeeper/api_v1.py:143-198`)
- `GET /api/v1/crypto`, `GET /api/v1/crypto/balances`
- `POST /api/v1/<crypto>/payout`, `POST /api/v1/<crypto>/multipayout`, `GET /api/v1/<crypto>/task/<id>`, `GET /api/v1/<crypto>/payout/status` (basic auth required for write paths)
- `POST /api/v1/decryption-key` — admin enters wallet-encryption key over API instead of the unlock UI

**Outgoing (SHKeeper -> merchant):**

All driven by APScheduler interval jobs in `shkeeper/tasks.py`; HTTP POSTs use the global `requests` (with `REQUESTS_NOTIFICATION_TIMEOUT`).

- Invoice payment notifications — POST to `Invoice.callback_url` with `X-Shkeeper-Api-Key` header. Payload includes invoice status (`UNPAID`/`PARTIAL`/`PAID`/`OVERPAID`), full transaction list with the triggering tx flagged, fee policy, and overpaid_fiat. Acceptance requires HTTP 202; otherwise retried every 60 s. Initial send is delayed by `NOTIFICATION_TASK_DELAY` seconds (default 60). See `send_notification` in `shkeeper/callback.py:68-139`. Scheduled by `task_callback` (`shkeeper/tasks.py:9-13`).
- Unconfirmed transaction notifications — POST same callback URL with `{status: "unconfirmed", external_id, crypto, addr, txid, amount}` for mempool-only transactions when `UNCONFIRMED_TX_NOTIFICATION=1`. See `send_unconfirmed_notification` in `shkeeper/callback.py:16-65`.
- Payout callback notifications — Optional, gated by `ENABLE_PAYOUT_CALLBACK=1`. POST to `Payout.callback_url` (no header auth; merchant should bind by URL secrecy or trust the source IP) with `{payout_id, external_id, tx_hash, status: "SUCCESS", amount, crypto, amount_fiat, currency_fiat: "USD", timestamp}`. Exponential-ish backoff `(retries+1)**2` seconds, capped by `MAX_RETRIES` (default 7). See `send_payout_notification` and `send_payout_callback_notifier` in `shkeeper/callback.py:236-316`. Scheduled by `task_send_payout_callback_notifier`.
- Payout polling — `task_poll_all_pending_payouts` and `task_poll_unconfirmed_payouts` in `shkeeper/tasks.py:15-23` ask each coin's sidecar `get_task(task_id)` until SUCCESS/FAILURE, then queue a `Notification` row for the payout callback notifier.
- Auto-payout — `task_payout` (`shkeeper/tasks.py:32-84`) sweeps wallets to the admin-configured `pdest` based on `PayoutPolicy.LIMIT` (balance >= threshold) or `PayoutPolicy.SCHEDULED` (every N minutes), respecting `PayoutReservePolicy.AMOUNT` / `PERCENT` reservations.

---

*Integration audit: 2026-04-30*
