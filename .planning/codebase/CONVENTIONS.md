# Coding Conventions

**Analysis Date:** 2026-04-30

## Naming Patterns

**Files:**
- snake_case `.py` files (e.g., `shkeeper/wallet.py`, `shkeeper/api_v1.py`, `shkeeper/wallet_encryption.py`)
- Crypto driver files use lower-case (sometimes hyphenated for tokens) reflecting the symbol name (e.g., `shkeeper/modules/cryptos/btc.py`, `shkeeper/modules/cryptos/eth-usdt.py`, `shkeeper/modules/cryptos/arb-pyusd.py`). Hyphens in filenames are unusual for Python and rely on the dynamic loader in `shkeeper/modules/cryptos/__init__.py`.

**Functions:**
- snake_case throughout (e.g., `get_crypto_label`, `send_unconfirmed_notification`, `update_confirmations`).
- Private/helper functions prefixed with underscore (e.g., `_filter_metrics`, `_FILTERED_METRIC_SUFFIXES` in `shkeeper/wallet.py`, `_build_balance` in `shkeeper/services/balance_service.py`, `_fetch_available_cryptos` in `shkeeper/services/crypto_cache.py`).

**Variables:**
- snake_case for locals and module-level state.
- Loop variables are often single letters (`r`, `t`, `c`, `i`, `p`, `w`, `k`, `v`) — see `shkeeper/wallet.py:228-241` and most query handlers.

**Classes:**
- PascalCase for normal classes (e.g., `PayoutService` in `shkeeper/services/payout_service.py`, `TTLCache` in `shkeeper/services/cache_service.py`, `Wallet`, `User`, `Invoice` in `shkeeper/models.py`).
- PascalCase for pydantic schemas (e.g., `TronAccount`, `TronAccountResponse`, `TronError` in `shkeeper/schemas.py`).
- INCONSISTENCY: `wallet_encryption` is a class but uses snake_case (`shkeeper/wallet_encryption.py:24`). It is used as a singleton-like static container and instantiated nowhere.
- INCONSISTENCY: Crypto driver classes mix conventions — abstract base classes are PascalCase (`Crypto`, `Btc`, `Ethereum` in `shkeeper/modules/classes/`), while concrete subclasses in `shkeeper/modules/cryptos/` are lowercase (`class btc(Btc):`, `class eth(Ethereum):`). The `__init_subclass__` hook in `shkeeper/modules/classes/crypto.py:17-90` keys off these lowercase class names for the env-driven enable/disable matrix — do not rename without updating the registry.

**Constants:**
- UPPER_SNAKE_CASE (e.g., `PUBLIC_SETTINGS` in `shkeeper/api_v1.py:51`, `DEFAULT_CURRENCY` in `shkeeper/callback.py:13`, `CACHE_TTL` in `shkeeper/services/crypto_cache.py:7`, `_FILTERED_METRIC_SUFFIXES` in `shkeeper/wallet.py:597`).
- Enum members in `models.py` are UPPER_SNAKE_CASE (e.g., `PayoutStatus.IN_PROGRESS`); members in `wallet_encryption.py` are lowercase (`pending`, `disabled`, `enabled`) — minor inconsistency.

**Modules/Packages:**
- snake_case directories under `shkeeper/` (e.g., `shkeeper/services/`, `shkeeper/modules/classes/`, `shkeeper/modules/cryptos/`, `shkeeper/modules/rates/`).

**Prescriptive guidance:**
- Use snake_case for any new function, variable, file, or module.
- Use PascalCase for any new class. The lowercase-class pattern in `shkeeper/modules/cryptos/` is load-bearing legacy — only follow it inside that exact directory.
- Use UPPER_SNAKE_CASE for module-level constants and enum members; align new enums with `models.py`.

## Code Style

**Formatting:**
- No formatter configured. There is no `pyproject.toml`, `.flake8`, `setup.cfg`, `setup.py`, `.editorconfig`, or `.pre-commit-config.yaml` at the repository root.
- Indent: 4 spaces (consistent across sampled files).
- Line length: not enforced. Code generally hovers around 80-120 chars; `shkeeper/__init__.py:11-15` and `shkeeper/wallet.py:482` show no hard wrap rule.
- Strings: double quotes are dominant (`"BTC"`, `"status": "success"`). Single quotes appear occasionally (e.g., `shkeeper/callback.py:13`).
- Black-compatible look-and-feel in newer code (trailing commas in multi-line collections, double quotes), but not enforced. The project would format cleanly under `black` with default settings.

