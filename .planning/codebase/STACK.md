# Technology Stack

**Analysis Date:** 2026-04-30

SHKeeper is an open-source, self-hosted, non-custodial cryptocurrency payment processor implemented as a single Flask web application. It does not run any blockchain code itself; it talks HTTP-RPC to a constellation of sidecar "shkeeper-*" services (one per coin), packaged together via Helm/Kubernetes. The Flask app provides the merchant API, admin UI, invoice/exchange-rate logic, callback dispatch, and Prometheus metrics aggregation.

## Languages

**Primary:**
- Python 3.13 — Backend Flask app at `shkeeper/` (Python interpreter pinned by the base image in `Dockerfile`)

**Secondary:**
- Jinja2 templates — Admin UI at `shkeeper/templates/` (e.g. `shkeeper/templates/wallet/wallets.j2`, `shkeeper/templates/auth/login.j2`)
- HTML/CSS/JS — Static assets at `shkeeper/static/`
- Shell — Embedded snippets in `README.md` for k3s/helm install (no .sh files in repo)
- SQL (via SQLAlchemy/Alembic) — Schema migrations at `migrations/versions/`

## Runtime

**Environment:**
- Python 3.13 (pulled as `FROM python:3.13` in `Dockerfile`)
- Debian-based container with apt packages: `python3`, `python3-pip`, `git`, `sqlite3`, `curl` (see `Dockerfile`)
- Container runtime: Docker; production target is Kubernetes via the `vsys-host/shkeeper` Helm chart (see `README.md`)
- WSGI server: gunicorn 25.1.0, 1 worker x 16 threads, `gthread` worker class, bind `0.0.0.0:5000`, entry `shkeeper:create_app()` (see `Dockerfile` lines 11-24)

**Package Manager:**
- pip — `requirements.txt` (no lockfile; pinned exact versions for most deps but `cryptography` is unpinned)

## Frameworks

**Core:**
- Flask 2.2.2 — Web framework. App factory `create_app()` defined in `shkeeper/__init__.py`. Blueprints registered: `auth`, `wallet`, `api_v1`, `callback`.
- Werkzeug 2.3.7 — WSGI utilities (`werkzeug.security` for password hashing helpers; pinned for compatibility with Flask 2.2)
- Flask-SQLAlchemy 2.5.1 + SQLAlchemy 1.4 — ORM. Models in `shkeeper/models.py`. DB instance constructed in `shkeeper/__init__.py` (`db = flask_sqlalchemy.SQLAlchemy(metadata=metadata)`).
- Flask-Migrate 4.0.5 + alembic 1.18.1 — Schema migrations. Config: `migrations/alembic.ini`, environment: `migrations/env.py`. CLI entry point: `manage.py`.
- Flask-Session 0.4.0 — Server-side sessions stored on the filesystem (`SESSION_TYPE="filesystem"`, `SESSION_FILE_DIR=<instance>/flask_session`; cleared on app start unless `DEV_MODE`). See `shkeeper/__init__.py:67-112`.
- Flask-APScheduler 1.12.4 — Background tasks. Scheduler started inside the Flask app context. Tasks defined in `shkeeper/tasks.py` (5 interval jobs at 60s, 1 at 10s).
- gunicorn 25.1.0 — Production WSGI server (see `Dockerfile`).

**Testing:**
- Not detected. No `tests/`, `pytest`, or `conftest.py` in the repo. `requirements.txt` has no test deps. `.gitignore` references `.coverage`/`.pytest_cache/` defensively only.

**Build/Dev:**
- Docker — `Dockerfile` (single-stage; copies entire context, installs requirements)
- GitHub Actions — `.github/workflows/ci.yml` builds/pushes `vsyshost/shkeeper` image on `v*.*.*` tags; `.github/workflows/ci-dev.yml` builds dev images on every push.
- No `docker-compose.yml`, no `Makefile`, no `pyproject.toml` — Helm chart is the canonical deploy unit.

## Key Dependencies

**Critical (crypto-payment domain):**
- `monero==1.1.1` — Native Python Monero RPC client. Used for XMR daemon + wallet RPC in `shkeeper/modules/cryptos/monero.py` (imports `monero.backends.jsonrpc.JSONRPCWallet`, `monero.daemon.Daemon`, `monero.wallet.Wallet`). Monero is the only coin handled in-process; every other coin is reached over HTTP via the shared `requests` client.
- `bcrypt==3.2.2` — Password hashing for admin login, wallet-encryption password hash, and 2FA backup codes (`shkeeper/models.py:32`, `shkeeper/wallet_encryption.py:84`, `contrib/shkeeper-change-password.py`). Cost factor 12.
- `pyotp==2.9.0` — TOTP 2FA implementation. `shkeeper/auth.py` (login + setup flows) and `shkeeper/models.py` (`User.verify_totp`, `User.generate_totp_secret`).
- `cryptography` (unpinned) — `cryptography.fernet.Fernet` + PBKDF2-SHA256 (500_000 iterations, fixed salt `Shkeeper4TheWin!`) for at-rest wallet-key encryption in `shkeeper/wallet_encryption.py`.
- `segno==1.6.6` — QR code generation for TOTP provisioning URIs and for invoice/payout addresses (`shkeeper/auth.py:301`, `shkeeper/wallet.py:9`).
- `pydantic==2.11.7` — Schema validation for Tron staking responses in `shkeeper/schemas.py` (`TronAccountResponse`, `TronError`, etc.). Note: only the Tron staking surface uses Pydantic; the rest of the codebase is plain dicts.
- `requests==2.28.1` — HTTP client to every per-coin sidecar RPC. Globally monkey-patched in `shkeeper/__init__.py:141-148` so every `requests.get/post/...` call carries `timeout=app.config['REQUESTS_TIMEOUT']` (default 10s). Always import via `from shkeeper import requests`, not the upstream package directly.

