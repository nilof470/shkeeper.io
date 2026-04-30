# Codebase Concerns

**Analysis Date:** 2026-04-30

> Crypto-payment processor — Flask/Python single-process worker with multi-chain integrations (BTC/ETH/TRX/XMR/Lightning + many tokens), webhook callbacks to merchants, and per-wallet payout flows. Auditing for security/correctness should be the team's overriding priority.

---

## Tech Debt

**SQLAlchemy 1.4 + Flask-SQLAlchemy 2.5 (legacy stack):**
- Issue: Pinned `SQLAlchemy==1.4` and `Flask-SQLAlchemy==2.5.1` in `requirements.txt`. SQLAlchemy 2.x has been GA since Jan 2023 and Flask-SQLAlchemy 3.x with it; pattern `Model.query.get(...)` (used heavily, e.g. `User.query.get(1)` at `shkeeper/auth.py:113`, `shkeeper/auth.py:171`) is *Legacy* in 1.4 and removed in 2.x.
- Files: `requirements.txt:7`, `requirements.txt:10`, `shkeeper/auth.py:113,171,179,219`, `shkeeper/models.py:124`, plus dozens of `*.query.get/filter_by` call sites.
- Impact: Future SQLAlchemy upgrade is a large breaking change; deprecated APIs accumulate; security/perf fixes in newer versions not reachable.
- Fix approach: Bump Flask-SQLAlchemy to 3.x and SQLAlchemy to 2.x in lockstep, replace `Model.query.get` with `db.session.get(Model, id)`, replace `Query.filter_by(...).first()` style with select() where called from new code, run a single migration test cycle.

**Outdated Flask & Werkzeug:**
- Issue: `Flask==2.2.2` (Aug 2022), `Werkzeug==2.3.7` — both subject to known security advisories (e.g., GHSA-m2qf-hxjv-5gpq Werkzeug debugger PIN, GHSA-2g68-c3qc-8985 Werkzeug DoS, Flask CVE-2023-30861 caching of permanent session cookies for clients sharing caches). The Werkzeug 2.3 line is EOL; 3.x is current.
- Files: `requirements.txt:3`, `requirements.txt:4`.
- Impact: Possible exposure to known disclosed CVEs; security patches not flowing.
- Fix approach: Upgrade to Flask 3.0+ / Werkzeug 3.0+, run smoke tests on auth, JSON encoder/decoder behaviour (`shkeeper/__init__.py:128-139` uses deprecated-style `app.json_decoder/encoder` attributes — Flask 2.3+ moves to `app.json` provider).

**Per-crypto class duplication / lack of abstraction:**
- Issue: `shkeeper/modules/classes/btc.py`, `shkeeper/modules/classes/ethereum.py`, `shkeeper/modules/classes/tron_token.py` and others reimplement the same skeleton (gethost / get_auth_creds / balance / mkaddr / metrics …) with copy-pasted bodies that differ only by URL fragments and crypto name. ~163 lines × 14 classes.
- Files: `shkeeper/modules/classes/btc.py`, `…/ethereum.py`, `…/avalanche.py`, `…/optimism.py`, `…/solana.py`, `…/polygon.py`, `…/tron_token.py`, `…/xrp.py`, `…/ton.py`, `…/bnb.py`, `…/doge.py`, `…/ltc.py`, `…/arbitrum.py`.
- Impact: Bug fixes need N copies (the recent "Fix metrics #213" commit (`fb4d3da`) is exactly this kind of fan-out fix). Likely site of subtle inconsistencies.
- Fix approach: Push HTTP-RPC primitives into `BitcoinLikeCrypto` / `Ethereum` base classes, parameterize on `(crypto, host_env_var, port_env_var, network_currency)`. Subclasses then only declare metadata.

**`models.py` is a 900-line god module:**
- Issue: One file holds `User`, `Wallet`, `Invoice`, `InvoiceAddress`, `Transaction`, `UnconfirmedTransaction`, `Payout`, `PayoutTx`, `Notification`, `Setting`, `BitcoinLightningInvoice`, `Fiat` helper, `ExchangeRate`, plus business logic in `Invoice.add`, `Transaction.add`, `Payout.update_from_task`, `Wallet.do_payout`. Mixing data layout with payout/exchange logic.
- Files: `shkeeper/models.py` (900 lines).
- Impact: Hard to test in isolation, circular-import risk (already side-stepped via local imports in `wallet_encryption.py:30,38,91,103`).
- Fix approach: Split into `models/{user,wallet,invoice,payout,settings}.py`, move fee/payout calculations into services (similar to `shkeeper/services/payout_service.py`).