**Linting:**
- None configured. No `flake8`, `pylint`, `ruff`, or `mypy` config detected.
- CI (`.github/workflows/ci.yml`, `.github/workflows/ci-dev.yml`) only builds and pushes a Docker image — there is no lint or test step.

**Type Hints:**
- Sparse and inconsistent. Around 20 annotated function returns across the entire `shkeeper/` package (per `grep "def .*->.*:" shkeeper/`).
- Used systematically only in pydantic models (`shkeeper/schemas.py`) and a few newer/refactored files:
  - `shkeeper/utils.py:4` and `shkeeper/utils.py:12` — typed parameters and return.
  - `shkeeper/services/cache_service.py:8` — `Callable[[], Any]`.
  - `shkeeper/services/balance_service.py:33` — uses `list[str] | None` (PEP 604, Python 3.10+).
  - `shkeeper/modules/classes/crypto.py:8` — `Dict[str, "Crypto"]`.
  - `shkeeper/modules/cryptos/monero.py` and `shkeeper/modules/cryptos/bitcoin_lightning.py` — partial annotations on overrides.
- Most Flask views, model methods, and crypto drivers are unannotated.

**Prescriptive guidance:**
- New code should add type hints, especially on public functions and service-layer methods. Match the style in `shkeeper/services/balance_service.py` and `shkeeper/services/cache_service.py`.
- If introducing a formatter, `black` with line length 100 would be a low-friction fit given existing style.

## Import Organization

**Order observed (loosely consistent — not enforced):**
1. stdlib (e.g., `os`, `functools`, `datetime`, `decimal`, `concurrent.futures`).
2. third-party (e.g., `flask`, `sqlalchemy`, `bcrypt`, `pyotp`, `requests`, `pydantic`, `segno`, `cryptography`).
3. local app (`from shkeeper import db`, `from shkeeper.models import ...`, `from .modules import ...`).

**Common patterns:**
- One import per line is dominant for `from x import y` (see `shkeeper/wallet.py:11-21`).
- Mid-module imports are common — see `shkeeper/__init__.py:154-156, 184-185, 192` (used to break circular dependencies between `shkeeper`, `shkeeper.models`, and crypto drivers).
- `from shkeeper.models import *` appears in `shkeeper/api_v1.py:30`, `shkeeper/tasks.py:7`, `shkeeper/callback.py:8`. Star imports are tolerated in this codebase but are not best practice.
- `from flask import current_app as app` is the canonical alias pattern (`shkeeper/wallet.py:20`, `shkeeper/api_v1.py:16`, `shkeeper/callback.py:5`).

**Path Aliases:**
- None — direct package imports only (`shkeeper.module.x`).

**Prescriptive guidance:**
- Avoid `from shkeeper.models import *` in new files — import the specific names you use.
- Keep mid-module imports only for genuine circular-dependency breaks; otherwise put imports at the top.

## Error Handling

**Patterns observed:**
- Heavy reliance on broad `except Exception as e:` followed by either `app.logger.exception(...)` or `app.logger.warning(...)`. Examples: `shkeeper/api_v1.py:60-64`, `shkeeper/api_v1.py:132-138`, `shkeeper/callback.py:124-127`, `shkeeper/wallet.py:107-109` (silent fallback on QR generation).
- API endpoints typically swallow exceptions and return JSON dicts of the form `{"status": "error", "message": str(e), "traceback": traceback.format_exc()}` with a 500 (or no) status code. See `shkeeper/api_v1.py:132-138, 191-197, 711-717`. Returning a full traceback to clients is a common pattern in this codebase but is a security risk.
- A reusable decorator `handle_request_error` exists in `shkeeper/api_v1.py:55-64` — wraps a view, catches exceptions, logs `"Payout error"`, and returns `{"status": "error", "message": str(e)}, 500`. Used on `payout` and `multipayout` endpoints. Prefer this for new API endpoints to standardize error shape.
- HTTP errors via Flask: `from werkzeug.exceptions import abort` then `abort(404)` (e.g., `shkeeper/wallet.py:192, 219`). Custom 404 and 500 handlers in `shkeeper/__init__.py:47-52, 241-242`.
- `KeyError` on `Crypto.instances[crypto_name]` is caught explicitly to return a "payment gateway is unavailable" message (`shkeeper/api_v1.py:103-109`).
- Custom exceptions: only one — `NotRelatedToAnyInvoice(Exception)` in `shkeeper/exceptions.py:1-2`. Raised in `shkeeper/models.py:573, 680` and caught in `shkeeper/api_v1.py:533-538`.
- Generic `raise Exception(f"...")` is used for domain errors (e.g., `shkeeper/models.py:198, 279, 297, 310, 502, 526`). This is widespread but is an anti-pattern.