**Infrastructure:**
- `SQLAlchemy==1.4` (kept on 1.4 line because Flask-SQLAlchemy 2.5.1 requires it) — Default DB is SQLite at `<instance>/shkeeper.sqlite` (`shkeeper/__init__.py:62-64`).
- `prometheus-client==0.16.0` — Metrics exposition at `GET /metrics` (`shkeeper/wallet.py:567-594`); aggregates per-coin sidecar `/metrics` and SHKeeper-internal counters. Default Python collectors (GC, platform, process) are explicitly unregistered (`shkeeper/wallet.py:55-57`).
- `gunicorn==25.1.0` — see Runtime.
- `alembic==1.18.1` — see Frameworks/Flask-Migrate.

## Configuration

**Environment:**
- All runtime config is read from environment variables in `shkeeper/__init__.py:55-89` (`create_app`) and per-coin in `shkeeper/modules/classes/*.py` and `shkeeper/modules/cryptos/*.py`.
- Optional `instance/config.py` is loaded via `app.config.from_pyfile("config.py", silent=True)` (`shkeeper/__init__.py:93`); not present in repo.
- Top-level Flask app config keys: `SECRET_KEY` (defaults to `"dev"` — must be overridden), `DATABASE`/`SQLALCHEMY_DATABASE_URI` (SQLite under instance dir), `SUGGESTED_WALLET_APIKEY` (auto-generated via `secrets.token_urlsafe(16)` for fresh installs), `SESSION_TYPE`/`SESSION_FILE_DIR`, `TRON_MULTISERVER_GUI`, `TRON_STAKING_GUI`, `FORCE_WALLET_ENCRYPTION`, `UNCONFIRMED_TX_NOTIFICATION`, `REQUESTS_TIMEOUT`, `MAX_RETRIES` (renamed `REQUESTS_NOTIFICATION_RETRIES`), `REQUESTS_NOTIFICATION_TIMEOUT`, `DEV_MODE`, `DEV_MODE_ENC_PW`, `ENABLE_PAYOUT_CALLBACK`, `MIN_CONFIRMATION_BLOCK_FOR_PAYOUT`, `NOTIFICATION_TASK_DELAY`, `DISABLE_CRYPTO_WHEN_LAGS`, `EXTRA_CURRENCIES`.
- Per-coin enable/disable: each crypto's auto-registration in `shkeeper/modules/classes/crypto.py:17-90` reads `<SYMBOL>_WALLET=enabled|disabled`. `btc`, `ltc`, `doge` default ON; everything else defaults OFF until enabled.
- Per-coin RPC: each subclass reads its host/port and HTTP basic-auth user/pass from env vars like `BTC_API_SERVER_HOST`, `BTC_SERVER_PORT`, `BTC_USERNAME`, `BTC_PASSWORD` (see `shkeeper/modules/classes/btc.py:15-23`). Same pattern for ETH, LTC, DOGE, TRON, BNB, XRP, MATIC, AVAX, SOL, ARB, OP, TON, FIRO. Monero is special and uses `MONERO_DAEMON_*` and `MONERO_WALLET_RPC_*` (see `shkeeper/modules/cryptos/monero.py:30-50`).
- Lightning-specific: `LNBITS_URL`, `LNBITS_ADMIN_PASSWORD`, `LND_REST_URL`, `LND_NETWORK`, `LIGHTNING_*` knobs (`shkeeper/modules/cryptos/bitcoin_lightning.py:24-78`).
- Metrics auth: `METRICS_USERNAME`, `METRICS_PASSWORD` (default `shkeeper/shkeeper`) — see `shkeeper/auth.py:36-37`.
- Backend webhook auth: `SHKEEPER_BTC_BACKEND_KEY` (default `shkeeper`) is the shared secret used by per-coin sidecars to call `/api/v1/walletnotify/...` and `/api/v1/payoutnotify/...` back into SHKeeper. Despite the name it covers all coins. See `shkeeper/api_v1.py:451,482,564,616`.

**Build:**
- `Dockerfile` — single stage, Python 3.13 base, installs `requirements.txt`, runs gunicorn.
- `.dockerignore` — excludes `instance/`, `__pycache__/`, `.venv/`, etc.
- No instance config committed; Helm chart is expected to mount/template values.

## Platform Requirements

**Development:**
- Python 3.13, pip
- Docker (for matching the production image base)
- SQLite 3 (provided by base image; data lives in `<instance>/shkeeper.sqlite`)
- Local `requests`-reachable instances of each coin's `*-shkeeper` sidecar are required to exercise non-stub paths — production typically uses the helm chart at `https://vsys-host.github.io/helm-charts`.

**Production:**
- Kubernetes via k3s + Helm (canonical install path in `README.md`).
- gunicorn fronts the Flask app; Traefik is the documented ingress (with optional cert-manager TLS via Let's Encrypt).
- Persistent storage: SQLite DB + Flask sessions live on a `PersistentVolumeClaim` (see paths in `contrib/shkeeper-change-password.py`).
- Bitcoin Lightning optional: requires port `9000` exposed publicly for LNURL.

---

*Stack analysis: 2026-04-30*
