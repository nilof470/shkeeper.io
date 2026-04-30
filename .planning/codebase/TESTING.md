# Testing Patterns

**Analysis Date:** 2026-04-30

## Test Framework

**Runner:**
- None. No test runner is configured in this repository.
- `requirements.txt` does not include `pytest`, `unittest` (stdlib doesn't need declaring), `pytest-flask`, `pytest-cov`, `coverage`, `tox`, `nox`, or any other test tooling.
- No `pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`, or `conftest.py` exists in the repository.

**Assertion Library:**
- Not detected. The only `assert` statements found are in production code (`shkeeper/wallet_encryption.py:128, 135` — used as runtime invariants inside `wait_for_key`), not as test assertions.

**Run Commands:**
```bash
# No test commands are configured.
# There is no `make test`, no `pytest`, no `python -m unittest`, no `tox`.
```

## Test File Organization

**Location:** Not detected. A repository-wide search for `test_*.py`, `*_test.py`, `tests/`, and `test/` directories returned no results (excluding `__pycache__`).

**Naming:** N/A — no tests exist.

**Structure:**
```
No test directory present.
```

## Test Structure

**Suite Organization:**
```python
# No test suites exist in this repository.
# Sample skeleton (recommended starting point) is in the Recommendations section below.
```

**Patterns:** None observed.

## Mocking

**Framework:** Not used. No imports of `unittest.mock`, `pytest-mock`, `mocker`, `responses`, or `requests-mock` anywhere in the codebase.

**Patterns:**
```python
# No mocking patterns established.
```

## Fixtures and Factories

**Test Data:** Not detected.

**Location:** N/A — no `conftest.py` exists.

## Coverage

**Requirements:** None enforced. No coverage tool configured.

**View Coverage:**
```bash
# Coverage not configured.
```

## Test Types

**Unit Tests:** Not detected.
**Integration Tests:** Not detected.
**E2E Tests:** Not used.

## Common Patterns

**Async Testing:** N/A — the codebase uses `concurrent.futures.ThreadPoolExecutor` (e.g., `shkeeper/services/balance_service.py:45-48`, `shkeeper/services/crypto_cache.py:15`, `shkeeper/wallet.py:582-589`) but no `asyncio` and no async test patterns.

**Error Testing:** N/A. There is anecdotal evidence of CLI-based smoke testing — `shkeeper/callback.py:362-403` exposes `flask callback list/send/update/add` Click commands that exercise the notification path manually, but these are operator tools, not automated tests.

## CI Test Hooks

The two GitHub Actions workflows in `.github/workflows/` (`ci.yml`, `ci-dev.yml`) only build and push a Docker image. There is no lint, type-check, or test step in CI.

```yaml
# .github/workflows/ci.yml — release on tag push
# - checkout
# - docker meta
# - login to Docker Hub
# - build and push image
```

## Recommendations

There are zero automated tests. Adding even a thin test suite would meaningfully reduce risk for a crypto-payment processor where bugs can cause direct financial loss. Recommended approach:

### 1. Framework: pytest

Add to `requirements.txt` (or a new `requirements-dev.txt`):

```
pytest>=8.0
pytest-flask>=1.3
pytest-mock>=3.12
responses>=0.25            # mock outgoing requests to crypto backends
freezegun>=1.4             # control time in scheduler/payout windows
coverage>=7.4
```

### 2. Layout

```
tests/
  conftest.py              # app + db fixtures, common patches
  unit/
    test_utils.py          # shkeeper.utils.format_decimal, remove_exponent
    test_wallet_encryption.py  # WalletEncryptionPersistentStatus, key derivation
    test_models_invoice.py # Invoice state transitions, fee math
    test_payout_service.py # PayoutService.single_payout / multiple_payout
    test_balance_service.py
    test_crypto_cache.py   # TTLCache.remember, get_available_cryptos
  integration/
    test_api_v1_payment_request.py
    test_api_v1_walletnotify.py    # tx ingestion -> invoice update -> callback
    test_api_v1_payout.py
    test_auth_login_2fa.py
```

### 3. conftest.py skeleton

```python
import pytest
from shkeeper import create_app, db as _db

@pytest.fixture
def app():
    app = create_app(test_config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def db(app):
    return _db
```

Note: `shkeeper.create_app()` already accepts a `test_config` argument (`shkeeper/__init__.py:55, 91-96`) — the API is test-friendly. The remaining work is mocking the crypto driver RPC calls (every driver's `gethost()` returns a Docker-network hostname that won't resolve in CI).

### 4. Mocking strategy

- Patch crypto drivers per-test with `mocker.patch.object(Crypto.instances["BTC"], "balance", return_value=Decimal("1.5"))` rather than relying on real `requests.post` calls to `bitcoin-shkeeper:6000`.
- Use `responses` library to stub the `requests.get/post` calls inside crypto driver methods when full-stack behavior is needed.
- For `requests` notification callbacks (in `shkeeper/callback.py`), use `responses` to assert the outgoing payload shape.

### 5. Priority ordering (highest financial-risk first)

1. **`PayoutService` and autopayout policy logic** (`shkeeper/services/payout_service.py`, `shkeeper/tasks.py:33-83`, `shkeeper/models.py` `Wallet.do_payout`). Every fee, reserve-policy branch, and external_id uniqueness check should be covered.
2. **Invoice state transitions** (`shkeeper/models.py` `Invoice.update_with_tx`, `Transaction.add`, the PAID/OVERPAID/UNDERPAID/EXPIRED paths). These determine when the merchant gets credited.
3. **Wallet encryption** (`shkeeper/wallet_encryption.py`). The persistent/runtime state machine and PBKDF2 key derivation are security-critical.
4. **Authentication & 2FA** (`shkeeper/auth.py`). Cover login, TOTP verification, backup-code single-use semantics, and session timeout.
5. **Webhook callback delivery** (`shkeeper/callback.py` `send_notification`, `send_payout_notification`, retry/backoff in `send_payout_callback_notifier`). These are the merchant-facing contract.
6. **API error response shape** — every endpoint that returns `{"status": "error", ...}` should be tested for both success and failure paths, including HTTP status codes (the codebase is currently inconsistent here — see CONVENTIONS.md "Error Handling").

### 6. CI integration

Add a `test` job to `.github/workflows/ci-dev.yml`:

```yaml
test:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v3
    - uses: actions/setup-python@v5
      with:
        python-version: "3.13"
    - run: pip install -r requirements.txt -r requirements-dev.txt
    - run: pytest --cov=shkeeper --cov-report=term-missing
```

### 7. Smoke tests for crypto drivers

Crypto drivers (`shkeeper/modules/cryptos/*.py`) follow a stable interface defined by `shkeeper/modules/classes/crypto.py`. A shared parameterized test that walks the abstract methods (`balance`, `getstatus`, `mkaddr`, `getaddrbytx`, `mkpayout`) per driver — with mocked HTTP — would catch regressions across the 30+ supported tokens cheaply.

---

*Testing analysis: 2026-04-30*