**Smell:**
- `shkeeper/api_v1.py:280-286` has `raise e` followed by unreachable `response = {...}` lines.
- `shkeeper/api_v1.py:455` returns `{"status": "error", ...}` without an HTTP status code, defaulting to 200.

**Prescriptive guidance for new code:**
- Use `@handle_request_error` (or a similar named decorator) for new API endpoints rather than copy-pasting try/except blocks.
- Do not include `traceback.format_exc()` in API responses — log it via `app.logger.exception(...)` and return `{"status": "error", "message": "..."}` only.
- Prefer specific exception classes (extend `shkeeper/exceptions.py`) over `raise Exception(...)`.
- Always include an explicit HTTP status code on error responses, e.g., `return {"status": "error", ...}, 400`.

## Logging

**Framework:** Flask's per-app logger via `flask.current_app.logger` (`from flask import current_app as app` then `app.logger.info(...)`). Inside scheduler tasks, `scheduler.app.logger` is used (`shkeeper/tasks.py`). Setup is in `shkeeper/__init__.py:11-15, 116-123` — sets a custom log format `"%(levelname)s %(filename)s:%(lineno)s %(funcName)s(): %(message)s"` and toggles DEBUG/INFO based on `DEV_MODE`.

**Patterns:**
- `app.logger.info(f"...")`, `app.logger.warning(f"...")`, `app.logger.error(f"...")`, `app.logger.exception(...)` for exceptions (auto-attaches traceback).
- f-strings for log messages (e.g., `app.logger.info(f"[{tx.crypto}/{tx.txid}] Notification has been accepted")` in `shkeeper/callback.py:136-138`).
- Tag prefixes in brackets to denote subsystem: `[Autopayout]`, `[Create Wallet]`, `[PAYOUT {id}]`, `[{crypto}/{txid}]` — useful for log filtering.
- `app.logger.warning` is sometimes (mis)used for routine info (`shkeeper/api_v1.py:234, 488`, `shkeeper/callback.py:37`). New code should reserve `warning` for actual concerns.

**Not used:**
- `import logging; logger = logging.getLogger(__name__)` — never seen in this codebase. Stick with `app.logger`.

**Prescriptive guidance:**
- Use `app.logger.<level>(...)` from `from flask import current_app as app` in request/task code.
- Use `app.logger.exception(...)` inside `except` blocks; do not pass `traceback.format_exc()` manually.
- Match log levels to severity: `info` for normal events, `warning` for unexpected but recoverable, `error` for failures, `exception` for unhandled.

## Comments

**When to Comment:**
- Sparse but present where the logic is non-obvious. Examples: `shkeeper/wallet.py:570` (deduplication rationale for metrics), `shkeeper/__init__.py:104` ("clear all session on app restart"), `shkeeper/auth.py:168` ("Normal login without 2FA").
- Larger commented-out sample data blocks appear at the top of `shkeeper/schemas.py:7-75` to document expected JSON payload shape — accept this as reference documentation.
- Commented-out code is left in several places (e.g., `shkeeper/api_v1.py:45-49, 121-135`, `shkeeper/callback.py:107-108`) — should be removed once obsolete.
- `# TODO: implement` markers in `shkeeper/api_v1.py:593, 600`.

**Docstrings:**
- Style: short, one-line, triple-double-quoted. Mostly behavioral descriptions. Not Google-, NumPy-, or Sphinx-style.
- Examples:
  - `shkeeper/__init__.py:56` — `"""Create and configure an instance of the Flask application."""`
  - `shkeeper/auth.py:51, 72, 107, 140` — view-decorator and view-function summaries.
  - `shkeeper/models.py:38, 42, 50, 58, 69` — User method docstrings (about 5-6 word summaries).
  - `shkeeper/callback.py:364, 370, 376` — CLI command docstrings (used by Click as `--help` text).
- Coverage is partial: most utility/private functions have no docstring; only public-facing views and model helpers tend to be documented.

**Prescriptive guidance:**
- For new code, add a short one-line docstring to every public function, view, and class method. Match the existing terse style in `shkeeper/auth.py`.
- Remove dead/commented-out code rather than leaving it in place.
- Use TODO comments sparingly, and link to an issue when possible.

## Function Design