**Re-raise-then-dead-code in API handlers:**
- Issue: `shkeeper/api_v1.py:280-286` does `except Exception as e: raise e` followed by unreachable `response = {...}`; multiple handlers swallow exceptions only to re-stuff `traceback.format_exc()` into the response body (`api_v1.py:138, 196, 285, 545, 571, 683, 716, 735, 760, 785`). The `traceback` is leaked over the wire.
- Files: `shkeeper/api_v1.py` (multiple handlers).
- Impact: (a) Information disclosure to API callers (file paths, library versions, internal state). (b) Dead code masks real intent.
- Fix approach: Drop `traceback` from JSON response; log server-side only. Replace bespoke try/except with the existing `@handle_request_error` wrapper (which is already used on payout/multipayout — generalize).

**`# TODO: implement` stubs in production routes:**
- Issue: `/api/v1/<crypto_name>/server/key` and `/api/v1/<crypto_name>/server/host` return literal "not implemented yet" but are wired into the URL map and protected by `@login_required` (`shkeeper/api_v1.py:590-601`).
- Files: `shkeeper/api_v1.py:590-601`.
- Impact: UI pages that POST to these endpoints will silently fail to update server config; user must edit env vars.
- Fix approach: Either implement the persistence (writing to `Setting`) or remove the routes and clean up callers in `shkeeper/templates/wallet/`.

**Hard-coded HTTP plaintext to backend RPCs:**
- Issue: Every backend RPC uses `f"http://{self.gethost()}/{self.crypto}/..."` — no TLS, no signed body. Credentials are sent via HTTP Basic auth in cleartext (e.g. `shkeeper/modules/classes/btc.py:27-35`, `…/ethereum.py:28-42`, `…/tron_token.py:36-44`).
- Files: All `shkeeper/modules/classes/*.py` and `shkeeper/modules/cryptos/doge.py`.
- Impact: Secure only if cluster network is fully trusted (k3s pod-to-pod). On any deployment that traverses untrusted network this leaks RPC credentials and lets an attacker forge payout requests to backends.
- Fix approach: Use `https://` for inter-service calls when deployed across nodes / over the internet, or require service-mesh mTLS. Make the scheme configurable.

**Unbounded/very long ThreadPoolExecutor:**
- Issue: `shkeeper/services/balance_service.py:45` — `with ThreadPoolExecutor()` (defaults to `min(32, cpu+4)` workers in Py3.8+) is used per request to fan out to N crypto backends; same pattern in `shkeeper/wallet.py:582`. `shkeeper/api_v1.py:4` imports `ThreadPoolExecutor` but doesn't use it directly.
- Files: `shkeeper/services/balance_service.py:45`, `shkeeper/wallet.py:582`.
- Impact: Under bursty load (16 gunicorn threads × 30+ cryptos × per-request executor) → thread amplification, increased latency, increased risk of node-RPC overload. Each `_build_balance` opens a fresh `app_context` (`shkeeper/services/balance_service.py:10`) which is correct but expensive.
- Fix approach: Bound workers (e.g. `max_workers=8`). Consider an app-scoped executor instead of per-request.

---

## Known Bugs

Findings from TODO/FIXME scan are minimal — only two `# TODO: implement` markers in `shkeeper/api_v1.py:593,600` (covered above). However, several things that read as latent bugs were observed:

- **Default-credential webhook endpoint:** `shkeeper/api_v1.py:451`, `:482`, `:564` use `environ.get("SHKEEPER_BTC_BACKEND_KEY", "shkeeper")` — if the env var is unset, the backend key defaults to the literal string `"shkeeper"`. Since `walletnotify` writes confirmed transactions and `decrypt_key` reveals the wallet decryption key, anyone reaching this endpoint with the default header value can post fake confirmations or read the encryption key.
- **`decrypt_key` (`shkeeper/api_v1.py:574-578`) returns `wallet_encryption.key()`** (the in-memory plaintext key) over an HTTP boundary if the backend key matches. Even if backend key is set correctly this is a sensitive cross-trust-boundary leak by design.
- **`models.py:114`** — `if user.passhash` on first-time-load when `User.query.get(user_id)` returns None will raise `AttributeError`. Suggests missing null-check.
- **Bare `except Exception:` swallows in `wallet.py:520`** (Tron QR generation) is benign, but `callback.py:212` and `callback.py:259` swallow ALL exceptions inside `poll_unconfirmed_payouts` and the payout-callback notifier — including DB errors that should surface.
- **`shkeeper/models.py:114-118`** — `if user.passhash:` is checked only after fetching by `user_id`. If passhash is later cleared mid-session, session is invalidated correctly, but the flow assumes `User.query.get(user_id)` cannot return None (it can after admin user deletion, which doesn't happen but is unprotected).

---

## Security Considerations

### Authentication & session

**Default admin password / first-login flow:**
- Risk: Demo deploys ship with `admin/admin` (per README §1.1). On a fresh instance, the very first request hitting `/login` redirects to `/set-password` (`shkeeper/auth.py:171`) — anyone reaching the instance before the operator does owns it.
- Files: `shkeeper/auth.py:138-189`, README:54-56.
- Current mitigation: 2FA can be enabled (`shkeeper/auth.py:250`) post-login; wallet encryption can be set up.
- Recommendations: Require `INITIAL_ADMIN_PASSWORD` env var on first run; fail closed if absent. Document the race condition.

**No rate limiting on login or 2FA endpoints:**
- Risk: `/login` (`shkeeper/auth.py:138`), `/2fa/verify` (`shkeeper/auth.py:199`), `/2fa/setup` and `/api/v1/decryption-key` (`shkeeper/api_v1.py:789`) are all unrate-limited. Brute-force of 6-digit TOTP (with `valid_window=1` in `models.py:55` widens the window further) and password attempts are unconstrained.
- Files: `shkeeper/auth.py` (entire file), no `Flask-Limiter` import anywhere in the codebase (verified by grep).
- Current mitigation: bcrypt rounds=12 makes per-attempt costly, but parallel attempts unbounded.
- Recommendations: Add Flask-Limiter (or similar) with stricter per-IP limits on `/login`, `/2fa/verify`, `/api/v1/decryption-key`, and exponential backoff for repeated failure on a single user record.

**No CSRF protection on form-based routes:**
- Risk: No `Flask-WTF`, no CSRF token in templates (verified — no `CSRFProtect` import in repo). Forms in `shkeeper/auth.py` (login, set-password, 2FA enable/disable, regenerate backup codes), `shkeeper/wallet.py:217` (rate edits), `:692` (set wallet encryption), `:709` (unlock) all rely on session cookie alone.
- Files: `shkeeper/auth.py:177-189` (set-password), `shkeeper/auth.py:309-336` (disable_2fa), `shkeeper/wallet.py:662-710` (process_unlock), `shkeeper/wallet.py:214-241` (save_rates).
- Current mitigation: Session cookies (Flask-Session filesystem). Default Flask cookie has `httponly=True` but no explicit `SameSite=Strict` / `Secure` configured.
- Recommendations: Add CSRF tokens; set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_SAMESITE='Lax'` (or Strict for the auth flow), `SESSION_COOKIE_HTTPONLY=True` explicitly.

**SECRET_KEY default:**
- Risk: `shkeeper/__init__.py:60` sets `SECRET_KEY="dev"` as a hard-coded fallback. Override is via `instance/config.py` only — there's no env var fallback and no fail-closed behavior. A misconfigured deployment ships with a guessable session key.
- Files: `shkeeper/__init__.py:60`.
- Current mitigation: Comment "should be overridden by instance config" (humans only).
- Recommendations: Use `secrets.token_urlsafe(64)` and persist in instance config on first run, OR refuse to boot when `SECRET_KEY` is the default in non-DEV_MODE.

**Timing-safe comparison on backend key checks:**
- Risk: `shkeeper/api_v1.py:452, 483, 565`: `if request.headers["X-Shkeeper-Backend-Key"] != bkey` uses Python's `!=` — not constant-time. Same for `apikey` lookup `Wallet.query.filter_by(apikey=apikey).first()` (`shkeeper/auth.py:96`) — a DB lookup leaks timing through index comparison patterns.
- Files: `shkeeper/api_v1.py:452,483,565`, `shkeeper/auth.py:96`.
- Current mitigation: None.
- Recommendations: Use `hmac.compare_digest()` for header/key comparisons. (`grep -rn "compare_digest"` returned no hits.)

**Default metric credentials:**
- Risk: `shkeeper/auth.py:36-37` defaults `METRICS_USERNAME=shkeeper`, `METRICS_PASSWORD=shkeeper` — and the README explicitly documents these defaults. `/metrics` exposes per-host RPC connectivity, version info, last-block heights.
- Files: `shkeeper/auth.py:33-47`.
- Current mitigation: Basic auth at all (better than open).
- Recommendations: Refuse to boot with default values in non-dev mode.

### Cryptocurrency private key handling

**PBKDF2 salt is a hard-coded string:**
- Risk: `shkeeper/wallet_encryption.py:73`: `salt = b"Shkeeper4TheWin!"` — hard-coded across all installations. Combined with PBKDF2-HMAC-SHA256 / 500k iterations / Fernet, this means rainbow-table-style precomputation is feasible across all SHKeeper installations (an attacker with a leaked encrypted seed from any instance can build a single attack table that works against all others).
- Files: `shkeeper/wallet_encryption.py:71-81`.
- Current mitigation: 500k iterations slow things down; user-chosen passphrase is the only entropy.
- Recommendations: Generate per-installation random salt at first encryption, store alongside the password hash in `Setting`. Migrate existing installations on next unlock.

**`wallet_encryption._key` default = `"shkeeper"`:**
- Risk: `shkeeper/wallet_encryption.py:25` initializes `_key = "shkeeper"`. If wallet encryption is *disabled*, this string is what `wait_for_key` returns (`shkeeper/wallet_encryption.py:124-125`). The code path therefore always derives a known-plaintext Fernet key for unencrypted-wallet deployments — meaning if anyone ever toggled the wallet to "disabled" but kept storing seeds via `encrypt_text()` (e.g. `bitcoin_lightning.py:391`), those seeds are recoverable by anyone with DB access.
- Files: `shkeeper/wallet_encryption.py:25, 67-68, 119-125`.
- Current mitigation: Persistent status check — if disabled, plaintext storage is intended.
- Recommendations: Document explicitly that disabled-encryption mode = plaintext-equivalent. Refuse to call `encrypt_text()` when `persistent_status() == disabled`.

**Lightning seed read-from-disk-then-encrypt race:**
- Risk: `shkeeper/modules/cryptos/bitcoin_lightning.py:383-401` reads the cleartext mnemonic from `LND_SHARED_DIR/wallet-seed`, encrypts it into the DB, then `unlink`s the file. If any error occurs between read and unlink the file persists; if any error occurs after read, the seed is in process memory until GC.
- Files: `shkeeper/modules/cryptos/bitcoin_lightning.py:368-407`.
- Current mitigation: try/except retries on each loop iteration.
- Recommendations: After reading, immediately `unlink` THEN encrypt — failure leaves an unbacked-up seed (worse for availability, better for confidentiality). Decision needed.

**Wallet backup endpoint downloads private material in JSON:**
- Risk: `/api/v1/<crypto_name>/backup` (`shkeeper/api_v1.py:604-625`) returns the wallet dump (which for Btc/Eth/Tron contains private keys / accounts) as a downloadable JSON file behind only `@login_required`. There is no MFA gate, no audit trail.
- Files: `shkeeper/api_v1.py:604`.
- Current mitigation: Login + wallet encryption for at-rest data.
- Recommendations: Require 2FA confirmation token before each backup. Log every backup with timestamp and source IP into a separate immutable audit log.

### Webhook signature verification

**No HMAC signature on outbound merchant callbacks:**
- Risk: `shkeeper/callback.py:41` (`send_unconfirmed_notification`), `:118` (`send_notification`), `:295` (`send_payout_notification`) POST JSON to merchant `callback_url` with only `X-Shkeeper-Api-Key: <wallet.apikey>` as the authentication header. There is no body signature, no timestamp, no nonce. A merchant cannot distinguish legitimate SHKeeper callbacks from attacker-replayed/forged ones if the API key ever leaks.
- Files: `shkeeper/callback.py:41-46, 117-123, 264-310`.
- Current mitigation: Static API-key in header.
- Recommendations: Sign the body with HMAC-SHA256 over `(timestamp, body)`, send `X-Shkeeper-Signature` and `X-Shkeeper-Timestamp` headers; merchants verify with shared secret. Document this in the README callback section.

**Callback-URL validation is permissive:**
- Risk: `shkeeper/services/payout_service.py:26-33` only checks scheme is http/https and netloc non-empty. No SSRF protection — a malicious merchant could submit `callback_url` like `http://169.254.169.254/...` (cloud metadata) or internal hosts; SHKeeper would dutifully POST to them.
- Files: `shkeeper/services/payout_service.py:26-33`.
- Current mitigation: Scheme allowlist only.
- Recommendations: Reject loopback/private/link-local on callback POST.

### Input validation on API endpoints

**LIKE-injection / unconstrained search via `field.contains`:**
- Risk: `shkeeper/wallet.py:284, 293, 298, 303, 308, 314, 424` build SQLAlchemy `.filter(field.contains(request.args[arg]))` directly from query strings. SQLAlchemy escapes the value (no SQLi), but **any** column with `hasattr(Transaction, arg) == True` becomes searchable, including internal fields like `id`, `invoice_id`, `callback_confirmed`. The `.contains()` substring search on indexed numeric columns will trigger full table scans.
- Files: `shkeeper/wallet.py:276-315, 419-429`.
- Current mitigation: Login required; SQLAlchemy parameterizes values.
- Recommendations: Allowlist the searchable fields explicitly.

**`request.get_json(force=True)` everywhere:**
- Risk: 22+ uses of `force=True` (e.g. `api_v1.py:124, 168, 214, 224, 268, 294, 323, 439, 456, 631, 668, 813`) — accepts JSON regardless of `Content-Type`, including text/plain. Combined with no input schema validation (Pydantic is imported but only used for Tron staking), this means malformed/oversized payloads are processed.
- Files: `shkeeper/api_v1.py` (many handlers).
- Current mitigation: Per-field key access raises KeyError if missing → 500 with traceback (information leak).
- Recommendations: Define Pydantic schemas (since `pydantic==2.11.7` already in deps) for every POST endpoint. Validate amount/address shapes explicitly.

**API-key lookup is global, not scoped to crypto:**
- Risk: `shkeeper/auth.py:96` — `Wallet.query.filter_by(apikey=apikey).first()` — any wallet's key authenticates any `<crypto>` endpoint. Combined with `payment_gateway_set_token` (`api_v1.py:221-228`) which **sets the same token across all wallets**, this is by design — but it means compromising any crypto API key compromises all of them.
- Files: `shkeeper/auth.py:89-102`, `shkeeper/api_v1.py:221-228`.
- Current mitigation: Documented as single-token system.
- Recommendations: Document the trust model loudly in README. Consider per-merchant tokens for multi-tenant deployments.

### Container security (Dockerfile)

**Running as root:**
- Risk: `Dockerfile` (entire file, only 24 lines) does **not** create or `USER` switch to a non-root user. Gunicorn runs as root inside the container; instance/sqlite DB writes happen as root.
- Files: `Dockerfile`.
- Current mitigation: None.
- Recommendations: Add `RUN useradd -u 10001 shkeeper && chown -R shkeeper /shkeeper.io` and `USER shkeeper`.

**`python:3.13` not pinned to digest:**
- Risk: `FROM python:3.13` (no `-slim`, no SHA256 digest). Image is ~1GB and any tag re-push by upstream changes the build.
- Files: `Dockerfile:1`.
- Current mitigation: None.
- Recommendations: `FROM python:3.13.X-slim@sha256:...`. Minimizes attack surface and locks the build.

**Apt install includes `git`, `curl`, `sqlite3` for runtime image:**
- Risk: `Dockerfile:3` installs build-time tooling into the runtime container. Increases attack surface (git lets an attacker who pops a shell easily exfiltrate). `python3 python3-pip` are also installed alongside the upstream Python image's already-present Python.
- Files: `Dockerfile:3`.
- Current mitigation: None.
- Recommendations: Multi-stage build (builder vs runtime); only `apt-get install -y --no-install-recommends sqlite3` if needed at all.

**`COPY . .` ships everything:**
- Risk: Without a `.dockerignore`, every git-ignored file in cwd at build time is shipped to the image (instance/, .env, secrets, .git history).
- Files: `Dockerfile:7`.
- Recommendations: Add `.dockerignore` that excludes `.git`, `instance/`, `*.sqlite`, `.env*`, tests, dev configs.

---

## Performance Bottlenecks

Performance profile unknown — no APM/profiling traces in repo. Below are observed structural risks; recommend instrumenting before optimizing.

**Synchronous fanout to crypto backends per request:**
- Problem: `/api/v1/crypto/balances` calls `_build_balance` for each enabled crypto via ThreadPoolExecutor → each crypto issues a synchronous HTTP POST to its sidecar service for balance + status.
- Files: `shkeeper/services/balance_service.py:9-50`.
- Cause: Synchronous I/O from a single worker; total latency = max(crypto_rpc_latency).
- Improvement path: Add Redis caching (already done via `get_available_cryptos` which has a TTL cache per docs) but extend to per-crypto balance with explicit invalidation on `walletnotify`.

**`/metrics` endpoint blocks while polling all crypto sidecars:**
- Problem: `shkeeper/wallet.py:567-594` issues one HTTP call per unique crypto base class. With 14+ crypto classes and a 10-second timeout each (`btc.py:149`), a slow sidecar can extend Prometheus scrape duration past Prometheus's 30s default.
- Files: `shkeeper/wallet.py:567-594`, `shkeeper/modules/classes/btc.py:141-155`.
- Cause: No caching layer between Prometheus and the sidecars; serial-with-thread-pool blocking.
- Improvement path: Background scrape into a Prometheus pull-cache; serve cached metrics from `/metrics`.

**Single gunicorn worker, 16 threads:**
- Problem: `Dockerfile:15-17` — `--workers 1 --threads 16 --worker-class gthread`. All requests share one Python interpreter (GIL) → CPU-heavy ops (bcrypt verify at rounds=12, PBKDF2 at 500k iterations) serialize.
- Files: `Dockerfile:11-23`.
- Cause: Designed for vertical, not horizontal, scaling.
- Improvement path: For a write-heavy single-master DB this is OK; document the assumption. For higher load, increase workers and migrate session storage off filesystem (see Scaling Limits).

**APScheduler running 60-second polling tasks:**
- Problem: `shkeeper/tasks.py` runs five jobs every 60s — `update_confirmations`, `send_callbacks`, `poll_all_pending_payouts`, `poll_unconfirmed_payouts`, `send_payout_callback_notifier`, `task_payout`. Each iterates all transactions/payouts and calls remote RPCs per row.
- Files: `shkeeper/tasks.py`, `shkeeper/callback.py:142-360`.
- Cause: O(n) scan with synchronous RPCs.
- Improvement path: Add indexes on `Transaction.callback_confirmed` and `Notification.retries`. Use a queue/worker model (Celery, RQ) once load grows.

---

## Fragile Areas

**`Invoice.add` (180 lines of branching):**
- Files: `shkeeper/models.py:414-507`.
- Why fragile: Mixes new-vs-existing-invoice paths, lightning vs non-lightning, BTC sub-invoice generation; multi-`db.session.commit()` calls without transactional grouping; raises `Exception` in middle of the function.
- Safe modification: Don't change without writing tests first; the code mutates DB state across multiple commits. A failure halfway leaves the DB in inconsistent state.
- Test coverage: **None observed.** No tests in repo.

**Wallet encryption singleton (class-level mutable state):**
- Files: `shkeeper/wallet_encryption.py:24-68`.
- Why fragile: `_key` and `_runtime_status` are class attributes mutated by `set_key`/`set_runtime_status`. Combined with `@cached_property` (`_fernet_key`), changing the key after first encrypt yields stale Fernet keys; thread-safety relies on GIL only.
- Safe modification: Avoid touching key handling unless replacing the whole module. Always derive Fernet from the current `_key` rather than caching.
- Test coverage: None.

**Threaded Lightning workers:**
- Files: `shkeeper/modules/cryptos/bitcoin_lightning.py:83-124`.
- Why fragile: `start_threads()` spawns 4 daemon threads at import time (`invoice_listener`, `invoice_refresher`, `invoice_notificator`, `seed_saver`, `lnurl_setup`). Each requires `app._get_current_object()` and a Flask app context. These threads wait on `shkeeper_initialized` (`shkeeper/events.py`). Reloads (gunicorn `--reload`) double-spawn; failures silently sleep-retry forever.
- Safe modification: If LND becomes slow, threads can wedge; restart only fix.
- Test coverage: None.

**Backend-key validation pattern duplicated 3 times:**
- Files: `shkeeper/api_v1.py:446-454, 470-485, 552-567`.
- Why fragile: Same env var read (`SHKEEPER_BTC_BACKEND_KEY` always — even for non-BTC routes!) with hard-coded default `"shkeeper"`. Three copies → three places to forget to fix.
- Safe modification: Replace with a `@backend_key_required` decorator; fix the misnamed env var simultaneously.
- Test coverage: None.

**`Transaction.add` and `update_with_tx` rely on float-decimal arithmetic + DB commits inside loop:**
- Files: `shkeeper/models.py:379-411, 667-698`.
- Why fragile: `tx.amount_fiat = tx.amount_crypto * invoice.exchange_rate` mixes Decimal precision (good) with subsequent comparison `tx.invoice.balance_fiat < (tx.invoice.amount_fiat * (tx.invoice.wallet.llimit / 100))` where `llimit` is `db.Numeric` and division by literal `100` could narrow precision. Status bucket boundaries (`PARTIAL`/`PAID`/`OVERPAID`) flip on cents.
- Safe modification: Add fixture-based tests verifying status transitions at boundary values exactly.
- Test coverage: None.

---

## Scaling Limits

**SQLite single-file DB:**
- Current capacity: Default `instance/shkeeper.sqlite` (`shkeeper/__init__.py:62-64`). SQLite's writer-serialization caps writes at ~hundreds/sec.
- Limit: With APScheduler firing 5 jobs every 60s + every `walletnotify` issuing a write + every payout creating multi-row inserts, contention grows. Filesystem-based session storage (`SESSION_TYPE="filesystem"`) compounds I/O.
- Scaling path: Move to PostgreSQL (Flask-SQLAlchemy supports it transparently — change `SQLALCHEMY_DATABASE_URI`). Migrate `Setting` and session storage off filesystem.

**Single gunicorn worker:**
- Current capacity: 1 worker × 16 threads (`Dockerfile:15`). Estimate ~50-100 RPS for I/O-bound flows.
- Limit: CPU-bound tasks (bcrypt, PBKDF2) serialize. Adding workers requires shared session storage and DB-coordinated APScheduler (current code starts `scheduler.start()` per worker — see `__init__.py:227` — multi-worker would multiply background jobs).
- Scaling path: Use `apscheduler` with a shared store, OR move scheduling to a sidecar (one container running scheduler-only). Increase `--workers` on the API container.

**Polling-based blockchain sync:**
- Current capacity: `update_confirmations` and `poll_*_payouts` run every 60s.
- Limit: Latency to mark TX confirmed = up to 60s + wait for confirmations from the chain. For high-throughput merchant flows this is OK; for trading-style integrations it's slow.
- Scaling path: Push notifications from sidecar containers via `walletnotify` are already in place — extend coverage so polling becomes a fallback only.

**Filesystem-backed Flask sessions:**
- Current capacity: `SESSION_TYPE="filesystem"` (`shkeeper/__init__.py:66-67`). With single instance, no problem.
- Limit: Multi-instance deploys break — sessions don't cross. `shkeeper/__init__.py:108-109` clears the session dir on app start (in non-DEV) which kills all logged-in users on restart.
- Scaling path: Switch to Redis-backed Flask-Session for multi-instance.

---

## Dependencies at Risk

**Flask 2.2.2 (Aug 2022):**
- Risk: 3+ years out of date; line is no longer maintained. Werkzeug 2.3.7 is the floor. Known CVE-2023-30861 (Cookie cache disclosure for `permanent_session_lifetime`).
- Impact: Security-only patches not flowing.
- Migration plan: Bump to Flask 3.x in tandem with Werkzeug 3.x — moderate effort because `app.json_encoder/decoder` is deprecated in Flask 2.3+ (see `shkeeper/__init__.py:138-139`).

**Werkzeug 2.3.7 (Sep 2023):**
- Risk: Pinned exact. EOL line.
- Impact: As above.
- Migration plan: With Flask upgrade.

**SQLAlchemy 1.4 (no minor version pinned):**
- Risk: Major version 2.0 GA Jan 2023; 1.4 line is "legacy" support. The `==1.4` (no patch) pin will float to whatever 1.4.x is current.
- Impact: New code can't use `select()` / 2.0-style API; future upgrade is breaking.
- Migration plan: Move to 2.x. Many `Model.query.get()` calls need rewriting.

**Flask-SQLAlchemy 2.5.1:**
- Risk: 3.x has been GA for years. 2.5.x doesn't support SQLAlchemy 2.x.
- Impact: Blocks SQLAlchemy upgrade.
- Migration plan: Tightly coupled — bump together.

**bcrypt 3.2.2 (May 2022):**
- Risk: 4.x available since 2023 with new wheels and bug fixes.
- Impact: Low — bcrypt is mature.
- Migration plan: Bump to 4.x; verify hashes still verify (compatible).

**requests 2.28.1 (June 2022):**
- Risk: 2.32.x out. Several CVEs fixed in 2.31.0+ (e.g. CVE-2023-32681 Proxy-Authorization leak).
- Impact: Direct exposure since `requests` is used for every backend RPC.
- Migration plan: Bump to latest 2.x.

**`cryptography` (unpinned):**
- Risk: `requirements.txt:14` has bare `cryptography` with no version. Whatever pip resolves at install time is what you get — unpredictable across deploys.
- Impact: Reproducibility, supply-chain.
- Migration plan: Pin a known-good major and minor (e.g. `cryptography>=42,<46`).

**Flask-APScheduler 1.12.4 (Jan 2022):**
- Risk: 1.13 available; in-process scheduling pattern fragile (see Scaling Limits).
- Impact: Multi-worker safety.
- Migration plan: Either pin to a recent version or replace with a dedicated scheduler container.

**Flask-Session 0.4.0 (2022):**
- Risk: 0.8.x current; older versions had a hash-collision issue.
- Impact: Low for filesystem mode.
- Migration plan: Bump alongside Flask.

---

## Missing Critical Features (gaps)

**No automated tests:**
- Problem: `find . -name 'test_*.py' -o -name '*_test.py'` returned **zero matches**. No `tests/`, no `pytest`/`unittest` config, no CI test step in `.github/workflows`.
- Blocks: Refactoring confidently. Releasing without manual QA. Verifying invoice status transitions, fee calculations, payout idempotency. For a payment processor handling real money, this is the single biggest gap.

**No structured logging / no request IDs:**
- Problem: All logs use the default Flask logger format (`shkeeper/__init__.py:11-15`) — `LEVELNAME file:line func(): message`. No correlation IDs, no JSON-structured fields, no per-request trace.
- Blocks: Investigating "merchant X says invoice Y status was wrong" — you can't grep across services, callback retries are not stitched to source events.

**No rate limiting:**
- Problem: Confirmed by grep — no `Limiter` import. `/login`, `/2fa/verify`, `/api/v1/decryption-key`, `/api/v1/<crypto>/payment_request` (called by merchants on every customer page load) are all wide open.
- Blocks: DoS resistance; brute force resistance.

**No webhook signing for outbound callbacks:**
- Problem: See Security Considerations §Webhook signature verification.
- Blocks: Merchants cannot trust callback bodies.

**No database transaction grouping in multi-step writes:**
- Problem: `Invoice.add` calls `db.session.commit()` 4-5 times in different branches (`shkeeper/models.py:454, 475, 481, 506`). A crash mid-flow leaves the DB partially updated.
- Blocks: Atomicity guarantees for invoice creation.

**No audit log for sensitive operations:**
- Problem: No table records "admin enabled wallet encryption", "API key rotated", "wallet backed up", "payout submitted manually". Logs are stdout only and rotated by container.
- Blocks: Forensic investigation of misuse.

**No HTTPS enforcement:**
- Problem: No `Talisman`, no HSTS header configuration. Flask app accepts plaintext on port 5000.
- Blocks: Defense against passive eavesdropping if the operator forgets the reverse proxy TLS step.

---

## Test Coverage Gaps

**No tests for invoice lifecycle (HIGH):**
- What's not tested: `Invoice.add` (new + update branches), `Invoice.update_with_tx` (PARTIAL/PAID/OVERPAID transitions across `llimit`/`ulimit` thresholds), exchange-rate recalculation timing.
- Files: `shkeeper/models.py:333-507, 379-412`.
- Risk: Wrong status sent to merchant → merchant ships goods on a partial payment, or doesn't ship on a fully-paid invoice. Direct financial loss.
- Priority: **HIGH**.

**No tests for address generation correctness (HIGH):**
- What's not tested: That `crypto.mkaddr()` returns a unique address per invoice, that `InvoiceAddress` dedupes on (invoice_id, crypto, addr) correctly, that updating an existing invoice with a new crypto reuses an old address.
- Files: `shkeeper/modules/classes/btc.py:82-88`, `shkeeper/models.py:444-481`.
- Risk: Two customers paying different invoices to the same address → cross-attribution of payments; or generation of an address never tied to an invoice → funds stranded.
- Priority: **HIGH**.

**No tests for payout flow / idempotency (HIGH):**
- What's not tested: `PayoutService.single_payout`, `PayoutService.multiple_payout`, idempotency on duplicate `external_id`, `Payout.update_from_task` matching `dest_addr.lower()`.
- Files: `shkeeper/services/payout_service.py`, `shkeeper/models.py:736-787`.
- Risk: Duplicate payouts (double-send funds) or skipped payouts (stuck with task_id but no transactions).
- Priority: **HIGH**.

**No tests for confirmation race conditions (HIGH):**
- What's not tested: `walletnotify` fires twice for same txid (commits handle integrity error), `is_more_confirmations_needed` marks confirmed mid-cycle while `send_callbacks` sends the notification — interleaving with APScheduler.
- Files: `shkeeper/api_v1.py:467-547`, `shkeeper/callback.py:149-182, 346-359`, `shkeeper/models.py:700-706`.
- Risk: Callback delivered twice (merchant double-credits) or never (merchant doesn't credit).
- Priority: **HIGH**.

**No tests for wallet encryption flows (MEDIUM-HIGH):**
- What's not tested: `wait_for_key()` blocking semantics, `set_key`/`set_runtime_status` race between the unlock route and Lightning seed-saver thread, key change while encrypted seeds exist.
- Files: `shkeeper/wallet_encryption.py:108-138`, `shkeeper/wallet.py:621-710`.
- Risk: Forgotten password = unrecoverable wallet. Race in init = decrypted-with-default-key.
- Priority: **MEDIUM-HIGH**.

**No tests for fee calculation policies (MEDIUM):**
- What's not tested: `ExchangeRate.get_fee`, `get_orig_amount` for all four `FeeCalculationPolicy` cases, especially `PERCENT_OR_MINIMAL_FIXED_FEE` edge cases.
- Files: `shkeeper/models.py:268-297`.
- Risk: Merchant fees miscalculated, leading to support tickets and refund flows.
- Priority: **MEDIUM**.

**No tests for callback retry / backoff (MEDIUM):**
- What's not tested: `send_payout_callback_notifier` exponential backoff (`(retries+1)**2` seconds), max retries cutoff, retry storm if merchant returns 500 in a tight loop.
- Files: `shkeeper/callback.py:236-316`.
- Risk: Either over-retry (DoS merchant) or under-retry (merchant misses callback permanently).
- Priority: **MEDIUM**.

**No tests for input validation on API:**
- What's not tested: Invalid amount strings, negative amounts, oversized JSON bodies, missing required fields, type coercion.
- Files: `shkeeper/api_v1.py` (most POST handlers).
- Risk: 500 errors with traceback leak, processing of malformed input as zero.
- Priority: **MEDIUM**.

---

*Concerns audit: 2026-04-30*