**Size:** Mixed. Many short helpers (5-30 lines) but several large request handlers exceed 80 lines (e.g., `shkeeper/api_v1.py:467-547` `walletnotify`, `shkeeper/wallet.py:269-395` `parts_transactions` ~125 lines). Background tasks like `shkeeper/tasks.py:33-83` `task_payout` are similarly long.

**Parameters:**
- Heavy use of positional + `**kwargs` on driver overrides (see `shkeeper/modules/classes/crypto.py:109` `mkaddr(self, **kwargs)`).
- API view handlers receive URL parameters as positional args (Flask's normal routing pattern).
- Default values are common and used to drive optional behavior (e.g., `format_decimal(d: Decimal, precision: int = 8, st: bool = False)` in `shkeeper/utils.py:12`).

**Return Values:**
- API endpoints return `dict` (Flask auto-jsonifies). Many also include an explicit status tuple `(dict, http_status)` — e.g., `shkeeper/api_v1.py:236, 254, 398, 419, 431, 472, 546`. Inconsistent: some return plain dicts which default to 200 even on errors.
- Service layer (`shkeeper/services/payout_service.py`) returns either dicts or raises `ValueError` for the calling layer to translate.
- HTML routes return `render_template(...)` or `redirect(url_for(...))`.
- Tuples are used as a tagged-result pattern: e.g., `get_balances` returns `(balances, error)` in `shkeeper/services/balance_service.py:42, 50`.
- Custom JSON serialization for `Decimal` is centralized in `ShkeeperJSONDecoder/Encoder` (`shkeeper/__init__.py:128-139`); always pass `Decimal` through the response, never floats.

**Prescriptive guidance:**
- Keep new view handlers under ~50 lines. Push business logic into `shkeeper/services/` modules (the pattern used by `payout_service.py`, `balance_service.py`, `crypto_cache.py`).
- Always include explicit HTTP status codes on API responses.
- Continue using `Decimal` for money — never `float`. The JSON encoder handles serialization.

## Module Design

**Exports:**
- `__all__` is not used anywhere.
- Convention: `_` prefix denotes private/internal (e.g., `_FILTERED_METRIC_SUFFIXES`, `_filter_metrics`, `_build_balance`). No formal export gate beyond that.

**Pattern:**
- Each Flask blueprint is its own module: `shkeeper/auth.py` (`bp = Blueprint("auth", ...)`), `shkeeper/wallet.py` (`bp = Blueprint("wallet", ...)`), `shkeeper/api_v1.py` (`bp = Blueprint("api_v1", ..., url_prefix="/api/v1/")`), `shkeeper/callback.py` (`bp = Blueprint("callback", ...)`). All registered in `shkeeper/__init__.py:235-240`.
- Background scheduling is centralized in `shkeeper/tasks.py` using `flask_apscheduler` `@scheduler.task` decorators.
- Domain logic for crypto drivers uses an inheritance-by-mixin pattern: abstract base in `shkeeper/modules/classes/crypto.py:7` (`class Crypto(abc.ABC)`); per-family bases in `shkeeper/modules/classes/btc.py`, `ethereum.py`, `tron_token.py`, etc.; concrete drivers in `shkeeper/modules/cryptos/*.py` that inherit and only override what differs (often just `__init__` to set `self.crypto` and `getname`).
- Driver auto-registration: `shkeeper/modules/cryptos/__init__.py` walks the directory and dynamically imports every `.py` file; `Crypto.__init_subclass__` (`shkeeper/modules/classes/crypto.py:17-90`) instantiates the class and registers it in `Crypto.instances` based on env-var gating.
- Service layer (`shkeeper/services/`) is newer and uses static/class methods on a service class (`PayoutService`) or plain module-level functions (`get_balances`, `get_available_cryptos`). New cross-cutting business logic should land here.
- Pydantic models in `shkeeper/schemas.py` for typed external/RPC payloads (only Tron is fully modeled today).

**Prescriptive guidance:**
- New blueprints: one module per blueprint, name the module after the blueprint, expose `bp` at module top.
- New crypto driver: add to `shkeeper/modules/cryptos/<name>.py` with a lowercase class name; reuse the family base (`Btc`, `Ethereum`, `TronToken`, etc.); register the env-var key in the `default_off`/`default_on` lists in `shkeeper/modules/classes/crypto.py:23-73` so it can be enabled in deployment.
- New cross-cutting service: prefer `shkeeper/services/<name>_service.py` with either a class of static/class methods (like `PayoutService`) or module-level functions. Do not put business logic directly in view handlers.

---

*Convention analysis: 2026-04-30*
